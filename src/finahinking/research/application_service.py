"""Application-owned composition of replayable inputs, spawn jobs and reports."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from finahinking.data.snapshot_store import SnapshotStore

from .contracts import (
    CheckpointIdentity,
    ResearchPlan,
    ResearchRequest,
    ResearchRunResult,
    ResearchState,
    stable_digest,
    to_jsonable,
)
from .factor_study_store import FactorStudyStore
from .job_queue import JobQueue, JobStatus, _ref
from .run_repository import RunRepository
from .run_store import ResearchRunStore, _decode_event, _decode_state

WORKFLOW_VERSION = "local-research.v1"
_ROLES = ("fundamentals", "technical", "sentiment", "news", "learning")
_STAGES = (
    "RECEIVED", "IDENTIFIED", "DATA_CHECKED", "ANALYSTS_RUNNING", "ANALYSTS_READY",
    "EVIDENCE_REVIEW", "RESEARCH_PLAN_READY", "QUANT_VALIDATION", "RISK_REVIEW",
    "PAPER_DECISION_READY", "REPORT_PUBLISHED", "LEARNING_RECORDED",
)
_FAILURES = (
    "RESOURCE_LIMIT", "TIMEOUT", "DATA_UNAVAILABLE", "DATA_INVALID", "NO_DATA_AVAILABLE",
    "QUANT_VALIDATION_FAILED", "RISK_REVIEW_FAILED", "VALIDATION_FAILED", "INTERNAL_ERROR",
    "ANALYST_REQUIRED_MISSING", "ANALYST_TIMEOUT", "SNAPSHOT_INTEGRITY_FAILED",
    "REQUEST_UNAVAILABLE", "PUBLICATION_INVALID", "RESULT_INVALID", "RUNNER_EXCEPTION", "CONTEXT_INTEGRITY_FAILED", "OUTBOUND_DISABLED",
    "WORKER_START_FAILED", "SUPERVISOR_STOPPED", "POLICY_CHANGED", "PROVIDER_NOT_CONFIGURED",
)
_RECEIPT_REF = re.compile(r"^research-result:([0-9a-f]{64})$")
_ATTEMPT_ID = re.compile(r"^[0-9a-f]{32}$")


def _utc(timestamp):
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


def _identity(request):
    return CheckpointIdentity(
        instrument=request.instrument, as_of=request.as_of,
        dataset_snapshot={"fingerprint": request.snapshot_ref}, analyst_set=request.analyst_roles,
        role_model_map={role: "offline/fixture-v1" for role in request.analyst_roles},
        skill_versions={"workflow": WORKFLOW_VERSION}, workflow_version=request.workflow_version,
        depth=1, rounds=1, config_digest=request.config_digest,
        research_plan_digest=stable_digest(request.research_plan),
    )


def _fence_digest(record):
    return stable_digest({"worker": record.worker_id, "attempt": record.attempts, "lease": record.lease_token})


def _read_json(path, *, max_bytes=1_000_000):
    if path.is_symlink():
        raise ValueError("research artifact integrity check failed")
    with path.open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError("research artifact size limit exceeded")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("research artifact JSON is invalid") from exc
    if not isinstance(value, dict):
        raise TypeError("research artifact must be an object")
    return value


def _write_private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(prefix=".research-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _model_input_digest(value):
    # Private settings contain endpoint/credential-reference KEYS, not values.
    # Fingerprint their canonical serialization without weakening public contracts.
    return stable_digest(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))


def verify_research_receipt(root, queue, record, reference, *, artifact_digest=None, verify_input=False):
    """Resolve only a current, correctly bound, filesystem-verified publication."""
    from .reports import MAX_MANIFEST_BYTES, verify_report_bundle

    match = _RECEIPT_REF.fullmatch(reference or "")
    if match is None or record.status not in {JobStatus.RUNNING, JobStatus.COMPLETED}:
        raise ValueError("research publication reference is invalid")
    digest = match.group(1)
    if artifact_digest is not None and artifact_digest != digest:
        raise ValueError("research publication digest mismatch")
    root = Path(root)
    receipt = _read_json(root / "results" / f"{digest}.json")
    if stable_digest(receipt) != digest:
        raise ValueError("research publication receipt integrity check failed")
    request = queue.load_request_snapshot(record.task_ref, expected_digest=queue.task_input_digest(record.task_ref))
    if (
        receipt.get("schema_version") != 1 or receipt.get("job_id") != record.job_id
        or receipt.get("run_id") != request.run_id or receipt.get("request_digest") != stable_digest(request)
        or receipt.get("snapshot_ref") != request.snapshot_ref or receipt.get("attempt") != record.attempts
    ):
        raise ValueError("research publication identity is not bound to this job")
    if record.status is JobStatus.RUNNING and receipt.get("fence_digest") != _fence_digest(record):
        raise ValueError("research publication lease identity mismatch")
    attempt_id = receipt.get("attempt_id")
    if not isinstance(attempt_id, str) or not _ATTEMPT_ID.fullmatch(attempt_id):
        raise ValueError("research publication attempt is invalid")
    bundle = root / "reports" / f"attempt-{attempt_id}" / request.run_id
    manifest_path = bundle / "manifest.json"
    if not manifest_path.resolve().is_relative_to((root / "reports").resolve()):
        raise ValueError("research publication bundle is outside its root")
    manifest = _read_json(manifest_path, max_bytes=MAX_MANIFEST_BYTES)
    if stable_digest(manifest) != receipt.get("manifest_digest") or manifest.get("run_id") != request.run_id:
        raise ValueError("research publication manifest identity mismatch")
    if any((bundle / relative).is_symlink() or not (bundle / relative).resolve().is_relative_to(bundle.resolve()) for relative in manifest.get("files", {})):
        raise ValueError("research publication contains an unsafe file")
    if not verify_report_bundle(manifest_path).ok:
        raise ValueError("research publication report integrity check failed")
    if manifest.get("source_snapshot", {}).get("dataset_fingerprint") != request.snapshot_ref:
        raise ValueError("research publication dataset binding mismatch")
    state = _decode_state(receipt.get("state"), request.run_id)
    if state.current_state is not ResearchState.LEARNING_RECORDED or not state.decision_eligible or manifest.get("source_snapshot", {}).get("state_digest") != stable_digest(state):
        raise ValueError("research publication is not a completed bound result")
    events = tuple(_decode_event(item) for item in receipt.get("events", ()))
    if any(event.run_id != request.run_id for event in events):
        raise ValueError("research publication events belong to another run")
    if verify_input:
        SnapshotStore(root / "snapshots").load(request.snapshot_ref)
    return {
        "result": ResearchRunResult(state=state, events=events), "manifest": manifest, "bundle": bundle,
        "created_at": _utc(record.created_at), "started_at": receipt["started_at"],
        "finished_at": manifest["created_at"], "request": request,
    }


def run_offline_research_stage(invocation, checkpoint_ref, publish_checkpoint):
    return run_local_research_stage(invocation, checkpoint_ref, publish_checkpoint, allow_model_calls=False)


def run_local_research_stage(invocation, checkpoint_ref, publish_checkpoint, *, allow_model_calls=True):
    """Trusted spawn entry: only a queue-owned reference crosses the pipe."""
    from .analyst_runtime import AnalystPool, ResearchManager
    from .drivers import OfflineDriver
    from .observability import current_runtime_budget
    from .reports import ReportBundleWriter
    from .snapshot_tools import SnapshotResearchTools
    from .workflow import ResearchOrchestrator

    del checkpoint_ref  # Replay deterministic work; never trust partial tool output.
    queue_path = os.environ.get("FINATHINK_QUEUE_PATH")
    if not queue_path:
        return {"status": "blocked", "failure_kind": "REQUEST_UNAVAILABLE"}
    queue = JobQueue(queue_path)
    root = queue.path.parent
    try:
        record = queue.get(invocation.job_id)
        if record.task_ref != invocation.task_ref or record.task_digest != invocation.task_digest or record.status is not JobStatus.RUNNING:
            raise ValueError("request identity mismatch")
        request = queue.load_request_snapshot(record.task_ref, expected_digest=queue.task_input_digest(record.task_ref))
    except (KeyError, ValueError, TypeError):
        return {"status": "blocked", "failure_kind": "REQUEST_UNAVAILABLE"}
    if request.workflow_version != WORKFLOW_VERSION or not isinstance(request.research_plan, ResearchPlan):
        return {"status": "blocked", "failure_kind": "POLICY_CHANGED"}
    try:
        snapshot = SnapshotStore(root / "snapshots").load(request.snapshot_ref)
    except (OSError, ValueError, TypeError):
        return {"status": "blocked", "failure_kind": "SNAPSHOT_INTEGRITY_FAILED"}
    from .learning_review import LearningReviewService, LearningReviewStore

    try:
        policy_record = LearningReviewStore(queue, root, scope_id=request.research_plan.validation_spec["scope_id"]).policy(request.previous_policy_fingerprint, as_of=request.as_of)
        lineage = LearningReviewService.lineage(policy_record, snapshot, request.instrument, rerun_of=request.research_plan.validation_spec.get("rerun_of"))
        if request.research_plan.validation_spec.get("policy_fingerprint") != policy_record["runtime_fingerprint"] or request.research_plan.validation_spec.get("policy_lineage", lineage) != lineage:
            raise ValueError("policy binding mismatch")
    except (KeyError, ValueError, TypeError, OSError):
        return {"status": "blocked", "failure_kind": "POLICY_CHANGED"}
    budget = current_runtime_budget()
    if budget is not None:
        budget.charge_bytes(len(snapshot.to_json().encode("utf-8")))
        budget.charge_experiments(1)
    attempt_id = uuid.uuid4().hex
    store = ResearchRunStore(root / "checkpoints" / attempt_id, workflow_version=WORKFLOW_VERSION)
    identity = _identity(request)

    def checkpoint(state):
        store.save_checkpoint(state, identity)
        publish_checkpoint(f"checkpoint:{attempt_id}")

    class Control:
        def is_cancelled(self, run_id):
            current = queue.get(record.job_id)
            return current.cancel_requested or current.status is not JobStatus.RUNNING or current.attempts != record.attempts or current.lease_token != record.lease_token

    spec = request.research_plan.validation_spec
    binding = spec.get("factor_study")
    study = studies = None
    publication_before = None
    if binding is not None:
        studies = FactorStudyStore(queue, scope_id=spec["scope_id"])
        study = studies.get(binding["study_id"])
        if study["spec_ref"] != binding["spec_ref"] or study["spec"]["snapshot_ref"] != snapshot.fingerprint or study["spec"]["policy_fingerprint"] != spec["policy_fingerprint"] or binding["phase"] not in {"validation", "test"}:
            return {"status": "blocked", "failure_kind": "CONTEXT_INTEGRITY_FAILED"}
        if binding["phase"] == "test" and study["test_key"] is None:
            return {"status": "blocked", "failure_kind": "VALIDATION_FAILED"}
        studies.set_job(study["study_id"], binding["phase"], record.job_id)
        if binding["phase"] == "validation":
            publication_before = study["spec"]["test_period"][0]
    tools = SnapshotResearchTools(snapshot, publication_before=publication_before, policy=policy_record["effective"], policy_lineage=lineage)
    driver, manager = OfflineDriver(), ResearchManager()
    if spec.get("execution_mode") == "provider-grounded":
        if not allow_model_calls:
            return {"status": "blocked", "failure_kind": "OUTBOUND_DISABLED"}
        from .analyst_runtime import GroundedResearchManager
        from .credentials import LocalProviderCredentialStore
        from .drivers import GroundedApiDriver
        from .model_call_journal import ModelCallJournal
        from .provider_config import ProviderConfig
        from .provider_transport import ProviderHttpTransport

        try:
            reference = spec["context_input_ref"]
            if not isinstance(reference, str) or not re.fullmatch(r"[0-9a-f]{64}", reference):
                raise ValueError()
            inputs = _read_json(root / "model-inputs" / f"{reference}.json")
            if _model_input_digest(inputs) != reference or inputs.get("scope_id") != spec["scope_id"]:
                raise ValueError()
            config = ProviderConfig.from_mapping(inputs["provider"])
            if not config.enabled or config.provider_id != spec["model_config_ref"]:
                raise ValueError()
            journal = ModelCallJournal(root / "model-calls.sqlite3", job_id=record.job_id, run_id=request.run_id, request_digest=stable_digest(request), attempt=record.attempts, scope_id=spec["scope_id"])
            driver = GroundedApiDriver(snapshot, config, materials=inputs["materials"], transport=ProviderHttpTransport(), credential_store=LocalProviderCredentialStore(), call_journal=journal)
            manager = GroundedResearchManager(driver)
            if budget is not None:
                budget.charge_bytes(len(json.dumps(inputs).encode("utf-8")))
        except (KeyError, ValueError, OSError, TypeError):
            return {"status": "blocked", "failure_kind": "CONTEXT_INTEGRITY_FAILED"}
    result = ResearchOrchestrator().run(request, driver, tools, analyst_pool=AnalystPool(), manager=manager, run_control=Control(), checkpoint_writer=checkpoint)
    if budget is not None and budget.resource_failures:
        checkpoint(result.state)
        return {"status": "blocked", "failure_kind": "RESOURCE_LIMIT"}
    if result.state.current_state is not ResearchState.LEARNING_RECORDED or result.decision is None or not result.decision.paper_only:
        checkpoint(result.state)
        return {"status": "blocked", "failure_kind": "RESOURCE_LIMIT" if budget is not None and budget.resource_failures else result.state.failure_kind.value if result.state.failure_kind else "VALIDATION_FAILED"}
    if spec.get("execution_mode") == "provider-grounded":
        tools.analysis["model_research"] = {
            "execution_mode": "provider-grounded", "provider": spec["model_config_ref"],
            "calls": journal.records(terminal=True), "context_input_ref": spec["context_input_ref"],
            "roles": [{"role": report.role, "status": report.status, "claims": list(report.claims), "evidence_refs": list(report.evidence_refs), "limitations": list(report.limitations)} for report in result.state.analyst_reports],
            "synthesis": driver.synthesis_summary,
            "limitations": ["Citation membership verifies input binding, not semantic truth. Model observations do not set financial metrics or policy."]}
    if binding is not None:
        from .factor_workbench import compute_study

        phase = binding["phase"]
        validation = None
        if phase == "test":
            validation_record = queue.get(study["validation_job_id"])
            validated = verify_research_receipt(root, queue, validation_record, validation_record.result_ref)
            validation = validated["manifest"]["source_snapshot"].get("factor_study")
        tools.analysis["factor_study"] = compute_study(snapshot, study, phase=phase, validation=validation)
    if budget is not None:
        budget.check_wall()
    manifest = ReportBundleWriter().write(result, root / "reports" / f"attempt-{attempt_id}", quant_analytics=tools.analysis)
    if budget is not None:
        bundle = root / "reports" / f"attempt-{attempt_id}" / request.run_id
        budget.charge_bytes(sum((bundle / relative).stat().st_size for relative in manifest.files) + (bundle / "manifest.json").stat().st_size)
    receipt = {
        "schema_version": 1, "job_id": record.job_id, "run_id": request.run_id,
        "request_digest": stable_digest(request), "snapshot_ref": request.snapshot_ref,
        "attempt": record.attempts, "attempt_id": attempt_id, "fence_digest": _fence_digest(record),
        "manifest_digest": stable_digest(manifest), "started_at": _utc(record.updated_at),
        "state": to_jsonable(result.state), "events": to_jsonable(result.events),
    }
    digest = stable_digest(receipt)
    reference = f"research-result:{digest}"
    _write_private_json(root / "results" / f"{digest}.json", receipt)
    try:
        verify_research_receipt(root, queue, record, reference, artifact_digest=digest, verify_input=True)
    except (ValueError, OSError, KeyError, TypeError):
        return {"status": "blocked", "failure_kind": "PUBLICATION_INVALID"}
    return {"status": "completed", "result_ref": reference, "artifact_digest": digest}


def index_verified_publication(root, publication):
    request = publication["request"]
    scope = request.research_plan.validation_spec["scope_id"]
    repository = RunRepository(Path(root) / "research-index.sqlite3", report_root=Path(root) / "reports", scope_id=scope)
    repository.record_result(publication["result"], publication["manifest"], bundle=publication["bundle"], created_at=publication["created_at"], started_at=publication["started_at"], finished_at=publication["finished_at"], strategy_version=request.research_plan.validation_spec["strategy_version"], policy_fingerprint=request.research_plan.validation_spec["policy_fingerprint"])


class ResearchApplicationService:
    def __init__(self, root: str | Path, *, scope_id="local-session", provider_configs=None, allow_model_calls=True):
        from .provider_config import ProviderConfigStore
        from .runtime_service import ResearchRuntimeService

        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.scope_id = scope_id
        self.provider_configs = provider_configs if provider_configs is not None else ProviderConfigStore()
        self.allow_model_calls = allow_model_calls
        self.snapshots = SnapshotStore(self.root / "snapshots")
        self.runs = RunRepository(self.root / "research-index.sqlite3", report_root=self.root / "reports", scope_id=scope_id)
        queue_path = self.root / "research-jobs.sqlite3"
        if queue_path.is_symlink():
            raise ValueError("research queue must not be a symlink")
        descriptor = os.open(queue_path, os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        self.queue = JobQueue(queue_path, max_attempts=3, backoff_base_seconds=0.1, backoff_max_seconds=1)
        self.factor_studies = FactorStudyStore(self.queue, scope_id=self.scope_id)
        from .learning_review import LearningReviewService

        self.learning = LearningReviewService(self)
        runner = run_local_research_stage if allow_model_calls else run_offline_research_stage
        self.runtime = ResearchRuntimeService(self.queue, stage_runners={"local_research": runner}, worker_id=f"application-{uuid.uuid4().hex[:16]}", timeout_seconds=300, autostart=False, supervisor_concurrency=2)

    def start(self):
        # Recover the small complete→index crash window without re-executing
        # a completed calculation. Ordinary completions are indexed by owner.
        with self.queue._connect() as db:
            rows = db.execute("SELECT j.job_id FROM jobs j JOIN request_snapshots r ON r.task_ref=j.task_ref WHERE j.status='completed' AND json_extract(r.payload, '$.research_plan.validation_spec.scope_id')=? AND json_extract(r.payload, '$.workflow_version')=?", (self.scope_id, WORKFLOW_VERSION)).fetchall()
        for row in rows:
            try:
                record = self.queue.get(row["job_id"])
                self._reconcile(record, self._request(record))
            except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
                # A damaged bundle stays unavailable; never invent a success.
                continue
        self.runtime.start_supervisor()

    def close(self):
        self.runtime.stop_supervisor()

    @staticmethod
    def _limits(raw, *, factor_study=False):
        from .runtime_service import RuntimeLimits

        if not isinstance(raw, dict):
            raise TypeError("budget must be an object")
        defaults = {"max_wall_seconds": 120, "max_attempts": 2, "max_provider_calls": 1, "max_bytes": 10 * 1024 * 1024, "max_experiments": 2}
        if set(raw) - defaults.keys() or any(value is None for value in raw.values()):
            raise ValueError("budget contains unsupported limits")
        limits = RuntimeLimits(**{**defaults, **raw}, max_concurrency=2)
        caps = {"max_wall_seconds": 300, "max_attempts": 3, "max_provider_calls": 10, "max_bytes": 64 * 1024 * 1024, "max_experiments": 256 if factor_study else 6}
        if any(getattr(limits, key) > value for key, value in caps.items()):
            raise ValueError("budget exceeds local resource limits")
        return limits

    def submit(self, values, *, factor_study=None, rerun_of=None):
        if not isinstance(values, dict):
            raise TypeError("research submission must be an object")
        if set(values) - {"idempotency_key", "snapshot_ref", "question", "instrument", "as_of", "model_config_ref", "budget", "materials", "allow_model_calls", "policy_ref"}:
            raise ValueError("research submission contains unsupported fields")
        key = _ref(values.get("idempotency_key"), "idempotency_key")
        question = values.get("question")
        if not isinstance(question, str) or not question.strip() or len(question) > 2000:
            raise ValueError("research question must contain 1 to 2000 characters")
        selected_provider = values.get("model_config_ref", "offline")
        config = None
        if selected_provider != "offline":
            config = self.provider_configs.get(selected_provider)
            if not self.allow_model_calls or values.get("allow_model_calls") is not True or config is None or not config.enabled or config.adapter_kind == "offline" or config.endpoint is None or config.credential_ref is None:
                raise ValueError("select a configured provider and explicitly authorize model calls")
        elif values.get("materials"):
            raise ValueError("select and authorize a model before supplying role materials")
        snapshot = self.snapshots.load(values.get("snapshot_ref"))
        instrument = values.get("instrument")
        if instrument not in snapshot.instruments:
            raise ValueError("selected snapshot does not contain the requested instrument")
        if factor_study is not None:
            study = self.factor_studies.get(factor_study["study_id"])
            if config is not None or factor_study["spec_ref"] != study["spec_ref"] or study["spec"]["snapshot_ref"] != snapshot.fingerprint or factor_study["phase"] not in {"validation", "test"}:
                raise ValueError("factor study request binding is invalid")
        raw_budget = values.get("budget", {})
        if config is not None:
            if not isinstance(raw_budget, dict) or raw_budget.get("max_attempts", 1) != 1:
                raise ValueError("online research has one attempt; resubmit explicitly after failure")
            raw_budget = {"max_attempts": 1, "max_provider_calls": 6, **raw_budget}
        limits = self._limits(raw_budget, factor_study=factor_study is not None)
        budget = asdict(limits)
        policy_record = self.learning.store.policy(values.get("policy_ref"), as_of=values.get("as_of"))
        policy = policy_record["effective"]
        if rerun_of is not None:
            _ref(rerun_of, "rerun_of")
        lineage = self.learning.lineage(policy_record, snapshot, instrument, rerun_of=rerun_of)
        if factor_study is not None and policy_record["version"] != 0:
            raise ValueError("因子实验只能使用其预先冻结的基准配置")
        policy_fingerprint = stable_digest(policy)
        request = ResearchRequest(
            run_id=f"research-{stable_digest({'scope': self.scope_id, 'key': key})[:24]}",
            instrument=instrument, as_of=values.get("as_of"),
            research_plan=ResearchPlan(hypotheses=(question.strip(),), factor_ids=(policy["factor_id"],), validation_spec={"scope_id": self.scope_id, "execution_mode": "provider-grounded" if config is not None else "local-deterministic", "model_config_ref": selected_provider, "data_mode": snapshot.mode, "budget": budget, "policy_fingerprint": policy_fingerprint, "strategy_version": policy["strategy_version"], "policy_lineage": lineage, **({"rerun_of": rerun_of} if rerun_of else {})}),
            analyst_roles=_ROLES, asset_class="EQUITY_OR_ETF", workflow_version=WORKFLOW_VERSION,
            config_digest=stable_digest({"workflow": WORKFLOW_VERSION, "budget": budget, "policy_fingerprint": policy_fingerprint}),
            snapshot_ref=snapshot.fingerprint,
            previous_policy_fingerprint=policy_record["policy_ref"],
        )
        if factor_study is not None:
            from dataclasses import replace

            plan = replace(request.research_plan, validation_spec={**request.research_plan.validation_spec, "factor_study": factor_study})
            request = replace(request, research_plan=plan, config_digest=stable_digest({"base": request.config_digest, "factor_study": factor_study}))
        if config is not None:
            from dataclasses import replace

            from .context_bundle import build_context_bundle, normalize_materials

            materials = normalize_materials(values.get("materials", []), request)
            # Validate every role and the manager's aggregate before any paid call.
            for role in (*_ROLES, "research"):
                build_context_bundle(request, snapshot, role, materials=materials)
            inputs = {"schema_version": 1, "scope_id": self.scope_id, "provider": config._stored(), "materials": materials}
            reference = _model_input_digest(inputs)
            target = self.root / "model-inputs" / f"{reference}.json"
            try:
                _write_private_json(target, inputs)
            except FileExistsError:
                if _model_input_digest(_read_json(target)) != reference:
                    raise ValueError("model input integrity mismatch") from None
            plan = replace(request.research_plan, validation_spec={**request.research_plan.validation_spec, "context_input_ref": reference})
            request = replace(request, research_plan=plan, config_digest=stable_digest({"base": request.config_digest, "context_input_ref": reference}))
        record = self.runtime.submit(request, idempotency_key=f"application:{stable_digest({'scope': self.scope_id, 'key': key})[:40]}", limits=limits)
        return self.get_job(record.job_id)

    def _method_store(self):
        from .method_handoff import MethodHandoffStore

        return MethodHandoffStore(self.root / "method-workspace" / stable_digest(self.scope_id), self.queue)

    def attach_research_fields(self, job_id, value):
        from finahinking.data.research_fields import ResearchFieldAttachment, ResearchFieldStore

        request = self._request(self.queue.get(job_id))
        attachment = value if isinstance(value, ResearchFieldAttachment) else ResearchFieldAttachment.from_dict(value)
        if attachment.snapshot_ref != request.snapshot_ref:
            raise ValueError("research fields belong to another snapshot")
        reference = ResearchFieldStore(self.snapshots).save(attachment)
        return {"snapshot_ref": request.snapshot_ref, "attachment_ref": reference,
                "fields": sorted(attachment.units), "status": "SAVED_NOT_EVALUATED", "paper_only": True}

    def export_method_handoff(self, job_id, route_request):
        """Explicit local export; it performs no provider call or market fetch."""
        from .asset_catalog import ResearchAssetCatalog
        from .context_bundle import build_context_bundle
        from .contracts import AgentTask
        from .method_context import MethodContextError, load_methods
        from .method_handoff import build_method_handoff
        from .method_router import select_methods

        request = self._request(self.queue.get(job_id))
        snapshot = self.snapshots.load(request.snapshot_ref)
        catalog = ResearchAssetCatalog.packaged()
        selected = select_methods(route_request, catalog)
        loaded = load_methods(selected, catalog)
        if loaded.status != "READY":
            raise MethodContextError()
        materials = ()
        source = request.research_plan.validation_spec.get("context_input_ref")
        if source is not None:
            if not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{64}", source):
                raise ValueError("model context reference is invalid")
            stored = _read_json(self.root / "model-inputs" / f"{source}.json")
            if _model_input_digest(stored) != source:
                raise ValueError("model context integrity mismatch")
            materials = stored["materials"]
        context = build_context_bundle(request, snapshot, "research", materials=materials)
        digest = stable_digest({"request": to_jsonable(request), "context": context.fingerprint,
                                "methods": loaded.fingerprint})
        task = AgentTask("research", f"method-{digest[:32]}", digest,
                         capabilities=("paper_only", "structured_output", "evidence_refs", "reasoning"))
        package = build_method_handoff(task, context, loaded)
        reference = self._method_store().export(package)
        return self.get_method_handoff(job_id, reference)

    def _bound_method_handoff(self, job_id, reference):
        request = self._request(self.queue.get(job_id))
        store = self._method_store()
        package = store.load(reference)
        if (package.context.to_dict()["run_id"] != request.run_id
                or package.context.to_dict()["dataset_fingerprint"] != request.snapshot_ref):
            raise KeyError(reference)
        return store, package

    def get_method_handoff(self, job_id, reference):
        from .reports import method_handoff_activity

        store, package = self._bound_method_handoff(job_id, reference)
        state = store.status(reference)
        return {"handoff_ref": reference, **state, "package": package.to_dict(),
                "summary": method_handoff_activity(package, state["status"])}

    def accept_method_handoff(self, job_id, reference, response):
        store, _ = self._bound_method_handoff(job_id, reference)
        outcome = store.accept(reference, response)
        return {"outcome": to_jsonable(outcome),
                "summary": self.get_method_handoff(job_id, reference)["summary"],
                "artifacts": list(store.artifacts(reference))}

    def method_handoff_artifacts(self, job_id, reference):
        store, _ = self._bound_method_handoff(job_id, reference)
        return store.artifacts(reference)

    def _request(self, record):
        request = self.queue.load_request_snapshot(record.task_ref, expected_digest=self.queue.task_input_digest(record.task_ref))
        if request.workflow_version != WORKFLOW_VERSION or not isinstance(request.research_plan, ResearchPlan) or request.research_plan.validation_spec.get("scope_id") != self.scope_id:
            raise KeyError(record.job_id)
        return request

    def _progress(self, record, request):
        reference = record.checkpoint_ref or ""
        attempt = reference.removeprefix("checkpoint:")
        if not reference.startswith("checkpoint:") or not _ATTEMPT_ID.fullmatch(attempt):
            return None
        try:
            path = self.root / "checkpoints" / attempt / f"{request.run_id}.json"
            if path.is_symlink() or path.stat().st_size > 65_536:
                return None
            return ResearchRunStore(path.parent, workflow_version=WORKFLOW_VERSION).load_checkpoint(request.run_id, _identity(request))
        except (OSError, ValueError):
            return None

    def _reconcile(self, record, request):
        if record.status is not JobStatus.COMPLETED:
            return None
        try:
            return self.runs.get_run(request.run_id)
        except KeyError:
            publication = verify_research_receipt(self.root, self.queue, record, record.result_ref)
            index_verified_publication(self.root, publication)
            return self.runs.get_run(request.run_id)

    def get_job(self, job_id):
        from .reports import redact_public_payload

        record = self.queue.get(job_id)
        request = self._request(record)
        entry = None
        publication_error = None
        try:
            entry = self._reconcile(record, request)
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
            publication_error = "PUBLICATION_INVALID"
        state = entry["state"] if entry is not None else self._progress(record, request)
        history = [item.value for item in state.state_history] if state is not None else []
        failure = next((code for code in _FAILURES if stable_digest(code) == record.last_error_digest), None)
        if record.status is JobStatus.FAILED and failure is None:
            failure = "RUNNER_EXCEPTION"
        failed_stage = {
            "DATA_UNAVAILABLE": "DATA_CHECKED", "DATA_INVALID": "DATA_CHECKED", "NO_DATA_AVAILABLE": "DATA_CHECKED",
            "SNAPSHOT_INTEGRITY_FAILED": "DATA_CHECKED", "QUANT_VALIDATION_FAILED": "QUANT_VALIDATION",
            "RISK_REVIEW_FAILED": "RISK_REVIEW", "PUBLICATION_INVALID": "REPORT_PUBLISHED",
        }.get(failure, history[-1] if history else "INPUT") if failure else None
        if failure == "RESOURCE_LIMIT" and state is not None and state.current_state is ResearchState.PAPER_DECISION_READY:
            failed_stage = "REPORT_PUBLISHED"
        metrics = {}
        report_status = entry["report_status"] if entry is not None else "UNAVAILABLE"
        if record.status is JobStatus.COMPLETED and report_status != "AVAILABLE":
            publication_error = "PUBLICATION_INVALID"
        source = entry["manifest"].get("source_snapshot", {}) if entry is not None else {}
        if report_status == "AVAILABLE":
            metrics = entry["manifest"].get("source_snapshot", {}).get("quant_analytics", {}).get("metrics", {})
        runtime_metrics = self.queue.load_metrics(record.job_id) or {}
        model_research = source.get("model_research")
        if request.research_plan.validation_spec.get("execution_mode") == "provider-grounded":
            from .model_call_journal import ModelCallJournal

            calls = []
            journal_path = self.root / "model-calls.sqlite3"
            if journal_path.exists():
                journal = ModelCallJournal(journal_path, job_id=record.job_id, run_id=request.run_id, request_digest=stable_digest(request), attempt=max(1, record.attempts), scope_id=self.scope_id)
                calls = journal.records(terminal=record.status in {JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.FAILED})
            model_research = {**(model_research or {}), "calls": calls, "usage_status": "UNKNOWN" if any(item["dispatched"] and item["usage"] is None for item in calls) else "RECORDED" if calls else "NOT_STARTED", "aggregate_counts": runtime_metrics}
        return redact_public_payload({
            "schema_version": 1, "paper_only": True, "execution_mode": request.research_plan.validation_spec.get("execution_mode", "local-deterministic"),
            "data_mode": source.get("data_mode", request.research_plan.validation_spec.get("data_mode", "UNKNOWN")),
            "data_limitations": source.get("data_limitations", []), "metric_basis": "OBSERVATION",
            "job_id": record.job_id, "run_id": request.run_id, "status": record.status.value,
            "snapshot_ref": request.snapshot_ref, "instrument": request.instrument, "as_of": request.as_of.isoformat(),
            "question": request.research_plan.hypotheses[0], "model_config_ref": request.research_plan.validation_spec.get("model_config_ref", "offline"),
            "model_research": model_research,
            "factor_study": source.get("factor_study"),
            "risk_portfolio": source.get("risk_portfolio"),
            "policy_ref": request.previous_policy_fingerprint or request.research_plan.validation_spec["policy_fingerprint"],
            "policy_lineage": source.get("policy_lineage", request.research_plan.validation_spec.get("policy_lineage")),
            "budget": request.research_plan.validation_spec["budget"], "attempts": record.attempts,
            "cancel_requested": record.cancel_requested,
            "created_at": _utc(record.created_at), "updated_at": _utc(record.updated_at),
            "elapsed_seconds": round(max(0, (record.updated_at if record.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED} else self.queue._clock()) - record.created_at), 3),
            "state": state.current_state.value if state is not None else "RECEIVED", "state_history": history,
            "stages": [{"name": stage, "status": "FAILED" if stage == failed_stage else "RUNNING" if record.status is JobStatus.RUNNING and state is not None and stage == state.current_state.value else "COMPLETE" if stage in history else "PENDING"} for stage in _STAGES],
            "failure_kind": failure or publication_error, "failure_stage": failed_stage,
            "report_status": report_status, "report_url": f"/research/{request.run_id}/report/complete" if report_status == "AVAILABLE" else None,
            "run_url": f"/research/{request.run_id}", "metrics": metrics,
            "runtime_metrics": runtime_metrics,
        })

    def list_jobs(self, *, limit=50, offset=0):
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
            raise ValueError("job pagination is invalid")
        with self.queue._connect() as db:
            where = "json_extract(r.payload, '$.research_plan.validation_spec.scope_id')=? AND json_extract(r.payload, '$.workflow_version')=?"
            args = (self.scope_id, WORKFLOW_VERSION)
            total = db.execute(f"SELECT COUNT(*) FROM jobs j JOIN request_snapshots r ON r.task_ref=j.task_ref WHERE {where}", args).fetchone()[0]
            rows = db.execute(f"SELECT j.job_id FROM jobs j JOIN request_snapshots r ON r.task_ref=j.task_ref WHERE {where} ORDER BY j.created_at DESC,j.job_id LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
        return {"schema_version": 1, "paper_only": True, "total": total, "limit": limit, "offset": offset, "jobs": [self.get_job(row["job_id"]) for row in rows]}

    def diagnostic_summary(self):
        """Bounded, scoped projection; never include questions or data rows."""
        latest = self.list_jobs(limit=1)
        return {"status": "AVAILABLE", "total_jobs": latest["total"], "latest_report_status": latest["jobs"][0]["report_status"] if latest["jobs"] else "NO_REPORT", "paper_only": True}

    def cancel(self, job_id):
        self._request(self.queue.get(job_id))
        self.queue.cancel(job_id)
        return self.get_job(job_id)

    def create_factor_study(self, values):
        from .factor_workbench import prepare_study
        from .snapshot_tools import local_research_policy

        if not isinstance(values, dict):
            raise TypeError("study submission must be an object")
        key = _ref(values.get("idempotency_key"), "idempotency_key")
        snapshot = self.snapshots.load(values.get("snapshot_ref"))
        spec = prepare_study(values, snapshot, policy_fingerprint=stable_digest(local_research_policy()))
        study_id = "study-" + stable_digest({"scope": self.scope_id, "key": key})[:24]
        study = self.factor_studies.create(study_id, spec, validation_key=key)
        job = self._submit_factor_job(study, "validation", key)
        self.factor_studies.set_job(study_id, "validation", job["job_id"])
        return self.factor_study(study_id)

    def _submit_factor_job(self, study, phase, key):
        spec = study["spec"]
        return self.submit({"idempotency_key": "factor-" + stable_digest({"study": study["study_id"], "phase": phase, "key": key})[:32], **{name: spec[name] for name in ("snapshot_ref", "instrument", "as_of", "question")}, "budget": {"max_wall_seconds": 120, "max_bytes": 64 * 1024 * 1024, "max_experiments": spec["evaluation_units"]}}, factor_study={"study_id": study["study_id"], "spec_ref": study["spec_ref"], "phase": phase})

    def factor_study(self, study_id):
        study = self.factor_studies.get(study_id)
        # Reconcile the two durable writes with the ORIGINAL idempotency key.
        # This also finds an already queued job after loss before set_job.
        phase = "validation" if study["state"] == "DECLARED" else "test" if study["state"] == "TEST_RESERVED" else None
        if phase is not None and study[f"{phase}_job_id"] is None:
            key = study["validation_key"] if phase == "validation" else study["test_key"]
            job = self._submit_factor_job(study, phase, key)
            study = self.factor_studies.set_job(study_id, phase, job["job_id"])
        jobs, results = {}, {}
        for phase in ("validation", "test"):
            job_id = study[f"{phase}_job_id"]
            if job_id is None:
                continue
            job = self.get_job(job_id)
            payload = job["factor_study"]
            if payload is not None and (payload.get("study_id") != study_id or payload.get("spec_ref") != study["spec_ref"]):
                raise ValueError("factor study result belongs to another charter")
            if job["status"] in {"completed", "failed", "cancelled"}:
                self.factor_studies.complete(study_id, phase, job_id, succeeded=job["status"] == "completed" and payload is not None)
            jobs[f"{phase}_job"] = job
            results["validation" if phase == "validation" else "test_result"] = payload
        return {**self.factor_studies.get(study_id), **jobs, **results}

    def freeze_factor_study(self, study_id, candidate_id):
        study = self.factor_study(study_id)
        result = study.get("validation")
        if study["state"] not in {"VALIDATED", "FROZEN"} or not result:
            raise ValueError("freeze requires verified validation")
        candidate = next((record for record in result["experiments"] if record["experiment_id"] == candidate_id and record["failure_reason"] is None), None)
        if candidate is None:
            raise ValueError("only a valid declared candidate can be frozen")
        self.factor_studies.freeze(study_id, candidate_id, candidate["digest"])
        return self.factor_study(study_id)

    def test_factor_study(self, study_id, idempotency_key):
        self.factor_study(study_id)
        study = self.factor_studies.reserve_test(study_id, idempotency_key)
        job = self._submit_factor_job(study, "test", idempotency_key)
        self.factor_studies.set_job(study_id, "test", job["job_id"])
        return self.factor_study(study_id)

    def review_factor_study(self, study_id, decision, reviewer_id):
        study = self.factor_study(study_id)
        if not study.get("test_result"):
            raise ValueError("review requires a verified final-test report")
        if decision == "APPROVE_FOR_RESEARCH" and study["test_result"]["test_summary"]["failure_reason"] is not None:
            raise ValueError("failed final-test evidence cannot be approved")
        self.factor_studies.review(study_id, decision, reviewer_id)
        return self.factor_study(study_id)

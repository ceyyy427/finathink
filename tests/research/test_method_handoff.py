"""Explicit external handoffs bind context, input evidence and typed artifacts."""

import ast
import copy
import hashlib
import importlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from finahinking.research.asset_catalog import ResearchAssetCatalog
from finahinking.research.asset_contracts import MethodRouteRequest
from finahinking.research.contracts import AgentTask, ResearchPlan, stable_digest, to_jsonable
from finahinking.research.job_queue import JobQueue
from finahinking.research.method_context import load_methods
from finahinking.research.method_router import select_methods
from tests.research.test_context_bundle import build


def _api():
    name = "finahinking.research.method_handoff"
    assert importlib.util.find_spec(name) is not None, "method handoff adapter is not implemented"
    return importlib.import_module(name)


def package():
    api = _api()
    catalog = ResearchAssetCatalog.packaged()
    loaded = load_methods(select_methods(MethodRouteRequest(purposes=("factor-mining",)), catalog), catalog)
    task = AgentTask("research", "method-task", "input-binding",
                     capabilities=("paper_only", "structured_output", "evidence_refs"))
    return api.build_method_handoff(task, build("research"), loaded)


def response_for(value):
    envelope = value.envelope
    return {"schema_version": "method-result.v1", "package_digest": value.package_digest,
            "result": {"schema_version": envelope.schema_version, "input_digest": envelope.input_digest,
                       "task_id": envelope.task_id, "role": envelope.role, "status": "READY",
                       "paper_only": True, "capabilities": list(envelope.capabilities),
                       "evidence_refs": list(value.context.evidence_index)},
            "output_artifacts": []}


def artifact_for(value, artifact_type="ResearchPlan"):
    if artifact_type == "ResearchPlan":
        payload = to_jsonable(ResearchPlan(hypotheses=("Compare a frozen observable baseline",),
                                           factor_ids=("close_momentum",),
                                           validation_spec={"paper_only": True}))
        schema = "research-plan.v1"
    else:
        from finahinking.research.factor_proposals import FactorHypothesis, FactorProposalCatalog

        hypothesis = FactorHypothesis("hypothesis", "momentum mechanism", "momentum", ("close",), 5, "positive")
        payload = to_jsonable(FactorProposalCatalog().propose(hypothesis, limit=1)[0])
        schema = "factor-proposal.v1"
    return {"artifact_type": artifact_type, "schema_version": schema,
            "input_digest": value.envelope.input_digest, "content_digest": stable_digest(payload),
            "payload": payload}


def test_package_binds_full_context_and_keeps_v1_envelope_data_only():
    api = _api()
    value = package()
    assert value.envelope.context_digest == value.context.fingerprint
    assert value.envelope.tool_names == () and value.envelope.paper_only is True
    assert value.to_dict()["schema_version"] == "method-handoff.v1"
    assert value.context.to_dict()["method_context"]["status"] == "READY"
    assert len(value.context._json.encode()) <= 32768
    assert api.MethodHandoffPackage.from_dict(value.to_dict()).package_digest == value.package_digest


def test_no_response_remains_waiting_without_completing_or_publishing(tmp_path):
    api = _api()
    value = package()
    store = api.MethodHandoffStore(tmp_path / "state", JobQueue(tmp_path / "queue.sqlite"))
    ref = store.export(value)
    outcome = store.accept(ref, None)
    assert outcome.status == "EXTERNAL_HANDOFF_REQUIRED"
    assert store.status(ref)["status"] == "EXTERNAL_HANDOFF_REQUIRED"
    assert not list((tmp_path / "state").rglob("*result*.json"))


@pytest.mark.parametrize("change", ["package", "task", "input", "role", "foreign-evidence", "tools"])
def test_wrong_bindings_or_permissions_reject_before_publication(tmp_path, change):
    api = _api()
    value = package()
    answer = response_for(value)
    if change == "package":
        answer["package_digest"] = "0" * 64
    elif change in {"task", "input", "role"}:
        answer["result"][{"task": "task_id", "input": "input_digest", "role": "role"}[change]] = "foreign"
    elif change == "foreign-evidence":
        answer["result"]["evidence_refs"] = ["artifact:valid-but-another-task"]
    else:
        answer["result"]["capabilities"].append("shell")
    answer["output_artifacts"] = [artifact_for(value)]
    store = api.MethodHandoffStore(tmp_path / "state", JobQueue(tmp_path / "queue.sqlite"))
    ref = store.export(value)
    result = store.accept(ref, answer)
    assert result.status == "REJECTED"
    assert store.status(ref)["status"] == "EXTERNAL_HANDOFF_REQUIRED"
    assert store.artifacts(ref) == ()


def test_authorized_membership_cannot_be_broadened_by_caller_argument():
    api = _api()
    value = package()
    answer = response_for(value)
    answer["result"]["evidence_refs"] = ["artifact:foreign"]
    result = api.accept_method_result(value, answer, allowed_evidence_refs=("artifact:foreign",))
    assert result.status == "REJECTED"


@pytest.mark.parametrize("change", ["too-many", "duplicate-type", "wrong-schema", "wrong-hash",
                                    "wrong-input", "arbitrary-code", "oversize", "self-evidence"])
def test_invalid_artifacts_never_become_completed_results(tmp_path, change):
    api = _api()
    value = package()
    answer = response_for(value)
    plan = artifact_for(value)
    proposal = artifact_for(value, "FactorProposal")
    answer["output_artifacts"] = [plan, proposal]
    if change == "too-many":
        answer["output_artifacts"].append(copy.deepcopy(plan))
    elif change == "duplicate-type":
        answer["output_artifacts"] = [plan, copy.deepcopy(plan)]
    elif change == "wrong-schema":
        plan["schema_version"] = "unknown.v1"
    elif change == "wrong-hash":
        plan["content_digest"] = "0" * 64
    elif change == "wrong-input":
        plan["input_digest"] = "another-input"
    elif change == "arbitrary-code":
        proposal["payload"]["expression"] = "__import__('os').system('anything')"
        proposal["content_digest"] = stable_digest(proposal["payload"])
    elif change == "oversize":
        plan["payload"]["hypotheses"] = ["研" * 22000]
        plan["content_digest"] = stable_digest(plan["payload"])
    else:
        answer["result"]["evidence_refs"] = ["artifact:" + plan["content_digest"]]
    store = api.MethodHandoffStore(tmp_path / "state", JobQueue(tmp_path / "queue.sqlite"))
    ref = store.export(value)
    assert store.accept(ref, answer).status == "REJECTED"
    assert store.artifacts(ref) == ()
    assert store.status(ref)["status"] == "EXTERNAL_HANDOFF_REQUIRED"


def test_two_typed_outputs_persist_and_replay_after_restart_is_idempotent(tmp_path):
    api = _api()
    value = package()
    root, queue_path = tmp_path / "state", tmp_path / "queue.sqlite"
    queue = JobQueue(queue_path)
    store = api.MethodHandoffStore(root, queue)
    before = sqlite3.connect(queue_path).execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    ref = store.export(value)
    answer = response_for(value)
    answer["output_artifacts"] = [artifact_for(value), artifact_for(value, "FactorProposal")]
    outcome = store.accept(ref, answer)
    assert outcome.status == "READY"
    outputs = store.artifacts(ref)
    assert len(outputs) == 2 and {item["artifact_type"] for item in outputs} == {"ResearchPlan", "FactorProposal"}
    restarted = api.MethodHandoffStore(root, JobQueue(queue_path))
    assert restarted.load(ref).package_digest == value.package_digest
    assert restarted.accept(ref, answer) == outcome
    assert restarted.artifacts(ref) == outputs
    changed = copy.deepcopy(answer)
    changed["result"]["status"] = "FAILED"
    assert restarted.accept(ref, changed).status == "REJECTED"
    after = sqlite3.connect(queue_path).execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    assert before == after
    raw = repr(sqlite3.connect(queue_path).execute("SELECT * FROM external_dispatches").fetchall())
    assert "hypotheses" not in raw and "method_context" not in raw


def test_package_rejects_private_machine_paths_and_credentials():
    api = _api()
    value = package()
    payload = value.context.to_dict()
    from finahinking.research.context_bundle import ResearchContextBundle
    from finahinking.research.method_context import LoadedMethods

    loaded = LoadedMethods.from_dict(payload.pop("method_context"))
    payload["question"] = "read /Users/person/private/file"
    context = ResearchContextBundle(json.dumps(payload))
    task = AgentTask("research", "path-test", "input", capabilities=("paper_only",))
    with pytest.raises(ValueError):
        api.build_method_handoff(task, context, loaded)


def test_nested_plan_evidence_cannot_cite_another_task(tmp_path):
    api = _api()
    value = package()
    answer = response_for(value)
    plan = artifact_for(value)
    plan["payload"]["validation_spec"]["evidence_refs"] = ["artifact:foreign"]
    plan["content_digest"] = stable_digest(plan["payload"])
    answer["output_artifacts"] = [plan]
    store = api.MethodHandoffStore(tmp_path / "state", JobQueue(tmp_path / "queue.sqlite"))
    ref = store.export(value)
    assert store.accept(ref, answer).status == "REJECTED"
    assert store.artifacts(ref) == ()


def test_json_pair_array_cannot_normalize_into_foreign_plan_evidence(tmp_path):
    api = _api()
    value = package()
    output = artifact_for(value)
    output["payload"]["validation_spec"] = [
        ["evidence_refs", ["artifact:foreign-task"]], ["paper_only", True],
    ]
    output["content_digest"] = stable_digest(output["payload"])
    answer = response_for(value)
    answer["output_artifacts"] = [output]
    store = api.MethodHandoffStore(tmp_path / "state", JobQueue(tmp_path / "queue.sqlite"))
    ref = store.export(value)
    assert store.accept(ref, answer).status == "REJECTED"
    assert store.status(ref)["status"] == "EXTERNAL_HANDOFF_REQUIRED"
    assert store.artifacts(ref) == ()


@pytest.mark.parametrize("key,private", [
    ("/srv/private/model.json", "fixture"), ("note", r"\\server\private\model.json"),
])
def test_machine_path_keys_and_unc_values_are_rejected_before_publication(tmp_path, key, private):
    api = _api()
    value = package()
    output = artifact_for(value)
    output["payload"]["validation_spec"][key] = private
    output["content_digest"] = hashlib.sha256(json.dumps(
        output["payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    answer = response_for(value)
    answer["output_artifacts"] = [output]
    store = api.MethodHandoffStore(tmp_path / "state", JobQueue(tmp_path / "queue.sqlite"))
    ref = store.export(value)
    assert store.accept(ref, answer).status == "REJECTED"
    assert store.status(ref)["status"] == "EXTERNAL_HANDOFF_REQUIRED"
    assert store.artifacts(ref) == ()


def test_concurrent_identical_callbacks_across_stores_return_same_outcome(tmp_path, monkeypatch):
    api = _api()
    root, queue_path = tmp_path / "state", tmp_path / "queue.sqlite"
    first = api.MethodHandoffStore(root, JobQueue(queue_path))
    second = api.MethodHandoffStore(root, JobQueue(queue_path))
    value = package()
    ref = first.export(value)
    answer = response_for(value)
    answer["output_artifacts"] = [artifact_for(value)]
    receipt_path = root / "method-results" / f"{ref.split(':')[1]}.json"
    actual_exists = Path.exists
    interleaved = []

    def exists(path):
        observed = actual_exists(path)
        if path == receipt_path and not observed and not interleaved:
            interleaved.append(True)
            assert second.accept(ref, answer).status == "READY"
        return observed

    monkeypatch.setattr(Path, "exists", exists)
    outcome = first.accept(ref, answer)
    assert interleaved == [True]
    assert outcome.status == "READY"
    assert first.accept(ref, answer).output_digest == outcome.output_digest
    assert len(first.artifacts(ref)) == 1


def test_documented_export_purposes_produce_ready_selection():
    guide = Path(__file__).resolve().parents[2] / "docs" / "CODEX_METHOD_HANDOFF_GUIDE.md"
    source = guide.read_text().split("```python", 1)[1].split("```", 1)[0]
    tree = ast.parse(source)
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == "MethodRouteRequest")
    fields = {item.arg: ast.literal_eval(item.value) for item in call.keywords}
    selected = select_methods(MethodRouteRequest(**fields), ResearchAssetCatalog.packaged())
    assert selected.status == "READY"


@pytest.mark.parametrize("key,value", [
    ("apiKey", "fixture-only"), ("clientSecret", "fixture-only"),
    ("note", "/etc/passwd"), ("note", "/srv/private/model.json"),
])
def test_nested_private_metadata_never_enters_output_store(tmp_path, key, value):
    api = _api()
    package_value = package()
    answer = response_for(package_value)
    output = artifact_for(package_value)
    output["payload"]["validation_spec"][key] = value
    # Simulate untrusted JSON, bypassing the client contract's credential-key rejection.
    output["content_digest"] = hashlib.sha256(json.dumps(
        output["payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    answer["output_artifacts"] = [output]
    store = api.MethodHandoffStore(tmp_path / "state", JobQueue(tmp_path / "queue.sqlite"))
    ref = store.export(package_value)
    assert store.accept(ref, answer).status == "REJECTED"
    assert store.artifacts(ref) == ()


def test_equivalent_artifact_order_replay_is_idempotent(tmp_path):
    api = _api()
    value = package()
    store = api.MethodHandoffStore(tmp_path / "state", JobQueue(tmp_path / "queue.sqlite"))
    ref = store.export(value)
    answer = response_for(value)
    answer["output_artifacts"] = [artifact_for(value), artifact_for(value, "FactorProposal")]
    first = store.accept(ref, answer)
    answer["output_artifacts"].reverse()
    second = store.accept(ref, answer)
    assert first.status == second.status == "READY"
    assert first.output_digest == second.output_digest
    assert len(store.artifacts(ref)) == 2


@pytest.mark.parametrize("corruption", ["receipt_missing", "output_changed", "path_in_index"])
def test_completed_result_corruption_fails_closed(tmp_path, corruption):
    api = _api()
    value = package()
    root = tmp_path / "state"
    store = api.MethodHandoffStore(root, JobQueue(tmp_path / "queue.sqlite"))
    ref = store.export(value)
    answer = response_for(value)
    answer["output_artifacts"] = [artifact_for(value)]
    assert store.accept(ref, answer).status == "READY"
    path = root / "method-results" / f"{ref.split(':')[1]}.json"
    receipt = json.loads(path.read_text())
    if corruption == "receipt_missing":
        path.unlink()
    elif corruption == "output_changed":
        output_path = root / "method-artifacts" / f"{receipt['artifacts'][0]['artifact_digest']}.json"
        document = json.loads(output_path.read_text())
        document["output"]["payload"]["hypotheses"] = ["changed"]
        output_path.write_text(json.dumps(document))
    else:
        receipt["artifacts"][0]["artifact_digest"] = "../another-task"
        path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        store.artifacts(ref)


def test_receipt_to_queue_crash_recovers_without_model_rerun(tmp_path, monkeypatch):
    api = _api()
    root, path = tmp_path / "state", tmp_path / "queue.sqlite"
    queue = JobQueue(path)
    store = api.MethodHandoffStore(root, queue)
    value = package()
    ref = store.export(value)
    answer = response_for(value)
    answer["output_artifacts"] = [artifact_for(value)]

    def interrupted(*args, **kwargs):
        raise RuntimeError("simulated crash before queue commit")

    monkeypatch.setattr(queue, "record_external_result", interrupted)
    with pytest.raises(RuntimeError):
        store.accept(ref, answer)
    resumed = api.MethodHandoffStore(root, JobQueue(path))
    assert resumed.status(ref)["status"] == "READY"
    assert len(resumed.artifacts(ref)) == 1


def test_receipt_corruption_is_not_silently_treated_as_no_outputs(tmp_path):
    api = _api()
    root, path = tmp_path / "state", tmp_path / "queue.sqlite"
    store = api.MethodHandoffStore(root, JobQueue(path))
    value = package()
    ref = store.export(value)
    answer = response_for(value)
    answer["output_artifacts"] = [artifact_for(value)]
    store.accept(ref, answer)
    receipt_path = next((root / "method-results").glob("*.json"))
    receipt = json.loads(receipt_path.read_text())
    receipt["artifacts"] = []
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        store.artifacts(ref)


def test_application_scopes_handoff_without_triggering_provider_or_new_main_job(tmp_path):
    _api()
    from finahinking.research.application_service import ResearchApplicationService
    from tests.research.test_application_service import payload

    root = tmp_path / "app"
    service = ResearchApplicationService(root, scope_id="owner", allow_model_calls=False)
    other = ResearchApplicationService(root, scope_id="other", allow_model_calls=False)
    try:
        job = service.submit(payload(service))
        exported = service.export_method_handoff(job["job_id"], MethodRouteRequest(purposes=("factor-mining",)))
        assert exported["status"] == "EXTERNAL_HANDOFF_REQUIRED"
        value = _api().MethodHandoffPackage.from_dict(exported["package"])
        answer = response_for(value)
        answer["output_artifacts"] = [artifact_for(value)]
        accepted = service.accept_method_handoff(job["job_id"], exported["handoff_ref"], answer)
        assert accepted["outcome"]["status"] == "READY"
        assert accepted["summary"]["deterministic_execution"] == "NOT_RUN"
        assert len(accepted["artifacts"]) == 1
        assert service.list_jobs()["total"] == 1 and other.list_jobs()["total"] == 0
        with pytest.raises(KeyError):
            other.get_method_handoff(job["job_id"], exported["handoff_ref"])
    finally:
        service.close()
        other.close()

"""Frozen, scoped validation plans and projections of their published worker reports."""

import json
import re
from types import SimpleNamespace

from finahinking.data.snapshot_store import SnapshotStore

from .contracts import stable_digest
from .job_queue import JobStatus
from .learning_review import LearningReviewStore
from .source_updates import require_active_research_source
from .strategy_settlement import StrategySettlementService

VERSION = "strategy-validation.v1"
STUDIO_FIELDS = {
    "portfolio",
    "run_id",
    "studio_version",
    "original_report",
    "original_ledger",
    "snapshot",
    "plan_fingerprint",
}


class StrategyValidationService:
    def __init__(self, app):
        self.app = app
        self.sources = StrategySettlementService(app)
        with app.queue._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS strategy_validation_plans(
                scope_id TEXT NOT NULL, fingerprint TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(scope_id,fingerprint))""")

    def _plan(self, source, options, portfolio):
        from .strategy_attribution import normalize_portfolio_options
        from .strategy_walkforward import build_validation_plan

        require_active_research_source(self.app.root, "STRATEGY", source["template_id"])
        engine = build_validation_plan(source, options)
        return {
            **engine,
            "portfolio": normalize_portfolio_options(portfolio),
            "run_id": source["run_id"],
            "studio_version": VERSION,
            "original_report": source["report_digest"],
            "original_ledger": source["ledger_digest"],
            "snapshot": source["snapshot_ref"],
        }

    def preview(self, run_id, options, portfolio):
        plan = self._plan(self.sources.replay_inputs(run_id), options, portfolio)
        ref = stable_digest([self.app.scope_id, plan])
        payload = json.dumps(
            plan, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        )
        with self.app.queue._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO strategy_validation_plans VALUES (?,?,?)",
                (self.app.scope_id, ref, payload),
            )
        return self.load(ref)

    def load(self, ref):
        if not isinstance(ref, str) or not re.fullmatch(r"[a-f0-9]{64}", ref):
            raise ValueError("验证方案指纹格式有误")
        with self.app.queue._connect() as db:
            row = db.execute(
                "SELECT payload FROM strategy_validation_plans WHERE scope_id=? AND fingerprint=?",
                (self.app.scope_id, ref),
            ).fetchone()
        if not row:
            raise KeyError("当前工作区没有这份冻结方案")
        plan = json.loads(row[0])
        if stable_digest([self.app.scope_id, plan]) != ref:
            raise ValueError("冻结方案完整性校验失败")
        return {**plan, "plan_fingerprint": ref}

    def bound(self, run_id, ref):
        plan = self.load(ref)
        if plan["run_id"] != run_id:
            raise ValueError("所选账本与冻结方案不同，请重新预览")
        source = self.sources.replay_inputs(run_id)
        current = self._plan(source, plan["options"], plan["portfolio"])
        if stable_digest([self.app.scope_id, current]) != ref:
            raise ValueError("数据、模板或执行口径已变化，请重新预览")
        return plan, source

    def submit(self, run_id, ref):
        plan, source = self.bound(run_id, ref)
        binding = {
            "source_run": run_id,
            "plan_ref": ref,
            "decision_at": source["binding"]["contract"]["observed_at"][-1],
        }
        return self.app.submit(
            {
                "snapshot_ref": source["snapshot_ref"],
                "instrument": source["instruments"][0],
                "policy_ref": source["policy_ref"],
                "as_of": source["as_of"],
                "idempotency_key": "validate-" + ref,
                "question": "核对纸面策略的样本外表现与组合风险",
                "budget": {
                    "max_wall_seconds": 300,
                    "max_bytes": 64 * 1024 * 1024,
                    "max_experiments": 1 + plan["evaluation_units"],
                },
            },
            strategy_validation=binding,
        )

    def history(self, *, offset=0, limit=20):
        with self.app.queue._connect() as db:
            rows = db.execute(
                """SELECT j.job_id AS study_id, j.status,
                    json_extract(r.payload,'$.research_plan.validation_spec.strategy_validation.source_run') AS run_id,
                    json_extract(r.payload,'$.research_plan.validation_spec.strategy_validation.plan_ref') AS plan_fingerprint
                FROM jobs j JOIN request_snapshots r ON j.task_ref=r.task_ref
                WHERE json_extract(r.payload,'$.research_plan.validation_spec.scope_id')=?
                AND json_type(r.payload,'$.research_plan.validation_spec.strategy_validation')='object'
                ORDER BY j.created_at DESC,j.job_id LIMIT ? OFFSET ?""",
                (self.app.scope_id, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, study_id):
        record = self.app.queue.get(study_id)
        request = self.app._request(record)
        binding = request.research_plan.validation_spec.get("strategy_validation")
        if not binding:
            raise KeyError("此任务没有策略验证方案")
        plan = self.load(binding["plan_ref"])
        result = {
            "study_id": study_id,
            "run_id": binding["source_run"],
            "plan_fingerprint": binding["plan_ref"],
            "status": record.status.value,
            "failure_kind": self.app.get_job(study_id)["failure_kind"]
            if record.status in (JobStatus.FAILED, JobStatus.RETRYABLE)
            else None,
            "report_url": None,
        }
        if record.status is JobStatus.COMPLETED:
            from .application_service import _read_json, verify_research_receipt

            publication = verify_research_receipt(
                self.app.root, self.app.queue, record, record.result_ref
            )
            analytics = _read_json(
                publication["bundle"] / "4_quant" / "analytics.json", max_bytes=16 * 1024 * 1024
            )
            evidence = analytics["strategy_validation"]
            if (
                evidence["plan"]["plan_fingerprint"] != binding["plan_ref"]
                or evidence["plan"]["run_id"] != binding["source_run"]
            ):
                raise ValueError("验证报告与冻结输入不一致")
            result.update(
                walkforward=evidence["walkforward"],
                portfolio=evidence["portfolio"],
                report_url=f"/research/{request.run_id}/report/complete",
            )
        return plan, result


def progress(app, key, job_id):
    """Poll only the verified request header and queue status, without opening any report."""
    record = app.queue.get(job_id)
    request = app._request(record)
    expected = "strategy_validation" if key == "study" else "strategy_study"
    if expected not in request.research_plan.validation_spec:
        raise KeyError("任务类型与所选记录不同")
    return {"status": record.status.value}


def worker_inputs(root, queue, request, binding):
    scope = request.research_plan.validation_spec["scope_id"]
    context = SimpleNamespace(
        root=root,
        queue=queue,
        scope_id=scope,
        snapshots=SnapshotStore(root / "snapshots"),
        learning=SimpleNamespace(store=LearningReviewStore(queue, root, scope_id=scope)),
    )
    plan, source = StrategyValidationService(context).bound(
        binding["source_run"], binding["plan_ref"]
    )
    contract = source["binding"]["contract"]
    if (
        source["snapshot_ref"] != request.snapshot_ref
        or source["policy_ref"] != request.previous_policy_fingerprint
        or contract["instrument_ids"][0] != request.instrument
        or binding["decision_at"] != contract["observed_at"][-1]
        or source["as_of"] != request.as_of.isoformat()
    ):
        raise ValueError("验证任务的输入与原策略不匹配")
    return plan, source


def evaluate(plan, source):
    from .strategy_attribution import analyze_portfolio
    from .strategy_walkforward import run_validation_plan

    engine = {key: value for key, value in plan.items() if key not in STUDIO_FIELDS}
    return {
        "plan": plan,
        "walkforward": run_validation_plan(source, engine),
        "portfolio": analyze_portfolio(source, plan["portfolio"]),
    }

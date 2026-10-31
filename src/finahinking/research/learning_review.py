"""Explicit local review, immutable policy versions and paper-only reruns.

The queue database owns review/configuration control state, not published run
facts. Outcomes are reconstructed from verified reports and saved observations.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime
from pathlib import Path

from .contracts import stable_digest, to_jsonable
from .job_queue import _ref
from .learning_manager import (
    AdmissionDecision,
    LearningAdmissionRecord,
    LearningManager,
    LearningProposalStore,
    VersionedResearchPolicy,
)
from .snapshot_tools import local_research_policy


def _object(values, allowed):
    if not isinstance(values, dict) or set(values) - set(allowed):
        raise ValueError("学习请求包含不支持的字段")


def _decode(row):
    payload = json.loads(row["payload"])
    if stable_digest(payload) != row["payload_digest"]:
        raise ValueError("学习记录完整性检查失败")
    return payload


def _json(payload):
    return json.dumps(to_jsonable(payload), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _baseline(policy):
    return {key: policy[key] for key in ("version", "factor_weights", "risk_rules", "registry")}


def _effective(policy):
    base = local_research_policy()
    weight, cvar = policy.factor_weights[base["factor_id"]], policy.risk_rules["max_cvar"]
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in (weight, cvar)) or not 0.1 <= weight <= 1 or not 0.01 <= cvar <= base["max_cvar"]:
        raise ValueError("学习配置超过本地纸面研究边界")
    return {**base, "version": f"local-paper.v3.learned-{policy.version}", "signal_budget_scale": weight, "max_cvar": cvar, "learning_policy_ref": policy.fingerprint}


class LearningReviewStore:
    def __init__(self, queue, root, *, scope_id):
        self.queue, self.scope_id = queue, scope_id
        self.proposals = LearningProposalStore(Path(root) / "learning" / stable_digest(scope_id))
        with queue._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS learning_reviews (
                    scope_id TEXT NOT NULL, review_id TEXT NOT NULL, run_id TEXT NOT NULL,
                    semantic_key TEXT NOT NULL, input_digest TEXT NOT NULL,
                    payload TEXT NOT NULL, payload_digest TEXT NOT NULL,
                    PRIMARY KEY(scope_id,review_id), UNIQUE(scope_id,run_id), UNIQUE(scope_id,semantic_key));
                CREATE TABLE IF NOT EXISTS learning_policies (
                    scope_id TEXT NOT NULL, policy_ref TEXT NOT NULL, proposal_digest TEXT NOT NULL,
                    payload TEXT NOT NULL, payload_digest TEXT NOT NULL,
                    PRIMARY KEY(scope_id,policy_ref), UNIQUE(scope_id,proposal_digest));
            """)

    @staticmethod
    def initial():
        effective = local_research_policy()
        return {"policy_ref": stable_digest(effective), "version": 0, "factor_weights": {effective["factor_id"]: 1.0}, "risk_rules": {"max_cvar": effective["max_cvar"]}, "registry": {}, "effective": effective, "runtime_fingerprint": stable_digest(effective), "parent_ref": None, "available_as_of": None, "source_proposal_digest": None, "paper_only": True}

    def policy(self, reference=None, *, as_of=None):
        initial = self.initial()
        if reference is None or reference == initial["policy_ref"]:
            return initial
        _ref(reference, "policy_ref")
        with self.queue._connect() as db:
            row = db.execute("SELECT * FROM learning_policies WHERE scope_id=? AND policy_ref=?", (self.scope_id, reference)).fetchone()
        if row is None:
            raise ValueError("配置尚未批准或不属于当前工作区")
        record = _decode(row)
        typed = VersionedResearchPolicy(**record["policy"])
        proposal = self.proposals.get(typed.source_proposal_digest)
        if record["policy_ref"] != reference or typed.fingerprint != reference or proposal.admission is None or proposal.admission.decision is not AdmissionDecision.APPROVED or proposal.preview is None or proposal.preview.policy_fingerprint != record["preview_fingerprint"] or record["effective"] != _effective(typed) or record["base_code_fingerprint"] != initial["policy_ref"] or record["runtime_fingerprint"] != stable_digest(record["effective"]):
            raise ValueError("批准配置与原始审核或当前执行内核不一致")
        if as_of is not None and date.fromisoformat(str(as_of)) < date.fromisoformat(record["available_as_of"]):
            raise ValueError("研究截止日早于该配置的批准可用日期")
        return record

    def _row(self, db, review_id):
        row = db.execute("SELECT * FROM learning_reviews WHERE scope_id=? AND review_id=?", (self.scope_id, _ref(review_id, "review_id"))).fetchone()
        if row is None:
            raise KeyError("复盘不存在")
        result = _decode(row)
        if result["review_id"] != review_id or result["run_id"] != row["run_id"]:
            raise ValueError("复盘引用不一致")
        return result

    def _preview(self, record, proposal):
        return LearningManager().preview_proposal(proposal, _baseline(self.policy(record["parent_policy_ref"])))

    def _recover_policy(self, db, record, proposal):
        if proposal.admission is None or proposal.admission.decision is not AdmissionDecision.APPROVED:
            return None
        parent = self.policy(record["parent_policy_ref"])
        manager = LearningManager()
        admitted = manager.admit_update(proposal.proposal, proposal.admission)
        preview = manager.preview_apply(admitted, _baseline(parent))
        if proposal.preview != preview:
            raise ValueError("已审核预览与原配置不一致")
        policy = manager.apply(admitted, _baseline(parent))
        effective = _effective(policy)
        payload = {**to_jsonable(policy), "policy": to_jsonable(policy), "policy_ref": policy.fingerprint, "effective": effective, "runtime_fingerprint": stable_digest(effective), "parent_ref": parent["policy_ref"], "available_as_of": proposal.admission.as_of.isoformat(), "available_at": proposal.admission.as_of.isoformat() + "T23:59:59.999999999+00:00", "preview_fingerprint": preview.fingerprint, "base_code_fingerprint": self.initial()["policy_ref"], "review_id": record["review_id"]}
        existing = db.execute("SELECT * FROM learning_policies WHERE scope_id=? AND policy_ref=?", (self.scope_id, policy.fingerprint)).fetchone()
        if existing is not None and _decode(existing) != payload:
            raise ValueError("版本配置冲突")
        db.execute("INSERT OR IGNORE INTO learning_policies VALUES (?,?,?,?,?)", (self.scope_id, policy.fingerprint, policy.source_proposal_digest, _json(payload), stable_digest(payload)))
        return payload

    def _view(self, db, record):
        stored = self.proposals.get(record["proposal_digest"])
        preview = self._preview(record, stored.proposal)
        policy = self._recover_policy(db, record, stored)
        decision = stored.admission.decision.value if stored.admission else "PENDING_REVIEW" if record["observation"]["has_updates"] else "KEEP_POLICY"
        return {**record, "evidence_level": "COHORT" if record.get("source_kind") == "LEARNING_COHORT" else "MONITORING" if record.get("source_kind") == "FACTOR_VALIDATION_MONITORING" else "EXPLORATORY", "status": decision, "baseline": _baseline(self.policy(record["parent_policy_ref"])), "proposal": to_jsonable(stored.proposal), "preview": to_jsonable(preview), "preview_fingerprint": preview.fingerprint, "admission": to_jsonable(stored.admission), "policy_ref": policy["policy_ref"] if policy else None, "available_as_of": policy["available_as_of"] if policy else None}

    def save(self, record, event):
        manager = LearningManager()
        proposal = manager.propose_update(event)
        record = {**record, "proposal_digest": proposal.proposal_digest, "settlement": to_jsonable(event), "settlement_fingerprint": event.fingerprint}
        return self.save_proposal(record, proposal)

    def save_proposal(self, record, proposal):
        record = {**record, "proposal_digest": proposal.proposal_digest}
        with self.queue._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT * FROM learning_reviews WHERE scope_id=? AND (run_id=? OR semantic_key=?)", (self.scope_id, record["run_id"], record["semantic_key"])).fetchone()
            if prior is not None:
                if prior["run_id"] != record["run_id"] or prior["input_digest"] != record["input_digest"]:
                    raise ValueError("该纸面信号已经复盘，不能重复计入学习")
                return self._view(db, _decode(prior))
            self.proposals.save(proposal)
            db.execute("INSERT INTO learning_reviews VALUES (?,?,?,?,?,?,?)", (self.scope_id, record["review_id"], record["run_id"], record["semantic_key"], record["input_digest"], _json(record), stable_digest(record)))
            return self._view(db, record)

    def get(self, review_id):
        with self.queue._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._view(db, self._row(db, review_id))

    def list_reviews(self):
        with self.queue._connect() as db:
            rows = db.execute("SELECT review_id FROM learning_reviews WHERE scope_id=? ORDER BY rowid DESC LIMIT 100", (self.scope_id,)).fetchall()
        return [self.get(row["review_id"]) for row in rows]

    def policies(self):
        # Complete the admission-file→SQLite crash window before listing refs.
        self.list_reviews()
        with self.queue._connect() as db:
            rows = db.execute("SELECT policy_ref FROM learning_policies WHERE scope_id=? ORDER BY rowid DESC LIMIT 100", (self.scope_id,)).fetchall()
        return [self.initial(), *(self.policy(row["policy_ref"]) for row in reversed(rows))]

    def decide(self, review_id, values):
        _object(values, ("decision", "expected_preview_fingerprint", "rationale", "allow_exploratory"))
        if not isinstance(values.get("rationale"), str) or not 1 <= len(values["rationale"].strip()) <= 500:
            raise ValueError("请填写1至500字审核依据")
        rationale = values["rationale"].strip()
        with self.queue._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            record = self._row(db, review_id)
            if not record["observation"]["has_updates"]:
                raise ValueError("本次复盘保留原配置，没有待审核修改")
            stored = self.proposals.get(record["proposal_digest"])
            preview = self._preview(record, stored.proposal)
            if values.get("expected_preview_fingerprint") != preview.fingerprint:
                raise ValueError("修改预览已失配，请重新读取后审核")
            decision = AdmissionDecision(values.get("decision"))
            if stored.admission is not None:
                if stored.admission.decision is not decision or stored.admission.rationale != rationale:
                    raise ValueError("提案已完成不可变审核")
                return self._view(db, record)
            exploratory = record.get("source_kind") not in {"LEARNING_COHORT", "FACTOR_VALIDATION_MONITORING"}
            if decision is AdmissionDecision.APPROVED and exploratory and values.get("allow_exploratory") is not True:
                raise ValueError("单次复盘仅是探索性试验，请明确确认探索属性后批准")
            admission = LearningAdmissionRecord(admission_id="review-" + stable_digest([review_id, decision.value])[:32], proposal_digest=stored.proposal.proposal_digest, admitted_by="local-user-exploratory" if exploratory else "local-user", decision=decision, as_of=max(datetime.now(UTC).date(), stored.proposal.as_of), rationale=rationale)
            self.proposals.save(stored.proposal, admission=admission, preview=preview if decision is AdmissionDecision.APPROVED else None)
            return self._view(db, record)


class LearningReviewService:
    def __init__(self, app):
        self.app = app
        self.store = LearningReviewStore(app.queue, app.root, scope_id=app.scope_id)

    def _source(self, run_id):
        _ref(run_id, "run_id")
        with self.app.queue._connect() as db:
            rows = db.execute("SELECT j.job_id FROM jobs j JOIN request_snapshots r ON r.task_ref=j.task_ref WHERE json_extract(r.payload,'$.run_id')=? AND json_extract(r.payload,'$.research_plan.validation_spec.scope_id')=?", (run_id, self.app.scope_id)).fetchall()
        if len(rows) != 1:
            raise ValueError("原研究运行不存在或不属于当前工作区")
        job = self.app.get_job(rows[0]["job_id"])
        if job["status"] != "completed" or job["report_status"] != "AVAILABLE" or job.get("factor_study") is not None:
            raise ValueError("需要完整的普通纸面研究报告，不能复用因子留出测试")
        entry = self.app.runs.get_run(run_id)
        request = self.app._request(self.app.queue.get(job["job_id"]))
        return job, entry, request

    def settle(self, values):
        from .settlement import observe_paper_outcome

        _object(values, ("run_id", "evidence_snapshot_ref", "as_of"))
        job, entry, request = self._source(values.get("run_id"))
        observed_at = datetime.now(UTC)
        if date.fromisoformat(values.get("as_of")) > observed_at.date():
            raise ValueError("未来复盘截止日不能作为已实现纸面学习证据")
        parent = self.store.policy(request.previous_policy_fingerprint)
        source = self.app.snapshots.load(request.snapshot_ref)
        report = entry["manifest"]["source_snapshot"]
        if request.research_plan.validation_spec["policy_fingerprint"] != parent["runtime_fingerprint"] or report.get("quant_analytics", {}).get("policy_fingerprint") != parent["runtime_fingerprint"] or report.get("dataset_fingerprint") != source.fingerprint:
            raise ValueError("原配置或数据绑定不匹配，不能用当前默认值重新解释旧运行")
        if self.lineage(parent, source, request.instrument)["retrospective"]:
            raise ValueError("回溯比较不能作为该配置生效后的新学习证据")
        evidence = self.app.snapshots.load(values.get("evidence_snapshot_ref"))
        risk = entry["manifest"]["source_snapshot"].get("risk_portfolio") or {}
        event, observation, derived = observe_paper_outcome(run_id=request.run_id, source=source, evidence=evidence, source_as_of=request.as_of, as_of=values.get("as_of"), allocation=risk["allocation"], execution_policy=risk["paper_ledger"]["execution_policy"], factor_id=parent["effective"]["factor_id"], factor_weights=parent["factor_weights"], risk_rules=parent["risk_rules"], known_at=observed_at)
        derived_ref = self.app.snapshots.save(derived)
        record = {"review_id": "learning-" + stable_digest([self.app.scope_id, request.run_id])[:32], "run_id": request.run_id, "job_id": job["job_id"], "input_digest": stable_digest(values), "semantic_key": stable_digest([request.instrument, request.as_of, parent["policy_ref"], risk["paper_ledger"]["execution_policy"]["signal_at"]]), "source_snapshot_ref": source.fingerprint, "evidence_snapshot_ref": evidence.fingerprint, "observed_snapshot_ref": derived_ref, "data_modes": {"source": source.mode, "evidence": evidence.mode, "observed": derived.mode}, "as_of": values["as_of"], "parent_policy_ref": parent["policy_ref"], "hypotheses": list(request.research_plan.hypotheses), "source_report_url": job["report_url"], "observation": observation, "paper_only": True}
        record["observed_at"] = observed_at.isoformat()
        return self.store.save(record, event)

    @staticmethod
    def lineage(policy, snapshot, instrument, *, rerun_of=None):
        import pandas as pd

        signal_at = max(row.timestamp for row in snapshot.observations if row.instrument == instrument)
        availability = policy.get("available_at")
        return {"policy_ref": policy["policy_ref"], "runtime_fingerprint": policy["runtime_fingerprint"], "version": policy["version"], "parent_ref": policy["parent_ref"], "available_as_of": policy["available_as_of"], "available_at": availability, "retrospective": availability is not None and signal_at <= pd.Timestamp(availability), "paper_only": True, **({"rerun_of": rerun_of} if rerun_of else {})}

    def rerun(self, values):
        _object(values, ("idempotency_key", "run_id", "policy_ref", "snapshot_ref", "as_of"))
        _, _, request = self._source(values.get("run_id"))
        return self.app.submit({"idempotency_key": values.get("idempotency_key"), "snapshot_ref": values.get("snapshot_ref", request.snapshot_ref), "as_of": values.get("as_of"), "question": request.research_plan.hypotheses[0], "instrument": request.instrument, "policy_ref": values.get("policy_ref"), "model_config_ref": "offline"}, rerun_of=request.run_id)

    def get(self, review_id):
        return self.store.get(review_id)

    def review_factor_monitoring(self, monitoring, *, parent_policy_ref=None):
        parent = self.store.policy(parent_policy_ref)
        proposal = LearningManager().propose_factor_monitoring(monitoring,
            existing_notes=parent["registry"].get("factor_monitoring", {}))
        value = monitoring.to_dict()
        identity = stable_digest([self.app.scope_id, monitoring.fingerprint])
        record = {"review_id": "learning-" + identity[:32], "run_id": "factor-monitoring-" + identity[:32],
            "job_id": None, "source_kind": "FACTOR_VALIDATION_MONITORING", "parent_policy_ref": parent["policy_ref"],
            "input_digest": stable_digest([monitoring.fingerprint, parent["policy_ref"]]), "semantic_key": identity,
            "source_snapshot_ref": None, "evidence_snapshot_ref": None, "observed_snapshot_ref": None,
            "as_of": value["as_of"], "source_report_url": None, "hypotheses": ["复核同定义、同范围的独立样本外窗口变化"],
            "observation": {"has_updates": True, "instrument": value["factor_ref"], "monitoring": value,
                "recommendation_basis": "仅登记研究复核建议；不调整因子权重、风险规则或用途批准。"},
            "settlement": None, "settlement_fingerprint": None, "paper_only": True,
            "observed_at": datetime.now(UTC).isoformat()}
        return self.store.save_proposal(record, proposal)

    def list_reviews(self):
        return self.store.list_reviews()

    def decide(self, review_id, values):
        return self.store.decide(review_id, values)

    def policies(self):
        return self.store.policies()

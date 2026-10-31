"""Scoped immutable cohort evidence, grounded again before creating a proposal."""
from datetime import UTC, date, datetime

from .contracts import stable_digest, to_jsonable
from .job_queue import _ref
from .learning_manager import LearningUpdateProposal, LearningUpdateStatus
from .learning_reliability import MAX_CANDIDATES, assess_outcomes, instant
from .learning_review import _decode, _json, _object
from .settlement import observe_paper_outcome


class LearningAssessmentService:
    def __init__(self, app):
        self.app = app
        with app.queue._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS learning_assessments (scope_id TEXT NOT NULL, assessment_id TEXT NOT NULL, payload TEXT NOT NULL, payload_digest TEXT NOT NULL, PRIMARY KEY(scope_id,assessment_id))")

    def _record(self, review_id):
        with self.app.queue._connect() as db:
            return self.app.learning.store._row(db, review_id)

    def _proof(self, record):
        if record.get("source_kind") or not record.get("settlement"):
            raise ValueError("需要普通纸面结算证据")
        _, entry, request = self.app.learning._source(record["run_id"])
        parent = self.app.learning.store.policy(record["parent_policy_ref"])
        source = self.app.snapshots.load(record["source_snapshot_ref"])
        evidence = self.app.snapshots.load(record["evidence_snapshot_ref"])
        report = entry["manifest"]["source_snapshot"]
        quant = report.get("quant_analytics", {})
        identity = report.get("reproduction", {}).get("identity")
        if not identity or not identity.get("source_digest"):
            raise ValueError("证据缺少冻结执行身份")
        if request.snapshot_ref != source.fingerprint or request.previous_policy_fingerprint != parent["policy_ref"] or report.get("dataset_fingerprint") != source.fingerprint or quant.get("policy_fingerprint") != parent["runtime_fingerprint"] or request.research_plan.validation_spec["policy_fingerprint"] != parent["runtime_fingerprint"]:
            raise ValueError("证据配置或数据绑定不匹配")
        if self.app.learning.lineage(parent, source, request.instrument)["retrospective"]:
            raise ValueError("回溯复跑不是新学习证据")
        observed_at = instant(record["observed_at"])
        if observed_at > datetime.now(UTC):
            raise ValueError("证据记录时间在未来")
        risk = report["risk_portfolio"]
        execution = risk["paper_ledger"]["execution_policy"]
        event, observation, derived = observe_paper_outcome(run_id=request.run_id, source=source, evidence=evidence, source_as_of=request.as_of, as_of=record["as_of"], allocation=risk["allocation"], execution_policy=execution, factor_id=parent["effective"]["factor_id"], factor_weights=parent["factor_weights"], risk_rules=parent["risk_rules"], known_at=observed_at)
        semantic = stable_digest([request.instrument, request.as_of, parent["policy_ref"], execution["signal_at"]])
        modes = {"source": source.mode, "evidence": evidence.mode, "observed": derived.mode}
        if event.fingerprint != record["settlement_fingerprint"] or to_jsonable(event) != record["settlement"] or to_jsonable(observation) != record["observation"] or derived.fingerprint != record["observed_snapshot_ref"] or self.app.snapshots.load(record["observed_snapshot_ref"]).fingerprint != derived.fingerprint or record["semantic_key"] != semantic or to_jsonable(modes) != record["data_modes"]:
            raise ValueError("结算证据重建失配")
        scope = {"instrument": request.instrument, "parent_policy_ref": parent["policy_ref"], "data_modes": to_jsonable(modes), "execution_identity": identity, "cost_policy": {key: value for key, value in execution.items() if key not in {"signal_at", "fingerprint"}}, "horizon": 5, "factor_id": parent["effective"]["factor_id"], "factor_weight": parent["factor_weights"][parent["effective"]["factor_id"]], "max_cvar": parent["risk_rules"]["max_cvar"]}
        return {"review_id": record["review_id"], "semantic_key": semantic, "settlement_digest": event.fingerprint, "ledger_digest": event.ledger.fingerprint, "dataset_digest": event.dataset_digest, "scope": scope, "verified": True, "decision_at": observation["decision_cutoff"], "mark_at": observation["mark_timestamp"], "available_at": observation["available_at"], "observed_at": record["observed_at"], "net_return": observation["net_return"], "quantity": observation["quantity"], "record_digest": stable_digest(record), "manifest_digest": stable_digest(entry["manifest"])}

    def assess(self, values):
        _object(values, ("anchor_review_id", "start", "as_of"))
        start, cutoff = date.fromisoformat(values["start"]), date.fromisoformat(values["as_of"])
        if cutoff > datetime.now(UTC).date() or start > cutoff:
            raise ValueError("评估日期范围无效或在未来")
        anchor = self._record(values.get("anchor_review_id"))
        scope = self._proof(anchor)["scope"]
        with self.app.queue._connect() as db:
            rows = db.execute("SELECT * FROM learning_reviews WHERE scope_id=? AND json_extract(payload,'$.source_kind') IS NULL AND json_extract(payload,'$.parent_policy_ref')=? AND json_extract(payload,'$.observation.instrument')=? AND json_extract(payload,'$.as_of')>=? AND json_extract(payload,'$.as_of')<=? ORDER BY review_id LIMIT ?", (self.app.scope_id, scope["parent_policy_ref"], scope["instrument"], start.isoformat(), cutoff.isoformat(), MAX_CANDIDATES+1)).fetchall()
        if len(rows) > MAX_CANDIDATES:
            raise ValueError("最多评估 200 条候选，请缩小日期范围")
        proofs = []
        for row in rows:
            try:
                proofs.append(self._proof(_decode(row)))
            except (ValueError, KeyError, TypeError, OSError):
                proofs.append({"review_id": row["review_id"], "verified": False, "exclusion_reason": "INVALID_PROOF", "stored_digest": row["payload_digest"]})
        result = assess_outcomes(proofs, scope=scope, start=start.isoformat(), as_of=cutoff.isoformat())
        payload = {**result, "anchor_review_id": anchor["review_id"], "anchor_run_id": anchor["run_id"], "proofs": proofs, "scope_id": self.app.scope_id}
        identity = "assessment-" + stable_digest(payload)
        payload["assessment_id"] = identity
        with self.app.queue._connect() as db:
            db.execute("INSERT OR IGNORE INTO learning_assessments VALUES (?,?,?,?)", (self.app.scope_id, identity, _json(payload), stable_digest(payload)))
        return self.get(identity)

    def get(self, identity):
        with self.app.queue._connect() as db:
            row = db.execute("SELECT * FROM learning_assessments WHERE scope_id=? AND assessment_id=?", (self.app.scope_id, _ref(identity, "assessment_id"))).fetchone()
        if row is None:
            raise KeyError("样本评估不存在")
        payload = _decode(row)
        if payload["assessment_id"] != identity or payload["scope_id"] != self.app.scope_id or "assessment-" + stable_digest({key: value for key, value in payload.items() if key != "assessment_id"}) != identity:
            raise ValueError("样本评估身份失配")
        return payload

    def list(self):
        with self.app.queue._connect() as db:
            rows = db.execute("SELECT assessment_id FROM learning_assessments WHERE scope_id=? ORDER BY rowid DESC LIMIT 100", (self.app.scope_id,)).fetchall()
        return [self.get(row[0]) for row in rows]

    def propose(self, identity):
        assessment = self.get(identity)
        if assessment["status"] != "RECOMMENDED":
            raise ValueError("样本不足或未达到固定建议门槛，保留原配置")
        # Recheck every frozen candidate, including excluded counterevidence. Later
        # observations form a new assessment; they never rewrite this snapshot.
        for proof in assessment["proofs"]:
            try:
                current = self._proof(self._record(proof["review_id"]))
            except (ValueError, KeyError, TypeError, OSError) as exc:
                if not proof.get("verified"):
                    continue
                raise ValueError("冻结证据已失效，请重新评估") from exc
            if current != proof:
                raise ValueError("冻结证据已变化，请重新评估")
        parent = self.app.learning.store.policy(assessment["scope"]["parent_policy_ref"])
        selected = [row for row in assessment["proofs"] if row["review_id"] in assessment["selected_review_ids"]]
        proposal = LearningUpdateProposal(LearningUpdateStatus.PROPOSAL_READY, stable_digest(assessment), assessment["as_of"], factor_weight_updates=assessment["factor_weight_updates"], risk_rule_updates=assessment["risk_rule_updates"], ledger_digest=stable_digest([row["ledger_digest"] for row in selected]), dataset_digest=stable_digest([row["dataset_digest"] for row in selected]))
        digest = stable_digest([self.app.scope_id, identity])
        anchor = self._record(assessment["anchor_review_id"])
        record = {"review_id": "learning-" + digest[:32], "run_id": "learning-cohort-" + digest[:32], "anchor_run_id": assessment["anchor_run_id"], "job_id": None, "source_kind": "LEARNING_COHORT", "parent_policy_ref": parent["policy_ref"], "input_digest": stable_digest(assessment), "semantic_key": digest, "source_snapshot_ref": anchor["source_snapshot_ref"], "evidence_snapshot_ref": None, "observed_snapshot_ref": None, "assessment_id": identity, "as_of": assessment["as_of"], "source_report_url": anchor["source_report_url"], "hypotheses": ["分离纸面窗口的固定规则评估；批准后开展成对版本研究"], "observation": {"instrument": assessment["scope"]["instrument"], "has_updates": True, "assessment": assessment, "recommendation_basis": "至少20个分离窗口、净收益均值小于零、95% Wilson 亏损比例下界超过0.5。仅为固定工程规则。"}, "settlement": None, "settlement_fingerprint": None, "paper_only": True, "observed_at": datetime.now(UTC).isoformat()}
        return self.app.learning.store.save_proposal(record, proposal)

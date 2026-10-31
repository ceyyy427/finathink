"""Durable two-child offline policy comparison with explicit enqueue recovery."""
from datetime import UTC, date, datetime
from math import isfinite

from .contracts import stable_digest
from .job_queue import _ref
from .learning_reliability import instant
from .learning_review import _decode, _json, _object
from .research_identity import execution_identity
from .snapshot_tools import local_research_policy


class LearningReplayService:
    def __init__(self, app):
        self.app = app
        with app.queue._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS learning_pairs (scope_id TEXT NOT NULL, pair_id TEXT NOT NULL, payload TEXT NOT NULL, payload_digest TEXT NOT NULL, PRIMARY KEY(scope_id,pair_id))")

    def _load(self, identity):
        with self.app.queue._connect() as db:
            row = db.execute("SELECT * FROM learning_pairs WHERE scope_id=? AND pair_id=?", (self.app.scope_id, _ref(identity, "pair_id"))).fetchone()
        if row is None:
            raise KeyError("成对复跑不存在")
        record = _decode(row)
        if record["pair_id"] != identity or record["binding"]["scope_id"] != self.app.scope_id or "pair-" + stable_digest(record["binding"]) != identity or set(record["children"]) - {"left", "right"}:
            raise ValueError("成对复跑身份失配")
        return record

    def create(self, values):
        _object(values, ("review_id", "snapshot_ref", "as_of"))
        review = self.app.learning.get(values.get("review_id"))
        if review["status"] != "APPROVED" or review.get("source_kind") == "FACTOR_VALIDATION_MONITORING":
            raise ValueError("请选择已批准的数值配置复盘")
        as_of = date.fromisoformat(values["as_of"])
        if as_of > datetime.now(UTC).date():
            raise ValueError("复跑截止日不能在未来")
        parent = self.app.learning.store.policy(review["parent_policy_ref"], as_of=as_of)
        approved = self.app.learning.store.policy(review["policy_ref"], as_of=as_of)
        anchor = review.get("anchor_run_id", review["run_id"])
        _, _, request = self.app.learning._source(anchor)
        snapshot = self.app.snapshots.load(values["snapshot_ref"])
        observations = [row for row in snapshot.observations if row.instrument == request.instrument]
        if not observations or snapshot.as_of.date() > as_of:
            raise ValueError("请选择截止日前包含该标的的冻结数据")
        earliest = min(row.timestamp.to_pydatetime() for row in observations)
        learning_end = instant(review["as_of"], end=True)
        retrospective = earliest <= max(learning_end, instant(approved["available_as_of"], end=True))
        binding = {"scope_id": self.app.scope_id, "review_id": review["review_id"], "anchor_run_id": anchor,
            "snapshot_ref": snapshot.fingerprint, "as_of": as_of.isoformat(), "instrument": request.instrument,
            "question": request.research_plan.hypotheses[0], "left_policy_ref": parent["policy_ref"], "right_policy_ref": approved["policy_ref"],
            "execution_identity": execution_identity(local_research_policy()), "comparison_kind": "RETROSPECTIVE" if retrospective else "FORWARD_DESCRIPTIVE"}
        identity = "pair-" + stable_digest(binding)
        record = {"pair_id": identity, "binding": binding, "children": {}, "paper_only": True}
        with self.app.queue._connect() as db:
            db.execute("INSERT OR IGNORE INTO learning_pairs VALUES (?,?,?,?)", (self.app.scope_id, identity, _json(record), stable_digest(record)))
        return self.resume(identity)

    def resume(self, identity):
        record = self._load(identity)
        binding = record["binding"]
        if binding["execution_identity"] != execution_identity(local_research_policy()):
            raise ValueError("代码或依赖身份已变化；请保留旧记录并创建新的成对复跑")
        # No queue enqueue happens inside a DB write transaction. The durable
        # reservation and deterministic keys cover either half of a crash.
        for side in ("left", "right"):
            if side in record["children"]:
                continue
            child = self.app.submit({"idempotency_key": identity + "-" + side,
                "snapshot_ref": binding["snapshot_ref"], "as_of": binding["as_of"],
                "question": binding["question"], "instrument": binding["instrument"],
                "policy_ref": binding[side + "_policy_ref"], "model_config_ref": "offline"}, rerun_of=binding["anchor_run_id"])
            ref = {"job_id": child["job_id"], "run_id": child["run_id"]}
            with self.app.queue._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM learning_pairs WHERE scope_id=? AND pair_id=?", (self.app.scope_id, identity)).fetchone()
                latest = _decode(row)
                prior = latest["children"].get(side)
                if prior is not None and prior != ref:
                    raise ValueError("复跑子任务绑定冲突")
                latest["children"][side] = ref
                db.execute("UPDATE learning_pairs SET payload=?,payload_digest=? WHERE scope_id=? AND pair_id=?", (_json(latest), stable_digest(latest), self.app.scope_id, identity))
            record = self._load(identity)
        return self.get(identity)

    def _child(self, record, side, ref):
        binding = record["binding"]
        job = self.app.get_job(ref["job_id"])
        request = self.app._request(self.app.queue.get(ref["job_id"]))
        expected_run = "research-" + stable_digest({"scope": self.app.scope_id, "key": record["pair_id"] + "-" + side})[:24]
        if request.run_id != expected_run or request.run_id != ref["run_id"] or request.snapshot_ref != binding["snapshot_ref"] or str(request.as_of) != binding["as_of"] or request.instrument != binding["instrument"] or request.previous_policy_fingerprint != binding[side+"_policy_ref"] or list(request.research_plan.hypotheses) != [binding["question"]] or request.research_plan.validation_spec.get("model_config_ref") != "offline" or request.research_plan.validation_spec.get("rerun_of") != binding["anchor_run_id"]:
            raise ValueError("子任务输入与冻结绑定不同")
        if job["status"] == "completed":
            if job["report_status"] != "AVAILABLE":
                raise ValueError("子任务报告未通过完整性校验")
            source = self.app.runs.get_run(request.run_id)["manifest"]["source_snapshot"]
            frozen = source.get("reproduction", {})
            policy = self.app.learning.store.policy(binding[side+"_policy_ref"])
            if frozen.get("identity") != binding["execution_identity"] or frozen.get("policy") != policy["effective"] or source.get("dataset_fingerprint") != binding["snapshot_ref"] or source.get("quant_analytics", {}).get("policy_fingerprint") != policy["runtime_fingerprint"]:
                raise ValueError("子任务报告执行身份失配")
        return {**ref, "status": job["status"], "report_status": job["report_status"], "report_url": job["report_url"], "metrics": job["metrics"]}

    def get(self, identity):
        record = self._load(identity)
        children, reasons, metrics = {}, [], []
        status = "INCOMPLETE" if len(record["children"]) < 2 else "RUNNING"
        for side, ref in record["children"].items():
            try:
                children[side] = self._child(record, side, ref)
                if children[side]["status"] in {"failed", "cancelled"}:
                    reasons.append(f"{side} 子任务失败或已取消")
            except (ValueError, KeyError, OSError, TypeError):
                children[side] = {**ref, "status": "INVALID", "report_status": "UNAVAILABLE", "report_url": None}
                reasons.append(f"{side} 子任务或报告身份校验失败")
        if reasons:
            status = "BLOCKED"
        elif len(children) == 2 and all(child["status"] == "completed" for child in children.values()):
            status = "AVAILABLE"
            left, right = children["left"]["metrics"], children["right"]["metrics"]
            for key in sorted(left.keys() & right.keys()):
                if all(isinstance(v, (int, float)) and not isinstance(v, bool) and isfinite(v) for v in (left[key], right[key])):
                    delta = right[key]-left[key]
                    if isfinite(delta):
                        metrics.append({"metric": key, "left": left[key], "right": right[key], "delta": delta})
        return {**record, "children": children, "status": status, "reasons": reasons, "metrics": metrics,
            "comparison_kind": record["binding"]["comparison_kind"], "model_called": False,
            "limits": ["同数据、同代码的描述性比较；配置版本是唯一预定差异。", "回溯复用学习期不能充当独立验证，较晚窗口也不证明未来有效。", "复跑不自动结算或写入新的学习证据。"]}

    def list(self):
        with self.app.queue._connect() as db:
            rows = db.execute("SELECT pair_id FROM learning_pairs WHERE scope_id=? ORDER BY rowid DESC LIMIT 100", (self.app.scope_id,)).fetchall()
        return [self.get(row[0]) for row in rows]

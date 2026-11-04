"""Bind research pool entries to verified, source-specific report products."""

from datetime import UTC, datetime

from finahinking.data.panel_contracts import panel_time

from .contracts import stable_digest
from .research_pools import ResearchPoolStore


def pool_store(service):
    return ResearchPoolStore(service.queue, scope_id=service.scope_id)


def published_signal(job, source, instrument, origin):
    """Resolve the attributed product and its observation time, when known."""
    strategy = source.get("strategy_study", {})
    result = strategy.get("result", {})
    if origin in {"USER", "QUANT_BASELINE", "AUTO_SCAN"}:
        if result.get("status") != "COMPLETE":
            raise ValueError("策略报告缺少完整目标记录")
        decision = next((row for row in reversed(result["decisions"]) if row["status"] == "SCHEDULED"), None)
        if not decision or instrument not in decision["target_weights"]:
            raise ValueError("已发布策略缺少该资产的纸面目标")
        if origin == "QUANT_BASELINE" and strategy["binding"]["template_id"] != "equal-weight":
            raise ValueError("量化基准需要等权模板结果")
        if origin == "AUTO_SCAN":
            rules = {
                row["rule_id"]: {key: row[key] for key in ("fingerprint", "status", "value", "limit")}
                for row in decision.get("risk", []) if row["status"] in {"PASS", "TRIGGERED"}
            }
            if not rules:
                raise ValueError("自动规则扫描需要已评估的规则")
            return {"kind": "rule-scan", "outcomes": rules,
                    "target_weight_input": float(decision["target_weights"][instrument])}, decision["observed_at"]
        return {"kind": "target-weight", "weight": float(decision["target_weights"][instrument])}, decision["observed_at"]

    if strategy or job["instrument"] != instrument:
        raise ValueError("模型与公共方法记录需要匹配的原始候选报告和资产")
    if origin == "MODEL":
        model = source.get("model_research", {})
        synthesis = model.get("synthesis", {})
        accepted = any(call.get("role") == "research" and call.get("status") == "READY"
                       and call.get("dispatched") is True and call.get("context_fingerprint") == synthesis.get("context_fingerprint")
                       for call in model.get("calls", []))
        if job["execution_mode"] != "provider-grounded" or not accepted or not synthesis.get("hypotheses"):
            raise ValueError("缺少已验证的模型研究假设")
        return {"kind": "model-hypothesis", "hypotheses": synthesis["hypotheses"]}, None
    if origin == "PUBLIC_METHOD":
        method = source.get("method_factor_snapshot", {})
        study = source.get("factor_study", {})
        summary = study.get("test_summary")
        if (study.get("phase") != "TEST" or not summary or summary.get("failure_reason")
                or method.get("snapshot_ref") != job["snapshot_ref"]
                or not method.get("methods_digest") or not method.get("method_manifest_digest")
                or not method.get("factor_spec_fingerprint") or not study.get("selected_candidate")):
            raise ValueError("公共方法需要绑定实际方法和已完成的冻结测试候选")
        return {"kind": "method-candidate", "factor_spec": method["factor_spec_fingerprint"],
                "candidate": study["selected_candidate"]}, None
    raise ValueError("所选来源缺少对应报告证据")


def record_published_decision(service, values):
    required = {"job_id", "pool_ref", "instrument", "decision_origin", "stage"}
    if set(values) - {"decision_at"} != required:
        raise ValueError("来源池需要明确的已发布任务、资产、来源与阶段")
    job = service.get_job(values["job_id"])
    if job["status"] != "completed" or job["report_status"] != "AVAILABLE":
        raise ValueError("需要已核验的离线报告")
    run = service.runs.get_run(job["run_id"])
    if run["report_status"] != "AVAILABLE":
        raise ValueError("来源报告完整性不匹配")
    manifest = run["manifest"]
    origin = values["decision_origin"]
    instrument = values["instrument"]
    signal, observed_at = published_signal(job, manifest["source_snapshot"], instrument, origin)
    snapshot = service.snapshots.load(job["snapshot_ref"])
    if observed_at is not None:
        observed = panel_time(observed_at)
    else:
        as_of = datetime.fromisoformat(job["as_of"]).date()
        observed = max(row.timestamp for row in snapshot.observations
                       if row.instrument == instrument and row.timestamp.date() <= as_of)
    published = panel_time(manifest["created_at"])
    available = observed if snapshot.mode in {"SAMPLE", "SYNTHETIC"} else max(observed, published)
    cutoff = panel_time(values["decision_at"]) if "decision_at" in values else available
    if not available <= cutoff <= datetime.now(UTC):
        raise ValueError("比较截止时点必须在来源可用之后")
    service.learning.store.policy(job["policy_ref"], as_of=cutoff.date().isoformat())
    signal_ref = stable_digest(signal)
    bound = {
        "pool_ref": values["pool_ref"], "instrument": instrument, "decision_origin": origin,
        "stage": values["stage"], "decision_at": cutoff.isoformat(),
        "available_at": available.isoformat(), "policy_ref": job["policy_ref"],
        "snapshot_ref": snapshot.fingerprint, "signal_fingerprint": signal_ref,
        "source_ref": stable_digest({"report": stable_digest(manifest), "origin": origin, "signal": signal_ref}),
    }
    return pool_store(service).record_pool_entry(bound, snapshot=snapshot)

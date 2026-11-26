"""Actual strategy replay on two copies of the original execution binding."""

from .contracts import stable_digest
from .strategy_runner import execute_strategy


def run_side(side, source, policy, policy_ref, frozen):
    binding = source["binding"]
    result = execute_strategy(binding["template_id"], source["panel"], binding["parameters"],
                              asset_classes=source["asset_classes"], policy=policy)
    if result.get("policy_fingerprint") != stable_digest(policy) or result.get("panel_fingerprint") != frozen["panel_ref"]:
        raise ValueError("策略执行器返回了不同的政策或面板指纹")
    if side == "baseline" and stable_digest(result) != frozen["original_result_digest"]:
        raise ValueError("基线复算与原报告逐项不符，请核对原执行版本")
    execution_binding = {**binding, "policy_ref": policy_ref, "policy_digest": stable_digest(policy)}
    return {
        "side": side, "run_id": frozen["run_id"], "report_digest": frozen["report_digest"],
        "input_fingerprint": frozen["input_fingerprint"], "policy_ref": policy_ref,
        "policy_fingerprint": stable_digest(policy), "policy": policy, "binding": execution_binding,
        "asset_classes": source["asset_classes"], "result": result, "execution_digest": stable_digest(result),
        "paper_only": True,
    }


def _metrics(result):
    ledger, benchmark = result["ledger"], result["benchmark"]
    if not ledger or not benchmark or ledger["status"] != "COMPLETE" or benchmark["status"] != "COMPLETE":
        return {}
    marks, comparable = ledger["marks"], benchmark["marks"]
    if not marks or [row["timestamp"] for row in marks] != [row["timestamp"] for row in comparable]:
        return {}
    net = marks[-1]["equity"] / marks[0]["equity"] - 1
    market = comparable[-1]["equity"] / comparable[0]["equity"] - 1
    return {"final_equity": marks[-1]["equity"], "net_return": net, "benchmark_return": market,
            "excess_return": net-market, "fees": ledger["total_fees"], "slippage": ledger["total_slippage"],
            "fill_count": len(ledger["fills"])}


def create_pair(identity, proposal, audit, source):
    policy = proposal["policy"]
    frozen = audit["frozen"]
    sides = {
        "baseline": run_side("baseline", source, source["policy"], proposal["parent_policy_ref"], frozen),
        "candidate": run_side("candidate", source, policy["effective"], policy["policy_ref"], frozen),
    }
    public = {}
    for side, evidence in sides.items():
        result = evidence["result"]
        public[side] = {
            "policy_ref": evidence["policy_ref"], "policy_fingerprint": evidence["policy_fingerprint"],
            "input_fingerprint": frozen["input_fingerprint"], "execution_digest": evidence["execution_digest"],
            "evidence_digest": stable_digest(evidence), "result_status": result["status"],
            "ledger_digest": stable_digest(result["ledger"]), "benchmark_digest": stable_digest(result["benchmark"]),
            "metrics": _metrics(result), "evidence_url": f"/api/research/learning/strategy/pairs/{identity}/{side}",
        }
    baseline, candidate = (sides[key]["result"] for key in ("baseline", "candidate"))
    if baseline["benchmark"] != candidate["benchmark"]:
        raise ValueError("两侧可比基准与冻结价格不一致")
    metrics, series = [], []
    if public["baseline"]["metrics"] and public["candidate"]["metrics"]:
        for key, left in public["baseline"]["metrics"].items():
            right = public["candidate"]["metrics"][key]
            metrics.append({"metric": key, "baseline": left, "candidate": right, "delta": right-left})
        for left, right, benchmark in zip(baseline["ledger"]["marks"], candidate["ledger"]["marks"],
                                          baseline["benchmark"]["marks"], strict=True):
            series.append({"timestamp": left["timestamp"], "baseline": left["equity"], "candidate": right["equity"],
                           "benchmark": benchmark["equity"]})
    value = {
        "pair_id": identity, "proposal_id": proposal["proposal_id"], "audit_id": audit["audit_id"],
        "status": "AVAILABLE" if metrics else "INCOMPARABLE", "comparison_kind": "RETROSPECTIVE_FROZEN_STRATEGY",
        "template_id": audit["scope"]["template_id"], "scope": audit["scope"], "effective_at": policy["effective_at"],
        "run_id": frozen["run_id"], "source_report_url": f"/research/{frozen['run_id']}/report/complete",
        "frozen": frozen, "sides": public, "metrics": metrics, "series": series,
        "fills_changed": (baseline["ledger"] or {}).get("fills") != (candidate["ledger"] or {}).get("fills"),
        "paper_only": True, "model_called": False,
    }
    return value, sides

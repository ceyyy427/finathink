"""Fixed, descriptive cohort gate. Inputs must first be grounded by the service."""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, time
from math import isfinite, sqrt
from statistics import fmean

from .contracts import stable_digest

MIN_SAMPLES = 20
MAX_CANDIDATES = 200


def instant(value, *, end=False):
    """Accept ISO dates/timestamps; dates denote UTC day boundaries."""
    value = str(value)
    result = datetime.fromisoformat(value)
    if len(value) == 10:
        result = datetime.combine(result.date(), time.max if end else time.min)
    return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)


def loss_interval(losses, total):
    """Two-sided 95% Wilson interval; NIST prc241, independent Bernoulli assumption."""
    if type(losses) is not int or type(total) is not int or not 0 <= losses <= total:
        raise ValueError("invalid loss count")
    if not total:
        return None
    z = 1.959963984540054
    p = losses / total
    denom = 1 + z*z / total
    center = (p + z*z/(2*total)) / denom
    width = z * sqrt(p*(1-p)/total + z*z/(4*total*total)) / denom
    return [max(0.0, center-width), min(1.0, center+width)]


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _reason(row, scope, start, cutoff):
    if not row.get("verified"):
        return row.get("exclusion_reason", "UNVERIFIED")
    if row.get("scope") != scope:
        return "SCOPE_MISMATCH"
    if not all(row.get(key) for key in ("review_id", "semantic_key", "settlement_digest", "ledger_digest")):
        return "UNKNOWN_IDENTITY"
    try:
        decision, mark, available, observed = [instant(row[key]) for key in ("decision_at", "mark_at", "available_at", "observed_at")]
    except (ValueError, KeyError, TypeError):
        return "INVALID_WINDOW"
    if decision < start or decision > cutoff:
        return "OUTSIDE_WINDOW"
    if max(available, mark, observed) > cutoff:
        return "LATE_EVIDENCE"
    if mark <= decision or available < mark:
        return "INVALID_WINDOW"
    if not _number(row.get("net_return")):
        return "UNKNOWN_OUTCOME"
    if not _number(row.get("quantity")) or row["quantity"] <= 0:
        return "UNFILLED"
    return None


def assess_outcomes(records, *, scope, start, as_of):
    if len(records) > MAX_CANDIDATES:
        raise ValueError("最多评估 200 条候选，请缩小日期范围")
    first, cutoff = instant(start), instant(as_of, end=True)
    if cutoff < first:
        raise ValueError("截止日期不能早于开始日期")
    selected, excluded, seen = [], [], set()
    last_end = None
    # Sort normalized instants, not ISO strings with different UTC offsets.
    def order(row):
        try:
            return instant(row.get("decision_at")), str(row.get("review_id", ""))
        except (ValueError, TypeError):
            return datetime.min.replace(tzinfo=UTC), str(row.get("review_id", ""))
    for row in sorted(records, key=order):
        reason = _reason(row, scope, first, cutoff)
        identities = {(key, row.get(key)) for key in ("semantic_key", "settlement_digest", "ledger_digest")}
        if not reason and seen.intersection(identities):
            reason = "DUPLICATE"
        if not reason and last_end is not None and instant(row["decision_at"]) <= last_end:
            reason = "OVERLAPPING_WINDOW"
        if reason:
            excluded.append({"review_id": row.get("review_id"), "reason": reason})
            continue
        seen.update(identities)
        last_end = max(instant(row["mark_at"]), instant(row["available_at"]))
        selected.append(row)
    returns = sorted(float(row["net_return"]) for row in selected)
    n = len(returns)
    losses = sum(value < 0 for value in returns)
    interval = loss_interval(losses, n)
    mean = fmean(returns) if n else None
    def quantile(p):
        if not n:
            return None
        index = (n-1)*p
        lo = int(index)
        return returns[lo] + (returns[min(lo+1, n-1)]-returns[lo]) * (index-lo)
    weights, rules = {}, {}
    # Missing counterevidence can bias retained rows. Show their statistics, but
    # never grant stronger authority because a candidate's proof is damaged.
    unresolved = any(not row.get("verified") for row in records)
    status = "OBSERVE" if n < MIN_SAMPLES or unresolved else "KEEP_POLICY"
    if not unresolved and n >= MIN_SAMPLES and mean < 0 and interval[0] > 0.5:
        old_weight, old_risk = scope["factor_weight"], scope["max_cvar"]
        weight, risk = max(0.1, old_weight*0.9), max(0.01, old_risk*0.9)
        if weight < old_weight:
            weights[scope["factor_id"]] = weight
        if risk < old_risk:
            rules["max_cvar"] = risk
        if weights or rules:
            status = "RECOMMENDED"
    payload = {"method": "separated-paper-cohort.v1", "scope": scope, "start": start, "as_of": as_of,
        "status": status, "candidate_count": len(records), "sample_count": n, "minimum_samples": MIN_SAMPLES,
        "selected_review_ids": [row["review_id"] for row in selected], "exclusions": excluded,
        "exclusion_counts": dict(sorted(Counter(item["reason"] for item in excluded).items())),
        "statistics": {"mean": mean, "minimum": min(returns) if n else None, "q25": quantile(.25), "median": quantile(.5), "q75": quantile(.75), "maximum": max(returns) if n else None, "losses": losses, "zero": returns.count(0.0), "non_losses": n-losses, "loss_interval_95": interval},
        "counterexamples": [{"review_id": row["review_id"], "net_return": row["net_return"]} for row in selected if row["net_return"] >= 0][:20],
        "coverage": {"first_decision": selected[0]["decision_at"] if n else None, "last_mark": selected[-1]["mark_at"] if n else None},
        "factor_weight_updates": weights, "risk_rule_updates": rules,
        "limits": ["窗口不重叠只表示操作上的分离，不保证统计独立。", "区间假设独立同分布的二项观察；相关市场序列可能低估不确定性。", "固定工程门槛与历史纸面描述，不代表未来盈利概率或市场校准。", "评估范围由用户选择，存在选择偏差；批准后才会产生新配置版本。"],
        "paper_only": True}
    return {**payload, "fingerprint": stable_digest(payload)}

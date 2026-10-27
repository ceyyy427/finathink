"""Bounded descriptive monitoring of comparable, completed factor tests.

Inputs are server-owned projections from verified reports. This is not a
settlement factory, statistical proof of decay, scheduler, or policy writer.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from itertools import pairwise

import pandas as pd

from finahinking.data.research_fields import field_time

from .contracts import _public_identifier, stable_digest


@dataclass(frozen=True, slots=True)
class MonitoringProposal:
    _json: str

    def __post_init__(self):
        value = self.to_dict()
        if (value.get("schema_version") != "factor-monitoring.v1"
                or value.get("fingerprint") != stable_digest({key: item for key, item in value.items() if key != "fingerprint"})):
            raise ValueError("factor monitoring proposal integrity failure")

    def to_dict(self):
        return json.loads(self._json)

    @property
    def fingerprint(self):
        return self.to_dict()["fingerprint"]


def monitoring_policy(values):
    if not isinstance(values, dict):
        raise TypeError("monitoring policy must be an object")
    allowed = {"as_of", "min_windows", "decline_windows", "drop_threshold"}
    if set(values) - allowed:
        raise ValueError("monitoring policy contains unsupported controls")
    result = {"as_of": datetime.now(UTC).date().isoformat(), "min_windows": 3,
              "decline_windows": 2, "drop_threshold": .1, **values}
    if not isinstance(result["as_of"], str):
        raise TypeError("monitoring cutoff must be an ISO date")
    cutoff = date.fromisoformat(result["as_of"])
    if cutoff > datetime.now(UTC).date():
        raise ValueError("future monitoring cutoff is not settled evidence")
    if (type(result["min_windows"]) is not int or not 3 <= result["min_windows"] <= 20
            or type(result["decline_windows"]) is not int or not 2 <= result["decline_windows"] < result["min_windows"]
            or isinstance(result["drop_threshold"], bool) or not isinstance(result["drop_threshold"], (int, float))
            or not math.isfinite(result["drop_threshold"]) or not 0 < result["drop_threshold"] <= 2):
        raise ValueError("monitoring controls exceed bounded research policy")
    return result


def evaluate_factor_monitoring(factor_ref, run_refs, policy) -> MonitoringProposal:
    factor_ref = _public_identifier(factor_ref, "factor_ref")
    policy = monitoring_policy(policy)
    if not isinstance(run_refs, (list, tuple)) or len(run_refs) > 200:
        raise ValueError("monitoring requires at most200 verified report projections")
    cutoff = field_time(policy["as_of"]) + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    usable, ignored, codes, seen = [], [], set(), set()
    for row in run_refs:
        reference = row.get("run_ref") if isinstance(row, dict) else None
        if (not isinstance(row, dict) or row.get("status") != "COMPLETE" or row.get("phase") != "test"
                or not row.get("settled") or not row.get("definition_matches") or len(row.get("stages", ())) != 9
                or any(step["status"] != "COMPLETE" for step in row.get("stages", ()))):
            ignored.append({"run_ref": reference, "reason": "NOT_A_VERIFIED_COMPLETE_FINAL_TEST"})
            continue
        if factor_ref not in {row["factor_ref"], row["factor_id"]}:
            codes.add("SCOPE_MISMATCH")
        if not re.fullmatch(r"[0-9a-f]{64}", row.get("evaluation_fingerprint", "")):
            ignored.append({"run_ref": reference, "reason": "INVALID_EVALUATION_REFERENCE"})
            continue
        window = row["scope"].get("final_test_window")
        metric = (row.get("local_metric") or {}).get("information_coefficient", {}).get("mean_oriented_time_series_ic")
        if (not isinstance(window, (list, tuple)) or len(window) != 2 or isinstance(metric, bool)
                or not isinstance(metric, (int, float)) or not math.isfinite(metric) or not -1 <= metric <= 1):
            ignored.append({"run_ref": reference, "reason": "UNKNOWN_WINDOW_OR_IC"})
            continue
        start, end = (field_time(item) for item in window)
        if start >= end or end > cutoff or field_time(row["scope"]["as_of"]) > cutoff:
            ignored.append({"run_ref": reference, "reason": "NOT_KNOWN_BY_CUTOFF"})
            continue
        if row["evaluation_fingerprint"] in seen:
            ignored.append({"run_ref": reference, "reason": "DUPLICATE_EVALUATION"})
            continue
        seen.add(row["evaluation_fingerprint"])
        comparison = {key: row.get(key) for key in ("factor_ref", "policy_fingerprint", "source_policy_fingerprint", "reproduction_scope")}
        comparison.update({key: row["scope"].get(key) for key in ("universe", "cost_bps", "execution_config", "code_version", "benchmark")})
        usable.append({"row": row, "start": start, "end": end, "ic": metric, "comparison": stable_digest(comparison)})
    usable.sort(key=lambda item: (item["start"], item["end"], item["row"]["evaluation_fingerprint"]))
    if len({item["comparison"] for item in usable}) > 1:
        codes.add("SCOPE_MISMATCH")
    if any(left["end"] >= right["start"] for left, right in pairwise(usable)):
        codes.add("OVERLAPPING_WINDOWS")
    status, baseline, recent = "OBSERVE", None, None
    if codes:
        status = "INCOMPARABLE"
    elif len(usable) >= policy["min_windows"]:
        prior, latest = usable[:-policy["decline_windows"]], usable[-policy["decline_windows"]:]
        baseline = math.fsum(item["ic"] for item in prior) / len(prior)
        recent = math.fsum(item["ic"] for item in latest) / len(latest)
        if all(item["ic"] <= baseline - policy["drop_threshold"] for item in latest):
            status = "REVIEW_RECOMMENDED"
    canonical = usable[0]["row"]["factor_ref"] if usable and not codes else factor_ref
    result = {"schema_version": "factor-monitoring.v1", "factor_ref": canonical,
        "scope_fingerprint": usable[0]["comparison"] if usable and not codes else None, "as_of": policy["as_of"],
        "status": status, "codes": sorted(codes), "policy": policy, "baseline_ic": baseline, "recent_ic": recent,
        "evidence_refs": [{"run_ref": item["row"]["run_ref"], "evaluation_fingerprint": item["row"]["evaluation_fingerprint"],
            "window": [item["start"].isoformat(), item["end"].isoformat()], "ic": item["ic"]} for item in usable],
        "reproduction_scope": sorted({item["row"]["reproduction_scope"] for item in usable}), "ignored": ignored,
        "factor_weight_updates": {}, "risk_rule_updates": {}, "financial_falsification": False, "production_write": False,
        "revisit_when": "取得下一独立、同定义与同范围的已完成最终测试后复核；观察建议需显式审核。",
        "limitations": ["Descriptive change is not statistical proof of decay or realized paper-trading performance.",
            "Formula fixtures remain engineering evidence; no source experiment or profitability is reproduced.",
            "Only a research observation registry note is proposed; weights, risk rules and use approvals stay unchanged."]}
    result["fingerprint"] = stable_digest(result)
    return MonitoringProposal(json.dumps(result, ensure_ascii=False, allow_nan=False, sort_keys=True))

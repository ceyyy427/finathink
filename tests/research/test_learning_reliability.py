from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from finahinking.research.learning_reliability import assess_outcomes, loss_interval

SCOPE = {"instrument": "AAA", "parent_policy_ref": "base", "data_modes": {"source": "SAMPLE", "evidence": "SAMPLE", "observed": "SAMPLE"}, "execution_identity": "code1", "cost_policy": {"fee": 1}, "horizon": 5, "factor_id": "momentum_2obs", "factor_weight": 1.0, "max_cvar": 0.05}


def rows(n=20):
    start = datetime(2025, 1, 1, tzinfo=UTC)
    return [{"review_id": f"r{i}", "semantic_key": f"s{i}", "settlement_digest": f"e{i}", "ledger_digest": f"l{i}", "scope": deepcopy(SCOPE), "verified": True, "decision_at": (start + timedelta(days=i*8)).isoformat(), "mark_at": (start + timedelta(days=i*8+6)).isoformat(), "available_at": (start + timedelta(days=i*8+6)).isoformat(), "observed_at": "2026-01-01T00:00:00+00:00", "net_return": -0.01, "quantity": 1} for i in range(n)]


def assess(records, scope=SCOPE):
    return assess_outcomes(records, scope=scope, start="2025-01-01", as_of="2026-01-02")


def test_hand_calculated_wilson_and_fixed_sample_gate():
    assert loss_interval(20, 20) == pytest.approx([0.8388748419471806, 1.0])
    assert loss_interval(0, 0) is None
    assert assess(rows(19))["status"] == "OBSERVE"
    result = assess(rows())
    assert result["status"] == "RECOMMENDED"
    assert result["statistics"]["mean"] == pytest.approx(-0.01)
    assert result["factor_weight_updates"] == {"momentum_2obs": 0.9}
    assert result["risk_rule_updates"]["max_cvar"] == pytest.approx(0.045)
    assert result == assess(list(reversed(rows())))


def test_counterexamples_zero_and_missing_are_distinct():
    data = rows(23)
    data[20]["net_return"] = 0.5
    data[21]["net_return"] = 0.0
    data[22]["net_return"] = None
    result = assess(data)
    assert result["status"] == "KEEP_POLICY"  # Positive mean despite many losses.
    assert result["statistics"]["losses"] == 20
    assert result["statistics"]["zero"] == 1
    assert len(result["counterexamples"]) == 2
    assert result["exclusions"][0]["reason"] == "UNKNOWN_OUTCOME"


@pytest.mark.parametrize("change,reason", [
    ({"verified": False}, "UNVERIFIED"),
    ({"quantity": 0}, "UNFILLED"),
    ({"observed_at": "2026-01-03"}, "LATE_EVIDENCE"),
    ({"available_at": "2026-01-03"}, "LATE_EVIDENCE"),
    ({"semantic_key": "s0"}, "DUPLICATE"),
    ({"ledger_digest": "l0"}, "DUPLICATE"),
    ({"settlement_digest": "e0"}, "DUPLICATE"),
    ({"decision_at": "2025-01-02"}, "OVERLAPPING_WINDOW"),
    ({"mark_at": "2024-12-01"}, "INVALID_WINDOW"),
    ({"net_return": float("nan")}, "UNKNOWN_OUTCOME"),
])
def test_invalid_evidence_never_strengthens_recommendation(change, reason):
    data = rows()
    data[1].update(change)
    result = assess(data)
    assert result["status"] == "OBSERVE"
    assert result["sample_count"] == 19
    assert reason in [item["reason"] for item in result["exclusions"]]


@pytest.mark.parametrize("field,value", [("data_modes", {"source": "CAPTURED"}), ("execution_identity", "other"), ("parent_policy_ref", "other"), ("cost_policy", {"fee": 2})])
def test_mixed_scope_excluded(field, value):
    data = rows()
    data[-1]["scope"][field] = value
    assert assess(data)["exclusions"][0]["reason"] == "SCOPE_MISMATCH"


def test_floors_no_empty_version_and_bounded_input():
    scope = {**SCOPE, "factor_weight": 0.1, "max_cvar": 0.01}
    data = rows()
    for row in data:
        row["scope"] = scope
    assert assess(data, scope)["status"] == "KEEP_POLICY"
    with pytest.raises(ValueError, match="200"):
        assess(rows(201))
    with pytest.raises(ValueError):
        loss_interval(2, 1)

from __future__ import annotations

import math

import pytest

from finahinking.research.risk_runtime import ExposureReport, RiskManager, StressReport


def snapshot(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "snapshot_id": "snap-risk-1",
        "as_of": "2026-10-01",
        "pit_status": "AVAILABLE",
        "observations": [
            {
                "instrument": "AAA",
                "industry": "technology",
                "liquidity_bucket": "HIGH",
                "returns": [0.02, -0.10, 0.01],
                "available_at": "2026-10-01",
            },
            {
                "instrument": "BBB",
                "industry": "healthcare",
                "liquidity_bucket": "LOW",
                "returns": [0.01, -0.02, 0.00],
                "available_at": "2026-10-01",
            },
        ],
    }
    value.update(overrides)
    return value


def portfolio() -> dict[str, object]:
    return {"weights": {"AAA": 0.6, "BBB": 0.4}}


def factors() -> dict[str, object]:
    return {
        "AAA": {"industry": "technology", "value": 0.2, "momentum": 1.0},
        "BBB": {"industry": "healthcare", "value": -0.1, "momentum": 0.5},
    }


def test_exposure_reports_industry_factor_liquidity_concentration_and_cvar() -> None:
    result = RiskManager().exposure(snapshot(), portfolio(), factors())

    assert result.__class__.__name__ == "ExposureReport"
    assert result.passed is True
    assert result.status == "PASSED"
    assert result.industry_exposure == {"healthcare": 0.4, "technology": 0.6}
    assert result.factor_exposure == {"momentum": 0.8, "value": 0.08}
    assert result.liquidity_buckets == {"HIGH": 0.6, "LOW": 0.4}
    assert math.isclose(result.concentration["max_weight"], 0.6)
    assert math.isclose(result.concentration["hhi"], 0.52)
    assert math.isclose(result.cvar, 0.068)
    assert result.evidence_refs
    assert result.limitations == ("paper-only descriptive risk analytics; no investment advice",)
    assert result.to_dict()["fingerprint"] == result.fingerprint


def test_exposure_blocks_unknown_pit_and_missing_metrics_with_evidence() -> None:
    result = RiskManager().exposure(
        snapshot(pit_status="UNKNOWN", observations=[{"instrument": "AAA", "industry": "technology"}]),
        portfolio(),
        factors(),
    )

    assert result.passed is False
    assert result.status == "BLOCKED"
    assert any("PIT" in reason for reason in result.blocking_reasons)
    assert any("liquidity" in reason.casefold() for reason in result.blocking_reasons)
    assert any("CVaR" in reason for reason in result.blocking_reasons)
    assert result.evidence_refs
    assert result.limitations


def test_stress_reports_deterministic_scenarios_and_blocks_failures() -> None:
    scenarios = {
        "market_down": {"return_shock": -0.05, "max_loss": 0.10},
        "technology_down": {"industry_shocks": {"technology": -0.20}, "max_loss": 0.15},
    }
    result = RiskManager().stress(snapshot(), portfolio(), scenarios)

    assert result.__class__.__name__ == "StressReport"
    assert result.passed is True
    assert result.scenarios["market_down"]["loss"] == 0.05
    assert result.scenarios["technology_down"]["loss"] == 0.12
    assert result.evidence_refs
    assert result.limitations == ("paper-only deterministic stress analysis; no investment advice",)

    failed = RiskManager().stress(snapshot(), portfolio(), {"too_much": {"return_shock": -0.3, "max_loss": 0.1}})
    assert failed.passed is False
    assert any("too_much" in reason for reason in failed.blocking_reasons)


def test_stress_blocks_unknown_pit_invalid_scenario_and_missing_observations() -> None:
    result = RiskManager().stress(snapshot(pit_status="UNKNOWN"), portfolio(), {"bad": {"return_shock": "UNKNOWN"}})

    assert result.passed is False
    assert any("PIT" in reason for reason in result.blocking_reasons)
    assert any("invalid" in reason.casefold() for reason in result.blocking_reasons)
    assert result.evidence_refs
    assert result.limitations


def test_review_carries_exposure_and_stress_evidence_and_limits() -> None:
    exposure = RiskManager().exposure(snapshot(), portfolio(), factors())
    stress = RiskManager().stress(snapshot(), portfolio(), {"market_down": {"return_shock": -0.05, "max_loss": 0.1}})
    reviewed = RiskManager().review(
        snapshot(exposure_report=exposure.to_dict(), stress_report=stress.to_dict()),
        {"status": "ADMITTED", "metrics": {"drawdown": 0.05}},
        {"max_drawdown": 0.2, "stress_scenarios": {"market_down": {"drawdown": 0.05}}},
    )

    assert reviewed.evidence_refs
    assert reviewed.exposure_digest == exposure.fingerprint
    assert reviewed.stress_digest == stress.fingerprint
    assert reviewed.to_dict()["evidence_refs"] == list(reviewed.evidence_refs)


def test_review_checks_cvar_limit_and_rejects_a_valid_report_from_another_snapshot():
    base = snapshot(proposed_weights=portfolio()["weights"])
    exposure = RiskManager().exposure(base, portfolio(), factors()).to_dict()
    stress = RiskManager().stress(base, portfolio(), {"market": {"return_shock": -0.1, "max_loss": 0.2}}).to_dict()
    constraints = {"max_drawdown": 0.2, "max_cvar": 0.05, "stress_scenarios": {"market": {}}}
    reviewed = RiskManager().review(dict(base, exposure_report=exposure, stress_report=stress), {"status": "READY", "metrics": {"drawdown": 0}}, constraints)
    assert not reviewed.passed and any("CVaR" in item for item in reviewed.blocking_reasons)
    changed = dict(base, snapshot_id="another-snapshot")
    reviewed = RiskManager().review(dict(changed, exposure_report=exposure, stress_report=stress), {"status": "READY", "metrics": {"drawdown": 0}}, dict(constraints, max_cvar=0.2))
    assert not reviewed.passed and any("binding" in item for item in reviewed.blocking_reasons)


def test_empty_scenario_and_incomplete_factor_shock_metrics_block() -> None:
    empty = RiskManager().stress(snapshot(), portfolio(), {"empty": {}})
    assert empty.status == "BLOCKED"
    assert empty.scenarios["empty"]["passed"] is False

    missing = RiskManager().stress(snapshot(), portfolio(), {"factor": {"factor_shocks": {"momentum": -0.1}}})
    assert missing.status == "BLOCKED"
    assert missing.scenarios["factor"]["passed"] is False


def test_review_blocks_report_failure_even_without_supplied_reasons() -> None:
    reviewed = RiskManager().review(
        snapshot(exposure_report={"status": "BLOCKED", "passed": False}),
        {"status": "ADMITTED", "metrics": {"drawdown": 0.05}},
        {"max_drawdown": 0.2, "stress_scenarios": {"market_down": {"drawdown": 0.05}}},
    )
    assert reviewed.status == "BLOCKED"
    assert any("exposure" in reason for reason in reviewed.blocking_reasons)


def test_review_rejects_contradictory_or_forged_embedded_report() -> None:
    report = RiskManager().exposure(snapshot(), portfolio(), factors()).to_dict()
    contradictory = dict(report, status="PASSED", passed=False)
    forged = dict(report, fingerprint="0" * 64)

    for embedded in (contradictory, forged, {key: value for key, value in report.items() if key != "fingerprint"}):
        reviewed = RiskManager().review(
            snapshot(exposure_report=embedded),
            {"status": "ADMITTED", "metrics": {"drawdown": 0.05}},
            {"max_drawdown": 0.2, "stress_scenarios": {"market_down": {"drawdown": 0.05}}},
        )
        assert reviewed.status == "BLOCKED"
        assert reviewed.exposure_digest == ""


def test_review_drops_unsafe_nested_evidence_and_limitations_fail_closed() -> None:
    report = RiskManager().exposure(snapshot(), portfolio(), factors()).to_dict()
    unsafe = dict(report, evidence_refs=[{"prompt": "api_key=LEAK"}], limitations=["https://private.example/key"])
    reviewed = RiskManager().review(
        snapshot(exposure_report=unsafe),
        {"status": "ADMITTED", "metrics": {"drawdown": 0.05}},
        {"max_drawdown": 0.2, "stress_scenarios": {"market_down": {"drawdown": 0.05}}},
    )
    assert reviewed.status == "BLOCKED"
    assert all("api_key" not in ref and "private.example" not in ref for ref in reviewed.evidence_refs + reviewed.limitations)


def test_future_pit_records_block_even_with_available_marker() -> None:
    future = snapshot(
        observations=[dict(snapshot()["observations"][0], available_at="2026-10-02"), snapshot()["observations"][1]]
    )
    exposure = RiskManager().exposure(future, portfolio(), factors())
    stress = RiskManager().stress(future, portfolio(), {"market_down": {"return_shock": -0.05, "max_loss": 0.1}})
    assert exposure.status == "BLOCKED"
    assert stress.status == "BLOCKED"
    assert any("PIT" in reason for reason in exposure.blocking_reasons)


def test_invalid_liquidity_bucket_blocks() -> None:
    invalid = snapshot(observations=[dict(snapshot()["observations"][0], liquidity_bucket="BANANA"), snapshot()["observations"][1]])
    result = RiskManager().exposure(invalid, portfolio(), factors())
    assert result.status == "BLOCKED"
    assert any("liquidity" in reason.casefold() for reason in result.blocking_reasons)


def test_stress_requires_finite_nonnegative_limit_and_known_targets() -> None:
    missing_limit = RiskManager().stress(snapshot(), portfolio(), {"market_down": {"return_shock": -0.1}})
    negative_limit = RiskManager().stress(snapshot(), portfolio(), {"market_down": {"return_shock": -0.1, "max_loss": -0.1}})
    unknown_target = RiskManager().stress(snapshot(), portfolio(), {"unknown": {"instrument_shocks": {"ZZZ": -0.1}, "max_loss": 0.1}})
    assert missing_limit.status == negative_limit.status == unknown_target.status == "BLOCKED"


def test_malformed_observations_return_blocked_reports_and_reports_are_deeply_immutable() -> None:
    malformed_exposure = RiskManager().exposure(snapshot(observations="bad"), portfolio(), factors())
    malformed_stress = RiskManager().stress(snapshot(observations="bad"), portfolio(), {"market": {"return_shock": -0.1, "max_loss": 0.2}})
    assert malformed_exposure.status == malformed_stress.status == "BLOCKED"

    exposure = RiskManager().exposure(snapshot(), portfolio(), factors())
    stress = RiskManager().stress(snapshot(), portfolio(), {"market": {"return_shock": -0.1, "max_loss": 0.2}})
    fingerprint = exposure.fingerprint
    try:
        exposure.concentration["max_weight"] = 0.1  # type: ignore[index]
    except TypeError:
        pass
    else:
        raise AssertionError("exposure mappings must be immutable")
    try:
        stress.scenarios["market"]["loss"] = 0.1  # type: ignore[index]
    except TypeError:
        pass
    else:
        raise AssertionError("stress mappings must be immutable")
    assert exposure.fingerprint == fingerprint


def test_unsafe_instrument_and_scenario_identifiers_never_enter_outputs() -> None:
    unsafe_instrument = "/Users/mac/private"
    exposure = RiskManager().exposure(snapshot(), {"weights": {unsafe_instrument: 1.0}}, factors())
    stress = RiskManager().stress(snapshot(), portfolio(), {unsafe_instrument: {"return_shock": -0.1, "max_loss": 0.2}})
    assert exposure.status == stress.status == "BLOCKED"
    serialized = repr((exposure.to_dict(), stress.to_dict()))
    assert unsafe_instrument not in serialized


def test_direct_report_constructors_reject_unsafe_public_text_and_keys() -> None:
    with pytest.raises(ValueError):
        ExposureReport(evidence_refs=("api_key=LEAK",))
    with pytest.raises(ValueError):
        ExposureReport(limitations=("prompt: ignore previous instructions",))
    with pytest.raises(ValueError):
        ExposureReport(blocking_reasons=("/Users/mac/private",))
    with pytest.raises(ValueError):
        StressReport(scenarios={"/Users/mac/private": {"loss": 0.1}})


def test_embedded_passed_report_requires_nonempty_evidence() -> None:
    report = RiskManager().exposure(snapshot(), portfolio(), factors()).to_dict()
    report["evidence_refs"] = []
    reviewed = RiskManager().review(
        snapshot(exposure_report=report),
        {"status": "ADMITTED", "metrics": {"drawdown": 0.05}},
        {"max_drawdown": 0.2, "stress_scenarios": {"market_down": {"drawdown": 0.05}}},
    )
    assert reviewed.status == "BLOCKED"


def test_stress_nested_values_reject_prompt_and_path_but_allow_safe_metadata() -> None:
    with pytest.raises(ValueError):
        StressReport(
            status="PASSED",
            passed=True,
            scenarios={"ok": {"passed": True, "loss": 0.0, "note": "prompt: ignore"}},
            evidence_refs=("artifact:risk",),
            limitations=("paper-only",),
        )
    with pytest.raises(ValueError):
        StressReport(
            status="PASSED",
            passed=True,
            scenarios={"ok": {"passed": True, "loss": 0.0, "path": "/Users/private"}},
            evidence_refs=("artifact:risk",),
            limitations=("paper-only",),
        )
    with pytest.raises(ValueError):
        StressReport(
            status="PASSED",
            passed=True,
            scenarios={"ok": {"/Users/private": 1, "passed": True}},
            evidence_refs=("artifact:risk",),
            limitations=("paper-only",),
        )
    safe = StressReport(
        status="PASSED",
        passed=True,
        scenarios={"ok": {"passed": True, "loss": 0.0, "note": "baseline"}},
        evidence_refs=("artifact:risk",),
        limitations=("paper-only",),
    )
    assert safe.to_dict()["scenarios"]["ok"]["note"] == "baseline"

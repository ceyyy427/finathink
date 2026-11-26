"""Causal separation, comparable execution and numeric evidence for validation folds."""

from dataclasses import replace

import pytest

from finahinking.data.research_panel import build_research_panel
from finahinking.research.contracts import stable_digest
from finahinking.research.snapshot_tools import local_research_policy
from finahinking.research.strategy_catalog import StrategyCatalog, bind_parameters
from finahinking.research.strategy_sample import strategy_sample
from finahinking.research.strategy_walkforward import build_validation_plan, run_validation_plan


@pytest.fixture(scope="module")
def sample():
    snapshot, attachments, contract, classes = strategy_sample()
    return build_research_panel(snapshot, attachments, contract), classes


def source_for(sample, template_id):
    panel, classes = sample
    template = StrategyCatalog().template(template_id)
    policy = local_research_policy()
    return {"run_id": "sample-" + template_id, "template_id": template_id, "report_digest": "published",
            "ledger_digest": stable_digest(None), "snapshot_ref": panel.contract.snapshot_ref,
            "policy_ref": "original", "policy": policy, "policy_available_at": None,
            "asset_classes": classes, "panel": panel, "data_mode": "SAMPLE",
            "binding": {"panel_ref": panel.fingerprint, "template_id": template_id,
                        "template_digest": stable_digest(template), "parameters": bind_parameters(template, {})}}


@pytest.mark.parametrize("template", ["equal-weight", "short-term-reversal", "etf-relative-strength",
                                      "regime-allocation", "event-study", "multifactor-neutral"])
def test_six_catalog_templates_three_frozen_windows(sample, template):
    source = source_for(sample, template)
    plan = build_validation_plan(source)
    result = run_validation_plan(source, plan)
    assert len(result["windows"]) == len(plan["windows"]) == 3
    assert plan["windows"][0]["test_end"] < plan["windows"][1]["test_start"]
    assert plan["windows"][1]["test_end"] < plan["windows"][2]["test_start"]
    assert plan["source"]["ledger_digest"] == source["ledger_digest"]
    assert plan["evaluation_units"] == (len(plan["parameter_values"]) + len(plan["scenarios"])) * 3
    for fold, dates in zip(result["windows"], plan["windows"], strict=True):
        assert dates.items() <= fold.items()
        assert dates["train_end"] < dates["test_start"]
        selection = fold["selection"]
        if selection["selected_value"] is not None:
            assert selection["selected_value"] in selection["stable_values"]
            assert selection["candidates"] and selection["stable_regions"]
    if template in {"event-study", "multifactor-neutral"}:
        assert result["status_counts"].get("THEORETICAL", 0) >= 1
        for fold in result["windows"]:
            assert len(fold["scenarios"]) == 1
            case = fold["scenarios"][0]
            assert case["ledger_digest"] is None and "net_return" not in case["metrics"]
        if template == "event-study":
            # Events in days 40–43 mature only in the first default holdout.
            assert [w["status"] for w in result["windows"]] == ["THEORETICAL", "INSUFFICIENT_DATA", "INSUFFICIENT_DATA"]
        return
    assert result["status_counts"] == {"PAPER_SIMULATED": 3}
    for fold in result["windows"]:
        cases = fold["scenarios"]
        assert [s["id"] for s in cases] == [s["id"] for s in plan["scenarios"]]
        for case in cases:
            assert case["status"] == "PAPER_SIMULATED"
            metrics = case["metrics"]
            assert metrics["fill_count"] > 0
            assert case["series"][0] == {"timestamp": fold["test_start"], "equity": 10000.0, "benchmark": 10000.0}
            assert case["series"][-1]["timestamp"] == fold["test_end"]
            assert metrics["net_return"] == pytest.approx(metrics["final_equity"] / 10000 - 1)
            assert metrics["gross_pnl"] - metrics["fees"] - metrics["slippage"] == pytest.approx(metrics["net_pnl"])


def test_future_holdout_prices_cannot_choose_training_parameter(sample):
    source = source_for(sample, "equal-weight")
    options = {"train_size": 14, "test_size": 6, "parameter_values": [2, 4]}
    plan = build_validation_plan(source, options)
    original = run_validation_plan(source, plan)
    panel, classes = sample
    changed = panel.frame
    dates = changed.index.get_level_values("observed_at")
    changed.loc[dates >= plan["windows"][0]["test_start"], "close"] *= 1.8
    modified = source_for((replace(panel, _frame=changed, fingerprint="changed-holdout"), classes), "equal-weight")
    modified_plan = build_validation_plan(modified, options)
    replay = run_validation_plan(modified, modified_plan)
    assert replay["windows"][0]["selection"] == original["windows"][0]["selection"]
    assert replay["windows"][0]["scenarios"] != original["windows"][0]["scenarios"]
    with pytest.raises(ValueError):
        run_validation_plan(modified, plan)
    with pytest.raises(ValueError):
        run_validation_plan(source, {**plan, "windows": []})


def test_zero_capacity_and_short_history_publish_explicit_windows(sample):
    source = source_for(sample, "equal-weight")
    plan = build_validation_plan(source, {"max_volume_participation": 0})
    output = run_validation_plan(source, plan)
    assert output["status_counts"] == {"UNTRADEABLE": 3}
    assert all("ZERO_VOLUME_CAPACITY" in fold["reasons"] for fold in output["windows"])
    plan = build_validation_plan(source, {"train_size": 120})
    assert plan["evaluation_units"] == 0
    output = run_validation_plan(source, plan)
    assert output["status_counts"] == {"INSUFFICIENT_DATA": 3}
    assert all("INSUFFICIENT_OBSERVATIONS" in fold["reasons"] for fold in output["windows"])


def test_invalid_options_and_precommitted_scenario_parameters(sample):
    source = source_for(sample, "equal-weight")
    for options in ({"train_size": True}, {"fee_bps": float("nan")}, {"bogus": 1},
                    {"parameter_values": [1, 1]}, {"parameter_values": [61]}):
        with pytest.raises(ValueError):
            build_validation_plan(source, options)
    plan = build_validation_plan(source, {"fee_bps": 1})
    assert plan["scenarios"][0]["fee_bps"] == 1
    assert plan["scenarios"][0]["slippage_bps"] == source["policy"]["slippage_bps"]
    assert build_validation_plan(source, {"fee_bps": 2}) != plan


def test_nonzero_baseline_and_liquidity_shock_keep_independent_execution(sample):
    source = source_for(sample, "equal-weight")
    plan = build_validation_plan(source, {"max_volume_participation": 0.0000015, "parameter_values": [3]})
    assert build_validation_plan(source, plan["options"]) == plan
    result = run_validation_plan(source, plan)
    for window in result["windows"]:
        base, *_, illiquid = window["scenarios"]
        assert base["status"] == "PAPER_SIMULATED"
        assert illiquid["status"] == "UNTRADEABLE" and "VOLUME_PARTICIPATION" in illiquid["reasons"]
        assert "net_return" not in illiquid["metrics"]
        assert base["ledger_digest"] != illiquid["ledger_digest"]


def test_later_approved_policy_cannot_select_historical_training_parameters(sample):
    source = source_for(sample, "short-term-reversal")
    plan = build_validation_plan(source)
    source["policy_available_at"] = plan["windows"][-1]["test_end"]
    audited = build_validation_plan(source)
    incompatible = run_validation_plan(source, audited)
    assert all(w["reasons"] == ["POLICY_NOT_EFFECTIVE"] and not w["selection"]["candidates"]
               for w in incompatible["windows"])


def test_missing_holdout_signal_is_visible_even_when_other_orders_fill(sample):
    panel, classes = sample
    options = {"parameter_values": [2]}
    original = source_for(sample, "regime-allocation")
    plan = build_validation_plan(original, options)
    frame = panel.frame
    stamps = frame.index.get_level_values("observed_at").unique()
    missing_at = stamps[stamps.get_loc(plan["windows"][0]["test_start"]) + 2]
    frame.loc[(missing_at, "EQA"), "policy_regime"] = None
    source = source_for((replace(panel, _frame=frame, fingerprint="missing-regime"), classes), "regime-allocation")
    fold = run_validation_plan(source, build_validation_plan(source, options))["windows"][0]
    assert fold["status"] == "INSUFFICIENT_DATA"
    assert "MISSING_DECISION_EVIDENCE" in fold["reasons"]
    assert "net_return" not in fold["scenarios"][0]["metrics"]


def test_only_scheduled_holdout_decision_can_trade_with_a_known_cash_opening(sample):
    source = source_for(sample, "equal-weight")
    plan = build_validation_plan(source, {"train_size": 18, "test_size": 6, "parameter_values": [6]})
    result = run_validation_plan(source, plan)
    assert result["status_counts"] == {"PAPER_SIMULATED": 3}
    assert all(w["scenarios"][0]["metrics"]["fill_count"] > 0 for w in result["windows"])


def cash_strategy_source(sample, *, missing_initial_nav=False):
    panel, classes = sample
    frame = panel.frame
    etfs = frame.index.get_level_values("instrument").isin(["ETFA", "ETFB"])
    frame.loc[etfs, "close"] = 100.0
    frame.loc[etfs, "cash_dividend"] = 0.0
    if missing_initial_nav:
        frame.loc[(frame.index[0][0], "ETFA"), "nav"] = float("nan")
    return source_for((replace(panel, _frame=frame, fingerprint="flat-etf"), classes), "etf-relative-strength")


def test_fully_observed_intentional_cash_scores_in_training_and_holdout(sample):
    source = cash_strategy_source(sample)
    result = run_validation_plan(source, build_validation_plan(source))
    assert result["status_counts"] == {"PAPER_SIMULATED": 3}
    for fold in result["windows"]:
        selection = fold["selection"]
        assert selection["selected_value"] == 5
        assert all(candidate["score"] == 0 and candidate["reasons"] == ["INTENTIONAL_CASH"]
                   for candidate in selection["candidates"])
        for case in fold["scenarios"]:
            assert case["status"] == "PAPER_SIMULATED" and case["reasons"] == ["INTENTIONAL_CASH"]
            metrics = case["metrics"]
            assert metrics["fill_count"] == metrics["order_count"] == metrics["fees"] == metrics["slippage"] == 0
            assert metrics["net_return"] == metrics["benchmark_return"] == metrics["excess_return"] == 0
            assert all(row["equity"] == row["benchmark"] == 10_000 for row in case["series"])


def test_missing_input_before_first_cash_signal_is_not_lookback_warmup(sample):
    source = cash_strategy_source(sample, missing_initial_nav=True)
    result = run_validation_plan(source, build_validation_plan(source))
    first = result["windows"][0]
    assert first["status"] == "INSUFFICIENT_DATA"
    assert all(row["score"] is None and row["status"] == "INSUFFICIENT_DATA"
               for row in first["selection"]["candidates"])
    assert result["windows"][1]["status"] == "PAPER_SIMULATED"


def test_gated_positive_targets_without_orders_are_untradeable(sample):
    panel, classes = sample
    source = source_for(sample, "etf-relative-strength")
    plan = build_validation_plan(source, {"parameter_values": [3]})
    frame = panel.frame
    first_oos = frame.index.get_level_values("observed_at") >= plan["windows"][0]["test_start"]
    frame.loc[first_oos, "trade_allowed"] = False
    source = source_for((replace(panel, _frame=frame, fingerprint="blocked-trades"), classes), "etf-relative-strength")
    result = run_validation_plan(source, build_validation_plan(source, plan["options"]))
    case = result["windows"][0]["scenarios"][0]
    assert case["status"] == "UNTRADEABLE" and "RISK_TRADEABILITY" in case["reasons"]
    assert "net_return" not in case["metrics"]


@pytest.mark.parametrize(("template_id", "asset", "field"), [
    ("etf-relative-strength", "ETFA", "nav"),
    ("regime-allocation", "EQA", "policy_regime"),
    ("equal-weight", "EQA", "amount"),
])
def test_later_fills_do_not_hide_missing_training_inputs(sample, template_id, asset, field):
    panel, classes = sample
    frame = panel.frame
    frame.loc[(frame.index[0][0], asset), field] = None
    source = source_for((replace(panel, _frame=frame, fingerprint="missing-" + field), classes), template_id)
    plan = build_validation_plan(source, {"parameter_values": [3]})
    results = run_validation_plan(source, plan)["windows"]
    trial, = results[0]["selection"]["candidates"]
    assert trial["status"] == "INSUFFICIENT_DATA" and trial["score"] is None
    assert "MISSING_DECISION_EVIDENCE" in trial["reasons"]
    assert results[0]["selection"]["selected_value"] is None
    assert results[1]["selection"]["candidates"][0]["status"] == "PAPER_SIMULATED"

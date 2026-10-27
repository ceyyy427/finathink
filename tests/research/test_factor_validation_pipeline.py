"""Real bounded calculation with distinguishable data, result and program states."""

import importlib
import importlib.util
import math
from dataclasses import replace

import pandas as pd
import pytest

from finahinking.data.research_fields import ResearchFieldAttachment
from finahinking.p8_2.contracts import DatasetSnapshot, MarketObservation
from finahinking.research.factor_inputs import UniverseSpec, build_factor_panel
from finahinking.research.factor_recipes import pilot_factor_specs


def api():
    name = "finahinking.research.factor_validation"
    assert importlib.util.find_spec(name) is not None, "structured nine-step validation is missing"
    return importlib.import_module(name)


def fixture_panel(*, fields=True, changed_test=False, suspended=False, assets=2):
    dates = pd.date_range("2024-01-01T16:00:00Z", periods=180)
    rows, extra = [], []
    for number in range(assets):
        instrument = f"CN:A{number}"
        for position, time in enumerate(dates):
            close = 100 + position * (0.03 + number * 0.02) + 3 * math.sin(position / (4 + number))
            if changed_test and position >= 144:
                close = 200 + position * 0.4 + 50 * math.cos(position / 2)
            rows.append(MarketObservation(instrument, time, close, close + 1, close - 1, close,
                volume=100000, amount=close * 100000, available_at=time, suspended=suspended,
                industry="fixture-industry"))
            extra.append({"instrument": instrument, "timestamp": time.isoformat(),
                          "available_at": time.isoformat(), "turnover_rate": 0.02 + math.sin(position / 3 + number) / 100})
    snapshot = DatasetSnapshot("nine-step-fixture", tuple(rows), mode="SAMPLE")
    attachment = ResearchFieldAttachment(snapshot.fingerprint, "CN",
        {"turnover_rate": "ratio_free_float", "amount": "CNY", "volume": "shares"}, extra) if fields else None
    return build_factor_panel(snapshot, attachment, as_of=dates[-1],
                               universe=UniverseSpec("CN", snapshot.instruments))


def selected():
    return next(value for value in pilot_factor_specs(5) if value.factor_id == "finathink-turnover-freefloat-mean")


def stages(result):
    return {item["stage_id"]: item for item in result.to_dict()["steps"]}


def test_nine_steps_reference_actual_computation_and_one_shared_binding():
    module = api()
    panel = fixture_panel()
    result = module.run_factor_validation(selected(), panel, module.factor_validation_policy(),
                                           gateway=module.FactorValidationGateway())
    payload = result.to_dict()
    steps = stages(result)
    assert len(steps) == 9
    assert steps["scope_data_audit"]["status"] == "COMPLETE"
    assert steps["semantic_check"]["status"] == "COMPLETE"
    assert steps["portfolio_backtest"]["status"] == "COMPLETE"
    assert steps["portfolio_backtest"]["metrics"]["metric_basis"] == "ACTUAL_NET_LEDGER"
    assert steps["information_coefficient"]["metrics"]["metric_basis"] != "ACTUAL_NET_LEDGER"
    assert steps["capacity"]["status"] == "COMPLETE"
    assert all(item["binding_digest"] == payload["binding_digest"] for item in steps.values())
    assert all(item["artifact_ref"] for item in steps.values() if item["status"] == "COMPLETE")
    assert payload["bindings"]["snapshot_ref"] == panel.snapshot_ref
    assert payload["bindings"]["attachment_ref"] == panel.attachment_ref
    assert payload["reproduction_scope"] == "FORMULA_FIXTURE"
    assert payload["final_test_metrics"] is None
    assert payload["requires_human_admission"] is True
    assert payload["production_write"] is False


def test_real_turnover_missing_is_a_data_block_not_a_bad_factor_or_volume_proxy():
    module = api()
    result = module.run_factor_validation(selected(), fixture_panel(fields=False),
        module.factor_validation_policy(), gateway=module.FactorValidationGateway())
    payload = result.to_dict()
    steps = stages(result)
    assert payload["status"] == "BLOCKED_DATA"
    assert steps["scope_data_audit"]["status"] == "BLOCKED_DATA"
    assert "turnover_rate" in steps["scope_data_audit"]["metrics"]["missing_fields"]
    assert all(row["status"] == "NOT_RUN" for key, row in steps.items() if key != "scope_data_audit")
    assert payload["financial_falsification"] is False


def test_negative_perfect_correlation_is_duplicate_while_unknown_is_not_zero():
    module = api()
    left = pd.Series([1.0, 2.0, 3.0, 4.0])
    duplicate = module.factor_pair_diagnostic(left, -left, min_samples=4)
    assert duplicate["status"] == "DUPLICATE"
    assert duplicate["absolute_correlation"] == pytest.approx(1)
    constant = module.factor_pair_diagnostic(left, pd.Series([1.0] * 4), min_samples=4)
    sparse = module.factor_pair_diagnostic(left.iloc[:1], left.iloc[:1], min_samples=4)
    assert constant["status"] == "UNDEFINED_CONSTANT"
    assert sparse["status"] == "INSUFFICIENT_SAMPLES"
    assert constant["absolute_correlation"] is sparse["absolute_correlation"] is None


def test_broken_gateway_preserves_completed_evidence_and_reports_execution_error():
    module = api()

    class Broken(module.FactorValidationGateway):
        def backtest(self, *args, **kwargs):
            raise RuntimeError("fixture engine interruption")

    result = module.run_factor_validation(selected(), fixture_panel(),
        module.factor_validation_policy(), gateway=Broken())
    assert stages(result)["information_coefficient"]["status"] == "COMPLETE"
    assert stages(result)["portfolio_backtest"]["status"] == "EXECUTION_ERROR"
    assert result.to_dict()["financial_falsification"] is False
    assert result.to_dict()["status"] == "EXECUTION_ERROR"


def test_fee_bps_are_applied_to_actual_notional_and_suspended_orders_do_not_fill():
    module = api()
    policy = module.factor_validation_policy(fee_bps=7, slippage_bps=3)
    result = module.run_factor_validation(selected(), fixture_panel(), policy,
                                          gateway=module.FactorValidationGateway())
    ledger = stages(result)["portfolio_backtest"]["metrics"]
    trades = ledger["trades"]
    assert trades
    assert ledger["fee_total"] == pytest.approx(sum(item["notional"] * 7 / 10000 for item in trades))
    blocked = module.run_factor_validation(selected(), fixture_panel(suspended=True), policy,
                                           gateway=module.FactorValidationGateway())
    assert stages(blocked)["portfolio_backtest"]["metrics"]["trades"] == []


def test_final_holdout_prices_do_not_change_validation_metrics_or_ranking():
    module = api()
    policy = module.factor_validation_policy(window_grid=[3, 5, 8], embargo=2, label_horizon=2)
    original = module.run_factor_validation(selected(), fixture_panel(), policy,
        gateway=module.FactorValidationGateway()).to_dict()
    changed = module.run_factor_validation(selected(), fixture_panel(changed_test=True), policy,
        gateway=module.FactorValidationGateway()).to_dict()
    first, second = stages(module.FactorValidationRun.from_dict(original)), stages(module.FactorValidationRun.from_dict(changed))
    assert first["information_coefficient"]["metrics"] == second["information_coefficient"]["metrics"]
    assert first["oos_stability"]["metrics"] == second["oos_stability"]["metrics"]
    assert original["final_test_metrics"] is changed["final_test_metrics"] is None
    assert original["bindings"]["snapshot_ref"] != changed["bindings"]["snapshot_ref"]
    split = first["oos_stability"]["metrics"]["split"]
    assert split["embargo"] == split["horizon"] == 2
    assert split["max_training_label_end"] < split["validation_start"]


def test_test_phase_requires_persisted_freeze_and_reservation_before_any_score():
    module = api()
    result = module.run_factor_validation(selected(), fixture_panel(assets=1),
        module.factor_validation_policy(phase="test"), gateway=module.FactorValidationGateway())
    assert result.to_dict()["status"] == "BLOCKED_CONTROL"
    assert result.to_dict()["final_test_metrics"] is None
    assert all(row["status"] == "NOT_RUN" for row in result.to_dict()["steps"])


def test_existing_study_freeze_and_once_test_claim_authorize_only_the_frozen_candidate(tmp_path):
    module = api()
    from finahinking.research.factor_study_store import FactorStudyStore
    from finahinking.research.job_queue import JobQueue

    panel = fixture_panel(assets=1)
    validation = module.run_factor_validation(selected(), panel, module.factor_validation_policy(),
        gateway=module.FactorValidationGateway()).to_dict()
    store = FactorStudyStore(JobQueue(tmp_path / "jobs.sqlite"), scope_id="fixture-owner")
    store.create("frozen-study", {"snapshot_ref": panel.snapshot_ref, "instrument": "CN:A0",
                                  "test_period": validation["bindings"]["final_test_window"]})
    store.set_job("frozen-study", "validation", "job-validation")
    store.complete("frozen-study", "validation", "job-validation", succeeded=True)
    store.freeze("frozen-study", selected().factor_id, validation["candidate_fingerprint"])
    store.reserve_test("frozen-study", "once-only")
    gateway = module.FactorValidationGateway(study_store=store, study_ids={"CN:A0": "frozen-study"})
    tested = module.run_factor_validation(selected(), panel, module.factor_validation_policy(phase="test"),
                                           gateway=gateway).to_dict()
    assert tested["final_test_metrics"] is not None
    assert tested["bindings"]["final_test_usage"] == "RESERVED_FROZEN_TEST"
    changed = next(value for value in pilot_factor_specs(6) if value.factor_id == selected().factor_id)
    denied = module.run_factor_validation(changed, panel, module.factor_validation_policy(phase="test"), gateway=gateway)
    assert denied.to_dict()["status"] == "BLOCKED_CONTROL"
    store.set_job("frozen-study", "test", "job-final")
    store.complete("frozen-study", "test", "job-final", succeeded=True)
    repeated = module.run_factor_validation(selected(), panel, module.factor_validation_policy(phase="test"), gateway=gateway)
    assert repeated.to_dict()["status"] == "BLOCKED_CONTROL"
    with pytest.raises(ValueError):
        store.reserve_test("frozen-study", "another-key")


def test_implementation_or_formula_change_is_semantic_rejection_not_financial_failure():
    module = api()
    wrong = replace(selected(), formula="rolling_mean(turnover_rate,6)")
    result = module.run_factor_validation(wrong, fixture_panel(), module.factor_validation_policy(),
                                           gateway=module.FactorValidationGateway())
    assert result.to_dict()["status"] == "SEMANTIC_REJECTION"
    assert result.to_dict()["financial_falsification"] is False


@pytest.mark.parametrize("periods", [5, 9])
def test_block_icir_cannot_be_replaced_with_ic_over_return_volatility(periods):
    api()
    from finahinking.factors.evaluation import evaluate_factor_candidate
    from finahinking.factors.mining import FactorCandidate

    index = pd.date_range("2025-01-01", periods=periods, tz="UTC")
    frame = pd.DataFrame({"close": range(1, periods + 1)}, index=index)
    labels = pd.Series(range(periods), index=index, dtype=float)
    candidate = FactorCandidate("constant-block-ic", "close", "fixture", "fixture", {})
    value = evaluate_factor_candidate(candidate, frame, labels,
        {"min_samples": 4, "phase": "validation", "shift_periods": 1})
    assert value.information_coefficient == pytest.approx(1)
    assert value.information_ratio is None


def test_performance_annualization_is_explicit_without_changing_total_returns():
    api()
    from finahinking.research.quant_analytics import compute_performance_report

    ledger = {"entries": [{"timestamp": "1", "equity": 100, "fees": 0, "slippage": 0},
                          {"timestamp": "2", "equity": 101, "fees": 0, "slippage": 0}],
              "returns": [0.01, -0.02, 0.03]}
    observed = compute_performance_report(ledger, annualization=1)
    yearly = compute_performance_report(ledger, annualization=252)
    assert observed.metrics["total_return"] == yearly.metrics["total_return"]
    assert yearly.metrics["volatility"] == pytest.approx(observed.metrics["volatility"] * math.sqrt(252))


def test_frozen_validation_rehydrates_without_loading_the_current_policy_directory(monkeypatch):
    module = api()
    result = module.run_factor_validation(selected(), fixture_panel(), module.factor_validation_policy(),
        gateway=module.FactorValidationGateway())

    def unavailable():
        raise RuntimeError("current resource directory is unavailable")

    monkeypatch.setattr(module, "_policy_definition", unavailable)
    assert module.FactorValidationRun.from_dict(result.to_dict()).fingerprint == result.fingerprint


def test_serial_diagnostics_never_concatenate_asset_boundaries():
    module = api()
    result = module.run_factor_validation(selected(), fixture_panel(), module.factor_validation_policy(),
        gateway=module.FactorValidationGateway())
    metrics = stages(result)["information_coefficient"]["metrics"]
    assert metrics["serial_scope"] == "WITHIN_ASSET_ONLY"
    assert all("product_lag1_autocorrelation" in row for row in metrics["per_asset"])


def test_step_metrics_cannot_be_changed_under_an_old_artifact_reference():
    module = api()
    from finahinking.research.contracts import stable_digest

    value = module.run_factor_validation(selected(), fixture_panel(), module.factor_validation_policy(),
        gateway=module.FactorValidationGateway()).to_dict()
    row = value["steps"][0]
    row["metrics"]["rows"] = 99999
    value["artifacts"][row["artifact_ref"]]["metrics"]["rows"] = 99999
    value["fingerprint"] = stable_digest({key: item for key, item in value.items() if key != "fingerprint"})
    with pytest.raises(ValueError):
        module.FactorValidationRun.from_dict(value)


def test_completed_bad_numeric_result_is_not_an_engine_failure_or_automatic_admission():
    module = api()
    result = module.run_factor_validation(selected(), fixture_panel(),
        module.factor_validation_policy(min_abs_ic=1), gateway=module.FactorValidationGateway())
    assert result.to_dict()["status"] in {"COMPLETE", "COMPLETE_WITH_BLOCKS"}
    decision = stages(result)["archive_decision"]["metrics"]
    assert decision["evaluation_outcome"] == "COMPLETED_WITH_REJECTION"
    assert decision["decision"] == "OBSERVATION_ONLY"
    assert result.to_dict()["financial_falsification"] is False


def test_a_different_code_label_cannot_claim_to_be_this_engine_execution():
    module = api()
    result = module.run_factor_validation(selected(), fixture_panel(),
        module.factor_validation_policy(code_version="another-build"), gateway=module.FactorValidationGateway())
    assert result.to_dict()["status"] == "BLOCKED_CONTROL"
    assert all(row["status"] == "NOT_RUN" for row in result.to_dict()["steps"])


def test_existing_research_pipeline_exposes_the_same_validation_artifacts():
    module = api()
    from finahinking.research.factor_pipeline import FactorResearchPipeline

    result = FactorResearchPipeline().validate_factor(selected(), fixture_panel(),
        module.factor_validation_policy(), gateway=module.FactorValidationGateway())
    assert isinstance(result, module.FactorValidationRun)
    assert len(result.to_dict()["steps"]) == 9

"""Independent cash, inventory, covariance and constrained-allocation examples."""

import json
import time
from copy import deepcopy
from statistics import covariance, variance
from types import SimpleNamespace

import pandas as pd
import pytest

from finahinking.research.strategy_attribution import analyze_portfolio, normalize_portfolio_options


def _source():
    times = pd.date_range("2025-01-01T16:00:00Z", periods=5)
    start, buy, split, end = [t.isoformat() for t in times[:4]]
    prices = {"A": (10., 10., 6., 6.6, 900.), "B": (20., 20., 19.8, 17.82, 2.), "C": (10.,) * 5}
    frame = pd.DataFrame([
        {"observed_at": time, "instrument": asset, "close": prices[asset][i],
         "split_factor": 2. if asset == "A" and i >= 2 else 1., "execution_eligible": True,
         "industry": "Energy" if asset == "A" else "Finance", "style_beta": 1.5 if asset == "A" else -.5}
        for i, time in enumerate(times) for asset in prices
    ]).set_index(["observed_at", "instrument"])
    opening = {"timestamp": start, "cash": 1000., "available_cash": 1000., "unsettled_cash": 0.,
               "positions": {"A": 0., "B": 0., "C": 0.}, "position_marks": {}, "position_value": 0., "equity": 1000.}
    closing = {"timestamp": end, "cash": 732., "available_cash": 700., "unsettled_cash": 32.,
               "positions": {"A": 15., "B": 10., "C": 0.}, "position_marks": {"A": 6.6, "B": 17.82},
               "position_value": 277.2, "equity": 1009.2}
    ledger = {
        "status": "COMPLETE", "currency": "CNY", "marks": [opening, closing],
        "fills": [
            {"timestamp": buy, "instrument": "A", "side": "buy", "quantity": 10., "mark": 10.,
             "notional": 101., "fees": 1., "slippage": 1.},
            {"timestamp": buy, "instrument": "B", "side": "buy", "quantity": 10., "mark": 20.,
             "notional": 201., "fees": 2., "slippage": 1.},
            {"timestamp": end, "instrument": "A", "side": "sell", "quantity": 5., "mark": 6.6,
             "notional": 32.5, "fees": .5, "slippage": .5},
        ],
        "corporate_actions": [
            {"timestamp": split, "instrument": "A", "kind": "SPLIT", "ratio": 2.},
            {"timestamp": split, "instrument": "A", "kind": "DIVIDEND", "per_share": .25},
        ],
    }
    selections = [{"observed_at": t.isoformat(), "source_observed_at": t.isoformat(), "instrument": asset,
                   "field": "split_factor", "attachment_ref": "action-rules"} for t in times for asset in prices]
    panel = SimpleNamespace(frame=frame, audit={"selections": selections, "row_reasons": []},
                            contract=SimpleNamespace(adjustment_ref="action-rules"))
    return {"execution_mode": "PAPER_LONG_ONLY", "ledger_digest": "original-ledger",
            "asset_classes": {"A": "EQUITY", "B": "ETF", "C": "EQUITY"}, "benchmark_instruments": ["A", "B", "C"],
            "result": {"ledger": ledger, "status": "COMPLETE"}, "panel": panel}


def test_original_fills_cash_shares_and_gross_to_net():
    source = _source()
    original = deepcopy(source["result"])
    report = analyze_portfolio(source)
    assert report["status"] == "COMPLETE"
    assert report["basis"] == "ORIGINAL_PUBLISHED_LEDGER"
    assert report["ledger_digest"] == "original-ledger"
    account, bridge = report["account"], report["attribution"]
    assert (account["available_cash"], account["unsettled_cash"], account["cash"]) == (700., 32., 732.)
    assert [(h["instrument"], h["quantity"]) for h in account["holdings"]] == [("A", 15.), ("B", 10.)]
    assert [h["value"] for h in account["holdings"]] == pytest.approx([99., 178.2])
    assert account["closing_equity"] == pytest.approx(700 + 32 + 99 + 178.2)
    assert bridge["status"] == "RECONCILED" and bridge["fill_count"] == 3
    assert [a["gross_pnl"] for a in bridge["by_asset"]] == pytest.approx([37., -21.8])
    assert [bridge[k] for k in ("dividends", "gross_pnl", "fees", "slippage", "net_pnl")] == pytest.approx(
        [5., 15.2, 3.5, 2.5, 9.2])
    assert [bridge[k] for k in ("gross_return", "net_return", "turnover")] == pytest.approx([.0152, .0092, .333])
    assert [bridge[k] for k in ("residual", "cash_residual", "equity_residual")] == pytest.approx([0., 0., 0.])
    assert bridge["quantity_residuals"] == dict.fromkeys("ABC", 0.)
    assert source["result"] == original
    json.dumps(report, allow_nan=False)


def test_independent_capped_solution_exposures_stress_and_correlated_risk():
    source = _source()
    actual_a, actual_b = 99. / 1009.2, 178.2 / 1009.2
    bounds = {"target_exposure": .7, "max_single_weight": .27}
    report = analyze_portfolio(source, bounds)
    solver = report["solver"]
    # B hits the cap. A and C share the remaining exposure by equal displacement.
    shift = (.7 - .27 - actual_a) / 2
    assert solver["status"] == "SOLVED" and solver["cash_weight"] == pytest.approx(.3)
    assert solver["weights"] == pytest.approx({"A": actual_a + shift, "B": .27, "C": shift})
    assert report["industry"]["weights"] == pytest.approx({"Energy": actual_a, "Finance": actual_b})
    assert report["factors"]["style_beta"]["value"] == pytest.approx(1.5 * actual_a - .5 * actual_b)
    a_returns, b_returns = [0., .2, .1], [0., -.01, -.1]
    daily = [actual_a * a + actual_b * b for a, b in zip(a_returns, b_returns, strict=True)]
    risk = report["risk"]
    assert risk["observations"] == 3 and risk["to_at"] == report["as_of"]
    assert risk["variance"] == pytest.approx(variance(daily))
    components = [actual_a * covariance(a_returns, daily), actual_b * covariance(b_returns, daily)]
    assert [r["variance"] for r in risk["contributions"]] == pytest.approx(components)
    assert sum(r["share"] for r in risk["contributions"]) == pytest.approx(1.)
    assert [s["pnl"] for s in report["stress"]] == pytest.approx([-.1 * 277.2, -.2 * 99. - .05 * 178.2])
    # The panel contains another date. Neither exposure nor risk may read past the ledger endpoint.
    source["panel"].frame = source["panel"].frame.iloc[:-3]
    assert analyze_portfolio(source, bounds) == report


def test_missing_fields_and_nonconsecutive_returns_are_explicit():
    source = _source()
    frame = source["panel"].frame
    end = frame.index.levels[0][-2]
    frame.at[(end, "B"), "style_beta"] = float("nan")
    frame.at[(end, "A"), "industry"] = "UNKNOWN"
    frame.at[(frame.index.levels[0][1], "A"), "close"] = float("nan")
    result = analyze_portfolio(source)
    assert result["status"] == "PARTIAL"
    assert result["industry"]["missing_weight"] == pytest.approx(99. / 1009.2)
    assert result["industry"]["missing_instruments"] == ["A"]
    beta = result["factors"]["style_beta"]
    assert beta["status"] == "PARTIAL" and beta["value"] is None
    assert beta["known_value"] == pytest.approx(1.5 * 99. / 1009.2)
    assert beta["missing_instruments"] == ["B"]
    assert result["risk"]["status"] == "NEEDS_DATA" and result["risk"]["observations"] == 1
    assert result["risk"]["variance"] is None
    json.dumps(result, allow_nan=False)


def test_infeasible_and_zero_budget_leave_actual_account_intact():
    source = _source()
    report = analyze_portfolio(source, {"max_single_weight": .2})
    assert report["solver"]["status"] == "INFEASIBLE"
    assert report["solver"]["weights"] == {} and report["solver"]["cash_weight"] is None
    assert report["solver"]["reason"] == "TARGET_EXCEEDS_ELIGIBLE_NAME_CAPACITY"
    cash_only = analyze_portfolio(source, {"target_exposure": 0., "max_single_weight": 0.})
    assert cash_only["solver"]["cash_weight"] == 1. and cash_only["solver"]["weights"] == {}
    assert cash_only["account"] == report["account"]
    source["result"]["ledger"]["marks"][-1]["positions"]["A"] += 1
    mismatch = analyze_portfolio(source)["attribution"]
    assert mismatch["status"] == "MISMATCH" and mismatch["quantity_residuals"]["A"] == 1.


def test_theoretical_and_missing_ledger_do_not_manufacture_returns():
    source = _source()
    source["execution_mode"] = "THEORETICAL"
    report = analyze_portfolio(source)
    assert report["status"] == "RESEARCH_ONLY" and report["account"] is None and report["attribution"] is None
    source["execution_mode"] = "PAPER_LONG_ONLY"
    source["result"]["ledger"] = None
    report = analyze_portfolio(source)
    assert report["status"] == "NEEDS_DATA" and report["risk"] is None and report["stress"] == []


@pytest.mark.parametrize("options", [{"foo": 1}, {"target_exposure": 1.01}, {"max_single_weight": -1.},
                                      {"target_exposure": True}, {"max_single_weight": float("nan")}, []])
def test_portfolio_constraint_input_bounds(options):
    with pytest.raises(ValueError):
        normalize_portfolio_options(options)


def test_published_paper_account_diagnostics_match_immutable_worker_ledger(tmp_path):
    from finahinking.research.application_service import ResearchApplicationService
    from finahinking.research.contracts import stable_digest
    from finahinking.research.strategy_settlement import StrategySettlementService

    app = ResearchApplicationService(tmp_path, allow_model_calls=False)
    try:
        sample = app.create_strategy_sample()
        job = app.run_strategy_template({"template_id": "equal-weight", "contract": sample["contract"],
                                        "attachment_refs": sample["attachment_refs"], "idempotency_key": "attribution"})
        app.start()
        timeout = time.monotonic() + 110
        while job["status"] not in {"completed", "failed", "cancelled"} and time.monotonic() < timeout:
            time.sleep(.1)
            job = app.get_job(job["job_id"])
        assert job["status"] == "completed", job
        source = StrategySettlementService(app).replay_inputs(job["run_id"])
        report = analyze_portfolio(source)
        ledger = source["result"]["ledger"]
        assert report["status"] == "COMPLETE"
        assert report["ledger_digest"] == stable_digest(ledger)
        assert report["as_of"] == ledger["marks"][-1]["timestamp"]
        assert report["attribution"]["status"] == "RECONCILED" and report["attribution"]["fill_count"] > 0
        assert report["attribution"]["net_return"] == pytest.approx(ledger["marks"][-1]["equity"] / ledger["marks"][0]["equity"] - 1)
        assert report["attribution"]["fees"] == pytest.approx(ledger["total_fees"])
        assert report["attribution"]["slippage"] == pytest.approx(ledger["total_slippage"])
        assert report["risk"]["observations"] == 60
        assert report["account"]["available_cash"] == ledger["marks"][-1]["available_cash"]
        assert source["ledger_digest"] == stable_digest(ledger)
    finally:
        app.close()


def test_late_historical_split_or_wrong_rule_reference_excludes_distorted_risk():
    source = _source()
    panel = source["panel"]
    stamps = [t.isoformat() for t in panel.frame.index.levels[0]]
    delayed = next(r for r in panel.audit["selections"] if r["observed_at"] == stamps[2] and r["instrument"] == "A")
    # Day 2's first disclosed split was effective on day 1. Neither return is certified.
    delayed["source_observed_at"] = stamps[1]
    risk = analyze_portfolio(source)["risk"]
    assert risk["status"] == "NEEDS_DATA" and risk["observations"] == 1 and risk["variance"] is None
    delayed["source_observed_at"] = stamps[2]
    delayed["attachment_ref"] = "unbound-attachment"
    risk = analyze_portfolio(source)["risk"]
    assert risk["status"] == "NEEDS_DATA" and risk["observations"] == 1


def test_all_cash_and_zero_variance_have_no_artificial_percentage_contribution():
    source = _source()
    ledger = source["result"]["ledger"]
    opening = ledger["marks"][0]
    end = ledger["marks"][-1]["timestamp"]
    ledger.update(marks=[opening, dict(opening, timestamp=end)], fills=[], corporate_actions=[])
    report = analyze_portfolio(source)
    assert report["account"]["holdings"] == [] and report["account"]["cash"] == 1000.
    assert report["attribution"]["net_return"] == 0. and report["attribution"]["status"] == "RECONCILED"
    assert report["risk"]["status"] == "CASH_ONLY" and report["risk"]["variance"] == 0.
    assert all(row["pnl"] == 0. for row in report["stress"])
    source = _source()
    frame = source["panel"].frame
    frame["close"] = 10.
    frame["split_factor"] = 1.
    risk = analyze_portfolio(source)["risk"]
    assert risk["variance"] == 0. and risk["observations"] == 3
    assert all(row["variance"] == 0. and row["share"] is None for row in risk["contributions"])


def test_missing_tradability_evidence_cannot_certify_an_infeasible_solver():
    source = _source()
    panel = source["panel"]
    end = panel.frame.index.levels[0][-2]
    panel.frame.at[(end, "C"), "execution_eligible"] = False
    bounds = {"max_single_weight": .3, "target_exposure": .8}
    assert analyze_portfolio(source, bounds)["solver"]["status"] == "INFEASIBLE"
    panel.audit["row_reasons"].append({"instrument": "C", "observed_at": end.isoformat()})
    solver = analyze_portfolio(source, bounds)["solver"]
    assert solver["status"] == "NEEDS_DATA" and solver["missing_eligibility"] == ["C"]
    assert solver["reason"] == "ELIGIBILITY_EVIDENCE_MISSING" and not solver["weights"]

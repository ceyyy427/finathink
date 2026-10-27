from dataclasses import replace

import pandas as pd
import pytest

from finahinking.data.models import Dataset, Provenance
from finahinking.quant.engines import BacktestEngine
from finahinking.quant.interfaces import BacktestConfig
from finahinking.research.paper_trader import PaperTrader
from finahinking.research.portfolio_runtime import PaperPortfolioProposal
from finahinking.research.risk_runtime import RiskManager


def test_cvar_uses_exact_fractional_empirical_tail_not_float_ceil():
    for returns, expected in (([-0.2] + [0.01] * 19, 0.2), ([-0.3, -0.1] + [0.01] * 28, 7 / 30)):
        snapshot = {"pit_status": "AVAILABLE", "observations": [{"instrument": "AAA", "industry": "technology", "volume": 10000, "returns": returns}]}
        report = RiskManager().exposure(snapshot, {"weights": {"AAA": 1}}, {"AAA": {"momentum": 1}})
        assert report.passed
        assert report.cvar == pytest.approx(expected)


class ConstantWeight:
    strategy_id = "constant"
    version = "v1"

    def generate(self, dataset):
        return pd.Series(0.5, index=dataset.frame.index)


def test_next_bar_execution_respects_halts_and_actual_bar_liquidity():
    index = pd.date_range("2026-01-01", periods=4, tz="UTC")
    frame = pd.DataFrame({"close": [100] * 4, "volume": [10000, 10000, 2, 10000], "suspended": [False, True, False, False]}, index=index)
    dataset = Dataset(frame, Provenance("controlled", "offline acceptance", license="test"))
    config = BacktestConfig(starting_cash=10000, fee_bps=0, slippage_bps=0, max_volume_participation=0.1)
    result = BacktestEngine().run(dataset, ConstantWeight(), config)
    assert next(timestamp for timestamp, position in result.positions if position > 0) == index[2].isoformat()
    assert result.trades[0].quantity == pytest.approx(0.2)
    assert len(result.trades) == 2
    assert result.positions[-1][1] == pytest.approx(50)
    assert BacktestConfig.from_dict(config.to_dict()) == config


def proposal(weights=None):
    weights = weights or {"AAA": 0.5, "BBB": 0.5}
    return PaperPortfolioProposal(status="PASSED", passed=True, weights=weights, cash_weight=1 - sum(weights.values()), risk_digest="controlled")


def test_paper_equity_marks_all_positions_and_deducts_fees_and_slippage():
    snapshot = {"as_of": "2026-01-01", "pit_status": "AVAILABLE", "observations": [{"instrument": name, "close": 100} for name in ("AAA", "BBB")]}
    ledger = PaperTrader().simulate(proposal(), snapshot, {"initial_cash": 1000, "fee_bps": 10, "slippage_bps": 20})
    assert ledger.entries[-1].equity == pytest.approx(1000 - ledger.total_fees - ledger.total_slippage)
    assert ledger.entries[-1].cash == pytest.approx(0)


def test_delayed_paper_fill_never_borrows_future_and_keeps_pending_cash():
    base = {"as_of": "2026-01-02T00:00:00+00:00", "pit_status": "AVAILABLE", "observations": [{"instrument": "AAA", "timestamp": "2026-01-01T00:00:00+00:00", "available_at": "2026-01-01T00:00:00+00:00", "close": 100, "volume": 10000}]}
    policy = {"initial_cash": 1000, "delay_observations": 1, "signal_at": "2026-01-01T00:00:00+00:00", "max_volume_participation": 0.1}
    pending = PaperTrader().simulate(proposal({"AAA": 0.5}), base, policy)
    assert pending.entries[0].event == "paper_pending"
    assert pending.entries[0].quantity == pending.total_fees == 0
    assert pending.entries[0].cash == pending.entries[0].equity == 1000
    following = {"instrument": "AAA", "timestamp": base["as_of"], "available_at": base["as_of"], "close": 110, "volume": 10000}
    filled = PaperTrader().simulate(proposal({"AAA": 0.5}), dict(base, observations=base["observations"] + [following]), policy)
    assert filled.entries[0].event == "paper_fill"
    assert filled.entries[0].reference_price == 110
    assert filled.entries[0].timestamp == base["as_of"]
    unavailable = dict(following, available_at="2026-01-03T00:00:00+00:00")
    assert PaperTrader().simulate(proposal({"AAA": 0.5}), dict(base, observations=base["observations"] + [unavailable]), policy).entries[0].event == "paper_pending"


@pytest.mark.parametrize("overrides,event", [({"suspended": True}, "paper_pending"), ({"volume": 0}, "paper_pending"), ({"volume": 2}, "paper_partial")])
def test_delayed_paper_halts_liquidity_and_partial_fills(overrides, event):
    observations = [{"instrument": "AAA", "timestamp": f"2026-01-0{day}T00:00:00+00:00", "available_at": f"2026-01-0{day}T00:00:00+00:00", "close": 100, "volume": 10000} for day in (1, 2)]
    observations[-1].update(overrides)
    ledger = PaperTrader().simulate(proposal({"AAA": 0.5}), {"pit_status": "AVAILABLE", "as_of": observations[-1]["timestamp"], "observations": observations}, {"initial_cash": 1000, "delay_observations": 1, "signal_at": observations[0]["timestamp"], "max_volume_participation": 0.1})
    assert ledger.entries[0].event == event
    assert ledger.entries[0].quantity == pytest.approx(0.2 if event == "paper_partial" else 0)


def test_optional_execution_config_remains_serializable_and_validated():
    config = BacktestConfig(1000, 0, 0)
    assert BacktestConfig.from_dict(config.to_dict()) == config
    for invalid in (0, -1, 1.1, True, float("nan")):
        with pytest.raises((TypeError, ValueError)):
            replace(config, max_volume_participation=invalid)


def test_delayed_multi_asset_ledger_is_chronological_and_marks_existing_holdings():
    def row(name, day, price):
        return {"instrument": name, "timestamp": f"2026-01-0{day}T00:00:00+00:00", "available_at": f"2026-01-0{day}T00:00:00+00:00", "close": price, "volume": 10000}
    # BBB fills first, despite alphabetic instrument order. Its already held
    # units double in marked value before AAA's next-observation fill.
    snapshot = {"pit_status": "AVAILABLE", "as_of": "2026-01-03T00:00:00+00:00", "observations": [row("AAA", 1, 100), row("AAA", 3, 100), row("BBB", 1, 100), row("BBB", 2, 100), row("BBB", 3, 200)]}
    ledger = PaperTrader().simulate(proposal(), snapshot, {"initial_cash": 1000, "delay_observations": 1, "signal_at": "2026-01-01T00:00:00+00:00"})
    assert [entry.instrument for entry in ledger.entries] == ["BBB", "AAA"]
    assert ledger.entries[-1].equity == pytest.approx(1500)
    assert ledger.entries[-1].cash == pytest.approx(0)

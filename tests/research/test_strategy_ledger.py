"""Independent accounting examples for the mixed-asset paper ledger."""
import pandas as pd
import pytest

from finahinking.data.panel_contracts import PanelContract, PanelFieldAttachment
from finahinking.data.research_panel import build_research_panel
from finahinking.p8_2.contracts import DatasetSnapshot, MarketObservation

TIMES = [pd.Timestamp(f"2026-01-{day:02}T16:00:00Z") for day in (2, 5, 6, 7, 8)]
POLICY = {
    "starting_cash": 1000,
    "fee_bps": 0,
    "slippage_bps": 0,
    "max_volume_participation": 1,
    "delay_observations": 1,
}
CLASSES = {"AAA": "EQUITY", "FUND": "ETF"}
RULE_UNITS = {
    "is_session": "boolean",
    "in_universe": "boolean",
    "trade_allowed": "boolean",
    "lot_size": "shares",
    "settlement_days": "sessions",
    "split_factor": "ratio",
    "cash_dividend": "USD_per_share",
    "limit_rule_version": "rule_version",
    "limit_up_price": "USD",
    "limit_down_price": "USD",
}


def panel_fixture(*, rows=None, prices=None, suspended=None, extra_rules=()):
    rows = rows or {}
    prices = prices or {}
    suspended = suspended or {}
    observations = []
    rules = []
    for asset in CLASSES:
        for idx, timestamp in enumerate(TIMES):
            price = prices.get((asset, idx), 100.0)
            observations.append(
                MarketObservation(
                    asset, timestamp, price, price, price, price, 1000,
                    amount=price * 1000, available_at=timestamp,
                    suspended=suspended.get((asset, idx), False),
                )
            )
            rule = {
                "instrument": asset, "timestamp": timestamp, "available_at": timestamp,
                "is_session": True, "in_universe": True, "trade_allowed": True,
                "lot_size": 1, "settlement_days": 0, "split_factor": 1,
                "cash_dividend": 0, "limit_rule_version": "UNRESTRICTED",
                **rows.get((asset, idx), {}),
            }
            if rule.get("split_factor") == "OMIT":
                del rule["split_factor"]
            rules.append(rule)
    snapshot = DatasetSnapshot(
        "phase26-ledger-fixture", tuple(observations),
        provenance={"adjustment": "RAW", "asset_classes": CLASSES},
    )
    attachment = PanelFieldAttachment(snapshot.fingerprint, RULE_UNITS, [*rules, *extra_rules], "Test ledger rules")
    contract = PanelContract(
        snapshot.fingerprint, list(CLASSES), TIMES,
        {"close": "USD", "volume": "shares", "amount": "USD"},
        "phase26-ledger-test", dict.fromkeys(CLASSES, "USD"), dict.fromkeys(CLASSES, "UTC"),
        attachment.fingerprint, attachment.fingerprint, attachment.fingerprint, attachment.fingerprint,
    )
    return build_research_panel(snapshot, [attachment], contract)


def test_cash_positions_actions_and_fees_reconcile():
    from finahinking.research.strategy_runner import simulate_paper_ledger

    panel = panel_fixture(
        rows={
            ("AAA", 2): {"split_factor": 2, "cash_dividend": 1},
            ("AAA", 3): {"split_factor": 2},
            ("AAA", 4): {"split_factor": 2},
        },
        prices={("AAA", idx): 50 for idx in (2, 3, 4)},
    )
    result = simulate_paper_ledger(
        panel, {TIMES[0]: {"AAA": 0.4, "FUND": 0.4}},
        asset_classes=CLASSES, policy={**POLICY, "fee_bps": 100},
    )
    # At the second observation, buy 4 + 4 shares at $100 and pay $8.
    # Four stock shares become eight at $50, followed by $8 dividend cash.
    assert result["marks"][0]["equity"] == 1000
    assert result["marks"][1]["cash"] == pytest.approx(192)
    assert result["marks"][1]["equity"] == pytest.approx(992)
    assert result["marks"][2]["cash"] == pytest.approx(200)
    assert result["marks"][2]["equity"] == pytest.approx(1000)
    assert result["marks"][2]["positions"] == {"AAA": 8.0, "FUND": 4.0}
    assert [event["kind"] for event in result["corporate_actions"]] == ["SPLIT", "DIVIDEND"]
    assert result["total_fees"] == pytest.approx(8)
    for mark in result["marks"]:
        assert mark["cash"] + mark["position_value"] == pytest.approx(mark["equity"])
    assert {fill["asset_class"] for fill in result["fills"]} == {"EQUITY", "ETF"}
    assert all(fill["decision_at"] == TIMES[0].isoformat() for fill in result["fills"])
    assert all(fill["timestamp"] == TIMES[1].isoformat() for fill in result["fills"])


def test_short_and_tradeability_constraints_are_explicit():
    from finahinking.research.strategy_runner import simulate_paper_ledger

    panel = panel_fixture(
        rows={("AAA", 1): {"limit_rule_version": None}},
        suspended={("FUND", 1): True},
    )
    result = simulate_paper_ledger(
        panel, {TIMES[0]: {"AAA": 0.5, "FUND": 0.5}, TIMES[1]: {"AAA": -0.5}},
        asset_classes=CLASSES, policy=POLICY,
    )
    assert not result["fills"]
    assert {row["reason"] for row in result["orders"]} == {
        "MISSING_PRICE_LIMIT_RULE", "SUSPENDED", "SHORT_UNSUPPORTED",
    }


def test_settlement_blocks_resale_and_unsettled_cash_cannot_fund_buy():
    from finahinking.research.strategy_runner import simulate_paper_ledger

    panel = panel_fixture(rows={("AAA", i): {"settlement_days": 2} for i in range(len(TIMES))})
    result = simulate_paper_ledger(
        panel,
        {TIMES[0]: {"AAA": 1}, TIMES[1]: {"FUND": 1}, TIMES[2]: {"FUND": 1}},
        asset_classes=CLASSES, policy=POLICY,
    )
    assert [fill["side"] for fill in result["fills"]] == ["buy", "sell"]
    assert result["fills"][1]["timestamp"] == TIMES[3].isoformat()
    assert result["marks"][3]["cash"] == pytest.approx(1000)
    assert result["marks"][3]["available_cash"] == 0
    assert result["marks"][3]["unsettled_cash"] == pytest.approx(1000)
    assert {row["reason"] for row in result["orders"]} >= {"SETTLEMENT_LOCK", "INSUFFICIENT_CASH"}


def test_late_split_does_not_rewrite_held_positions():
    from finahinking.research.strategy_runner import simulate_paper_ledger

    panel = panel_fixture(
        rows={("AAA", index): {"split_factor": "OMIT"} for index in (2, 3, 4)},
        extra_rules=[{"instrument": "AAA", "timestamp": TIMES[2], "available_at": TIMES[3], "split_factor": 2}],
    )
    # Ledger sees an old effective split when its disclosure arrives later.
    result = simulate_paper_ledger(
        panel, {TIMES[0]: {"AAA": 0.5}}, asset_classes=CLASSES, policy=POLICY,
    )
    assert result["status"] == "NEEDS_DATA"
    assert result["failure"]["reason"] == "LATE_SPLIT_DISCLOSURE"
    assert not result["corporate_actions"]
    assert result["marks"][-1]["positions"]["AAA"] == 5


def test_benchmark_revalues_split_and_dividend_on_common_grid():
    from finahinking.research.strategy_runner import execute_strategy

    panel = panel_fixture(
        rows={("AAA", index): {"split_factor": 2, "cash_dividend": int(index == 2)}
              for index in (2, 3, 4)},
        prices={("AAA", index): 50 for index in (2, 3, 4)},
    )
    result = execute_strategy(
        "equal-weight", panel, {}, asset_classes=CLASSES,
        policy={**POLICY, "cash_buffer": 0.5, "target_weight": 0.3},
    )
    assert result["decisions"][0]["target_weights"] == {"AAA": 0.25, "FUND": 0.25}
    benchmark = result["benchmark"]
    assert benchmark["status"] == "COMPLETE"
    assert [mark["timestamp"] for mark in benchmark["marks"]] == [time.isoformat() for time in TIMES]
    # The benchmark bought 5 shares per asset at inception and receives $10
    # on its 10 post-split stock shares. Its cash and other holding stay flat.
    assert [mark["equity"] for mark in benchmark["marks"]] == [1000, 1000, 1010, 1010, 1010]
    assert benchmark["marks"][2]["return"] == pytest.approx(0.01)
    assert result["ledger"]["fills"][0]["quantity"] == 2
    with pytest.raises(ValueError, match="fee_bps is fixed"):
        execute_strategy("equal-weight", panel, {"fee_bps": 25}, asset_classes=CLASSES, policy=POLICY)


def test_limit_bands_partial_volume_lots_and_slippage():
    from finahinking.research.strategy_runner import simulate_paper_ledger

    panel = panel_fixture(rows={
        ("AAA", 1): {"limit_rule_version": "bounded", "limit_up_price": 100, "limit_down_price": 90},
        ("FUND", 1): {"lot_size": 2},
    })
    result = simulate_paper_ledger(
        panel, {TIMES[0]: {"AAA": 0.4, "FUND": 0.6}},
        asset_classes=CLASSES, policy={**POLICY, "slippage_bps": 100, "max_volume_participation": 0.003},
    )
    assert [order["reason"] for order in result["orders"]] == ["PRICE_LIMIT", "VOLUME_PARTICIPATION"]
    fill, = result["fills"]
    assert fill["instrument"] == "FUND"
    assert fill["quantity"] == 2
    assert fill["price"] == pytest.approx(101)
    assert fill["slippage"] == pytest.approx(2)
    assert result["marks"][1]["equity"] == pytest.approx(998)

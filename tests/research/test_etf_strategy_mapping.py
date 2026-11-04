import importlib.util

import pytest

from finahinking.p8_2.contracts import DatasetSnapshot, MarketObservation

DATES = [f"2026-01-{day:02}T20:00:00Z" for day in (2, 5, 6, 7, 8)]


def mapping():
    assert importlib.util.find_spec("finahinking.research.etf_strategy_mapping"), "ETF strategy mapping is missing"
    from finahinking.research.etf_strategy_mapping import map_etf_strategy

    return map_etf_strategy


def prices(*, drop=False):
    records = []
    for instrument, initial in (("AAA", 100), ("ETF1", 200)):
        for i, (date, change) in enumerate(zip(DATES, (0, 2, 1, 5, 3), strict=True)):
            if drop and instrument == "ETF1" and i == 2:
                continue
            close = initial * (1 + change / 100)
            records.append(MarketObservation(instrument, date, close, close, close, close,
                                             volume=100, amount=close * 100, available_at=date, suspended=False))
    return DatasetSnapshot(
        "mapping-fixture", tuple(records), provenance={
            "adjustment": "RAW",
            "units": {"price_currency": "CNY", "amount": "CNY"},
            "asset_classes": {"AAA": "EQUITY", "ETF1": "ETF"},
        },
    )


def test_etf_mapping_uses_common_window_and_asof_holdings():
    holdings = [
        {"equity": "AAA", "etf": "ETF1", "weight": 0.15, "observed_at": DATES[0], "available_at": DATES[0]},
        {"equity": "AAA", "etf": "ETF1", "weight": 0.90, "observed_at": DATES[0], "available_at": DATES[3]},
    ]
    result = mapping()(prices(), equity="AAA", etf="ETF1", start=DATES[1], end=DATES[4], holdings=holdings)
    assert result["status"] == "READY"
    assert result["return_correlation"] == pytest.approx(1)
    assert result["paired_returns"] == 3
    assert result["observations"][0]["holdings_weight"] == 0.15
    assert result["observations"][-1]["holdings_weight"] == 0.90
    assert result["observations"][0]["observed_at"].startswith("2026-01-05")
    assert result["equity_mean_amount"] == pytest.approx((102+101+105+103)*25)


def test_etf_mapping_never_bridges_missing_sessions_or_uses_future_disclosures():
    holdings = [{"equity": "AAA", "etf": "ETF1", "weight": 0.5,
                 "observed_at": DATES[0], "available_at": DATES[-1]}]
    result = mapping()(prices(drop=True), equity="AAA", etf="ETF1", start=DATES[0], end=DATES[3], holdings=holdings)
    assert result["status"] == "NEEDS_DATA"
    assert result["paired_returns"] == 1
    assert all(row["holdings_weight"] is None for row in result["observations"])
    assert result["return_correlation"] is None

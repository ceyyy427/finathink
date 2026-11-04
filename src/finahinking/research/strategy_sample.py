"""Deterministic mixed equity and ETF scenario for Phase 26 verification."""

import math

import pandas as pd

from finahinking.data.panel_contracts import PanelContract, PanelFieldAttachment
from finahinking.p8_2.contracts import DatasetSnapshot, MarketObservation

ASSET_CLASSES = {
    "EQA": "EQUITY", "EQB": "EQUITY", "EQC": "EQUITY", "EQD": "EQUITY",
    "ETFA": "ETF", "ETFB": "ETF",
}


def strategy_sample():
    """Return the frozen SAMPLE snapshot, attachment, panel contract and classifications."""
    times = pd.bdate_range("2025-01-02T16:00:00Z", periods=80)
    fields = {
        "is_session": "boolean", "in_universe": "boolean", "trade_allowed": "boolean",
        "lot_size": "shares", "settlement_days": "sessions", "split_factor": "ratio",
        "cash_dividend": "CNY_per_share", "limit_rule_version": "rule_version",
        "limit_up_price": "CNY", "limit_down_price": "CNY",
        "industry": "historical_classification", "market_cap": "CNY",
        "net_profit": "CNY", "total_assets": "CNY", "book_equity": "CNY",
        "style_beta": "ratio", "nav": "CNY_per_share", "tracking_error": "ratio",
        "event_type": "event_category", "policy_regime": "policy_category",
    }
    observations, records = [], []
    for idx, (asset, asset_class) in enumerate(ASSET_CLASSES.items()):
        for offset, time in enumerate(times):
            split = 2 if asset == "EQA" and offset >= 25 else 1
            level = 26 + idx * 3 + (.11 + .047 * idx) * offset
            level += (0.45 + 0.12 * idx) * math.sin(offset / (3 + idx * .33) + idx)
            close = round(level / split, 6)
            volume = 1_000_000 + idx * 20_000 + offset * 10
            observations.append(MarketObservation(
                asset, time, close, close * 1.005, close * 0.995, close, volume,
                amount=close * volume, source="phase26-synthetic", available_at=time, suspended=False,
            ))
            records.append({
                "instrument": asset, "timestamp": time, "available_at": time,
                "is_session": True, "in_universe": True, "trade_allowed": True,
                "lot_size": 10 if asset_class == "EQUITY" else 1,
                "settlement_days": 1 if asset_class == "EQUITY" else 0,
                "split_factor": split, "cash_dividend": 0.12 if asset == "ETFA" and offset == 30 else 0.0,
                "limit_rule_version": "sample-band-v1",
                "limit_up_price": close * 1.10, "limit_down_price": close * 0.90,
                "industry": f"sample-sector-{idx % 4}",
                "market_cap": (4 + idx) * 1e8 + offset * 10000,
                "net_profit": (1 + idx % 3) * 3e7 + (offset // 20) * 10000,
                "total_assets": (7 + idx) * 1e8, "book_equity": (3 + (idx * 3) % 5) * 1e8,
                "style_beta": [0.7, 1.35, 0.9, 1.1, 0.82, 1.2][idx],
                "nav": close / (1 + 0.005 * idx), "tracking_error": 0.006 + idx * 0.001,
                "event_type": "EARNINGS" if idx < 4 and offset in {18 + idx, 40 + idx} else "NONE",
                "policy_regime": ("RISK_ON", "NEUTRAL", "RISK_OFF")[(offset // 12) % 3],
            })
    snapshot = DatasetSnapshot(
        "phase26-strategy-sample", tuple(observations), mode="SAMPLE",
        provenance={"adjustment": "RAW", "asset_classes": ASSET_CLASSES,
                    "units": {"price_currency": "CNY", "volume_unit": "shares"}},
    )
    attachment = PanelFieldAttachment(snapshot.fingerprint, fields, records, "Frozen Phase 26 synthetic scenario v1")
    assets = list(ASSET_CLASSES)
    contract = PanelContract(
        snapshot.fingerprint, assets, list(times),
        {"close": "CNY", "volume": "shares", "amount": "CNY"},
        "phase26-strategy-sample", dict.fromkeys(assets, "CNY"), dict.fromkeys(assets, "UTC"),
        attachment.fingerprint, attachment.fingerprint, attachment.fingerprint, attachment.fingerprint,
    )
    return snapshot, (attachment,), contract, ASSET_CLASSES.copy()

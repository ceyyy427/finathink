"""Fixed synthetic panels for the research workspace paths."""
import math

import pandas as pd

from finahinking.data.panel_contracts import PanelContract, PanelFieldAttachment
from finahinking.data.research_fields import ResearchFieldStore
from finahinking.p8_2.contracts import DatasetSnapshot, MarketObservation

from .factor_library import FactorLibrary

SUPPORTED_FACTORS = frozenset({
    "lib-trend-roc", "lib-trend-ma-spread", "lib-volatility-atr", "lib-quality-roe",
    "lib-reversal-rsi", "lib-value-book-market", "lib-volatility-return-std", "lib-growth-asset-growth",
})
_NATIVE_UNITS = {"open": "CNY", "high": "CNY", "low": "CNY", "close": "CNY", "volume": "shares"}
_ASSETS = ("DEMO-A", "DEMO-B", "DEMO-C")


def create_discovery_sample(service, question):
    blueprint = service.preview_discovery({"question": question})
    target = blueprint.get("target_id")
    if blueprint.get("target_kind") == "STRATEGY":
        return service.create_strategy_sample()
    if target not in SUPPORTED_FACTORS:
        raise ValueError("此因子样例需要用户准备面板")
    definition = FactorLibrary().get(target)
    times = pd.date_range("2026-01-01", periods=120, tz="UTC")
    units = {**_NATIVE_UNITS, **definition["field_units"]}
    derived = {key: unit for key, unit in units.items() if key not in _NATIVE_UNITS}
    observations, records = [], []
    for asset_index, asset in enumerate(_ASSETS):
        for i, stamp in enumerate(times):
            close = 30 + asset_index * 7 + i * .03 + math.sin(i * 1.7 + asset_index) * (1 + asset_index * .3)
            volume = 10000 + i * 13
            observations.append(MarketObservation(asset, stamp, close-.1, close+.2, close-.2, close, volume,
                amount=close*volume, available_at=stamp, suspended=False))
            fields = {key: 1000 + (j+1)*50 + asset_index*100 + i*(asset_index+1)
                      + math.sin(i/(3+asset_index))*(j+1)*30 for j, key in enumerate(derived)}
            records.append({"instrument": asset, "timestamp": stamp, "available_at": stamp,
                "is_session": True, "in_universe": True, "trade_allowed": True,
                "lot_size": 1, "settlement_days": 0, "split_factor": 1, **fields})
    snapshot = DatasetSnapshot(f"workbench-sample-{target}", tuple(observations), mode="SAMPLE",
                               provenance={"adjustment": "RAW"})
    service.snapshots.save(snapshot)
    attachment = PanelFieldAttachment(snapshot.fingerprint, {
        "is_session": "boolean", "in_universe": "boolean", "trade_allowed": "boolean",
        "lot_size": "shares", "settlement_days": "sessions", "split_factor": "ratio", **derived,
    }, records, "Frozen workbench synthetic panel v1")
    ref = ResearchFieldStore(service.snapshots).save(attachment)
    contract = PanelContract(snapshot.fingerprint, _ASSETS, list(times), units, "workbench-sample",
                             dict.fromkeys(_ASSETS, "CNY"), dict.fromkeys(_ASSETS, "UTC"), ref, ref, ref, ref)
    return {"snapshot_ref": snapshot.fingerprint, "contract": contract.to_dict(), "attachment_refs": [ref]}

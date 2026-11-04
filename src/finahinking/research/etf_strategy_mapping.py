"""Compare a stock and ETF on common observations and dated holding disclosures."""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping

from finahinking.data.panel_contracts import CURRENCIES, instrument, panel_time

from .contracts import stable_digest
from .research_pools import _basis, _eligible

_DISCLOSURE_KEYS = {"equity", "etf", "observed_at", "available_at", "weight"}


def _disclosures(holdings, equity, etf):
    if not isinstance(holdings, (tuple, list)) or len(holdings) > 2000:
        raise ValueError("holdings must be a bounded list of disclosures")
    disclosures = {}
    for item in holdings:
        if (not isinstance(item, Mapping) or set(item) != _DISCLOSURE_KEYS
                or item["equity"] != equity or item["etf"] != etf):
            raise ValueError("holding disclosure must describe the selected equity and ETF")
        observed = panel_time(item["observed_at"])
        available = panel_time(item["available_at"])
        weight = item["weight"]
        if (available < observed or type(weight) not in (int, float)
                or not math.isfinite(weight) or not 0 <= weight <= 1):
            raise ValueError("invalid holding disclosure timestamp or weight")
        key = (observed, available)
        if key in disclosures and disclosures[key] != weight:
            raise ValueError("conflicting holding disclosures")
        disclosures[key] = weight
    return disclosures


def _holding_at(disclosures, timestamp):
    selected = max((key for key in disclosures if key[1] <= timestamp), default=None)
    if selected is None:
        return {"holdings_weight": None, "holdings_observed_at": None, "holdings_available_at": None}
    return {
        "holdings_weight": disclosures[selected],
        "holdings_observed_at": selected[0].isoformat(),
        "holdings_available_at": selected[1].isoformat(),
    }


def _pair_returns(previous, current):
    returns = tuple(mark.close / base.close - 1 for base, mark in zip(previous, current, strict=True))
    return returns if all(math.isfinite(value) for value in returns) else None


def _correlation(returns, minimum):
    if len(returns) < minimum:
        return None
    equity_returns, etf_returns = zip(*returns, strict=True)
    try:
        value = statistics.correlation(equity_returns, etf_returns)
    except (statistics.StatisticsError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def map_etf_strategy(snapshot, *, equity, etf, start, end, holdings=(), min_observations=3):
    """Describe a candidate mapping with matched liquidity and adjacent mark returns.

    Holding disclosures carry forward only after publication. Raw close changes
    exclude distributions and splits. The mapping is research evidence only.
    """
    instrument(equity)
    instrument(etf)
    first, last = panel_time(start), panel_time(end)
    if equity == etf or first >= last or type(min_observations) is not int or not 3 <= min_observations <= 252:
        raise ValueError("invalid ETF pair, observation window or minimum")
    disclosures = _disclosures(holdings, equity, etf)
    cutoff = min(last, snapshot.as_of)
    grid = {(row.instrument, row.timestamp): row for row in snapshot.observations
            if row.instrument in (equity, etf) and first <= row.timestamp <= last}
    dates = sorted({timestamp for _, timestamp in grid})
    if len(dates) > 2000:
        raise ValueError("ETF mapping window exceeds the observation limit")
    observations, returns = [], []
    previous = None
    for timestamp in dates:
        rows = (grid.get((equity, timestamp)), grid.get((etf, timestamp)))
        if not all(_eligible(row, cutoff) for row in rows):
            # Reset the interval. A gap may be a suspension or missing data.
            previous = None
            continue
        row = {
            "observed_at": timestamp.isoformat(),
            "equity_amount": rows[0].amount, "etf_amount": rows[1].amount,
            **_holding_at(disclosures, timestamp),
        }
        if previous is not None:
            pair = _pair_returns(previous, rows)
            if pair is not None:
                returns.append(pair)
                row.update(equity_return=pair[0], etf_return=pair[1])
        observations.append(row)
        previous = rows

    reasons = []
    basis = _basis(snapshot)
    classes = snapshot.provenance.get("asset_classes", {})
    if not isinstance(classes, Mapping) or classes.get(equity) != "EQUITY" or classes.get(etf) != "ETF":
        reasons.append("MISSING_ASSET_CLASS_DECLARATIONS")
    compatible_basis = basis["price_basis"] == "RAW" and basis["currency"] in CURRENCIES
    if not compatible_basis:
        reasons.append("MISSING_RAW_PRICE_BASIS_OR_CURRENCY")
    if last > snapshot.as_of:
        reasons.append("WINDOW_EXTENDS_BEYOND_SNAPSHOT")
    if len(observations) != len(dates):
        reasons.append("INCOMPLETE_COMMON_WINDOW")
    correlation = _correlation(returns, min_observations) if compatible_basis else None
    if correlation is None:
        reasons.append("INSUFFICIENT_OR_CONSTANT_PAIRED_RETURNS")
    if not observations or any(row["holdings_weight"] is None for row in observations):
        reasons.append("HOLDINGS_NOT_DISCLOSED_FOR_COMMON_WINDOW")
    amounts_known = bool(observations) and compatible_basis and all(
        row[key] is not None and row[key] > 0 for row in observations
        for key in ("equity_amount", "etf_amount")
    )
    if not amounts_known:
        reasons.append("MISSING_MATCHED_TURNOVER_AMOUNTS")
    result = {
        "status": "NEEDS_DATA" if reasons else "READY",
        "reasons": reasons, "equity": equity, "etf": etf,
        "snapshot_ref": snapshot.fingerprint, "data_mode": snapshot.mode,
        "start": first.isoformat(), "end": last.isoformat(), "as_of": cutoff.isoformat(),
        "window_observations": len(dates), "common_observations": len(observations),
        "paired_returns": len(returns), "return_correlation": correlation,
        "equity_mean_amount": (statistics.mean(row["equity_amount"] for row in observations)
                               if amounts_known else None),
        "etf_mean_amount": (statistics.mean(row["etf_amount"] for row in observations)
                            if amounts_known else None),
        "amount_currency": basis["currency"],
        "return_basis": "UNADJUSTED_CLOSE_CHANGE_EXCLUDING_ACTIONS_AND_COSTS",
        "observations": observations, "paper_only": True, "policy_updates": [],
    }
    result["fingerprint"] = stable_digest(result)
    return result

"""Portfolio diagnostics on the original, verified paper strategy account."""

from collections import defaultdict
from collections.abc import Mapping
from math import fsum, isclose, isfinite, sqrt

import numpy as np

from finahinking.data.panel_contracts import panel_time
from finahinking.quant.portfolio.allocation import validate_target_weights

from .strategy_runner import EPSILON
from .strategy_selection import StrategyDataUnavailable, adjusted_price_matrix

PORTFOLIO_DEFAULTS = {"target_exposure": 0.8, "max_single_weight": 0.4}
RISK_INTERVALS = 60


def normalize_portfolio_options(options=None):
    if options is None:
        options = {}
    if not isinstance(options, Mapping) or set(options) - PORTFOLIO_DEFAULTS.keys():
        raise ValueError("组合约束仅支持 target_exposure 与 max_single_weight")
    bounds = {**PORTFOLIO_DEFAULTS, **options}
    for field, value in bounds.items():
        if type(value) not in (int, float) or not 0 <= value <= 1:
            raise ValueError(f"{field} 必须是 0 到 1 之间的有限数值")
    return {field: float(value) for field, value in bounds.items()}


def _holdings(mark):
    result = []
    for asset, qty in sorted(mark["positions"].items()):
        if qty > EPSILON:
            price = mark["position_marks"][asset]
            result.append({"instrument": asset, "quantity": qty, "price": price,
                           "value": qty * price, "weight": qty * price / mark["equity"]})
    return result


def _attribute(ledger, opening, closing, holdings):
    """Inventory plus reference-price proceeds independently bridges gross to booked NAV."""
    start, end = opening["timestamp"], closing["timestamp"]
    fills = [fill for fill in ledger["fills"] if start < fill["timestamp"] <= end]
    actions = [row for row in ledger["corporate_actions"] if start < row["timestamp"] <= end]
    # Splits and dividend credits precede fills at the same timestamp.
    events = [(row["timestamp"], 0, row) for row in actions]
    events.extend((row["timestamp"], 1, row) for row in fills)
    quantities = dict(opening["positions"])
    flows = defaultdict(lambda: {"reference_flow": 0.0, "dividends": 0.0, "fees": 0.0, "slippage": 0.0})
    cash_flows = [opening["cash"]]
    for _, is_fill, row in sorted(events, key=lambda event: event[:2]):
        asset = row["instrument"]
        flow = flows[asset]
        if is_fill:
            direction = 1 if row["side"] == "buy" else -1
            quantities[asset] += direction * row["quantity"]
            flow["reference_flow"] -= direction * row["mark"] * row["quantity"]
            flow["fees"] += row["fees"]
            flow["slippage"] += row["slippage"]
            cash_flows.append(-direction * row["notional"] - row["fees"])
        elif row["kind"] == "SPLIT":
            quantities[asset] *= row["ratio"]
        elif row["kind"] == "DIVIDEND":
            dividend = quantities[asset] * row["per_share"]
            flow["dividends"] += dividend
            cash_flows.append(dividend)
    opening_values = {row["instrument"]: row["value"] for row in _holdings(opening)}
    closing_values = {row["instrument"]: row["value"] for row in holdings}
    by_asset = []
    for asset in sorted(set(flows) | opening_values.keys() | closing_values.keys()):
        flow = flows[asset]
        price_pnl = closing_values.get(asset, 0.0) - opening_values.get(asset, 0.0) + flow["reference_flow"]
        gross = price_pnl + flow["dividends"]
        by_asset.append({"instrument": asset, "price_pnl": price_pnl, "dividends": flow["dividends"],
                         "gross_pnl": gross, "fees": flow["fees"], "slippage": flow["slippage"],
                         "net_pnl": gross - flow["fees"] - flow["slippage"]})
    gross = fsum(row["gross_pnl"] for row in by_asset)
    fees = fsum(row["fees"] for row in by_asset)
    slippage = fsum(row["slippage"] for row in by_asset)
    base = opening["equity"]
    net = closing["equity"] - base
    residual = net - (gross - fees - slippage)
    cash_residual = closing["cash"] - fsum(cash_flows)
    equity_residual = closing["equity"] - fsum([closing["available_cash"], closing["unsettled_cash"],
                                               *closing_values.values()])
    cash_split_residual = closing["cash"] - (closing["available_cash"] + closing["unsettled_cash"])
    position_value_residual = closing["position_value"] - fsum(closing_values.values())
    qty_residuals = {asset: closing["positions"][asset] - qty for asset, qty in quantities.items()}
    tolerance = max(1e-8, max(abs(base), abs(closing["equity"])) * 1e-10)
    residuals = (residual, cash_residual, equity_residual, cash_split_residual, position_value_residual)
    reconciled = (all(abs(v) <= tolerance for v in residuals)
                  and all(isclose(quantities[a], closing["positions"][a], rel_tol=1e-10, abs_tol=1e-8)
                          for a in quantities))
    return {"status": "RECONCILED" if reconciled else "MISMATCH", "from_at": start, "to_at": end,
            "gross_pnl": gross, "dividends": fsum(row["dividends"] for row in by_asset),
            "fees": fees, "slippage": slippage, "net_pnl": net, "gross_return": gross / base,
            "net_return": net / base, "residual": residual, "cash_residual": cash_residual,
            "equity_residual": equity_residual, "cash_split_residual": cash_split_residual,
            "position_value_residual": position_value_residual,
            "quantity_residuals": qty_residuals, "by_asset": by_asset, "fill_count": len(fills),
            "turnover": fsum(row["quantity"] * row["mark"] for row in fills) / base,
            "turnover_basis": "GROSS_REFERENCE_NOTIONAL_OVER_OPENING_EQUITY"}


def _coverage(weights, missing):
    known_weight = fsum(w for name, w in weights.items() if name not in missing)
    missing_weight = fsum(weights[name] for name in missing)
    status = "COMPLETE" if not missing else "PARTIAL" if known_weight > 0 else "NEEDS_DATA"
    return {"status": status, "known_weight": known_weight, "missing_weight": missing_weight,
            "missing_instruments": sorted(missing)}


def _exposures(latest, weights):
    groups, betas = defaultdict(float), {}
    no_industry, no_beta = [], []
    for asset, weight in weights.items():
        row = latest.loc[asset]
        industry = row.get("industry")
        if isinstance(industry, str) and industry.strip() and industry.strip().upper() != "UNKNOWN":
            groups[industry] += weight
        else:
            no_industry.append(asset)
        beta = row.get("style_beta")
        if beta is not None and isfinite(beta):
            betas[asset] = float(beta) * weight
        else:
            no_beta.append(asset)
    value = fsum(betas.values())
    return (
        {**_coverage(weights, no_industry), "weights": dict(sorted(groups.items()))},
        {"style_beta": {**_coverage(weights, no_beta), "value": None if no_beta else value,
                        "known_value": value if betas or not weights else None,
                        "contributions": betas, "unit": "NAV_WEIGHT_TIMES_DECLARED_BETA"}},
    )


def _historical_returns(prices, prefix, selections, adjustment_ref):
    # A current holding may have had bad action data before it was owned.
    # Require the historical split to come from the bound adjustment attachment.
    dates = {t.isoformat(): t for t in prices.index}
    sources = {(r["observed_at"], r["instrument"]): r for r in selections
               if r["field"] == "split_factor" and r["instrument"] in prices.columns
               and r["observed_at"] in dates and r["attachment_ref"] == adjustment_ref}
    splits = prefix["split_factor"].unstack("instrument").reindex(index=prices.index, columns=prices.columns)
    late = []
    for asset in prices.columns:
        previous = None
        for stamp, ts in dates.items():
            row = sources.get((stamp, asset))
            if not row:
                prices.at[ts, asset] = np.nan
            elif previous is not None and previous != splits.at[ts, asset] and row["source_observed_at"] != stamp:
                late.append((asset, panel_time(row["source_observed_at"]), ts))
            previous = splits.at[ts, asset] if row and np.isfinite(splits.at[ts, asset]) else None
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        returns = prices.pct_change(fill_method=None).iloc[1:]
    for asset, effective, disclosed in late:
        returns.loc[effective:disclosed, asset] = np.nan
    return returns[np.isfinite(returns).all(axis=1)]


def _risk(prefix, weights, selections, adjustment_ref):
    """Euler variance of the closing holdings using aligned, split-adjusted price returns."""
    dates = prefix.index.get_level_values("observed_at").unique()[-(RISK_INTERVALS + 1):]
    possible = len(dates) - 1
    report = {"method": "sample-covariance-euler.v1", "basis": "PER_OBSERVATION_SPLIT_ADJUSTED_PRICE",
              "from_at": dates[0].isoformat(), "to_at": dates[-1].isoformat(), "ddof": 1,
              "possible_intervals": possible, "observations": 0, "variance": None,
              "volatility": None, "contributions": [], "status": "NEEDS_DATA"}
    if not weights:
        return {**report, "status": "CASH_ONLY", "variance": 0.0, "volatility": 0.0}
    assets = list(weights)
    try:
        prices = adjusted_price_matrix(prefix, assets, RISK_INTERVALS + 1)
    except StrategyDataUnavailable:
        return {**report, "reason": "MISSING_PRICES_OR_SPLITS"}
    returns = _historical_returns(prices, prefix, selections, adjustment_ref)
    report["observations"] = len(returns)
    if len(returns) < 2:
        return {**report, "reason": "INSUFFICIENT_COMMON_INTERVALS"}
    vector = np.array(list(weights.values()))
    with np.errstate(over="ignore", invalid="ignore"):
        cov = np.atleast_2d(np.cov(returns.to_numpy(), rowvar=False, ddof=1))
        contributions = vector * (cov @ vector)
        variance = float(np.var(returns.to_numpy() @ vector, ddof=1))
    if not isfinite(variance) or not np.isfinite(contributions).all():
        return {**report, "reason": "NONFINITE_COVARIANCE"}
    return {**report, "status": "COMPLETE" if len(returns) == possible else "PARTIAL",
            "variance": variance, "volatility": sqrt(variance),
            "contributions": [{"instrument": a, "variance": float(v),
                               "share": float(v / variance) if variance > 0 else None}
                              for a, v in zip(assets, contributions, strict=True)]}


def _solve(latest, universe, actual, bounds, missing):
    """Project the original weights onto a hypothetical capped, long-only portfolio."""
    target, cap = bounds["target_exposure"], bounds["max_single_weight"]
    eligible = sorted(asset for asset in universe if latest.at[asset, "execution_eligible"])
    unknown = sorted(set(universe).intersection(missing) - set(eligible))
    report = {**bounds, "basis": "HYPOTHETICAL_CLOSEST_TO_PUBLISHED_WEIGHTS",
              "eligible_instruments": eligible, "missing_eligibility": unknown,
              "weights": {}, "cash_weight": None, "reason": None}
    if target == 0:
        return {**report, "status": "SOLVED", "cash_weight": 1.0}
    capacity = cap * len(eligible)
    if target > capacity and not isclose(target, capacity, rel_tol=1e-14):
        if unknown and cap * (len(eligible) + len(unknown)) >= target:
            return {**report, "status": "NEEDS_DATA", "reason": "ELIGIBILITY_EVIDENCE_MISSING"}
        return {**report, "status": "INFEASIBLE", "reason": "TARGET_EXCEEDS_ELIGIBLE_NAME_CAPACITY"}
    # The least-squares optimum has w_i = clip(original_i + shift, 0, cap).
    # Its sum is monotone in shift. [-1, 1] spans the full feasible range.
    starting = np.array([actual.get(a, 0.0) for a in eligible])
    low, high = -1.0, 1.0
    for _ in range(64):
        shift = (low + high) / 2
        if np.clip(starting + shift, 0.0, cap).sum() < target:
            low = shift
        else:
            high = shift
    proposed = np.clip(starting + high, 0.0, cap)
    weights = validate_target_weights(dict(zip(eligible, proposed, strict=True)), max_weight=cap)
    return {**report, "status": "SOLVED", "weights": weights, "cash_weight": max(0.0, 1.0 - fsum(weights.values()))}


SHOCKS = {"MARKET_DOWN_10": {"EQUITY": -0.10, "ETF": -0.10},
          "EQUITY_DOWN_20_ETF_DOWN_05": {"EQUITY": -0.20, "ETF": -0.05}}


def _stress(classes, weights, equity):
    rows = []
    for scenario, shocks in SHOCKS.items():
        missing = [asset for asset in weights if classes.get(asset) not in shocks]
        change = None if missing else fsum(w * shocks[classes[a]] for a, w in weights.items())
        rows.append({"scenario": scenario, "status": "NEEDS_DATA" if missing else "COMPLETE",
                     "shocks": dict(shocks, CASH=0.0), "missing_instruments": missing,
                     "portfolio_return": change, "pnl": None if change is None else equity * change})
    return rows


def analyze_portfolio(source, options=None):
    """Describe the original account returned by StrategySettlementService.replay_inputs."""
    bounds = normalize_portfolio_options(options)
    result, ledger = source["result"], source["result"]["ledger"]
    report = {"status": "NEEDS_DATA", "basis": "ORIGINAL_PUBLISHED_LEDGER", "ledger_digest": source["ledger_digest"],
              "as_of": None, "currency": (ledger or {}).get("currency"), "account": None, "attribution": None,
              "risk": None, "stress": [], "industry": None, "factors": {}, "solver": None}
    if source["execution_mode"] != "PAPER_LONG_ONLY":
        return {**report, "status": "RESEARCH_ONLY", "reason": "THEORETICAL_TEMPLATE"}
    if not ledger or not ledger["marks"]:
        return {**report, "reason": result.get("reason") or "MISSING_LEDGER_MARKS"}
    opening, closing = ledger["marks"][0], ledger["marks"][-1]
    if opening["equity"] <= 0 or closing["equity"] <= 0:
        return {**report, "reason": "NONPOSITIVE_EQUITY"}
    as_of = closing["timestamp"]
    prefix = source["panel"].frame.loc[:panel_time(as_of)]
    latest = prefix.xs(panel_time(as_of), level="observed_at")
    holdings = _holdings(closing)
    weights = {row["instrument"]: row["weight"] for row in holdings}
    industry, factors = _exposures(latest, weights)
    attribution = _attribute(ledger, opening, closing, holdings)
    audit = source["panel"].audit
    risk = _risk(prefix, weights, audit["selections"], source["panel"].contract.adjustment_ref)
    missing = {row["instrument"] for row in audit["row_reasons"] if row["observed_at"] == as_of}
    solver = _solve(latest, source["benchmark_instruments"], weights, bounds, missing)
    stress = _stress(source["asset_classes"], weights, closing["equity"])
    account = {"from_at": opening["timestamp"], "as_of": as_of, "opening_equity": opening["equity"],
               "closing_equity": closing["equity"], "cash": closing["cash"], "available_cash": closing["available_cash"],
               "unsettled_cash": closing["unsettled_cash"], "position_value": closing["position_value"],
               "holdings": holdings}
    complete = (result["status"] == "COMPLETE" and ledger["status"] == "COMPLETE"
                and industry["status"] == factors["style_beta"]["status"] == "COMPLETE"
                and attribution["status"] == "RECONCILED" and risk["status"] in {"COMPLETE", "CASH_ONLY"}
                and solver["status"] == "SOLVED" and all(row["status"] == "COMPLETE" for row in stress))
    return {**report, "status": "COMPLETE" if complete else "PARTIAL", "as_of": as_of, "account": account,
            "source_status": result["status"], "ledger_failure": ledger.get("failure"), "attribution": attribution,
            "industry": industry, "factors": factors, "risk": risk, "stress": stress, "solver": solver}

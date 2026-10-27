"""Causal single-instrument research caps; no optimizer or inferred industry."""

import math
from itertools import pairwise

import pandas as pd

from .risk_runtime import empirical_cvar


def constrained_target(rows, raw_weight, policy):
    """Apply declared caps in order using only this decision's known prefix.

    The signal exposure is weight × observed momentum return, NOT a beta or
    an estimate of an external style factor. Tail risk is descriptive only.
    """
    last = rows[-1]
    closes = [item.close for item in rows[-policy["risk_lookback"]:]]
    returns = [right / left - 1 for left, right in pairwise(closes)]
    momentum = last.close / rows[-3].close - 1 if len(rows) >= 3 else 0.0
    cvar = empirical_cvar(returns, policy["cvar_confidence"]) if returns else None
    industry = last.industry or "UNKNOWN"
    industry_cap = policy["unknown_industry_cap"] if industry == "UNKNOWN" else policy["industry_cap"]
    change = abs(returns[-1]) if returns else 0.0
    price_drawdown = 1 - last.close / max(closes)
    caps = (
        ("single_instrument", policy["target_weight"], "long-only per-instrument limit"),
        ("cash_buffer", 1 - policy["cash_buffer"], "declared minimum cash"),
        ("industry", industry_cap, "unknown industry remains UNKNOWN; no classification inferred"),
        ("signal_exposure", min(1.0, policy["signal_exposure_cap"] / abs(momentum)) if momentum else 1.0, "weight times observed momentum, not market beta"),
        ("historical_cvar", min(1.0, policy["max_cvar"] / cvar) if cvar else 1.0, "fractional empirical tail loss; not a forecast"),
        ("market_stress", policy["market_loss_limit"] / abs(policy["market_shock"]), "declared market-down scenario"),
        ("gap_stress", policy["gap_loss_limit"] / abs(policy["gap_shock"]), "declared gap-down scenario"),
        ("industry_stress", policy["industry_loss_limit"] / abs(policy["industry_shock"]), "declared industry-down scenario including UNKNOWN bucket"),
        ("liquidity", min(1.0, last.volume * policy["max_volume_participation"] * last.close / policy["starting_cash"]) if last.volume >= policy["min_liquidity"] else 0.0, "known signal-bar capacity estimate; execution bar checked again"),
        ("suspension", 0.0 if last.suspended is True else 1.0, "explicit suspension veto; absent flag is not proof of future tradability"),
        ("price_jump", 0.0 if change > policy["max_price_jump"] else 1.0, "absolute latest observation return veto"),
        ("drawdown", 0.0 if price_drawdown > policy["max_drawdown"] else 1.0, "known-prefix price drawdown veto"),
    )
    weight = float(raw_weight)
    effects = []
    for name, cap, reason in caps:
        after = min(weight, max(0.0, cap))
        effects.append({"constraint": name, "limit_weight": cap, "before_weight": weight, "after_weight": after, "cash_added": weight - after, "reason": reason})
        weight = after
    scale = policy.get("signal_budget_scale", 1.0)
    if isinstance(scale, bool) or not math.isfinite(float(scale)) or not 0 <= scale <= 1:
        raise ValueError("signal budget scale must be in [0, 1]")
    if scale != 1:
        effects.append({"constraint": "learned_signal_budget", "limit_weight": weight * scale, "before_weight": weight, "after_weight": weight * scale, "cash_added": weight * (1 - scale), "reason": "explicitly approved learned signal-budget multiplier"})
        weight *= scale
    return weight, effects, {"industry": industry, "momentum": momentum, "unit_cvar": cvar, "returns": returns}


class RiskScaledStrategy:
    """Cache prefix-only weights once; the engine receives no future signal."""

    strategy_id = "risk_scaled_momentum"
    version = "v1"

    def __init__(self, rows, raw_weights, policy):
        self.weights = pd.Series([constrained_target(rows[:index + 1], float(raw_weights.iloc[index]), policy)[0] for index in range(len(rows))], index=raw_weights.index)

    def generate(self, dataset):
        return self.weights.loc[dataset.frame.index].copy()

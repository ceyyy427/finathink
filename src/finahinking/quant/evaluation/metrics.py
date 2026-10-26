"""Descriptive evaluation metrics for a P5 backtest result."""

from __future__ import annotations

import math

import pandas as pd

from ..interfaces import BacktestResult, EvaluationReport
from ..risk.metrics import (
    conditional_value_at_risk,
    downside_deviation,
    maximum_drawdown,
    value_at_risk,
)


def _series(values: tuple[tuple[str, float], ...]) -> pd.Series:
    return pd.Series({pd.Timestamp(timestamp): float(value) for timestamp, value in values}, dtype=float)


def _annualized_return(equity: pd.Series, annualization: int) -> float | None:
    if len(equity) < 2 or equity.iloc[0] <= 0 or equity.iloc[-1] <= 0:
        return None
    periods = len(equity) - 1
    return float((equity.iloc[-1] / equity.iloc[0]) ** (annualization / periods) - 1.0)


def evaluate_backtest(result: BacktestResult) -> EvaluationReport:
    equity = _series(result.equity_curve)
    returns = _series(result.returns)
    benchmark = _series(result.benchmark_returns)
    annualization = result.config.annualization
    volatility = None
    if len(returns) > 1:
        volatility = float(returns.std(ddof=1) * math.sqrt(annualization))
    mean_return = float(returns.mean()) if not returns.empty else None
    sharpe = None if volatility in (None, 0.0) or mean_return is None else float(mean_return * math.sqrt(annualization) / returns.std(ddof=1))
    downside = downside_deviation(returns, annualization)
    sortino = None if downside in (None, 0.0) or mean_return is None else float(mean_return * math.sqrt(annualization) / downside)
    total_return = None
    if len(equity) >= 2 and equity.iloc[0] != 0:
        total_return = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    benchmark_return = float((1.0 + benchmark).prod() - 1.0) if not benchmark.empty else None
    turnover = float(sum(trade.notional for trade in result.trades) / result.config.starting_cash)
    metrics = {
        "total_return": total_return,
        "annualized_return": _annualized_return(equity, annualization),
        "volatility": volatility,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": maximum_drawdown(equity),
        "value_at_risk_95": value_at_risk(returns),
        "conditional_value_at_risk_95": conditional_value_at_risk(returns),
        "turnover": turnover,
        "total_fees": float(sum(trade.fees for trade in result.trades)),
        "total_slippage": float(sum(trade.slippage for trade in result.trades)),
        "benchmark_return": benchmark_return,
        "excess_return": None if total_return is None or benchmark_return is None else total_return - benchmark_return,
        "trade_count": float(len(result.trades)),
    }
    if result.config.frequency == "observation":
        # An observation is not a calendar/session duration. Re-evaluation
        # must preserve this boundary, including from a saved BacktestResult.
        for key in ("annualized_return", "volatility", "sharpe", "sortino"):
            metrics[key] = None
    return EvaluationReport(
        result_fingerprint=result.fingerprint,
        metrics=metrics,
        benchmark=result.config.benchmark,
        limitations=(
            "descriptive historical evidence only; not investment advice.",
            "No out-of-sample, walk-forward, liquidity, survivorship, or corporate-action model is implied.",
        ),
    )

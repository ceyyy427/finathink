"""Small, deterministic, single-asset historical backtest engine."""

from __future__ import annotations

import math

import pandas as pd

from finahinking.data.models import Dataset
from finahinking.data.validation import validate_price_dataset
from finahinking.experiments.models import dataset_payload

from ..interfaces import (
    BacktestConfig,
    BacktestResult,
    Strategy,
    Trade,
    _digest,
    validate_identifier,
)


class BacktestEngine:
    """Execute target weights at the next bar with explicit costs."""

    engine_version = "p5.inhouse.0.2"

    def run(self, dataset: Dataset, strategy: Strategy, config: BacktestConfig) -> BacktestResult:
        frame = validate_price_dataset(dataset.frame, "close")
        if "suspended" in frame and any(not isinstance(value, bool) for value in frame["suspended"].tolist()):
            raise ValueError("suspended must contain explicit booleans")
        if config.max_volume_participation is not None and ("volume" not in frame or any(not math.isfinite(float(value)) or float(value) < 0 for value in frame["volume"])):
            raise ValueError("volume participation requires finite non-negative volumes")
        if not hasattr(strategy, "strategy_id") or not hasattr(strategy, "version"):
            raise ValueError("strategy must define strategy_id and version")
        strategy_id = validate_identifier(strategy.strategy_id, "strategy identifier")
        strategy_version = validate_identifier(strategy.version, "strategy version")
        generated_weights: list[float] = []
        for end in range(1, len(frame) + 1):
            as_of_dataset = Dataset(frame.iloc[:end].copy(), dataset.provenance)
            signals = strategy.generate(as_of_dataset)
            if not isinstance(signals, pd.Series):
                raise TypeError("strategy must return a pandas Series")
            try:
                signal_index = pd.DatetimeIndex(pd.to_datetime(signals.index))
            except (TypeError, ValueError) as exc:
                raise ValueError("strategy index is invalid") from exc
            if not signal_index.equals(as_of_dataset.frame.index):
                raise ValueError("strategy index must match dataset index")
            numeric = pd.to_numeric(signals, errors="coerce")
            if numeric.replace([float("inf"), float("-inf")], pd.NA).notna().sum() != len(numeric.dropna()):
                raise ValueError("strategy weights must be finite")
            generated_weights.append(float(numeric.iloc[-1]) if pd.notna(numeric.iloc[-1]) else 0.0)
        numeric = pd.Series(generated_weights, index=frame.index, dtype=float)
        if (numeric.abs() > config.max_abs_weight + 1e-12).any():
            raise ValueError("strategy weight exceeds configured limit")

        prices = frame["close"].astype(float)
        cash = config.starting_cash
        position = 0.0
        previous_equity = config.starting_cash
        equity_curve: list[tuple[str, float]] = []
        returns: list[tuple[str, float]] = []
        weights: list[tuple[str, float]] = []
        positions: list[tuple[str, float]] = []
        trades: list[Trade] = []
        for index, (timestamp, price_value) in enumerate(prices.items()):
            timestamp_text = pd.Timestamp(timestamp).isoformat()
            price = float(price_value)
            equity_before_trade = cash + position * price
            target_weight = 0.0 if index == 0 else float(numeric.iloc[index - 1])
            target_position = target_weight * equity_before_trade / price
            delta = target_position - position
            # Actual execution-bar availability can prevent or partially fill
            # yesterday's intent; it never changes yesterday's signal.
            if "suspended" in frame and bool(frame["suspended"].iloc[index]):
                delta = 0.0
            elif config.max_volume_participation is not None:
                capacity = float(frame["volume"].iloc[index]) * config.max_volume_participation
                delta = math.copysign(min(abs(delta), capacity), delta)
            if abs(delta) > 1e-12:
                side = "buy" if delta > 0 else "sell"
                quantity = abs(delta)
                direction = 1.0 if side == "buy" else -1.0
                execution_price = price * (1.0 + direction * config.slippage_bps / 10_000.0)
                notional = quantity * execution_price
                fees = notional * config.fee_bps / 10_000.0
                slippage = quantity * abs(execution_price - price)
                if side == "buy":
                    candidate_cash = cash - notional - fees
                else:
                    candidate_cash = cash + notional - fees
                if not config.allow_negative_cash and candidate_cash < -1e-9:
                    raise ValueError("negative cash is not allowed")
                cash = candidate_cash
                position += delta
                trades.append(
                    Trade(
                        timestamp=timestamp,
                        side=side,
                        quantity=quantity,
                        price=execution_price,
                        notional=notional,
                        fees=fees,
                        slippage=slippage,
                        reason=f"{strategy_id} target-weight rebalance",
                    )
                )
            equity = cash + position * price
            if not math.isfinite(equity):
                raise ValueError("equity must be finite")
            period_return = 0.0 if index == 0 or previous_equity == 0 else equity / previous_equity - 1.0
            current_weight = 0.0 if equity == 0 else position * price / equity
            equity_curve.append((timestamp_text, equity))
            returns.append((timestamp_text, period_return))
            weights.append((timestamp_text, current_weight))
            positions.append((timestamp_text, position))
            previous_equity = equity

        benchmark = prices.pct_change().fillna(0.0)
        benchmark_returns = [(pd.Timestamp(timestamp).isoformat(), float(value)) for timestamp, value in benchmark.items()]
        return BacktestResult(
            dataset_fingerprint=_digest(dataset_payload(dataset)),
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            engine_version=self.engine_version,
            config=config,
            equity_curve=tuple(equity_curve),
            returns=tuple(returns),
            weights=tuple(weights),
            positions=tuple(positions),
            trades=tuple(trades),
            benchmark_returns=tuple(benchmark_returns),
        )

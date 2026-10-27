"""Domain-only contracts for the P5 historical research runtime."""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import pandas as pd

from finahinking.data.models import Dataset
from finahinking.experiments.models import canonical_json

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _digest(value: Any) -> str:
    import hashlib

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_identifier(value: str, field: str = "identifier") -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} is invalid")
    return value


def _timestamp(value: pd.Timestamp | datetime | str) -> pd.Timestamp:
    parsed = pd.Timestamp(value)
    if pd.isna(parsed):
        raise ValueError("timestamp is invalid")
    return parsed


def _timestamp_text(value: pd.Timestamp | datetime | str) -> str:
    return _timestamp(value).isoformat()


def _finite(value: float, field: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


class Strategy(Protocol):
    """A pure target-weight generator; it cannot submit orders or fetch data."""

    strategy_id: str
    version: str

    def generate(self, dataset: Dataset) -> pd.Series:
        """Return dated target weights in the inclusive range [-1, 1]."""


@dataclass(frozen=True)
class StrategyIntent:
    timestamp: pd.Timestamp | datetime | str
    target_weight: float
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _timestamp(self.timestamp))
        weight = _finite(self.target_weight, "target weight")
        if weight < -1.0 or weight > 1.0:
            raise ValueError("target weight must be between -1 and 1")
        object.__setattr__(self, "target_weight", weight)
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "target_weight": self.target_weight,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class BacktestConfig:
    starting_cash: float
    fee_bps: float
    slippage_bps: float
    frequency: str = "daily"
    benchmark: str = "buy_and_hold"
    allow_negative_cash: bool = False
    annualization: int = 252
    max_abs_weight: float = 1.0
    max_volume_participation: float | None = None

    def __post_init__(self) -> None:
        starting_cash = _finite(self.starting_cash, "starting cash")
        fee_bps = _finite(self.fee_bps, "fee")
        slippage_bps = _finite(self.slippage_bps, "slippage")
        max_abs_weight = _finite(self.max_abs_weight, "max absolute weight")
        if starting_cash <= 0:
            raise ValueError("starting cash must be positive")
        if fee_bps < 0:
            raise ValueError("fee must be non-negative")
        if slippage_bps < 0:
            raise ValueError("slippage must be non-negative")
        if max_abs_weight <= 0 or max_abs_weight > 1:
            raise ValueError("max absolute weight must be in (0, 1]")
        if not isinstance(self.frequency, str) or not self.frequency.strip():
            raise ValueError("frequency is required")
        if not isinstance(self.benchmark, str) or not self.benchmark.strip():
            raise ValueError("benchmark is required")
        if self.benchmark != "buy_and_hold":
            raise ValueError("benchmark must be buy_and_hold until a benchmark series contract exists")
        if not isinstance(self.annualization, int) or isinstance(self.annualization, bool) or self.annualization < 1:
            raise ValueError("annualization must be a positive integer")
        object.__setattr__(self, "starting_cash", starting_cash)
        object.__setattr__(self, "fee_bps", fee_bps)
        object.__setattr__(self, "slippage_bps", slippage_bps)
        object.__setattr__(self, "max_abs_weight", max_abs_weight)
        if self.max_volume_participation is not None:
            if isinstance(self.max_volume_participation, bool):
                raise TypeError("volume participation must be numeric")
            participation = _finite(self.max_volume_participation, "volume participation")
            if not 0 < participation <= 1:
                raise ValueError("volume participation must be in (0, 1]")
            object.__setattr__(self, "max_volume_participation", participation)

    def to_dict(self) -> dict[str, Any]:
        return {
            "starting_cash": self.starting_cash,
            "fee_bps": self.fee_bps,
            "slippage_bps": self.slippage_bps,
            "frequency": self.frequency,
            "benchmark": self.benchmark,
            "allow_negative_cash": self.allow_negative_cash,
            "annualization": self.annualization,
            "max_abs_weight": self.max_abs_weight,
            **({"max_volume_participation": self.max_volume_participation} if self.max_volume_participation is not None else {}),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> BacktestConfig:
        return cls(**payload)


@dataclass(frozen=True)
class Trade:
    timestamp: pd.Timestamp | datetime | str
    side: str
    quantity: float
    price: float
    notional: float
    fees: float
    slippage: float
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _timestamp(self.timestamp))
        if self.side not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        quantity = _finite(self.quantity, "quantity")
        price = _finite(self.price, "price")
        notional = _finite(self.notional, "notional")
        fees = _finite(self.fees, "fees")
        slippage = _finite(self.slippage, "slippage")
        if quantity <= 0 or price <= 0:
            raise ValueError("quantity and price must be positive")
        if abs(notional - quantity * price) > max(1e-9, abs(notional) * 1e-9):
            raise ValueError("notional does not match quantity and price")
        if fees < 0 or slippage < 0:
            raise ValueError("fees and slippage must be non-negative")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason is required")
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "price", price)
        object.__setattr__(self, "notional", notional)
        object.__setattr__(self, "fees", fees)
        object.__setattr__(self, "slippage", slippage)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "side": self.side,
            "quantity": self.quantity,
            "price": self.price,
            "notional": self.notional,
            "fees": self.fees,
            "slippage": self.slippage,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Trade:
        return cls(**payload)


def _series_payload(values: tuple[tuple[str, float], ...]) -> list[list[Any]]:
    return [[str(timestamp), _finite(value, "series value")] for timestamp, value in values]


@dataclass(frozen=True)
class BacktestResult:
    dataset_fingerprint: str
    strategy_id: str
    strategy_version: str
    engine_version: str
    config: BacktestConfig
    equity_curve: tuple[tuple[str, float], ...]
    returns: tuple[tuple[str, float], ...]
    weights: tuple[tuple[str, float], ...]
    positions: tuple[tuple[str, float], ...]
    trades: tuple[Trade, ...]
    benchmark_returns: tuple[tuple[str, float], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.dataset_fingerprint, str) or not self.dataset_fingerprint:
            raise ValueError("dataset fingerprint is required")
        validate_identifier(self.strategy_id, "strategy identifier")
        validate_identifier(self.strategy_version, "strategy version")
        validate_identifier(self.engine_version, "engine version")
        for name in ("equity_curve", "returns", "weights", "positions", "benchmark_returns"):
            values = getattr(self, name)
            timestamps = [pd.Timestamp(timestamp) for timestamp, _ in values]
            if timestamps != sorted(timestamps):
                raise ValueError(f"{name} timestamps must be monotonic")
            _series_payload(values)
        trade_timestamps = [trade.timestamp for trade in self.trades]
        if trade_timestamps != sorted(trade_timestamps):
            raise ValueError("trade timestamps must be monotonic")

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "dataset_fingerprint": self.dataset_fingerprint,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "engine_version": self.engine_version,
            "config": self.config.to_dict(),
            "equity_curve": _series_payload(self.equity_curve),
            "returns": _series_payload(self.returns),
            "weights": _series_payload(self.weights),
            "positions": _series_payload(self.positions),
            "trades": [trade.to_dict() for trade in self.trades],
            "benchmark_returns": _series_payload(self.benchmark_returns),
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self._payload())

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "fingerprint": self.fingerprint}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> BacktestResult:
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("backtest result schema is invalid")
        required = {
            "dataset_fingerprint",
            "strategy_id",
            "strategy_version",
            "engine_version",
            "config",
            "equity_curve",
            "returns",
            "weights",
            "positions",
            "trades",
            "benchmark_returns",
            "fingerprint",
        }
        if not required.issubset(payload):
            raise ValueError("backtest result schema is invalid")
        values = {key: payload[key] for key in payload if key not in {"schema_version", "fingerprint"}}
        result = cls(
            dataset_fingerprint=values["dataset_fingerprint"],
            strategy_id=values["strategy_id"],
            strategy_version=values["strategy_version"],
            engine_version=values["engine_version"],
            config=BacktestConfig.from_dict(values["config"]),
            equity_curve=tuple((str(timestamp), float(value)) for timestamp, value in values["equity_curve"]),
            returns=tuple((str(timestamp), float(value)) for timestamp, value in values["returns"]),
            weights=tuple((str(timestamp), float(value)) for timestamp, value in values["weights"]),
            positions=tuple((str(timestamp), float(value)) for timestamp, value in values["positions"]),
            trades=tuple(Trade.from_dict(item) for item in values["trades"]),
            benchmark_returns=tuple((str(timestamp), float(value)) for timestamp, value in values["benchmark_returns"]),
        )
        if payload.get("fingerprint") != result.fingerprint:
            raise ValueError("backtest result fingerprint is invalid")
        return result


@dataclass(frozen=True, init=False)
class EvaluationReport:
    result_fingerprint: str
    _metrics: dict[str, float | None]
    benchmark: str
    limitations: tuple[str, ...]

    def __init__(
        self,
        result_fingerprint: str,
        metrics: dict[str, float | None],
        benchmark: str,
        limitations: tuple[str, ...],
    ) -> None:
        if not isinstance(metrics, dict):
            raise TypeError("metrics must be a mapping")
        object.__setattr__(self, "result_fingerprint", result_fingerprint)
        object.__setattr__(self, "_metrics", copy.deepcopy(metrics))
        object.__setattr__(self, "benchmark", benchmark)
        object.__setattr__(self, "limitations", tuple(limitations))
        self.__post_init__()

    @property
    def metrics(self) -> dict[str, float | None]:
        return copy.deepcopy(self._metrics)

    def __post_init__(self) -> None:
        if not self.result_fingerprint:
            raise ValueError("result fingerprint is required")
        if not isinstance(self.benchmark, str) or not self.benchmark.strip():
            raise ValueError("benchmark is required")
        if self.benchmark != "buy_and_hold":
            raise ValueError("benchmark must be buy_and_hold until a benchmark series contract exists")
        for key, value in self._metrics.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("metric name is required")
            if value is not None and not math.isfinite(float(value)):
                raise ValueError("metric values must be finite or None")

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "result_fingerprint": self.result_fingerprint,
            "metrics": copy.deepcopy(self._metrics),
            "benchmark": self.benchmark,
            "limitations": list(self.limitations),
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self._payload())

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "fingerprint": self.fingerprint}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> EvaluationReport:
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("evaluation report schema is invalid")
        required = {"result_fingerprint", "metrics", "benchmark", "limitations", "fingerprint"}
        if not required.issubset(payload):
            raise ValueError("evaluation report schema is invalid")
        report = cls(
            result_fingerprint=payload["result_fingerprint"],
            metrics=dict(payload["metrics"]),
            benchmark=payload["benchmark"],
            limitations=tuple(payload["limitations"]),
        )
        if payload.get("fingerprint") != report.fingerprint:
            raise ValueError("evaluation report fingerprint is invalid")
        return report

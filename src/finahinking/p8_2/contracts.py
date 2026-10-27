"""Small, immutable, JSON-safe contracts for P8.2 research workflows.

These types are deliberately independent from external research libraries.
Every provider (fixture, QMT, Qlib, or another future adapter) must normalize
into these records before the result can be persisted or shown in the UI.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from itertools import product
from typing import Any, Self

import pandas as pd

from finahinking.experiments.models import canonical_json

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_EXECUTABLE_KEYS = {
    "__builtins__",
    "__code__",
    "__file__",
    "__globals__",
    "__import__",
    "__loader__",
    "__package__",
    "__spec__",
    "callable",
    "cmd",
    "command",
    "eval",
    "exec",
    "executable",
    "expression",
    "shell",
    "source_code",
    "script",
}
_MAX_PAYLOAD_BYTES = 1_000_000
_MAX_EXPERIMENTS = 10_000


def _reject_executable_key(key: object) -> str:
    if not isinstance(key, str):
        raise TypeError("payload keys must be strings")
    normalized = key.casefold()
    if normalized in _EXECUTABLE_KEYS or normalized.startswith("__"):
        raise ValueError(f"executable payload key is not allowed: {key}")
    return key


def _json_safe(value: Any) -> Any:
    """Return a defensive JSON-safe copy, rejecting unsafe/non-finite data."""

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            result[_reject_executable_key(key)] = _json_safe(item)
        return result
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        raise TypeError("sets are not deterministic JSON payloads")
    if isinstance(value, (datetime, pd.Timestamp)):
        return _timestamp_text(value)
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("numeric payload values must be finite")
        return value
    # numpy scalar values are common in adapters, but importing numpy here is
    # unnecessary.  ``item`` is intentionally restricted to scalar results.
    item = getattr(value, "item", None)
    if callable(item):
        return _json_safe(item())
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _json_safe(to_dict())
    raise TypeError(f"value of type {type(value).__name__} is not JSON-safe")


def _payload(value: Any) -> dict[str, Any]:
    normalized = _json_safe(value)
    if not isinstance(normalized, dict):
        raise TypeError("contract payload must be a mapping")
    encoded = canonical_json(normalized)
    if len(encoded.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise ValueError("contract payload exceeds the bounded size")
    return copy.deepcopy(normalized)


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(_json_safe(value)).encode("utf-8")).hexdigest()


def _identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field_name} is invalid")
    return value


def _text(value: str, field_name: str, *, default: str | None = None) -> str:
    if value is None and default is not None:
        value = default
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value.strip()


def _finite(value: float, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be finite")
    return result


def _timestamp(value: Any) -> pd.Timestamp:
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("timestamp is invalid") from exc
    if pd.isna(parsed):
        raise ValueError("timestamp is invalid")
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed


def _timestamp_text(value: Any) -> str:
    return _timestamp(value).isoformat()


def _fingerprint_text(value: str, field_name: str = "fingerprint") -> str:
    # Providers may use a stable, human-readable fixture identity before a
    # content hash is available.  Core-generated fingerprints are always
    # SHA-256, while adapters may carry a bounded identifier such as
    # ``dataset-fixture-v1`` at the boundary.
    if not isinstance(value, str) or not value.strip() or len(value) > 128 or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field_name} must be a stable fingerprint identifier")
    return value


def _scope(value: Mapping[str, Any] | str | None) -> dict[str, Any] | str | None:
    if value is None or isinstance(value, str):
        return value
    return _payload(value)


@dataclass(frozen=True)
class MarketObservation:
    """One point-in-time OHLCV observation normalized from any provider."""

    instrument: str
    timestamp: pd.Timestamp | datetime | str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    amount: float | None = None
    source: str = "unknown"
    provider: str = "unknown"
    retrieved_at: pd.Timestamp | datetime | str | None = None
    available_at: pd.Timestamp | datetime | str | None = None
    adapter_version: str = "p8.2"
    industry: str | None = None
    suspended: bool | None = None

    def __post_init__(self) -> None:
        _identifier(self.instrument, "instrument")
        timestamp = _timestamp(self.timestamp)
        values = {
            "open": _finite(self.open, "open"),
            "high": _finite(self.high, "high"),
            "low": _finite(self.low, "low"),
            "close": _finite(self.close, "close"),
            "volume": _finite(self.volume, "volume"),
        }
        if any(values[key] <= 0 for key in ("open", "high", "low", "close")):
            raise ValueError("OHLC prices must be positive")
        if values["high"] < max(values["open"], values["close"]) or values["low"] > min(values["open"], values["close"]):
            raise ValueError("OHLC bounds are inconsistent")
        if values["volume"] < 0:
            raise ValueError("volume must be non-negative")
        amount = None if self.amount is None else _finite(self.amount, "amount")
        if amount is not None and amount < 0:
            raise ValueError("amount must be non-negative")
        source = _text(self.source, "source")
        provider = _text(self.provider, "provider")
        retrieved = None if self.retrieved_at is None else _timestamp(self.retrieved_at)
        available = None if self.available_at is None else _timestamp(self.available_at)
        adapter_version = _text(self.adapter_version, "adapter_version")
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "retrieved_at", retrieved)
        object.__setattr__(self, "available_at", available)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "adapter_version", adapter_version)
        for key, value in values.items():
            object.__setattr__(self, key, value)
        object.__setattr__(self, "amount", amount)
        if self.industry is not None:
            _identifier(self.industry, "industry")
        if self.suspended is not None and not isinstance(self.suspended, bool):
            raise TypeError("suspended must be an explicit boolean")

    @property
    def symbol(self) -> str:
        return self.instrument

    @property
    def time(self) -> pd.Timestamp:
        """Alias matching chart payload terminology."""

        return self.timestamp

    @property
    def data_source(self) -> str:
        return self.source

    @property
    def availability_time(self) -> pd.Timestamp | None:
        return self.available_at

    @property
    def fingerprint(self) -> str:
        """Content address of this normalized observation."""

        return _digest(self.to_dict())

    def available_at_time(self, as_of: Any) -> bool:
        try:
            return self.available_at is not None and self.timestamp <= _timestamp(as_of) and self.available_at <= _timestamp(as_of)
        except (TypeError, ValueError):
            return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "instrument": self.instrument,
            "timestamp": self.timestamp.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "amount": self.amount,
            "source": self.source,
            "provider": self.provider,
            "retrieved_at": self.retrieved_at.isoformat() if self.retrieved_at is not None else None,
            "available_at": self.available_at.isoformat() if self.available_at is not None else None,
            "adapter_version": self.adapter_version,
            **({"industry": self.industry} if self.industry is not None else {}),
            **({"suspended": self.suspended} if self.suspended is not None else {}),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Self:
        if not isinstance(payload, Mapping):
            raise TypeError("market observation schema is invalid")
        values = dict(payload)
        values.pop("schema_version", None)
        return cls(**values)


@dataclass(frozen=True)
class DatasetSnapshot:
    """A bounded, fingerprinted collection of point-in-time observations."""

    dataset_id: str
    observations: tuple[MarketObservation, ...]
    mode: str = "SAMPLE"
    as_of: pd.Timestamp | datetime | str | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)
    limitations: tuple[str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        _identifier(self.dataset_id, "dataset_id")
        if self.schema_version != 1:
            raise ValueError("unsupported dataset snapshot schema")
        records = tuple(self.observations)
        if not records:
            raise ValueError("dataset snapshot cannot be empty")
        if any(not isinstance(item, MarketObservation) for item in records):
            raise TypeError("observations must be MarketObservation records")
        timestamps = [(item.instrument, item.timestamp) for item in records]
        if timestamps != sorted(timestamps, key=lambda value: (value[0], value[1])):
            raise ValueError("observations must be ordered by instrument and timestamp")
        if len({(item.instrument, item.timestamp) for item in records}) != len(records):
            raise ValueError("dataset snapshot contains duplicate observations")
        mode = _text(self.mode, "mode").upper()
        if mode not in {"SAMPLE", "SYNTHETIC", "CAPTURED", "LIVE", "PAPER"}:
            raise ValueError("dataset mode is invalid")
        as_of = max(item.available_at if item.available_at is not None else item.timestamp for item in records) if self.as_of is None else _timestamp(self.as_of)
        provenance = _payload(self.provenance)
        limitations = tuple(_text(item, "limitation") for item in self.limitations)
        object.__setattr__(self, "observations", records)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "provenance", provenance)
        object.__setattr__(self, "limitations", limitations)

    @property
    def instruments(self) -> tuple[str, ...]:
        return tuple(sorted({item.instrument for item in self.observations}))

    @property
    def data_source(self) -> str:
        return str(self.provenance.get("source", self.provenance.get("provider", "unknown")))

    @property
    def id(self) -> str:
        """Stable alias used by view-models and provider adapters."""

        return self.dataset_id

    @property
    def fingerprint(self) -> str:
        payload = self.to_dict(include_fingerprint=False)
        return _digest(payload)

    def pit_available(self, as_of: Any | None = None) -> bool:
        boundary = self.as_of if as_of is None else _timestamp(as_of)
        return all(item.available_at_time(boundary) for item in self.observations)

    def available_observations(self, as_of: Any | None = None) -> tuple[MarketObservation, ...]:
        boundary = self.as_of if as_of is None else _timestamp(as_of)
        return tuple(item for item in self.observations if item.available_at_time(boundary))

    def to_dict(self, *, include_fingerprint: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "observations": [item.to_dict() for item in self.observations],
            "mode": self.mode,
            "as_of": self.as_of.isoformat(),
            "provenance": copy.deepcopy(dict(self.provenance)),
            "limitations": list(self.limitations),
        }
        if include_fingerprint:
            payload["fingerprint"] = self.fingerprint
        return payload

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Self:
        if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
            raise ValueError("dataset snapshot schema is invalid")
        values = dict(payload)
        claimed = values.pop("fingerprint", None)
        observations = tuple(MarketObservation.from_dict(item) for item in values.pop("observations", ()))
        snapshot = cls(observations=observations, **values)
        if claimed != snapshot.fingerprint:
            raise ValueError("dataset snapshot fingerprint is invalid")
        return snapshot

    @classmethod
    def from_json(cls, encoded: str) -> Self:
        try:
            return cls.from_dict(json.loads(encoded))
        except (TypeError, json.JSONDecodeError, KeyError) as exc:
            raise ValueError("dataset snapshot schema is invalid") from exc


@dataclass(frozen=True)
class ResearchPoint:
    point_id: str
    instrument: str
    timestamp: pd.Timestamp | datetime | str
    value: float
    dataset_fingerprint: str
    source: str = "normalized"
    available_at: pd.Timestamp | datetime | str | None = None
    features: Mapping[str, float] = field(default_factory=dict)
    events: tuple[str, ...] = ()
    signal: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.point_id, "point_id")
        _identifier(self.instrument, "instrument")
        timestamp = _timestamp(self.timestamp)
        value = _finite(self.value, "value")
        fingerprint = _fingerprint_text(self.dataset_fingerprint, "dataset_fingerprint")
        source = _text(self.source, "source")
        available = timestamp if self.available_at is None else _timestamp(self.available_at)
        features = _payload(self.features)
        normalized_features: dict[str, float] = {}
        for key, item in features.items():
            normalized_features[key] = _finite(item, f"feature {key}")
        events = tuple(_text(item, "event") for item in self.events)
        if self.signal is not None:
            _text(self.signal, "signal")
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "dataset_fingerprint", fingerprint)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "available_at", available)
        object.__setattr__(self, "features", normalized_features)
        object.__setattr__(self, "events", events)

    @property
    def fingerprint(self) -> str:
        return _digest(self.to_dict(include_fingerprint=False))

    def to_dict(self, *, include_fingerprint: bool = True) -> dict[str, Any]:
        payload = {
            "schema_version": 1,
            "point_id": self.point_id,
            "instrument": self.instrument,
            "timestamp": self.timestamp.isoformat(),
            "value": self.value,
            "dataset_fingerprint": self.dataset_fingerprint,
            "source": self.source,
            "available_at": self.available_at.isoformat(),
            "features": copy.deepcopy(dict(self.features)),
            "events": list(self.events),
            "signal": self.signal,
        }
        if include_fingerprint:
            payload["fingerprint"] = self.fingerprint
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Self:
        values = dict(payload)
        claimed = values.pop("fingerprint", None)
        values.pop("schema_version", None)
        point = cls(**values)
        if claimed is not None and claimed != point.fingerprint:
            raise ValueError("research point fingerprint is invalid")
        return point


@dataclass(frozen=True)
class FeatureObservation:
    feature_name: str
    instrument: str
    timestamp: pd.Timestamp | datetime | str
    value: float
    dataset_fingerprint: str
    source: str = "normalized"
    available_at: pd.Timestamp | datetime | str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _identifier(self.feature_name, "feature_name")
        _identifier(self.instrument, "instrument")
        timestamp = _timestamp(self.timestamp)
        available = timestamp if self.available_at is None else _timestamp(self.available_at)
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "available_at", available)
        object.__setattr__(self, "value", _finite(self.value, "feature value"))
        object.__setattr__(self, "dataset_fingerprint", _fingerprint_text(self.dataset_fingerprint, "dataset_fingerprint"))
        object.__setattr__(self, "source", _text(self.source, "source"))
        object.__setattr__(self, "metadata", _payload(self.metadata))

    @property
    def name(self) -> str:
        return self.feature_name

    @property
    def time(self) -> pd.Timestamp:
        return self.timestamp

    @property
    def fingerprint(self) -> str:
        return _digest(self.to_dict(include_fingerprint=False))

    def to_dict(self, *, include_fingerprint: bool = True) -> dict[str, Any]:
        payload = {
            "schema_version": 1,
            "feature_name": self.feature_name,
            "instrument": self.instrument,
            "timestamp": self.timestamp.isoformat(),
            "value": self.value,
            "dataset_fingerprint": self.dataset_fingerprint,
            "source": self.source,
            "available_at": self.available_at.isoformat(),
            "metadata": copy.deepcopy(dict(self.metadata)),
        }
        if include_fingerprint:
            payload["fingerprint"] = self.fingerprint
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Self:
        values = dict(payload)
        claimed = values.pop("fingerprint", None)
        values.pop("schema_version", None)
        result = cls(**values)
        if claimed is not None and claimed != result.fingerprint:
            raise ValueError("feature observation fingerprint is invalid")
        return result


@dataclass(frozen=True)
class ParameterSweepSpecification:
    strategy_version: str = "unversioned"
    parameters: Mapping[str, Any] = field(default_factory=dict)
    parameter_ranges: Mapping[str, Sequence[Any]] = field(default_factory=dict)
    experiment_count: int | None = None
    train_scope: Mapping[str, Any] | str | None = None
    validation_scope: Mapping[str, Any] | str | None = None
    oos_scope: Mapping[str, Any] | str | None = None
    selection_policy: str = "pre_specified"
    max_experiments: int = _MAX_EXPERIMENTS

    def __post_init__(self) -> None:
        _text(self.strategy_version, "strategy_version")
        parameters = _payload(self.parameters)
        ranges_raw = _payload(self.parameter_ranges)
        ranges: dict[str, tuple[Any, ...]] = {}
        for key, values in ranges_raw.items():
            _identifier(key, "parameter name")
            if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
                raise TypeError("parameter ranges must be sequences")
            normalized = tuple(_json_safe(item) for item in values)
            if not normalized:
                raise ValueError(f"parameter range {key} cannot be empty")
            if len({canonical_json(item) for item in normalized}) != len(normalized):
                raise ValueError(f"parameter range {key} contains duplicates")
            ranges[key] = normalized
        count = 1
        for values in ranges.values():
            count *= len(values)
        max_experiments = int(self.max_experiments)
        if max_experiments < 1 or count > max_experiments:
            raise ValueError("experiment count exceeds the bounded sweep limit")
        if self.experiment_count is not None and int(self.experiment_count) != count:
            raise ValueError("experiment_count must match the parameter grid")
        selection = _text(self.selection_policy, "selection_policy")
        object.__setattr__(self, "parameters", parameters)
        object.__setattr__(self, "parameter_ranges", ranges)
        object.__setattr__(self, "experiment_count", count)
        object.__setattr__(self, "selection_policy", selection)
        object.__setattr__(self, "max_experiments", max_experiments)
        for field_name in ("train_scope", "validation_scope", "oos_scope"):
            object.__setattr__(self, field_name, _scope(getattr(self, field_name)))

    def enumerate_parameters(self) -> tuple[dict[str, Any], ...]:
        keys = tuple(sorted(self.parameter_ranges))
        if not keys:
            return (copy.deepcopy(dict(self.parameters)),)
        records: list[dict[str, Any]] = []
        for values in product(*(self.parameter_ranges[key] for key in keys)):
            item = copy.deepcopy(dict(self.parameters))
            item.update(dict(zip(keys, values, strict=True)))
            records.append(item)
        return tuple(records)

    @property
    def fingerprint(self) -> str:
        return _digest(self.to_dict(include_fingerprint=False))

    def to_dict(self, *, include_fingerprint: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "strategy_version": self.strategy_version,
            "parameters": copy.deepcopy(dict(self.parameters)),
            "parameter_ranges": copy.deepcopy(dict(self.parameter_ranges)),
            "experiment_count": self.experiment_count,
            "train_scope": copy.deepcopy(self.train_scope),
            "validation_scope": copy.deepcopy(self.validation_scope),
            "oos_scope": copy.deepcopy(self.oos_scope),
            "selection_policy": self.selection_policy,
            "max_experiments": self.max_experiments,
        }
        if include_fingerprint:
            payload["fingerprint"] = self.fingerprint
        return payload

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Self:
        values = dict(payload)
        claimed = values.pop("fingerprint", None)
        values.pop("schema_version", None)
        spec = cls(**values)
        if claimed is not None and claimed != spec.fingerprint:
            raise ValueError("parameter sweep fingerprint is invalid")
        return spec

    @classmethod
    def from_json(cls, encoded: str) -> Self:
        try:
            return cls.from_dict(json.loads(encoded))
        except (TypeError, json.JSONDecodeError, KeyError) as exc:
            raise ValueError("parameter sweep schema is invalid") from exc


@dataclass(frozen=True)
class SweepResult:
    specification_fingerprint: str
    experiments: tuple[Mapping[str, Any], ...]
    warnings: tuple[str, ...] = ()
    multiple_testing: Mapping[str, Any] = field(default_factory=dict)
    robust_regions: tuple[Mapping[str, Any], ...] = ()
    unstable_regions: tuple[Mapping[str, Any], ...] = ()
    oos_comparison: tuple[Mapping[str, Any], ...] = ()
    engine: str = "finathink-deterministic-sweep"
    status: str = "COMPLETE"
    fallback_used: bool = False
    fallback_reason: str | None = None

    def __post_init__(self) -> None:
        _fingerprint_text(self.specification_fingerprint, "specification_fingerprint")
        normalized = tuple(_payload(item) for item in self.experiments)
        if not normalized:
            raise ValueError("sweep result must contain experiments")
        object.__setattr__(self, "experiments", normalized)
        object.__setattr__(self, "warnings", tuple(_text(item, "warning") for item in self.warnings))
        object.__setattr__(self, "multiple_testing", _payload(self.multiple_testing))
        object.__setattr__(self, "robust_regions", tuple(_payload(item) for item in self.robust_regions))
        object.__setattr__(self, "unstable_regions", tuple(_payload(item) for item in self.unstable_regions))
        object.__setattr__(self, "oos_comparison", tuple(_payload(item) for item in self.oos_comparison))
        object.__setattr__(self, "engine", _text(self.engine, "engine"))
        object.__setattr__(self, "status", _text(self.status, "status").upper())
        if not isinstance(self.fallback_used, bool):
            raise TypeError("fallback_used must be boolean")
        if self.fallback_reason is not None:
            object.__setattr__(self, "fallback_reason", _text(self.fallback_reason, "fallback_reason"))

    @property
    def experiment_count(self) -> int:
        return len(self.experiments)

    @property
    def results(self) -> tuple[Mapping[str, Any], ...]:
        return self.experiments

    @property
    def fingerprint(self) -> str:
        return _digest(self.to_dict(include_fingerprint=False))

    def to_dict(self, *, include_fingerprint: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "specification_fingerprint": self.specification_fingerprint,
            "experiment_count": self.experiment_count,
            "experiments": [copy.deepcopy(dict(item)) for item in self.experiments],
            "warnings": list(self.warnings),
            "multiple_testing": copy.deepcopy(dict(self.multiple_testing)),
            "robust_regions": [copy.deepcopy(dict(item)) for item in self.robust_regions],
            "unstable_regions": [copy.deepcopy(dict(item)) for item in self.unstable_regions],
            "oos_comparison": [copy.deepcopy(dict(item)) for item in self.oos_comparison],
            "engine": self.engine,
            "status": self.status,
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
        }
        if include_fingerprint:
            payload["fingerprint"] = self.fingerprint
        return payload

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_json(cls, encoded: str) -> Self:
        try:
            return cls.from_dict(json.loads(encoded))
        except (TypeError, json.JSONDecodeError, KeyError) as exc:
            raise ValueError("sweep result schema is invalid") from exc

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Self:
        values = dict(payload)
        claimed = values.pop("fingerprint", None)
        values.pop("schema_version", None)
        values.pop("experiment_count", None)
        result = cls(**values)
        if claimed is not None and claimed != result.fingerprint:
            raise ValueError("sweep result fingerprint is invalid")
        return result


@dataclass(frozen=True)
class MLDatasetSplit:
    train: Mapping[str, Any] | str
    validation: Mapping[str, Any] | str | None = None
    test: Mapping[str, Any] | str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "train", _scope(self.train))
        object.__setattr__(self, "validation", _scope(self.validation))
        object.__setattr__(self, "test", _scope(self.test))
        if self.train is None:
            raise ValueError("ML train scope is required")

    def to_dict(self) -> dict[str, Any]:
        return {"train": copy.deepcopy(self.train), "validation": copy.deepcopy(self.validation), "test": copy.deepcopy(self.test)}


@dataclass(frozen=True)
class MLModelSpecification:
    name: str = "finathink-baseline"
    family: str = "deterministic-baseline"
    hyperparameters: Mapping[str, Any] = field(default_factory=dict)
    seed: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "model name"))
        object.__setattr__(self, "family", _text(self.family, "model family"))
        object.__setattr__(self, "hyperparameters", _payload(self.hyperparameters))
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise TypeError("model seed must be an integer")

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "family": self.family, "hyperparameters": copy.deepcopy(dict(self.hyperparameters)), "seed": self.seed}


@dataclass(frozen=True)
class MLResearchSpecification:
    research_id: str
    dataset_fingerprint: str
    target: str
    features: tuple[str, ...]
    train_scope: Mapping[str, Any] | str
    validation_scope: Mapping[str, Any] | str | None = None
    test_scope: Mapping[str, Any] | str | None = None
    model: MLModelSpecification | Mapping[str, Any] | None = None
    model_specification: MLModelSpecification | Mapping[str, Any] | None = None
    seed: int = 0
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.research_id, "research_id")
        _fingerprint_text(self.dataset_fingerprint, "dataset_fingerprint")
        _identifier(self.target, "target")
        features = tuple(_identifier(item, "feature") for item in self.features)
        if not features:
            raise ValueError("at least one feature is required")
        resolved_model = self.model_specification if self.model_specification is not None else self.model
        if resolved_model is None:
            resolved_model = MLModelSpecification()
        elif isinstance(resolved_model, Mapping):
            resolved_model = MLModelSpecification(**dict(resolved_model))
        if not isinstance(resolved_model, MLModelSpecification):
            raise TypeError("model specification is invalid")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise TypeError("ML seed must be an integer")
        object.__setattr__(self, "features", features)
        object.__setattr__(self, "train_scope", _scope(self.train_scope))
        object.__setattr__(self, "validation_scope", _scope(self.validation_scope))
        object.__setattr__(self, "test_scope", _scope(self.test_scope))
        object.__setattr__(self, "model", resolved_model)
        object.__setattr__(self, "model_specification", resolved_model)
        object.__setattr__(self, "limitations", tuple(_text(item, "limitation") for item in self.limitations))

    @property
    def fingerprint(self) -> str:
        return _digest(self.to_dict(include_fingerprint=False))

    def to_dict(self, *, include_fingerprint: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "research_id": self.research_id,
            "dataset_fingerprint": self.dataset_fingerprint,
            "target": self.target,
            "features": list(self.features),
            "train_scope": copy.deepcopy(self.train_scope),
            "validation_scope": copy.deepcopy(self.validation_scope),
            "test_scope": copy.deepcopy(self.test_scope),
            "model": self.model.to_dict(),
            "seed": self.seed,
            "limitations": list(self.limitations),
        }
        if include_fingerprint:
            payload["fingerprint"] = self.fingerprint
        return payload

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Self:
        values = dict(payload)
        claimed = values.pop("fingerprint", None)
        values.pop("schema_version", None)
        values["model_specification"] = values.pop("model", values.get("model_specification"))
        result = cls(**values)
        if claimed is not None and claimed != result.fingerprint:
            raise ValueError("ML research specification fingerprint is invalid")
        return result

    @classmethod
    def from_json(cls, encoded: str) -> Self:
        try:
            return cls.from_dict(json.loads(encoded))
        except (TypeError, json.JSONDecodeError, KeyError) as exc:
            raise ValueError("ML research specification schema is invalid") from exc


@dataclass(frozen=True)
class ModelMetric:
    name: str
    value: float
    split: str
    interpretation: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _identifier(self.name, "metric name"))
        object.__setattr__(self, "split", _identifier(self.split, "metric split"))
        object.__setattr__(self, "value", _finite(self.value, "metric value"))
        object.__setattr__(self, "interpretation", str(self.interpretation))

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "value": self.value, "split": self.split, "interpretation": self.interpretation}


@dataclass(frozen=True)
class MLResearchResult:
    specification_fingerprint: str
    dataset_fingerprint: str
    status: str
    engine: str
    metrics: tuple[ModelMetric | Mapping[str, Any], ...] = ()
    predictions: tuple[Mapping[str, Any], ...] = ()
    feature_importance: Mapping[str, float] = field(default_factory=dict)
    limitations: tuple[str, ...] = ()
    fallback_used: bool = False
    model_artifact: Mapping[str, Any] | None = None
    fallback_reason: str | None = None

    def __post_init__(self) -> None:
        _fingerprint_text(self.specification_fingerprint, "specification_fingerprint")
        _fingerprint_text(self.dataset_fingerprint, "dataset_fingerprint")
        object.__setattr__(self, "status", _text(self.status, "status").upper())
        object.__setattr__(self, "engine", _text(self.engine, "engine"))
        metric_values = tuple(item if isinstance(item, ModelMetric) else ModelMetric(**dict(item)) for item in self.metrics)
        object.__setattr__(self, "metrics", metric_values)
        object.__setattr__(self, "predictions", tuple(_payload(item) for item in self.predictions))
        importance = _payload(self.feature_importance)
        object.__setattr__(self, "feature_importance", {key: _finite(value, f"feature importance {key}") for key, value in importance.items()})
        object.__setattr__(self, "limitations", tuple(_text(item, "limitation") for item in self.limitations))
        if self.model_artifact is not None:
            object.__setattr__(self, "model_artifact", _payload(self.model_artifact))
        if self.fallback_reason is not None:
            object.__setattr__(self, "fallback_reason", _text(self.fallback_reason, "fallback_reason"))

    @property
    def fingerprint(self) -> str:
        return _digest(self.to_dict(include_fingerprint=False))

    def to_dict(self, *, include_fingerprint: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "specification_fingerprint": self.specification_fingerprint,
            "dataset_fingerprint": self.dataset_fingerprint,
            "status": self.status,
            "engine": self.engine,
            "metrics": [item.to_dict() for item in self.metrics],
            "predictions": [copy.deepcopy(dict(item)) for item in self.predictions],
            "feature_importance": copy.deepcopy(dict(self.feature_importance)),
            "limitations": list(self.limitations),
            "fallback_used": self.fallback_used,
            "model_artifact": copy.deepcopy(self.model_artifact),
            "fallback_reason": self.fallback_reason,
        }
        if include_fingerprint:
            payload["fingerprint"] = self.fingerprint
        return payload

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Self:
        values = dict(payload)
        claimed = values.pop("fingerprint", None)
        values.pop("schema_version", None)
        result = cls(
            specification_fingerprint=values["specification_fingerprint"],
            dataset_fingerprint=values["dataset_fingerprint"],
            status=values["status"],
            engine=values["engine"],
            metrics=tuple(values.get("metrics", ())),
            predictions=tuple(values.get("predictions", ())),
            feature_importance=values.get("feature_importance", {}),
            limitations=tuple(values.get("limitations", ())),
            fallback_used=bool(values.get("fallback_used", False)),
            model_artifact=values.get("model_artifact"),
            fallback_reason=values.get("fallback_reason"),
        )
        if claimed is not None and claimed != result.fingerprint:
            raise ValueError("ML research result fingerprint is invalid")
        return result

    @classmethod
    def from_json(cls, encoded: str) -> Self:
        try:
            return cls.from_dict(json.loads(encoded))
        except (TypeError, json.JSONDecodeError, KeyError) as exc:
            raise ValueError("ML research result schema is invalid") from exc


__all__ = [
    "DatasetSnapshot",
    "FeatureObservation",
    "MLDatasetSplit",
    "MLModelSpecification",
    "MLResearchResult",
    "MLResearchSpecification",
    "MarketObservation",
    "ModelMetric",
    "ParameterSweepSpecification",
    "ResearchPoint",
    "SweepResult",
]

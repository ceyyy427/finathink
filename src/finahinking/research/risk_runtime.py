"""Deterministic risk gate for the paper-only research runtime.

The gate accepts only a Finathink-normalized snapshot and deterministic factor
metrics.  It deliberately has no provider, network, broker, or execution
dependency; a missing or ambiguous gate is a blocking result.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, is_dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from .contracts import _public_identifier, _unsafe_public_text, stable_digest

_FORBIDDEN = {"broker", "order", "orders", "cancel", "account", "live", "endpoint", "credential", "secret"}
_PIT_PASS = {"AVAILABLE", "VALID", "VERIFIED", "KNOWN", "READY", "TRUE", "OK"}
_PIT_FAIL = {"UNKNOWN", "UNAVAILABLE", "INVALID", "FALSE", "STALE", "MISSING", "UNVERIFIED"}
_LIQUIDITY_BUCKETS = {"HIGH", "MEDIUM", "LOW"}
_PROMPT_TEXT = re.compile(r"\b(?:prompt|system\s+message|developer\s+message)\b", re.IGNORECASE)
_UNSAFE_NESTED_KEY = re.compile(r"(?:api[-_]?key|secret|token|password|credential|authorization|endpoint|(?:^|[_-])path(?:$|[_-]))", re.IGNORECASE)


def _safe(value: Any, path: str = "value") -> Any:
    if callable(value):
        raise TypeError(f"{path} contains forbidden executable or live input")
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, child in value.items():
            name = str(key)
            lowered = name.casefold()
            if any(token in lowered for token in _FORBIDDEN):
                raise ValueError(f"{path} contains forbidden paper-only field")
            result[name] = _safe(child, f"{path}.{name}")
        return result
    if isinstance(value, (list, tuple)):
        return [_safe(item, f"{path}[]") for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must be finite")
        return value
    item = getattr(value, "item", None)
    if callable(item):
        return _safe(item(), path)
    raise TypeError(f"{path} must be normalized JSON data")


def _payload(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return _safe(value, label)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        data = to_dict()
        if not isinstance(data, Mapping):
            raise TypeError(f"{label} must normalize to a mapping")
        return _safe(data, label)
    if is_dataclass(value):
        # Dataclasses from Finathink contracts expose only data fields.  Avoid
        # accepting arbitrary objects with executable attributes.
        from dataclasses import asdict

        return _safe(asdict(value), label)
    raise TypeError(f"{label} must be a normalized mapping")


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{label} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _known_number(value: Any, label: str) -> float | None:
    if value is None or (isinstance(value, str) and value.strip().upper() in {"UNKNOWN", "UNAVAILABLE", "MISSING", "N/A", "NONE"}):
        return None
    return _number(value, label)


def _nested(data: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in data:
            return data[name]
    return None


def _pit_status(snapshot: Mapping[str, Any]) -> str:
    raw = _nested(snapshot, "pit_status", "pit", "pit_state", "availability_status")
    if raw is None:
        semantics = _nested(snapshot, "pit_semantics", "point_in_time")
        if isinstance(semantics, bool):
            return "AVAILABLE" if semantics else "UNKNOWN"
        raw = semantics
    explicit: str | None = None
    if isinstance(raw, str):
        value = raw.strip().upper().replace("-", "_")
        if value in _PIT_PASS or value in _PIT_FAIL:
            explicit = value
        elif "UNKNOWN" in value or "UNAVAILABLE" in value:
            explicit = "UNKNOWN"
        elif "AVAILABLE" in value or "VERIFIED" in value:
            explicit = "AVAILABLE"
    if isinstance(raw, Mapping):
        nested_status = _nested(raw, "status", "state", "value")
        if nested_status is not None:
            explicit = _pit_status({"pit_status": nested_status})
    observations = snapshot.get("observations", snapshot.get("records", ()))
    as_of = snapshot.get("as_of")
    if as_of is not None and observations is not None:
        try:
            records = _observations(snapshot)
        except (TypeError, ValueError):
            return "UNKNOWN"
        if records:
            for item in records:
                available_at = item.get("available_at", item.get("timestamp"))
                if available_at is None or str(available_at) > str(as_of):
                    return "UNKNOWN"
            if explicit in _PIT_FAIL:
                return explicit
            return explicit if explicit in _PIT_PASS else "AVAILABLE"
    if explicit is not None:
        return explicit
    return "UNKNOWN"


def _observations(snapshot: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw = _nested(snapshot, "observations", "records", "data")
    if raw is None:
        return ()
    if isinstance(raw, Mapping):
        raw = tuple(raw.values())
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise TypeError("snapshot observations must be a sequence")
    normalized: list[Mapping[str, Any]] = []
    for item in raw:
        if isinstance(item, Mapping):
            normalized.append(item)
        else:
            to_dict = getattr(item, "to_dict", None)
            if not callable(to_dict) or not isinstance(to_dict(), Mapping):
                raise TypeError("snapshot observations must be normalized records")
            normalized.append(to_dict())
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class RiskReviewResult:
    """Immutable, typed result of all deterministic risk gates."""

    status: str = "BLOCKED"
    passed: bool = False
    gates: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    metrics: Mapping[str, float | str] = field(default_factory=dict)
    snapshot_digest: str = ""
    factor_digest: str = ""
    policy_version: str = "risk.v1"
    limits: Mapping[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    exposure_digest: str = ""
    stress_digest: str = ""
    paper_only: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.passed, bool):
            raise TypeError("passed must be boolean")
        status = str(self.status).strip().upper()
        if status not in {"PASSED", "BLOCKED", "FAILED"}:
            raise ValueError("risk status is invalid")
        if self.passed and status != "PASSED":
            object.__setattr__(self, "status", "PASSED")
        elif not self.passed and status == "PASSED":
            object.__setattr__(self, "status", "BLOCKED")
        else:
            object.__setattr__(self, "status", status)
        object.__setattr__(self, "gates", tuple(str(item) for item in self.gates))
        object.__setattr__(self, "blocking_reasons", tuple(str(item) for item in self.blocking_reasons))
        object.__setattr__(self, "metrics", dict(self.metrics))
        object.__setattr__(self, "limits", dict(self.limits))
        object.__setattr__(self, "evidence_refs", tuple(dict.fromkeys(str(item) for item in self.evidence_refs if str(item).strip())))
        object.__setattr__(self, "limitations", tuple(str(item) for item in self.limitations if str(item).strip()))
        object.__setattr__(self, "exposure_digest", str(self.exposure_digest))
        object.__setattr__(self, "stress_digest", str(self.stress_digest))
        if self.paper_only is not True:
            raise ValueError("risk review must be paper-only")

    @property
    def blocking(self) -> bool:
        return not self.passed

    @property
    def fingerprint(self) -> str:
        return stable_digest(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "passed": self.passed,
            "gates": list(self.gates),
            "blocking_reasons": list(self.blocking_reasons),
            "metrics": dict(self.metrics),
            "snapshot_digest": self.snapshot_digest,
            "factor_digest": self.factor_digest,
            "policy_version": self.policy_version,
            "limits": dict(self.limits),
            "evidence_refs": list(self.evidence_refs),
            "limitations": list(self.limitations),
            "exposure_digest": self.exposure_digest,
            "stress_digest": self.stress_digest,
            "paper_only": self.paper_only,
            "fingerprint": self.fingerprint,
        }


def _report_texts(value: Any, field_name: str) -> tuple[str, ...]:
    raw = value.get(field_name, ()) if isinstance(value, Mapping) else ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        return ()
    if any(not isinstance(item, str) or _unsafe_public_text(item) or _PROMPT_TEXT.search(item) for item in raw):
        return ()
    return tuple(dict.fromkeys(item.strip() for item in raw if item.strip()))


def _safe_text_tuple(values: Any, field_name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError(f"{field_name} must be a sequence of public text")
    if any(not isinstance(item, str) or _unsafe_public_text(item) or _PROMPT_TEXT.search(item) for item in values):
        raise ValueError(f"{field_name} contains unsafe public text")
    return tuple(dict.fromkeys(item.strip() for item in values if item.strip()))


def _safe_nested_scenario(value: Any, path: str = "scenario") -> Any:
    normalized = _safe(value, path)
    if isinstance(normalized, Mapping):
        sanitized: dict[str, Any] = {}
        for key, child in normalized.items():
            key_text = str(key)
            if _unsafe_public_text(key_text) or _PROMPT_TEXT.search(key_text) or _UNSAFE_NESTED_KEY.search(key_text):
                raise ValueError("scenario contains unsafe public text")
            sanitized[key_text] = _safe_nested_scenario(child, f"{path}.value")
        return sanitized
    if isinstance(normalized, list):
        return [_safe_nested_scenario(child, f"{path}[]") for child in normalized]
    if isinstance(normalized, str) and (_unsafe_public_text(normalized) or _PROMPT_TEXT.search(normalized)):
        raise ValueError("scenario contains unsafe public text")
    return normalized


def _safe_identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} identifier is invalid")  # noqa: TRY004 - preserve public validation contract
    try:
        return _public_identifier(value.strip(), field_name)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} identifier is invalid") from exc


def _normalize_report_status(value: Mapping[str, Any]) -> bool:
    if not isinstance(value, Mapping) or value.get("paper_only") is not True:
        return False
    status = str(value.get("status", "")).strip().upper()
    passed = value.get("passed")
    if status not in {"PASSED", "BLOCKED", "FAILED"} or not isinstance(passed, bool):
        return False
    if (status == "PASSED") != passed:
        return False
    fingerprint = value.get("fingerprint")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        return False
    raw_refs = value.get("evidence_refs", ())
    raw_limits = value.get("limitations", ())
    if raw_refs is None:
        raw_refs = ()
    if raw_limits is None:
        raw_limits = ()
    if isinstance(raw_refs, (str, bytes)) or not isinstance(raw_refs, Sequence):
        return False
    if isinstance(raw_limits, (str, bytes)) or not isinstance(raw_limits, Sequence):
        return False
    if _report_texts(value, "evidence_refs") != tuple(raw_refs):
        return False
    if _report_texts(value, "limitations") != tuple(raw_limits):
        return False
    if not raw_refs or not raw_limits:
        return False
    try:
        if "industry_exposure" in value:
            required = {"status", "passed", "industry_exposure", "factor_exposure", "liquidity_buckets", "concentration", "cvar", "confidence", "blocking_reasons", "evidence_refs", "limitations", "snapshot_digest", "portfolio_digest", "factor_digest", "paper_only", "fingerprint"}
            if set(value) != required:
                return False
            report = ExposureReport(
                status=status,
                passed=passed,
                industry_exposure=value["industry_exposure"],
                factor_exposure=value["factor_exposure"],
                liquidity_buckets=value["liquidity_buckets"],
                concentration=value["concentration"],
                cvar=value["cvar"],
                confidence=value["confidence"],
                blocking_reasons=value.get("blocking_reasons", ()),
                evidence_refs=_report_texts(value, "evidence_refs"),
                limitations=_report_texts(value, "limitations"),
                snapshot_digest=value.get("snapshot_digest", ""),
                portfolio_digest=value.get("portfolio_digest", ""),
                factor_digest=value.get("factor_digest", ""),
                paper_only=True,
            )
        elif "scenarios" in value:
            required = {"status", "passed", "scenarios", "blocking_reasons", "evidence_refs", "limitations", "snapshot_digest", "portfolio_digest", "paper_only", "fingerprint"}
            if set(value) != required:
                return False
            report = StressReport(
                status=status,
                passed=passed,
                scenarios=value["scenarios"],
                blocking_reasons=value.get("blocking_reasons", ()),
                evidence_refs=_report_texts(value, "evidence_refs"),
                limitations=_report_texts(value, "limitations"),
                snapshot_digest=value.get("snapshot_digest", ""),
                portfolio_digest=value.get("portfolio_digest", ""),
                paper_only=True,
            )
        else:
            return False
    except (TypeError, ValueError):
        return False
    return passed and report.fingerprint == fingerprint


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(child) for child in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return value


def _weights(portfolio: Any) -> tuple[dict[str, float], dict[str, Any]]:
    data = _payload(portfolio, "portfolio")
    raw = data.get("weights")
    if raw is None:
        raw = data.get("positions")
    if raw is None and data and all(not isinstance(value, (Mapping, list, tuple)) for value in data.values()):
        raw = data
    if isinstance(raw, Mapping):
        items = raw.items()
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        items = []
        parsed: list[tuple[str, Any]] = []
        for item in raw:
            if not isinstance(item, Mapping):
                raise TypeError("portfolio positions must be mappings")
            name = item.get("instrument", item.get("symbol", item.get("id")))
            if name is None:
                raise ValueError("portfolio position instrument is required")
            parsed.append((str(name), item.get("weight", item.get("value"))))
        items = parsed
    else:
        raise ValueError("portfolio weights are unavailable")  # noqa: TRY004 - preserve public validation contract
    result: dict[str, float] = {}
    for name, value in items:
        try:
            instrument = _safe_identifier(name, "portfolio instrument")
        except ValueError as exc:
            raise ValueError("portfolio instrument identifier is invalid") from exc
        weight = _number(value, f"portfolio.weights.{instrument}")
        if weight < -1e-12:
            raise ValueError("portfolio weights must be non-negative")
        result[instrument] = result.get(instrument, 0.0) + max(0.0, weight)
    return dict(sorted(result.items())), data


def _observation_index(snapshot: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(item.get("instrument", item.get("symbol", item.get("id")))).strip(): item
        for item in _observations(snapshot)
        if item.get("instrument", item.get("symbol", item.get("id"))) is not None
    }


def _factor_payload(factor_map: Any) -> dict[str, Any]:
    data = _payload(factor_map, "factor_map")
    if isinstance(data.get("factors"), Mapping):
        return dict(data["factors"])
    return data


def _series(item: Mapping[str, Any]) -> list[float] | None:
    raw = _nested(item, "returns", "return_history", "historical_returns")
    if raw is None and "return" in item:
        raw = [item["return"]]
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        return None
    values: list[float] = []
    try:
        for index, value in enumerate(raw):
            values.append(_number(value, f"returns[{index}]"))
    except (TypeError, ValueError):
        return None
    return values or None


@dataclass(frozen=True, slots=True)
class ExposureReport:
    """Deterministic paper-only portfolio exposure and tail-risk report."""

    status: str = "BLOCKED"
    passed: bool = False
    industry_exposure: Mapping[str, float] = field(default_factory=dict)
    factor_exposure: Mapping[str, float] = field(default_factory=dict)
    liquidity_buckets: Mapping[str, float] = field(default_factory=dict)
    concentration: Mapping[str, float | str] = field(default_factory=dict)
    cvar: float | None = None
    confidence: float = 0.95
    blocking_reasons: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ("paper-only descriptive risk analytics; no investment advice",)
    snapshot_digest: str = ""
    portfolio_digest: str = ""
    factor_digest: str = ""
    paper_only: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.passed, bool):
            raise TypeError("passed must be boolean")
        passed = self.passed
        object.__setattr__(self, "passed", passed)
        object.__setattr__(self, "status", "PASSED" if passed else "BLOCKED")
        try:
            industry = {_safe_identifier(k, "industry"): round(_number(v, "industry exposure"), 12) for k, v in self.industry_exposure.items()}
            factors = {_safe_identifier(k, "factor"): round(_number(v, "factor exposure"), 12) for k, v in self.factor_exposure.items()}
            liquidity = {_safe_identifier(k, "liquidity bucket"): round(_number(v, "liquidity exposure"), 12) for k, v in self.liquidity_buckets.items()}
        except (TypeError, ValueError) as exc:
            raise ValueError("exposure identifiers must be public identifiers") from exc
        object.__setattr__(self, "industry_exposure", _freeze(dict(sorted(industry.items()))))
        object.__setattr__(self, "factor_exposure", _freeze(dict(sorted(factors.items()))))
        object.__setattr__(self, "liquidity_buckets", _freeze(dict(sorted(liquidity.items()))))
        clean_concentration: dict[str, float | str] = {}
        for key, value in self.concentration.items():
            try:
                safe_key = _safe_identifier(key, "concentration")
            except ValueError as exc:
                raise ValueError("concentration identifiers must be public identifiers") from exc
            clean_concentration[safe_key] = value if isinstance(value, str) else round(_number(value, "concentration value"), 12)
        object.__setattr__(self, "concentration", _freeze(dict(sorted(clean_concentration.items()))))
        if self.cvar is not None:
            object.__setattr__(self, "cvar", round(_number(self.cvar, "cvar"), 12))
        object.__setattr__(self, "confidence", _number(self.confidence, "confidence"))
        object.__setattr__(self, "blocking_reasons", _safe_text_tuple(self.blocking_reasons, "blocking_reasons"))
        object.__setattr__(self, "evidence_refs", _safe_text_tuple(self.evidence_refs, "evidence_refs"))
        object.__setattr__(self, "limitations", _safe_text_tuple(self.limitations, "limitations"))
        if self.paper_only is not True:
            raise ValueError("exposure report must be paper-only")

    @property
    def blocking(self) -> bool:
        return not self.passed

    @property
    def industries(self) -> Mapping[str, float]:
        return self.industry_exposure

    @property
    def factors(self) -> Mapping[str, float]:
        return self.factor_exposure

    @property
    def liquidity(self) -> Mapping[str, float]:
        return self.liquidity_buckets

    @property
    def cvar_95(self) -> float | None:
        return self.cvar if abs(self.confidence - 0.95) <= 1e-12 else None

    @property
    def fingerprint(self) -> str:
        return stable_digest(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "passed": self.passed,
            "industry_exposure": _thaw(self.industry_exposure),
            "factor_exposure": _thaw(self.factor_exposure),
            "liquidity_buckets": _thaw(self.liquidity_buckets),
            "concentration": _thaw(self.concentration),
            "cvar": self.cvar,
            "confidence": self.confidence,
            "blocking_reasons": list(self.blocking_reasons),
            "evidence_refs": list(self.evidence_refs),
            "limitations": list(self.limitations),
            "snapshot_digest": self.snapshot_digest,
            "portfolio_digest": self.portfolio_digest,
            "factor_digest": self.factor_digest,
            "paper_only": self.paper_only,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class StressReport:
    """Deterministic paper-only scenario loss report."""

    status: str = "BLOCKED"
    passed: bool = False
    scenarios: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    blocking_reasons: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ("paper-only deterministic stress analysis; no investment advice",)
    snapshot_digest: str = ""
    portfolio_digest: str = ""
    paper_only: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.passed, bool):
            raise TypeError("passed must be boolean")
        passed = self.passed
        object.__setattr__(self, "passed", passed)
        object.__setattr__(self, "status", "PASSED" if passed else "BLOCKED")
        normalized_scenarios: dict[str, Any] = {}
        for key, value in self.scenarios.items():
            if not isinstance(value, Mapping):
                raise TypeError("stress scenarios must be mappings")
            try:
                safe_key = _safe_identifier(key, "stress scenario")
            except ValueError as exc:
                raise ValueError("stress scenario identifiers must be public identifiers") from exc
            normalized_scenarios[safe_key] = _safe_nested_scenario(value)
        object.__setattr__(self, "scenarios", _freeze(dict(sorted(normalized_scenarios.items()))))
        object.__setattr__(self, "blocking_reasons", _safe_text_tuple(self.blocking_reasons, "blocking_reasons"))
        object.__setattr__(self, "evidence_refs", _safe_text_tuple(self.evidence_refs, "evidence_refs"))
        object.__setattr__(self, "limitations", _safe_text_tuple(self.limitations, "limitations"))
        if self.paper_only is not True:
            raise ValueError("stress report must be paper-only")

    @property
    def blocking(self) -> bool:
        return not self.passed

    @property
    def scenario_results(self) -> Mapping[str, Mapping[str, Any]]:
        return self.scenarios

    @property
    def fingerprint(self) -> str:
        return stable_digest(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "passed": self.passed,
            "scenarios": _thaw(self.scenarios),
            "blocking_reasons": list(self.blocking_reasons),
            "evidence_refs": list(self.evidence_refs),
            "limitations": list(self.limitations),
            "snapshot_digest": self.snapshot_digest,
            "portfolio_digest": self.portfolio_digest,
            "paper_only": self.paper_only,
            "fingerprint": self.fingerprint,
        }


def empirical_cvar(returns: Sequence[float], confidence: float = 0.95) -> float:
    """Fractional empirical lower-tail expected loss, not an estimated forecast."""
    confidence = _number(confidence, "confidence")
    if not 0 < confidence < 1 or not returns:
        raise ValueError("CVaR needs observations and confidence in (0, 1)")
    losses = sorted((-_number(value, "return") for value in returns), reverse=True)
    mass = (Decimal(1) - Decimal(str(confidence))) * len(losses)
    whole = int(mass)
    fraction = float(mass - whole)
    loss = sum(losses[:whole]) + (fraction * losses[whole] if fraction else 0.0)
    return max(0.0, loss / float(mass))


class RiskManager:
    """Apply deterministic exposure, liquidity, drawdown and stress gates."""

    def exposure(self, snapshot: Any, portfolio: Any, factor_map: Any) -> ExposureReport:
        snap = _payload(snapshot, "snapshot")
        pit = _pit_status(snap)
        reasons: list[str] = []
        try:
            weights, portfolio_data = _weights(portfolio)
        except (TypeError, ValueError) as exc:
            weights, portfolio_data = {}, {}
            reasons.append(f"portfolio metrics unavailable: {exc}")
        try:
            factors = _factor_payload(factor_map)
        except (TypeError, ValueError) as exc:
            factors = {}
            reasons.append(f"factor metadata unavailable: {exc}")
        try:
            observations = _observation_index(snap)
        except (TypeError, ValueError) as exc:
            observations = {}
            reasons.append(f"snapshot observations malformed: {exc}")
        snapshot_digest = stable_digest(snap)
        portfolio_digest = stable_digest(portfolio_data)
        factor_digest = stable_digest(factors)
        if pit not in _PIT_PASS:
            reasons.append("PIT status is unknown or unavailable")
        if not weights:
            reasons.append("portfolio weights are unavailable")
        total = sum(weights.values())
        if total > 1.0 + 1e-9:
            reasons.append("portfolio exposure exceeds one")
        industry: dict[str, float] = {}
        factor_totals: dict[str, float] = {}
        liquidity: dict[str, float] = {}
        histories: dict[str, list[float]] = {}
        missing_history: list[str] = []
        for instrument, weight in weights.items():
            observation = observations.get(instrument)
            factor = factors.get(instrument)
            if observation is None:
                reasons.append(f"missing snapshot observation: {instrument}")
                continue
            if not isinstance(factor, Mapping):
                reasons.append(f"factor metadata unavailable: {instrument}")
                factor = {}
            industry_name = _nested(factor, "industry", "sector") or _nested(observation, "industry", "sector")
            if industry_name is None or not str(industry_name).strip():
                reasons.append(f"industry exposure unavailable: {instrument}")
            else:
                try:
                    name = _safe_identifier(industry_name, "industry")
                except ValueError:
                    reasons.append("industry exposure identifier is invalid")
                else:
                    industry[name] = industry.get(name, 0.0) + weight
            raw_exposures = _nested(factor, "factors", "factor_exposures")
            if raw_exposures is None:
                raw_exposures = {key: value for key, value in factor.items() if str(key).casefold() not in {"industry", "sector", "factors", "factor_exposures"}}
            if not isinstance(raw_exposures, Mapping):
                reasons.append(f"factor exposure unavailable: {instrument}")
            else:
                for name, value in raw_exposures.items():
                    try:
                        safe_name = _safe_identifier(name, "factor")
                        factor_totals[safe_name] = factor_totals.get(safe_name, 0.0) + weight * _number(value, "factor exposure")
                    except (TypeError, ValueError):
                        reasons.append("factor exposure identifier or value is invalid")
            bucket = _nested(observation, "liquidity_bucket", "liquidity_tier")
            if bucket is None and isinstance(observation.get("liquidity"), str):
                bucket = observation["liquidity"]
            if bucket is not None and isinstance(bucket, str):
                bucket_name = bucket.strip().upper()
                if bucket_name not in _LIQUIDITY_BUCKETS:
                    reasons.append(f"liquidity bucket invalid: {instrument}")
                    bucket_name = "UNKNOWN"
            elif bucket is not None:
                reasons.append(f"liquidity bucket invalid: {instrument}")
                bucket_name = "UNKNOWN"
            else:
                raw_liquidity = _nested(observation, "average_volume", "volume", "dollar_volume", "liquidity_value")
                if raw_liquidity is None and "liquidity" in observation and not isinstance(observation["liquidity"], str):
                    raw_liquidity = observation["liquidity"]
                try:
                    value = _known_number(raw_liquidity, f"liquidity.{instrument}")
                except (TypeError, ValueError):
                    value = None
                if value is None:
                    reasons.append(f"liquidity metric unavailable: {instrument}")
                    bucket_name = "UNKNOWN"
                elif value >= 1000:
                    bucket_name = "HIGH"
                elif value >= 100:
                    bucket_name = "MEDIUM"
                else:
                    bucket_name = "LOW"
            liquidity[bucket_name] = liquidity.get(bucket_name, 0.0) + weight
            history = _series(observation)
            if history is None:
                missing_history.append(instrument)
            else:
                histories[instrument] = history
        if not factor_totals:
            reasons.append("factor exposure metrics are unavailable")
        portfolio_history = _series(portfolio_data)
        if portfolio_history is not None and missing_history:
            histories = {"__portfolio__": portfolio_history}
            reasons = [reason for reason in reasons if not reason.startswith("return history unavailable:")]
        else:
            reasons.extend(f"return history unavailable: {instrument}" for instrument in missing_history)
        cvar: float | None = None
        confidence = 0.95
        raw_confidence = _nested(snap, "cvar_confidence", "confidence", "alpha")
        if raw_confidence is not None:
            try:
                confidence = _number(raw_confidence, "cvar_confidence")
            except (TypeError, ValueError):
                reasons.append("CVaR confidence is invalid")
        if not 0 < confidence < 1:
            reasons.append("CVaR confidence is invalid")
        elif histories and (len(histories) == len(weights) or "__portfolio__" in histories) and len({len(values) for values in histories.values()}) == 1:
            periods = len(next(iter(histories.values())))
            if "__portfolio__" in histories:
                portfolio_returns = list(histories["__portfolio__"])
            else:
                portfolio_returns = [sum(weights[name] * histories[name][period] for name in weights) for period in range(periods)]
            if portfolio_returns:
                cvar = empirical_cvar(portfolio_returns, confidence)
            else:
                reasons.append("CVaR return history is empty")
        else:
            reasons.append("CVaR metrics are unavailable")
        concentration = {
            "max_weight": max(weights.values()) if weights else 0.0,
            "hhi": sum(value * value for value in weights.values()),
            "total_exposure": total,
        }
        limitations = ["paper-only descriptive risk analytics; no investment advice"]
        if reasons:
            limitations.extend(dict.fromkeys(reasons))
        refs = (
            f"snapshot:{snapshot_digest}",
            f"portfolio:{portfolio_digest}",
            f"factor:{factor_digest}",
            f"risk:exposure:{stable_digest({'industry': industry, 'factors': factor_totals, 'liquidity': liquidity, 'concentration': concentration, 'cvar': cvar})}",
        )
        return ExposureReport(
            status="PASSED" if not reasons else "BLOCKED",
            passed=not reasons,
            industry_exposure=industry,
            factor_exposure=factor_totals,
            liquidity_buckets=liquidity,
            concentration=concentration,
            cvar=cvar,
            confidence=confidence,
            blocking_reasons=tuple(dict.fromkeys(reasons)),
            evidence_refs=refs,
            limitations=tuple(dict.fromkeys(limitations)),
            snapshot_digest=snapshot_digest,
            portfolio_digest=portfolio_digest,
            factor_digest=factor_digest,
        )

    def stress(self, snapshot: Any, portfolio: Any, scenarios: Any) -> StressReport:
        snap = _payload(snapshot, "snapshot")
        reasons: list[str] = []
        try:
            weights, portfolio_data = _weights(portfolio)
        except (TypeError, ValueError) as exc:
            weights, portfolio_data = {}, {}
            reasons.append(f"portfolio metrics unavailable: {exc}")
        try:
            observations = _observation_index(snap)
        except (TypeError, ValueError) as exc:
            observations = {}
            reasons.append(f"snapshot observations malformed: {exc}")
        if any(instrument not in observations for instrument in weights):
            reasons.append("snapshot observations are missing portfolio instruments")
        snapshot_digest = stable_digest(snap)
        portfolio_digest = stable_digest(portfolio_data)
        if _pit_status(snap) not in _PIT_PASS:
            reasons.append("PIT status is unknown or unavailable")
        if not weights:
            reasons.append("portfolio weights are unavailable")
        if not isinstance(scenarios, Mapping) or not scenarios:
            reasons.append("stress scenarios are invalid or unavailable")
            scenario_items: list[tuple[Any, Any]] = []
        else:
            scenario_items = list(scenarios.items())
        results: dict[str, Mapping[str, Any]] = {}
        for raw_name, raw_config in scenario_items:
            try:
                name = _safe_identifier(raw_name, "stress scenario")
            except ValueError:
                name = f"scenario:{stable_digest(str(raw_name))}"
                reasons.append("stress scenario identifier is invalid")
                results[name] = {"status": "BLOCKED", "passed": False, "loss": None, "blocking_reasons": ["scenario identifier is invalid"]}
                continue
            outcome: dict[str, Any] = {"status": "BLOCKED", "passed": False, "loss": None, "blocking_reasons": []}
            if not isinstance(raw_config, Mapping):
                outcome["blocking_reasons"] = ["invalid scenario configuration"]
                reasons.append(f"stress scenario invalid: {name}")
                results[name] = outcome
                continue
            attribution: dict[str, float] = {}
            try:
                config = _safe(raw_config, f"stress.{name}")
                declared_status = str(config.get("status", "")).upper()
                if config.get("passed") is False or declared_status in {"FAILED", "BLOCKED", "INVALID"}:
                    outcome["blocking_reasons"] = ["scenario was declared failed"]
                    reasons.append(f"stress scenario failed: {name}")
                    results[name] = outcome
                    continue
                instrument_shocks = _nested(config, "instrument_shocks", "asset_shocks", "shocks") or {}
                industry_shocks = _nested(config, "industry_shocks", "sector_shocks") or {}
                factor_shocks = _nested(config, "factor_shocks", "factors") or {}
                if not all(isinstance(item, Mapping) for item in (instrument_shocks, industry_shocks, factor_shocks)):
                    raise ValueError("scenario shock maps must be mappings")
                if {str(key) for key in instrument_shocks} - set(weights):
                    raise ValueError("scenario contains unknown instrument shock target")
                known_industries = {
                    str(_nested(item, "industry", "sector"))
                    for item in observations.values()
                    if _nested(item, "industry", "sector") is not None
                }
                if {str(key) for key in industry_shocks} - known_industries:
                    raise ValueError("scenario contains unknown industry shock target")
                known_factors = {
                    str(factor_name)
                    for item in observations.values()
                    for factor_name in (_nested(item, "factors", "factor_exposures") or {})
                }
                if {str(key) for key in factor_shocks} - known_factors:
                    raise ValueError("scenario contains unknown factor shock target")
                explicit_loss = _nested(config, "loss", "drawdown")
                if explicit_loss is not None:
                    loss = _number(explicit_loss, f"stress.{name}.loss")
                    if loss < 0:
                        raise ValueError("stress scenario loss must be non-negative")
                    attribution = {"explicit": loss}
                else:
                    scalar = _nested(config, "return_shock", "market_shock", "shock")
                    scalar_was_supplied = scalar is not None
                    if scalar is None:
                        scalar = 0.0
                    scalar_value = _number(scalar, f"stress.{name}.shock")
                    if not scalar_was_supplied and not instrument_shocks and not industry_shocks and not factor_shocks:
                        raise ValueError("scenario has no shock metric")
                    loss = 0.0
                    for instrument, weight in weights.items():
                        observation = observations.get(instrument)
                        if observation is None:
                            raise ValueError(f"missing snapshot observation: {instrument}")
                        shock = scalar_value + _number(instrument_shocks.get(instrument, 0.0), f"stress.{name}.{instrument}")
                        industry = str(_nested(observation, "industry", "sector") or "")
                        shock += _number(industry_shocks.get(industry, 0.0), f"stress.{name}.industry")
                        exposures = _nested(observation, "factors", "factor_exposures") or {}
                        if factor_shocks and (not isinstance(exposures, Mapping) or not exposures):
                            raise ValueError(f"factor exposure unavailable: {instrument}")
                        if isinstance(exposures, Mapping):
                            for factor_name, factor_value in exposures.items():
                                _safe_identifier(factor_name, "factor")
                                shock += _number(factor_value, "stress factor exposure") * _number(factor_shocks.get(factor_name, 0.0), "stress factor shock")
                        contribution = max(0.0, -weight * shock)
                        loss += contribution
                        attribution[instrument] = contribution
                threshold = _nested(config, "max_loss", "loss_limit", "max_drawdown")
                if threshold is None:
                    raise ValueError("stress scenario max_loss is unavailable")
                threshold_value = _number(threshold, f"stress.{name}.max_loss")
                if threshold_value < 0:
                    raise ValueError("stress scenario max_loss must be non-negative")
                passed = threshold_value is None or loss <= threshold_value + 1e-12
                outcome = {
                    "status": "PASSED" if passed else "BLOCKED",
                    "passed": passed,
                    "loss": loss,
                    "max_loss": threshold_value,
                    "attribution": dict(sorted(attribution.items())),
                    "blocking_reasons": [] if passed else ["scenario loss exceeds limit"],
                }
                if not passed:
                    reasons.append(f"stress scenario failed: {name}")
            except (TypeError, ValueError) as exc:
                outcome["blocking_reasons"] = [str(exc)]
                reasons.append(f"stress scenario invalid: {name}")
            results[name] = outcome
        refs = (
            f"snapshot:{snapshot_digest}",
            f"portfolio:{portfolio_digest}",
            f"risk:stress:{stable_digest(results)}",
        )
        limitations = ["paper-only deterministic stress analysis; no investment advice"]
        if reasons:
            limitations.extend(dict.fromkeys(reasons))
        return StressReport(
            status="PASSED" if not reasons else "BLOCKED",
            passed=not reasons,
            scenarios=results,
            blocking_reasons=tuple(dict.fromkeys(reasons)),
            evidence_refs=refs,
            limitations=tuple(dict.fromkeys(limitations)),
            snapshot_digest=snapshot_digest,
            portfolio_digest=portfolio_digest,
        )

    def review(self, snapshot: Any, factor_result: Any, constraints: Mapping[str, Any] | Any) -> RiskReviewResult:
        snap = _payload(snapshot, "snapshot")
        factor = _payload(factor_result, "factor_result")
        policy = _payload(constraints, "constraints") if not isinstance(constraints, Mapping) else _safe(constraints, "constraints")
        if not isinstance(policy, Mapping):
            raise TypeError("constraints must be a mapping")
        reasons: list[str] = []
        gates: list[str] = []
        evidence_refs: list[str] = []
        limitations: list[str] = []
        exposure_digest = ""
        stress_digest = ""
        pit = _pit_status(snap)
        gates.append("pit")
        if pit not in _PIT_PASS:
            reasons.append("PIT status is unknown or unavailable")

        factor_status = str(_nested(factor, "status", "admission_status", "evaluation_status") or "").upper()
        if not factor_status:
            reasons.append("factor status is unavailable")
        elif factor_status not in {"ADMITTED", "VALID", "PASSED", "READY", "HUMAN_ADMITTED"}:
            reasons.append("factor result is not eligible")
        if factor.get("passed") is False:
            reasons.append("factor result failed")
        if factor.get("production_write") is True:
            reasons.append("factor result requests production write")
        gates.append("factor")

        try:
            observations = _observations(snap)
        except (TypeError, ValueError) as exc:
            observations = ()
            reasons.append(f"snapshot observations malformed: {exc}")
        metrics = factor.get("metrics") if isinstance(factor.get("metrics"), Mapping) else {}
        snapshot_metrics = snap.get("metrics") if isinstance(snap.get("metrics"), Mapping) else {}

        exposure_report = snap.get("exposure_report")
        if isinstance(exposure_report, Mapping):
            if _normalize_report_status(exposure_report):
                exposure_digest = str(exposure_report["fingerprint"])
                evidence_refs.extend(_report_texts(exposure_report, "evidence_refs"))
                limitations.extend(_report_texts(exposure_report, "limitations"))
            else:
                report_reasons = _report_texts(exposure_report, "blocking_reasons")
                reasons.extend(f"exposure: {item}" for item in (report_reasons or ("embedded report is invalid or blocked",)))
            gates.append("exposure")
        stress_report = snap.get("stress_report")
        if isinstance(stress_report, Mapping):
            if _normalize_report_status(stress_report):
                stress_digest = str(stress_report["fingerprint"])
                evidence_refs.extend(_report_texts(stress_report, "evidence_refs"))
                limitations.extend(_report_texts(stress_report, "limitations"))
            else:
                reasons.extend(f"stress: {item}" for item in _report_texts(stress_report, "blocking_reasons"))
                if not _report_texts(stress_report, "blocking_reasons"):
                    reasons.append("stress: embedded report is invalid or blocked")
            gates.append("stress_report")

        if isinstance(snap.get("proposed_weights"), Mapping):
            base_snapshot = {key: value for key, value in snap.items() if key not in {"exposure_report", "stress_report"}}
            portfolio_digest = stable_digest({"weights": snap["proposed_weights"]})
            for name, report in (("exposure", exposure_report), ("stress", stress_report)):
                if not isinstance(report, Mapping) or report.get("snapshot_digest") != stable_digest(base_snapshot) or report.get("portfolio_digest") != portfolio_digest:
                    reasons.append(f"{name} report snapshot/portfolio binding mismatch")
            gates.append("report_binding")
        if "max_cvar" in policy:
            try:
                cvar_value = _known_number(exposure_report.get("cvar") if isinstance(exposure_report, Mapping) else None, "CVaR")
                cvar_limit = _number(policy["max_cvar"], "max_cvar")
                if cvar_value is None or cvar_limit < 0 or cvar_value > cvar_limit + 1e-12:
                    reasons.append("CVaR metric unavailable or limit failed")
            except (TypeError, ValueError):
                reasons.append("CVaR metric unavailable or invalid")
            gates.append("cvar")

        liquidity = _nested(metrics, "liquidity", "min_liquidity", "average_volume")
        if liquidity is None:
            liquidity = _nested(snap, "liquidity", "min_liquidity")
        if liquidity is None and observations:
            values = [float(item.get("volume", item.get("amount", 0.0)) or 0.0) for item in observations]
            liquidity = min(values) if values else None
        minimum_liquidity = _nested(policy, "min_liquidity", "minimum_liquidity")
        if minimum_liquidity is not None:
            try:
                liquidity_value = _known_number(liquidity, "liquidity")
            except (TypeError, ValueError):
                liquidity_value = None
            if liquidity_value is None:
                reasons.append("liquidity metric is unavailable")
            elif liquidity_value < _number(minimum_liquidity, "min_liquidity"):
                reasons.append("liquidity limit failed")
        gates.append("liquidity")

        concentration = _nested(metrics, "concentration", "max_concentration")
        if concentration is None:
            concentration = _nested(snap, "concentration", "max_concentration")
        if concentration is None and observations:
            notionals = []
            for item in observations:
                try:
                    notionals.append(max(0.0, _number(item.get("close", 0.0), "close")) * max(0.0, _number(item.get("volume", 0.0), "volume")))
                except (TypeError, ValueError):
                    continue
            total = sum(notionals)
            concentration = max(notionals) / total if notionals and total else None
        max_concentration = _nested(policy, "max_concentration", "concentration_limit")
        if max_concentration is not None:
            try:
                concentration_value = _known_number(concentration, "concentration")
            except (TypeError, ValueError):
                concentration_value = None
            if concentration_value is None:
                reasons.append("concentration metric is unavailable")
            elif concentration_value > _number(max_concentration, "max_concentration") + 1e-12:
                reasons.append("concentration limit failed")
        gates.append("concentration")

        drawdown = _nested(metrics, "drawdown", "max_drawdown")
        if drawdown is None:
            drawdown = _nested(snapshot_metrics, "drawdown", "max_drawdown")
        if drawdown is None:
            drawdown = _nested(snap, "drawdown", "max_drawdown")
        drawdown_unknown = isinstance(drawdown, str) and drawdown.strip().upper() in {"UNKNOWN", "UNAVAILABLE", "MISSING", "N/A", "NONE"}
        if drawdown_unknown or drawdown is None:
            reasons.append("drawdown metric is unavailable")
            drawdown = 0.0
        else:
            drawdown = abs(_number(drawdown, "drawdown"))
        max_drawdown = _nested(policy, "max_drawdown", "drawdown_limit")
        if max_drawdown is not None and drawdown > _number(max_drawdown, "max_drawdown") + 1e-12:
            reasons.append("drawdown limit failed")
        gates.append("drawdown")

        stress = _nested(snap, "stress_results") or _nested(metrics, "stress_results")
        if stress is None and isinstance(stress_report, Mapping):
            stress = stress_report.get("scenarios")
        scenarios = _nested(policy, "stress_scenarios", "stress_tests")
        if scenarios is None:
            reasons.append("stress scenarios are unavailable")
        elif scenarios is not None:
            if not isinstance(scenarios, Mapping) or not scenarios:
                reasons.append("stress scenarios are invalid")
            else:
                for name, scenario in scenarios.items():
                    outcome = stress.get(name) if isinstance(stress, Mapping) else None
                    if not isinstance(outcome, Mapping):
                        reasons.append(f"stress scenario result unavailable: {name}")
                    elif outcome.get("passed") is False:
                        reasons.append(f"stress scenario failed: {name}")
                    elif outcome.get("passed") is not True:
                        reasons.append(f"stress scenario result unknown: {name}")
                    elif (
                        isinstance(scenario, Mapping)
                        and "drawdown" in scenario
                        and max_drawdown is not None
                        and abs(_number(scenario["drawdown"], f"stress.{name}.drawdown")) > _number(max_drawdown, "max_drawdown") + 1e-12
                    ):
                        reasons.append(f"stress scenario failed: {name}")
        gates.append("stress")

        try:
            clean_liquidity = _known_number(liquidity, "liquidity")
        except (TypeError, ValueError):
            clean_liquidity = None
        try:
            clean_concentration = _known_number(concentration, "concentration")
        except (TypeError, ValueError):
            clean_concentration = None
        clean_metrics: dict[str, float | str] = {
            "pit_status": pit,
            "liquidity": clean_liquidity if clean_liquidity is not None else "UNKNOWN",
            "concentration": clean_concentration if clean_concentration is not None else "UNKNOWN",
            "drawdown": float(drawdown),
        }
        return RiskReviewResult(
            status="PASSED" if not reasons else "BLOCKED",
            passed=not reasons,
            gates=tuple(gates),
            blocking_reasons=tuple(dict.fromkeys(reasons)),
            metrics=clean_metrics,
            snapshot_digest=stable_digest(snap),
            factor_digest=stable_digest(factor),
            policy_version=str(policy.get("policy_version", policy.get("version", "risk.v1"))),
            limits=policy,
            evidence_refs=tuple(dict.fromkeys((*evidence_refs, f"snapshot:{stable_digest(snap)}", f"factor:{stable_digest(factor)}"))),
            limitations=tuple(dict.fromkeys(limitations)),
            exposure_digest=exposure_digest,
            stress_digest=stress_digest,
        )


__all__ = ["ExposureReport", "RiskManager", "RiskReviewResult", "StressReport"]

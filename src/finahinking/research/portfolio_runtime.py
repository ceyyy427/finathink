"""Deterministic long-only portfolio construction for paper research."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .contracts import stable_digest
from .risk_runtime import RiskReviewResult

_FORBIDDEN = {"broker", "order", "cancel", "account", "live", "endpoint", "credential", "secret"}
_UNSAFE_KEY = re.compile(r"(?:api[-_ ]?key|secret|token|password|credential|authorization|raw[_ -]?provider)", re.IGNORECASE)
_UNSAFE_TEXT = re.compile(r"(?:api[-_ ]?key|secret|token|password|credential|authorization)\s*[:=]|(?:https?|file|ftp|ssh|s3)://|(?:^|[\s:(])(?:/|~[/]|\.{1,2}[/])|\\", re.IGNORECASE)


def _safe(value: Any, path: str = "value") -> Any:
    if callable(value):
        raise TypeError(f"{path} contains a forbidden executable value")
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for key, child in value.items():
            name = str(key)
            if any(token in name.casefold() for token in _FORBIDDEN) or _UNSAFE_KEY.search(name):
                raise ValueError(f"{path} contains a forbidden paper-only field")
            output[name] = _safe(child, f"{path}.{name}")
        return output
    if isinstance(value, (tuple, list)):
        return [_safe(item, f"{path}[]") for item in value]
    if isinstance(value, str):
        if _UNSAFE_TEXT.search(value):
            raise ValueError(f"{path} contains unsafe paper-only text")
        return value
    if value is None or isinstance(value, (int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must be finite")
        return value
    raise TypeError(f"{path} must be normalized JSON data")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(child) for child in value)
    if isinstance(value, tuple):
        return tuple(_freeze(child) for child in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(child) for child in value]
    return value


def _number(value: Any, label: str, *, default: float | None = None) -> float:
    if value is None and default is not None:
        return default
    if isinstance(value, bool):
        raise TypeError(f"{label} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return _safe(value, label)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        if isinstance(result, Mapping):
            return _safe(result, label)
    # Small typed test doubles may expose only the public result attributes.
    # Read this closed allow-list rather than serializing arbitrary objects.
    if not isinstance(value, (str, bytes, int, float, bool)):
        known = {name: getattr(value, name) for name in ("passed", "status", "blocking_reasons", "fingerprint", "risk_digest") if hasattr(value, name)}
        if known:
            return _safe(known, label)
    raise TypeError(f"{label} must be a structured result")


@dataclass(frozen=True, slots=True)
class PaperPortfolioProposal:
    status: str = "BLOCKED"
    passed: bool = False
    weights: Mapping[str, float] = field(default_factory=dict)
    cash_weight: float = 1.0
    candidates: tuple[str, ...] = ()
    risk_digest: str = ""
    constraints: Mapping[str, Any] = field(default_factory=dict)
    rationale: str = ""
    paper_only: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.passed, bool):
            raise TypeError("passed must be boolean")
        status = str(self.status).strip().upper()
        object.__setattr__(self, "status", "PASSED" if self.passed else ("BLOCKED" if status == "PASSED" else status))
        normalized: dict[str, float] = {}
        for instrument, weight in dict(self.weights).items():
            name = str(instrument).strip()
            if not name or any(token in name.casefold() for token in _FORBIDDEN):
                raise ValueError("instrument is invalid for paper-only portfolio")
            value = _number(weight, f"weights.{name}")
            if value < -1e-12:
                raise ValueError("portfolio weights must be long-only")
            normalized[name] = max(0.0, value)
        object.__setattr__(self, "weights", MappingProxyType({key: normalized[key] for key in sorted(normalized)}))
        object.__setattr__(self, "cash_weight", _number(self.cash_weight, "cash_weight"))
        if self.cash_weight < -1e-12:
            raise ValueError("cash weight must be non-negative")
        object.__setattr__(self, "candidates", tuple(str(item) for item in self.candidates))
        object.__setattr__(self, "constraints", _freeze(_safe(self.constraints, "constraints")))
        object.__setattr__(self, "rationale", str(self.rationale))
        if self.paper_only is not True:
            raise ValueError("portfolio proposal must be paper-only")
        if sum(self.weights.values()) + self.cash_weight > 1.0 + 1e-9:
            raise ValueError("portfolio exposure exceeds one")

    @property
    def fingerprint(self) -> str:
        return stable_digest(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "passed": self.passed,
            "weights": dict(self.weights),
            "cash_weight": self.cash_weight,
            "candidates": list(self.candidates),
            "risk_digest": self.risk_digest,
            "constraints": _thaw(self.constraints),
            "rationale": self.rationale,
            "paper_only": self.paper_only,
            "fingerprint": self.fingerprint,
        }


class PortfolioManager:
    """Construct a transparent equal-weight paper portfolio after risk gates."""

    def construct(self, risk_result: RiskReviewResult | Mapping[str, Any], candidates: Sequence[Any] | Mapping[str, Any], constraints: Mapping[str, Any] | Any) -> PaperPortfolioProposal:
        if not isinstance(risk_result, RiskReviewResult):
            raise TypeError("risk_result must be a RiskReviewResult")
        risk = risk_result.to_dict()
        policy = _mapping(constraints, "constraints")
        if policy.get("long_only", True) is not True:
            raise ValueError("portfolio runtime is long-only")
        if risk_result.blocking_reasons:
            return PaperPortfolioProposal(
                status="BLOCKED",
                passed=False,
                risk_digest=risk_result.fingerprint,
                constraints=policy,
                rationale="risk gate blocked portfolio: " + "; ".join(risk_result.blocking_reasons),
            )
        if risk.get("passed") is not True and str(risk.get("status", "")).upper() != "PASSED":
            reasons = tuple(str(item) for item in risk.get("blocking_reasons", ()))
            return PaperPortfolioProposal(status="BLOCKED", passed=False, candidates=(), risk_digest=str(risk.get("fingerprint", "")), constraints=policy, rationale="risk gate blocked portfolio: " + "; ".join(reasons))

        extracted: list[tuple[str, float | None]] = []
        if isinstance(candidates, Mapping):
            source = tuple(candidates.items())
            for instrument, weight in source:
                value = _number(weight, f"candidate.{instrument}")
                if not 0 <= value <= 1:
                    raise ValueError("candidate weight is outside long-only bounds")
                extracted.append((str(instrument), value))
        elif isinstance(candidates, (str, bytes)) or not isinstance(candidates, Sequence):
            raise TypeError("candidates must be a sequence or mapping")
        else:
            for item in candidates:
                if isinstance(item, str):
                    extracted.append((item, None))
                elif isinstance(item, Mapping):
                    instrument = item.get("instrument", item.get("symbol", item.get("id")))
                    if instrument is None:
                        raise ValueError("candidate instrument is required")
                    score = item.get("score", item.get("rank_score"))
                    extracted.append((str(instrument), None if score is None else _number(score, "candidate.score")))
                else:
                    instrument = getattr(item, "instrument", getattr(item, "symbol", None))
                    if instrument is None:
                        raise TypeError("candidates must be normalized records")
                    score = getattr(item, "score", None)
                    extracted.append((str(instrument), None if score is None else _number(score, "candidate.score")))
        names = sorted({name.strip() for name, _ in extracted if name.strip()})
        if not names:
            return PaperPortfolioProposal(status="BLOCKED", passed=False, risk_digest=str(risk.get("fingerprint", "")), constraints=policy, rationale="no candidates")
        if any(any(token in name.casefold() for token in _FORBIDDEN) for name in names):
            raise ValueError("candidate contains forbidden paper-only execution field")
        max_single = _number(policy.get("max_single_weight", policy.get("max_weight", 1.0)), "max_single_weight")
        max_exposure = _number(policy.get("max_exposure", 1.0), "max_exposure")
        cash_buffer = _number(policy.get("cash_buffer", 0.0), "cash_buffer")
        risk_max_concentration = risk_result.limits.get("max_concentration", risk_result.limits.get("concentration_limit"))
        if risk_max_concentration is None:
            risk_max_concentration = risk_result.metrics.get("max_concentration_limit")
        if risk_max_concentration is not None:
            risk_limit = _number(risk_max_concentration, "risk.max_concentration")
            observed = risk_result.metrics.get("concentration")
            if isinstance(observed, (int, float)) and float(observed) > risk_limit + 1e-12:
                return PaperPortfolioProposal(
                    status="BLOCKED",
                    passed=False,
                    risk_digest=risk_result.fingerprint,
                    constraints=policy,
                    rationale="risk concentration gate failed",
                )
            max_single = min(max_single, risk_limit)
        if not 0 <= max_single <= 1 or not 0 <= max_exposure <= 1 or not 0 <= cash_buffer <= 1:
            raise ValueError("portfolio constraints must be between zero and one")
        max_exposure = min(max_exposure, 1.0 - cash_buffer)
        target_exposure = min(max_exposure, 1.0 - cash_buffer)
        share = min(max_single, target_exposure / len(names))
        weights = {name: share for name in names}
        used = share * len(names)
        return PaperPortfolioProposal(
            status="PASSED",
            passed=True,
            weights=weights,
            cash_weight=max(0.0, 1.0 - used),
            candidates=tuple(names),
            risk_digest=str(risk.get("fingerprint", risk.get("risk_digest", ""))),
            constraints=policy,
            rationale="deterministic long-only paper allocation; no investment recommendation",
        )

    def optimize(
        self,
        candidates: Sequence[Any] | Mapping[str, Any],
        risk_result: RiskReviewResult,
        constraints: Mapping[str, Any] | Any,
    ) -> PaperPortfolioProposal:
        """Build a deterministic constrained long-only allocation.

        This is deliberately a small allocation engine rather than a numerical
        optimizer.  Candidate order, tie breaks, clipping, and exposure repair
        are all deterministic, and every infeasible request remains BLOCKED.
        """
        try:
            if not isinstance(risk_result, RiskReviewResult):
                raise TypeError("risk_result must be a RiskReviewResult")
            policy = _mapping(constraints, "constraints")
            digest = risk_result.fingerprint
            if not risk_result.passed or risk_result.blocking_reasons:
                return PaperPortfolioProposal(status="BLOCKED", risk_digest=digest, constraints=policy, rationale="risk gate blocked portfolio")
            # A caller-supplied PASSED flag is not sufficient evidence.  The
            # Task 10 metrics and limits must agree before allocation begins.
            metrics = risk_result.metrics
            limits = risk_result.limits
            pit_status = str(metrics.get("pit_status", "UNKNOWN")).strip().upper()
            if pit_status not in {"AVAILABLE", "VALID", "VERIFIED", "KNOWN", "READY", "TRUE", "OK"}:
                return PaperPortfolioProposal(status="BLOCKED", risk_digest=digest, constraints=policy, rationale="risk gate metrics are contradictory")
            checks = (
                ("concentration", ("max_concentration", "concentration_limit"), "max"),
                ("drawdown", ("max_drawdown", "drawdown_limit"), "max"),
                ("liquidity", ("min_liquidity", "minimum_liquidity"), "min"),
            )
            for metric_name, limit_names, direction in checks:
                limit = next((limits[name] for name in limit_names if name in limits), None)
                if limit is None:
                    continue
                observed = metrics.get(metric_name)
                try:
                    observed_value = _number(observed, f"risk.metrics.{metric_name}")
                    limit_value = _number(limit, f"risk.limits.{metric_name}")
                except (TypeError, ValueError):
                    return PaperPortfolioProposal(status="BLOCKED", risk_digest=digest, constraints=policy, rationale="risk gate metrics are unavailable")
                if (direction == "max" and observed_value > limit_value + 1e-12) or (direction == "min" and observed_value < limit_value - 1e-12):
                    return PaperPortfolioProposal(status="BLOCKED", risk_digest=digest, constraints=policy, rationale="risk gate metrics violate limits")
            if policy.get("long_only", True) is not True:
                raise ValueError("portfolio runtime is long-only")

            records: list[dict[str, Any]] = []
            source = tuple(candidates.items()) if isinstance(candidates, Mapping) else candidates
            if isinstance(source, (str, bytes)) or not isinstance(source, Sequence):
                raise TypeError("candidates must be a sequence or mapping")
            for item in source:
                if isinstance(candidates, Mapping):
                    instrument, value = item
                    record = dict(value) if isinstance(value, Mapping) else {"score": value}
                    record["instrument"] = instrument
                elif isinstance(item, str):
                    record = {"instrument": item}
                elif isinstance(item, Mapping):
                    record = dict(item)
                else:
                    record = {name: getattr(item, name) for name in ("instrument", "symbol", "score", "industry", "factors") if hasattr(item, name)}
                instrument = str(record.get("instrument", record.get("symbol", record.get("id", "")))).strip()
                if not instrument or any(token in instrument.casefold() for token in _FORBIDDEN):
                    raise ValueError("candidate instrument is invalid")
                factors = record.get("factors", record.get("factor_exposures", record.get("factor_exposure", record.get("factor_scores", {}))))
                if not isinstance(factors, Mapping):
                    raise TypeError("candidate factors must be a mapping")
                clean_factors = {str(key): _number(value, f"factor.{key}") for key, value in factors.items()}
                score = _number(record.get("score", record.get("rank_score", 1.0)), f"score.{instrument}")
                records.append({"instrument": instrument, "score": max(0.0, score), "industry": str(record.get("industry", record.get("sector", ""))), "factors": clean_factors})
            unique: dict[str, dict[str, Any]] = {item["instrument"]: item for item in records}
            if len(unique) != len(records):
                raise ValueError("duplicate portfolio candidates")
            records = sorted(unique.values(), key=lambda item: (-item["score"], item["instrument"]))
            if not records:
                raise ValueError("no candidates")

            max_single = _number(policy.get("max_single_weight", policy.get("max_weight", 1.0)), "max_single_weight")
            risk_single = risk_result.limits.get("max_concentration", risk_result.limits.get("concentration_limit"))
            if risk_single is not None:
                max_single = min(max_single, _number(risk_single, "risk.max_concentration"))
            max_exposure = _number(policy.get("max_exposure", policy.get("max_total_exposure", policy.get("total_exposure", 1.0))), "max_exposure")
            cash_buffer = _number(policy.get("cash_buffer", policy.get("cash_minimum", policy.get("min_cash", 0.0))), "cash_buffer")
            if not (0 <= max_single <= 1 and 0 <= max_exposure <= 1 and 0 <= cash_buffer <= 1):
                raise ValueError("portfolio constraints must be between zero and one")
            target = min(max_exposure, 1.0 - cash_buffer)
            industry_limits = policy.get("industry_limits", policy.get("industry_caps", policy.get("industry_exposure_limits", {})))
            factor_limits = policy.get("factor_limits", policy.get("factor_bounds", policy.get("factor_exposure_limits", {})))
            if not isinstance(industry_limits, Mapping) or not isinstance(factor_limits, Mapping):
                raise TypeError("exposure limits must be mappings")
            if any(str(name) not in item["factors"] for name in factor_limits for item in records):
                raise ValueError("constrained factor exposure is unavailable")
            industry_limits = {str(key): _number(value, f"industry_limits.{key}") for key, value in industry_limits.items()}
            if any(value < 0 or value > 1 for value in industry_limits.values()):
                raise ValueError("industry limits must be between zero and one")
            parsed_factors: dict[str, tuple[float, float]] = {}
            for key, value in factor_limits.items():
                if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) == 2:
                    bounds = (_number(value[0], f"factor_limits.{key}[0]"), _number(value[1], f"factor_limits.{key}[1]"))
                else:
                    bound = abs(_number(value, f"factor_limits.{key}"))
                    bounds = (-bound, bound)
                if bounds[0] > bounds[1]:
                    raise ValueError("factor limit lower bound exceeds upper bound")
                parsed_factors[str(key)] = bounds

            previous_raw = policy.get("previous_weights", policy.get("current_weights", {}))
            if not isinstance(previous_raw, Mapping):
                raise TypeError("previous_weights must be a mapping")
            previous = {}
            for key, value in previous_raw.items():
                parsed_previous = _number(value, f"previous_weights.{key}")
                if parsed_previous < 0 or parsed_previous > 1:
                    raise ValueError("previous weights must be between zero and one")
                previous[str(key)] = parsed_previous
            max_turnover = policy.get("max_turnover", policy.get("turnover_limit"))
            max_turnover_value = None if max_turnover is None else _number(max_turnover, "max_turnover")
            if max_turnover_value is not None and max_turnover_value < 0:
                raise ValueError("max_turnover must be non-negative")
            cost_bps = _number(policy.get("transaction_cost_bps", policy.get("cost_bps", 0.0)), "transaction_cost_bps")
            max_cost = policy.get("max_transaction_cost", policy.get("cost_limit"))
            max_cost_value = None if max_cost is None else _number(max_cost, "max_transaction_cost")
            if cost_bps < 0 or (max_cost_value is not None and max_cost_value < 0):
                raise ValueError("transaction costs must be non-negative")

            positive_score = sum(item["score"] for item in records)
            weights: dict[str, float] = {}
            industry_used: dict[str, float] = {}
            preserve_previous = bool(previous) and sum(previous.values()) <= target + 1e-8
            for item in records:
                name = item["instrument"]
                desired = target * (item["score"] / positive_score if positive_score else 1.0 / len(records))
                cap = max_single
                industry = item["industry"]
                if industry in industry_limits:
                    cap = min(cap, max(0.0, industry_limits[industry] - industry_used.get(industry, 0.0)))
                if max_turnover_value is not None:
                    cap = min(cap, previous.get(name, 0.0) + max_turnover_value)
                if preserve_previous and name in previous:
                    desired = previous[name]
                weights[name] = max(0.0, min(desired, cap))
                industry_used[industry] = industry_used.get(industry, 0.0) + weights[name]

            # Repair factor bounds by moving weight from the violating side to
            # the most suitable opposing candidate.  A bounded iteration keeps
            # behavior stable while avoiding an unconstrained fallback.
            for _ in range(len(records) * 4 + 1):
                changed = False
                for factor, (lower, upper) in parsed_factors.items():
                    exposure = sum(weights[item["instrument"]] * item["factors"].get(factor, 0.0) for item in records)
                    if exposure > upper + 1e-10:
                        donors = sorted((item for item in records if item["factors"].get(factor, 0.0) > 0), key=lambda item: (-item["factors"].get(factor, 0.0), item["instrument"]))
                        receivers = sorted((item for item in records if item["factors"].get(factor, 0.0) < 0), key=lambda item: (item["factors"].get(factor, 0.0), item["instrument"]))
                        excess = exposure - upper
                    elif exposure < lower - 1e-10:
                        donors = sorted((item for item in records if item["factors"].get(factor, 0.0) < 0), key=lambda item: (item["factors"].get(factor, 0.0), item["instrument"]))
                        receivers = sorted((item for item in records if item["factors"].get(factor, 0.0) > 0), key=lambda item: (-item["factors"].get(factor, 0.0), item["instrument"]))
                        excess = lower - exposure
                    else:
                        continue
                    if not donors or not receivers:
                        continue
                    donor, receiver = donors[0], receivers[0]
                    donor_name, receiver_name = donor["instrument"], receiver["instrument"]
                    delta = min(weights[donor_name], excess / max(abs(donor["factors"].get(factor, 0.0)) + abs(receiver["factors"].get(factor, 0.0)), 1e-12))
                    receiver_cap = max_single
                    if receiver["industry"] in industry_limits:
                        receiver_cap = min(receiver_cap, max(0.0, industry_limits[receiver["industry"]] - sum(weights[item["instrument"]] for item in records if item["industry"] == receiver["industry"])))
                    delta = min(delta, max(0.0, receiver_cap - weights[receiver_name]))
                    if delta > 1e-12:
                        weights[donor_name] -= delta
                        weights[receiver_name] += delta
                        changed = True
                if not changed:
                    break

            total = sum(weights.values())
            turnover = sum(abs(weights.get(name, 0.0) - previous.get(name, 0.0)) for name in set(weights) | set(previous))
            cost = turnover * cost_bps / 10000.0
            factor_values = {factor: sum(weights[item["instrument"]] * item["factors"].get(factor, 0.0) for item in records) for factor in parsed_factors}
            industry_values = {industry: sum(weights[item["instrument"]] for item in records if item["industry"] == industry) for industry in industry_limits}
            valid = total <= target + 1e-8 and all(value <= limit + 1e-8 for industry, value in industry_values.items() for limit in [industry_limits[industry]]) and all(lower - 1e-8 <= factor_values[factor] <= upper + 1e-8 for factor, (lower, upper) in parsed_factors.items())
            if max_turnover_value is not None:
                valid = valid and turnover <= max_turnover_value + 1e-8
            if max_cost is not None:
                valid = valid and cost <= max_cost_value + 1e-8
            if policy.get("require_target_exposure") is True:
                valid = valid and total >= target - 1e-8
            if not valid:
                return PaperPortfolioProposal(status="BLOCKED", risk_digest=digest, constraints=policy, rationale="portfolio constraints are infeasible")
            return PaperPortfolioProposal(status="PASSED", passed=True, weights=weights, cash_weight=max(0.0, 1.0 - total), candidates=tuple(item["instrument"] for item in records), risk_digest=digest, constraints=policy, rationale="deterministic constrained long-only paper allocation; no investment recommendation")
        except (TypeError, ValueError, KeyError):
            # Keep diagnostics generic so malformed/unsafe inputs cannot leak
            # paths, credentials, or arbitrary caller text into the proposal.
            return PaperPortfolioProposal(status="BLOCKED", risk_digest=risk_result.fingerprint if isinstance(risk_result, RiskReviewResult) else "", constraints={}, rationale="portfolio optimization blocked")


__all__ = ["PaperPortfolioProposal", "PortfolioManager"]

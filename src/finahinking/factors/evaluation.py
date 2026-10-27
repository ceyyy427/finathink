"""Deterministic, leakage-aware factor evaluation and admission evidence."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any

import pandas as pd

from .dsl import evaluate_expression
from .mining import FactorCandidate


class FactorEvaluationStatus(str, Enum):
    VALID = "VALID"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    LEAKAGE_BLOCKED = "LEAKAGE_BLOCKED"
    UNSTABLE = "UNSTABLE"
    INVALID = "INVALID"


def _digest(value: Any) -> str:
    def normalize(item: Any) -> Any:
        if isinstance(item, Enum):
            return item.value
        if hasattr(item, "__dataclass_fields__"):
            return normalize(asdict(item))
        if isinstance(item, Mapping):
            return {str(key): normalize(item[key]) for key in sorted(item, key=str)}
        if isinstance(item, (tuple, list)):
            return [normalize(entry) for entry in item]
        if isinstance(item, (pd.Timestamp,)):
            return item.isoformat()
        return item

    encoded = json.dumps(normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class FactorDecayProfile:
    horizons: tuple[int, ...]
    information_coefficients: tuple[float | None, ...]

    def __post_init__(self) -> None:
        if len(self.horizons) != len(self.information_coefficients):
            raise ValueError("decay horizons and values must have the same length")
        if any(isinstance(item, bool) or int(item) != item or int(item) < 1 for item in self.horizons):
            raise ValueError("decay horizons must be positive integers")


@dataclass(frozen=True, slots=True)
class FactorEvaluation:
    candidate_id: str
    factor_fingerprint: str
    status: FactorEvaluationStatus
    sample_size: int
    coverage: float
    information_coefficient: float | None
    information_ratio: float | None
    quantile_returns: tuple[tuple[str, float], ...]
    long_short_return: float | None
    turnover: float | None
    transaction_cost: float | None
    decay: FactorDecayProfile
    oos_status: str
    oos_information_coefficient: float | None
    warnings: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    provenance: Mapping[str, Any]

    @property
    def fingerprint(self) -> str:
        return _digest(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "factor_fingerprint": self.factor_fingerprint,
            "status": self.status.value,
            "sample_size": self.sample_size,
            "coverage": self.coverage,
            "information_coefficient": self.information_coefficient,
            "information_ratio": self.information_ratio,
            "quantile_returns": list(self.quantile_returns),
            "long_short_return": self.long_short_return,
            "turnover": self.turnover,
            "transaction_cost": self.transaction_cost,
            "decay": {
                "horizons": list(self.decay.horizons),
                "information_coefficients": list(self.decay.information_coefficients),
            },
            "oos_status": self.oos_status,
            "oos_information_coefficient": self.oos_information_coefficient,
            "warnings": list(self.warnings),
            "evidence_refs": list(self.evidence_refs),
            "provenance": dict(self.provenance),
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class FactorAdmissionDecision:
    candidate_id: str
    status: str
    evaluation_fingerprint: str
    reasons: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    @property
    def fingerprint(self) -> str:
        return _digest(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "status": self.status,
            "evaluation_fingerprint": self.evaluation_fingerprint,
            "reasons": list(self.reasons),
            "evidence_refs": list(self.evidence_refs),
            "fingerprint": self.fingerprint,
        }


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _correlation(left: pd.Series, right: pd.Series) -> float | None:
    aligned = pd.concat([left.astype(float), right.astype(float)], axis=1).dropna()
    if len(aligned) < 2 or aligned.iloc[:, 0].nunique() < 2 or aligned.iloc[:, 1].nunique() < 2:
        return None
    return _finite(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))


def _empty_evaluation(candidate: FactorCandidate, status: FactorEvaluationStatus, warning: str) -> FactorEvaluation:
    return FactorEvaluation(
        candidate_id=candidate.candidate_id,
        factor_fingerprint=candidate.fingerprint,
        status=status,
        sample_size=0,
        coverage=0.0,
        information_coefficient=None,
        information_ratio=None,
        quantile_returns=(),
        long_short_return=None,
        turnover=None,
        transaction_cost=None,
        decay=FactorDecayProfile((), ()),
        oos_status="BLOCKED",
        oos_information_coefficient=None,
        warnings=(warning,),
        evidence_refs=(f"factor:{candidate.candidate_id}:rejected",),
        provenance={"source": candidate.source},
    )


def _label(value: Any) -> str | None:
    return None if value is None else str(value)


def _decay_profile(
    factor: pd.Series,
    frame: pd.DataFrame,
    forward_return: pd.Series,
    horizons: tuple[int, ...],
    shift_periods: int,
    scoring_index: pd.Index,
    label_horizon: int = 1,
) -> FactorDecayProfile:
    values: list[float | None] = []
    for horizon in horizons:
        if horizon == label_horizon:
            future = forward_return
        elif f"forward_return_{horizon}d" in frame:
            future = frame[f"forward_return_{horizon}d"]
        elif "close" in frame:
            future = frame["close"].pct_change(horizon, fill_method=None).shift(-horizon)
        else:
            future = pd.Series(index=frame.index, dtype=float)
        # Purge labels that extend beyond this phase's data boundary.
        phase_end = frame.index.get_loc(scoring_index[-1])
        allowed = frame.index[: phase_end + 1 - horizon].intersection(scoring_index)
        values.append(_correlation(factor.shift(shift_periods).loc[allowed], future.loc[allowed]))
    return FactorDecayProfile(horizons, tuple(values))


def evaluate_factor_candidate(
    candidate: FactorCandidate,
    frame: pd.DataFrame,
    forward_return: pd.Series,
    spec: Mapping[str, Any],
) -> FactorEvaluation:
    """Evaluate one candidate with fixed timing, costs, and split semantics."""

    shift_periods = int(spec.get("shift_periods", 1))
    if shift_periods < 1:
        return _empty_evaluation(candidate, FactorEvaluationStatus.LEAKAGE_BLOCKED, "T+1 shift is required")
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return _empty_evaluation(candidate, FactorEvaluationStatus.INSUFFICIENT_DATA, "factor frame is empty")
    if not isinstance(forward_return, pd.Series) or not forward_return.index.equals(frame.index):
        raise ValueError("forward_return must use the same index as frame")
    as_of = spec.get("as_of")
    available_at = spec.get("available_at")
    factor = evaluate_expression(candidate.expression, frame, as_of=as_of, available_at=available_at)
    bounded_frame = frame.loc[factor.index]
    aligned = pd.concat(
        [factor.shift(shift_periods).rename("factor"), forward_return.loc[factor.index].rename("forward")], axis=1
    ).replace([float("inf"), float("-inf")], float("nan"))
    label_horizon = int(spec.get("label_horizon", 1))
    if not 1 <= label_horizon <= 252:
        raise ValueError("label_horizon must be between 1 and 252")
    if spec.get("evaluation_start") is not None and spec.get("evaluation_end") is not None:
        start = pd.Timestamp(spec["evaluation_start"])
        end = pd.Timestamp(spec["evaluation_end"])
        aligned = aligned.loc[(aligned.index >= start) & (aligned.index <= end)]
    if spec.get("scoring_index") is not None:
        aligned = aligned.loc[aligned.index.intersection(spec["scoring_index"])]
    scoring_index = aligned.index
    aligned = aligned.dropna()
    coverage = len(aligned) / max(len(scoring_index), 1)
    min_samples = int(spec.get("min_samples", 8))
    if len(aligned) < min_samples:
        result = _empty_evaluation(candidate, FactorEvaluationStatus.INSUFFICIENT_DATA, "sample size is below the minimum")
        return replace(result, coverage=float(coverage), provenance={"source": candidate.source, "as_of": _label(as_of)})

    ic = _correlation(aligned["factor"], aligned["forward"])
    chunk_size = max(3, len(aligned) // 4)
    chunks = [aligned.iloc[start : start + chunk_size] for start in range(0, len(aligned), chunk_size)]
    chunk_ics = [value for value in (_correlation(chunk.factor, chunk.forward) for chunk in chunks) if value is not None]
    if len(chunk_ics) >= 2 and pd.Series(chunk_ics).std(ddof=1) > 1e-12:
        icir = _finite(pd.Series(chunk_ics).mean() / pd.Series(chunk_ics).std(ddof=1))
    else:
        icir = None

    quantiles = int(spec.get("quantiles", 5))
    if quantiles < 2 or quantiles > 10:
        raise ValueError("quantiles must be between 2 and 10")
    labels = pd.qcut(aligned["factor"].rank(method="first"), q=quantiles, labels=False, duplicates="drop") + 1
    quantile_returns = tuple((str(int(label)), float(aligned.loc[labels == label, "forward"].mean())) for label in sorted(labels.dropna().unique()))
    long_short = None if len(quantile_returns) < 2 else quantile_returns[-1][1] - quantile_returns[0][1]
    turnover = _finite(aligned["factor"].rank(pct=True).diff().abs().mean())
    transaction_cost = None if turnover is None else turnover * float(spec.get("cost_per_turnover", 0.0))

    train_ratio = float(spec.get("train_ratio", 0.6))
    validation_ratio = float(spec.get("validation_ratio", 0.2))
    if not 0 < train_ratio < 1 or not 0 < validation_ratio < 1 or train_ratio + validation_ratio >= 1:
        raise ValueError("train_ratio and validation_ratio must leave a test period")
    test_start = int(len(aligned) * (train_ratio + validation_ratio))
    oos = aligned if spec.get("evaluation_start") is not None else aligned.iloc[test_start:]
    phase = str(spec.get("phase", "test"))
    oos_status = "HIDDEN" if phase != "test" else ("BLOCKED" if spec.get("oos") is not True else ("PASS" if len(oos) >= 2 and _correlation(oos["factor"], oos["forward"]) is not None else "INSUFFICIENT_DATA"))
    oos_ic = _correlation(oos["factor"], oos["forward"]) if oos_status == "PASS" else None
    warnings: list[str] = []
    if ic is None:
        warnings.append("information coefficient is undefined")
    if icir is None:
        warnings.append("information ratio is not stable across blocks")
    if oos_status not in {"PASS", "HIDDEN"}:
        warnings.append("out-of-sample evaluation is unavailable")
    status = FactorEvaluationStatus.VALID if ic is not None and oos_status in {"PASS", "HIDDEN"} else FactorEvaluationStatus.UNSTABLE
    decay_horizons = tuple(int(item) for item in spec.get("decay_horizons", (1, 5, 20)))
    evidence = (
        f"factor:{candidate.candidate_id}:compute",
        f"factor:{candidate.candidate_id}:ic",
        f"factor:{candidate.candidate_id}:oos",
    )
    return FactorEvaluation(
        candidate_id=candidate.candidate_id,
        factor_fingerprint=candidate.fingerprint,
        status=status,
        sample_size=len(aligned),
        coverage=float(coverage),
        information_coefficient=ic,
        information_ratio=icir,
        quantile_returns=quantile_returns,
        long_short_return=_finite(long_short),
        turnover=turnover,
        transaction_cost=_finite(transaction_cost),
        decay=_decay_profile(factor, bounded_frame, forward_return.loc[factor.index], decay_horizons, shift_periods, scoring_index, label_horizon),
        oos_status=oos_status,
        oos_information_coefficient=oos_ic,
        warnings=tuple(warnings),
        evidence_refs=evidence,
        provenance={
            "source": candidate.source,
            "as_of": str(as_of) if as_of is not None else None,
            "shift_periods": shift_periods,
            "phase": phase,
            "evaluation_start": str(spec.get("evaluation_start")),
            "evaluation_end": str(spec.get("evaluation_end")),
            "method": "single-instrument time-series Pearson IC; block ICIR; ex-post quantile and rank-turnover diagnostics, not portfolio P&L",
        },
    )


def build_factor_admission(evaluation: FactorEvaluation, spec: Mapping[str, Any]) -> FactorAdmissionDecision:
    reasons = list(evaluation.warnings)
    if evaluation.status is not FactorEvaluationStatus.VALID:
        reasons.append(f"evaluation status is {evaluation.status.value}")
    if evaluation.information_coefficient is None or abs(evaluation.information_coefficient) < float(spec.get("min_abs_ic", 0.0)):
        reasons.append("absolute information coefficient is below the threshold")
    if evaluation.information_ratio is None or evaluation.information_ratio < float(spec.get("min_icir", float("-inf"))):
        reasons.append("information ratio is below the threshold")
    if evaluation.turnover is not None and evaluation.turnover > float(spec.get("max_turnover", float("inf"))):
        reasons.append("turnover exceeds the threshold")
    if spec.get("phase", "test") == "test" and evaluation.oos_status != "PASS":
        reasons.append("out-of-sample evaluation did not pass")
    status = "ADMITTED" if not reasons else "REJECTED"
    return FactorAdmissionDecision(
        candidate_id=evaluation.candidate_id,
        status=status,
        evaluation_fingerprint=evaluation.fingerprint,
        reasons=tuple(dict.fromkeys(reasons)),
        evidence_refs=evaluation.evidence_refs,
    )

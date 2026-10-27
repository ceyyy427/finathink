"""Bounded, paper-only factor parameter experiments.

This module is deliberately a thin orchestration layer over the existing
``evaluate_factor_candidate`` evaluator.  It expands a finite parameter grid,
evaluates each point on the validation/PIT phase, and keeps the OOS boundary
explicit.  No experiment result is an execution or trading recommendation.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import pandas as pd

from finahinking.factors.dsl import evaluate_expression
from finahinking.factors.evaluation import (
    FactorEvaluation,
    FactorEvaluationStatus,
    _empty_evaluation,
    evaluate_factor_candidate,
)
from finahinking.factors.mining import FactorCandidate

from .factor_pipeline import _dataset_digest
from .observability import current_runtime_budget
from .temporal_validation import block_sign_pvalue, bonferroni_adjust, purged_walk_forward


def _jsonable(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _digest(value: Any) -> str:
    encoded = json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validate_finite(value: Any, path: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{path} must contain only finite values")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _validate_finite(item, f"{path}[{key!r}]")
    elif isinstance(value, (list, tuple, set, frozenset)):
        for index, item in enumerate(value):
            _validate_finite(item, f"{path}[{index}]")


def _validate_parameter_mapping(value: Mapping[Any, Any], path: str) -> None:
    if not value:
        raise ValueError(f"{path} must contain at least one parameter axis")
    if any(not isinstance(key, str) or not key.strip() for key in value):
        raise TypeError(f"{path} keys must be non-empty strings")
    if len(set(value)) != len(value):
        raise ValueError(f"{path} contains duplicate parameter keys")
    _validate_finite(value, path)


def _stable_axis_key(value: Any) -> str:
    """Return a cross-process ordering key for unordered axis values."""

    return json.dumps({"type": type(value).__name__, "value": _jsonable(value)}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True, slots=True)
class FactorExperimentSpec:
    parameters: Mapping[str, Any] | Sequence[Mapping[str, Any]]
    max_experiments: int = 20
    split: Mapping[str, float] | None = None
    decay_horizons: tuple[int, ...] = (1, 5, 20)
    n_splits: int = 1
    label_horizon: int = 1
    embargo: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.max_experiments, bool) or not isinstance(self.max_experiments, int) or not 1 <= self.max_experiments <= 256:
            raise ValueError("max_experiments must be between 1 and 256")
        split = {"train": 0.6, "validation": 0.2, "test": 0.2} if self.split is None else dict(self.split)
        if set(split) != {"train", "validation", "test"}:
            raise ValueError("split must contain positive train, validation and test ratios summing to one")
        ratios = tuple(split.values())
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) <= 0 for value in ratios) or abs(sum(float(value) for value in ratios) - 1.0) > 1e-9:
            raise ValueError("split must contain positive finite train, validation and test ratios summing to one")
        if isinstance(self.parameters, Mapping):
            _validate_parameter_mapping(self.parameters, "parameters")
            for name, value in self.parameters.items():
                if isinstance(value, (list, tuple, set, frozenset)) and not value:
                    raise ValueError(f"parameter axis {name!r} must not be empty")
        elif isinstance(self.parameters, Sequence) and not isinstance(self.parameters, (str, bytes)):
            if not self.parameters:
                raise ValueError("parameters must contain at least one experiment mapping")
            for index, item in enumerate(self.parameters):
                if not isinstance(item, Mapping):
                    raise TypeError(f"parameters[{index}] must be a mapping")
                _validate_parameter_mapping(item, f"parameters[{index}]")
        else:
            raise TypeError("parameters must be a mapping or sequence of mappings")
        horizons = tuple(self.decay_horizons)
        if not horizons or any(isinstance(item, bool) or int(item) != item or int(item) < 1 for item in horizons):
            raise ValueError("decay_horizons must contain positive integers")
        object.__setattr__(self, "split", {key: float(split[key]) for key in ("train", "validation", "test")})
        object.__setattr__(self, "decay_horizons", tuple(int(item) for item in horizons))
        if any(type(value) is not int or not lower <= value <= upper for value, lower, upper in ((self.n_splits, 1, 8), (self.label_horizon, 1, 252), (self.embargo, 0, 252))):
            raise ValueError("invalid fold count, label horizon or embargo")

    def to_dict(self) -> dict[str, Any]:
        return {"parameters": _jsonable(self.parameters), "max_experiments": self.max_experiments, "split": dict(self.split or {}), "decay_horizons": list(self.decay_horizons), "n_splits": self.n_splits, "label_horizon": self.label_horizon, "embargo": self.embargo}


@dataclass(frozen=True, slots=True)
class FactorExperimentRecord:
    experiment_id: str
    parameters: Mapping[str, Any]
    evaluation: FactorEvaluation
    digest: str
    dataset_fingerprint: str
    config_digest: str
    research_fingerprint: str
    split: Mapping[str, float]
    limitations: tuple[str, ...]
    folds: tuple[Mapping[str, Any], ...] = ()
    failure_reason: str | None = None
    p_value: float | None = None
    adjusted_p_value: float | None = None
    sample_ids: tuple[tuple[str, ...], ...] = ()

    @property
    def ic(self) -> float | None:
        return self.evaluation.information_coefficient

    @property
    def ir(self) -> float | None:
        return self.evaluation.information_ratio

    @property
    def turnover(self) -> float | None:
        return self.evaluation.turnover

    @property
    def decay(self) -> Mapping[str, float | None]:
        return {str(horizon): value for horizon, value in zip(self.evaluation.decay.horizons, self.evaluation.decay.information_coefficients)}

    def to_dict(self) -> dict[str, Any]:
        return {"experiment_id": self.experiment_id, "parameters": _jsonable(self.parameters), "evaluation": self.evaluation.to_dict(), "digest": self.digest, "dataset_fingerprint": self.dataset_fingerprint, "config_digest": self.config_digest, "research_fingerprint": self.research_fingerprint, "split": dict(self.split), "ic": self.ic, "ir": self.ir, "turnover": self.turnover, "decay": dict(self.decay), "folds": _jsonable(self.folds), "failure_reason": self.failure_reason, "p_value": self.p_value, "adjusted_p_value": self.adjusted_p_value, "sample_ids": _jsonable(self.sample_ids), "limitations": list(self.limitations), "paper_only": True}


@dataclass(frozen=True, slots=True)
class FactorExperimentResult:
    experiments: tuple[FactorExperimentRecord, ...]
    ranking: tuple[str, ...]
    experiment_digest: str
    dataset_fingerprint: str
    config_digest: str
    research_fingerprint: str
    split: Mapping[str, float]
    limitations: tuple[str, ...]
    multiple_testing: Mapping[str, Any] = field(default_factory=dict)
    ranking_groups: tuple[Mapping[str, Any], ...] = ()

    @property
    def fingerprint(self) -> str:
        return self.experiment_digest

    def to_dict(self) -> dict[str, Any]:
        return {"experiments": [item.to_dict() for item in self.experiments], "ranking": list(self.ranking), "ranking_groups": list(self.ranking_groups), "experiment_digest": self.experiment_digest, "dataset_fingerprint": self.dataset_fingerprint, "config_digest": self.config_digest, "research_fingerprint": self.research_fingerprint, "split": dict(self.split), "multiple_testing": dict(self.multiple_testing), "limitations": list(self.limitations), "paper_only": True}

    def comparison_payload(self) -> dict[str, Any]:
        return {"schema_version": 1, "paper_only": True, "runs": [{"run_id": item.experiment_id, "inputs": {"parameters": _jsonable(item.parameters), "dataset_fingerprint": item.dataset_fingerprint, "config_digest": item.config_digest, "research_fingerprint": item.research_fingerprint}, "metrics": {"ic": item.ic, "ir": item.ir, "turnover": item.turnover, "decay": dict(item.decay)}, "limitations": list(item.limitations)} for item in self.experiments]}

    @property
    def comparison(self) -> dict[str, Any]:
        """Server-owned descriptive payload suitable for ``compare_experiments``."""

        return self.comparison_payload()


def _grid(parameters: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    if isinstance(parameters, Mapping):
        _validate_parameter_mapping(parameters, "parameters")
        names = tuple(sorted(parameters))
        axes = []
        for name in names:
            value = parameters[name]
            if isinstance(value, (set, frozenset)):
                value = tuple(sorted(value, key=_stable_axis_key))
            elif isinstance(value, (list, tuple)):
                value = tuple(value)
            if not value and isinstance(value, (list, tuple)):
                raise ValueError(f"parameter axis {name!r} must not be empty")
            _validate_finite(value, f"parameters[{name!r}]")
            axes.append(tuple(value) if isinstance(value, (list, tuple)) else (value,))
        return tuple(dict(zip(names, values)) for values in itertools.product(*axes))
    if isinstance(parameters, Sequence) and not isinstance(parameters, (str, bytes)):
        if not all(isinstance(item, Mapping) for item in parameters):
            raise TypeError("parameter sequence must contain mappings")
        return tuple(dict(item) for item in parameters)
    raise TypeError("parameters must be a mapping or sequence of mappings")


def _candidate_for(parameters: Mapping[str, Any], dataset: Mapping[str, Any], index: int) -> FactorCandidate:
    raw = parameters.get("expression", parameters.get("factor"))
    if raw is None:
        candidates = dataset.get("candidates", ())
        if candidates and isinstance(candidates, Sequence):
            candidate = candidates[index % len(candidates)]
            if isinstance(candidate, FactorCandidate):
                return candidate
            if isinstance(candidate, Mapping):
                raw = candidate.get("expression")
        raw = raw or "rank(close)"
    expression = str(raw).format(**dict(parameters))
    return FactorCandidate(f"experiment-{index:04d}", expression, "bounded parameter experiment", "factor-experiment", dict(parameters))


def run_factor_experiments(spec: FactorExperimentSpec, dataset: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> FactorExperimentResult:
    if not isinstance(spec, FactorExperimentSpec):
        raise TypeError("spec must be FactorExperimentSpec")
    if not isinstance(dataset, Mapping):
        raise TypeError("dataset must be a mapping")
    frame, forward = dataset.get("frame"), dataset.get("forward_return")
    if not isinstance(frame, pd.DataFrame) or not isinstance(forward, pd.Series):
        raise TypeError("dataset requires frame and forward_return")
    if not frame.index.equals(forward.index):
        raise ValueError("forward_return must use the same index as frame")
    dataset_fingerprint = _dataset_digest(dataset)
    config_payload = dict(config or dataset.get("config", {}))
    cost = config_payload.get("cost_per_turnover", 0.0)
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
        raise ValueError("cost_per_turnover must be finite and nonnegative")
    temporal = purged_walk_forward(frame.index, train_ratio=spec.split["train"], validation_ratio=spec.split["validation"], horizon=spec.label_horizon, embargo=spec.embargo, n_splits=spec.n_splits, label_end_times=dataset.get("label_end_times"))
    config_digest = _digest(config_payload)
    research_fingerprint = _digest({"dataset_fingerprint": dataset_fingerprint, "config_digest": config_digest, "spec": spec.to_dict()})
    grid = _grid(spec.parameters)
    if len(grid) > spec.max_experiments:
        grid = grid[: spec.max_experiments]
    if not grid:
        raise ValueError("parameter grid must contain at least one experiment")
    budget = current_runtime_budget()
    if budget is not None:
        budget.charge_experiments(len(grid) * (2 * spec.n_splits + 1))

    def evaluate(candidate, positions, cutoff):
        if not positions:
            return _empty_evaluation(candidate, FactorEvaluationStatus.INSUFFICIENT_DATA, "no samples remain after purge/embargo")
        evaluation_spec = {**config_payload, "phase": "validation", "oos": False, "train_ratio": spec.split["train"], "validation_ratio": spec.split["validation"], "decay_horizons": spec.decay_horizons, "label_horizon": spec.label_horizon, "shift_periods": int(config_payload.get("shift_periods", 1)), "scoring_index": frame.index[positions], "evaluation_start": frame.index[positions[0]], "evaluation_end": frame.index[positions[-1]], "as_of": frame.index[cutoff - 1], "min_samples": int(config_payload.get("min_samples", 8))}
        try:
            return evaluate_factor_candidate(candidate, frame, forward, evaluation_spec)
        except (ValueError, TypeError, KeyError):
            return _empty_evaluation(candidate, FactorEvaluationStatus.INVALID, "candidate failed DSL/field validation")

    records: list[FactorExperimentRecord] = []
    for index, parameters in enumerate(grid, start=1):
        candidate = _candidate_for(parameters, dataset, index)
        folds, fold_probabilities, scoring, sample_ids = [], [], [], []
        for fold in temporal["folds"]:
            training = evaluate(candidate, fold["train"], fold["validation_start"] - spec.embargo)
            validation = evaluate(candidate, fold["validation"], fold["validation_end"] - spec.embargo)
            scoring.extend(fold["validation"])
            probability = None
            identifiers = ()
            if validation.status is FactorEvaluationStatus.VALID:
                factor = evaluate_expression(candidate.expression, frame.iloc[:fold["validation_end"] - spec.embargo]).shift(1)
                pairs = pd.concat([factor.rename("factor"), forward.rename("forward")], axis=1).loc[frame.index[fold["validation"]]].dropna()
                identifiers = tuple(value.isoformat() for value in pairs.index)
                products = ((pairs.factor - pairs.factor.mean()) * (pairs.forward - pairs.forward.mean())).tolist()
                probability = block_sign_pvalue(products, block_size=max(4, spec.label_horizon))
            fold_probabilities.append(probability)
            sample_ids.append(identifiers)
            folds.append({"train": training.to_dict(), "validation": validation.to_dict(), "train_positions": fold["train"], "validation_positions": fold["validation"], "train_label_end": max((temporal["label_ends"][i] for i in fold["train"]), default=frame.index[0]).isoformat(), "validation_start": frame.index[fold["validation_start"]].isoformat(), "p_value": probability})
        evaluation = evaluate(candidate, scoring, temporal["validation_end"] - spec.embargo)
        failed = next((entry[phase]["status"] for entry in folds for phase in ("train", "validation") if entry[phase]["status"] != "VALID"), None)
        if failed is not None:
            evaluation = replace(evaluation, status=FactorEvaluationStatus(failed), warnings=(*evaluation.warnings, "not all walk-forward folds supplied valid evidence"))
        probability = max(fold_probabilities) if fold_probabilities and all(value is not None for value in fold_probabilities) else None
        failure = None if evaluation.status is FactorEvaluationStatus.VALID else evaluation.status.value
        limitations = tuple(dict.fromkeys((*evaluation.warnings, "OOS/test evidence remains hidden until an explicit frozen test evaluation", "paper-only descriptive factor diagnostics; no trade advice", "Sign randomization assumes independent sign-symmetric blocks; assumptions are not established by this diagnostic.")))
        digest = _digest({"parameters": parameters, "evaluation": evaluation.to_dict(), "folds": folds, "p_value": probability, "dataset_fingerprint": dataset_fingerprint, "config_digest": config_digest, "research_fingerprint": research_fingerprint})
        records.append(FactorExperimentRecord(f"experiment-{index:04d}", parameters, evaluation, digest, dataset_fingerprint, config_digest, research_fingerprint, spec.split, limitations, tuple(folds), failure, probability, sample_ids=tuple(sample_ids)))
    adjusted = bonferroni_adjust([record.p_value for record in records])
    records = [replace(record, adjusted_p_value=value, digest=_digest({"raw": record.digest, "adjusted_p_value": value, "family_size": len(records)})) for record, value in zip(records, adjusted)]
    def score(item: FactorExperimentRecord) -> tuple[float, str]:
        return (-abs(item.ic or 0.0), item.experiment_id)
    groups = {}
    for item in sorted(records, key=score):
        if item.failure_reason is None:
            group = _digest({"dataset": dataset_fingerprint, "config": config_digest, "split": spec.to_dict() | {"parameters": "VARIED"}, "samples": item.sample_ids})
            groups.setdefault(group, []).append(item.experiment_id)
    ranking_groups = tuple({"group_id": group, "ranking": members} for group, members in sorted(groups.items()))
    ranking = tuple(ranking_groups[0]["ranking"]) if len(ranking_groups) == 1 else ()
    result_digest = _digest({"experiments": [item.digest for item in records], "ranking": ranking, "dataset_fingerprint": dataset_fingerprint, "config_digest": config_digest, "research_fingerprint": research_fingerprint})
    multiple = {"method": "BONFERRONI", "family_size": len(records), "failed_trials": sum(item.failure_reason is not None for item in records), "raw_method": "max per-fold exact block-sign p-value", "alpha": 0.05, "selection_metric": "absolute validation Pearson IC, then declared experiment id; never test metrics"}
    return FactorExperimentResult(tuple(records), ranking, result_digest, dataset_fingerprint, config_digest, research_fingerprint, spec.split, ("bounded parameter grid", "PIT T+1 timing", "OOS/test remains gated", "paper-only; no trade advice"), multiple, ranking_groups)


__all__ = ["FactorExperimentRecord", "FactorExperimentResult", "FactorExperimentSpec", "block_sign_pvalue", "bonferroni_adjust", "purged_walk_forward", "run_factor_experiments"]

"""Nine-step evidence over the existing DSL, evaluator and paper backtest engine."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

from finahinking.data.models import Dataset, Provenance
from finahinking.factors.dsl import evaluate_expression
from finahinking.factors.evaluation import evaluate_factor_candidate
from finahinking.factors.mining import FactorCandidate
from finahinking.quant.artifacts import Artifact
from finahinking.quant.engines import BacktestEngine
from finahinking.quant.interfaces import BacktestConfig
from finahinking.quant.runtime import _code_commit

from .asset_catalog import ResearchAssetCatalog
from .contracts import _unsafe_public_text, stable_digest, to_jsonable
from .factor_inputs import FactorPanel
from .factor_recipes import factor_input_requirements, pilot_factor_specs, resolve_factor_spec
from .observability import RuntimeLimitExceeded, current_runtime_budget
from .quant_analytics import compute_parameter_surface, compute_performance_report
from .temporal_validation import purged_walk_forward

_STAGE_IDS = ("scope_data_audit", "semantic_check", "information_coefficient", "deduplication",
              "portfolio_backtest", "oos_stability", "incremental_value", "capacity", "archive_decision")
_STAGE_STATUSES = {"COMPLETE", "BLOCKED_DATA", "EXECUTION_ERROR", "NOT_RUN"}


def _policy_definition():
    return json.loads(ResearchAssetCatalog.packaged().read_text("factor-validation-v1"))


def factor_validation_policy(**overrides):
    values = {"version": "pilot-validation.v1", "phase": "validation", "min_samples": 8,
              "label_horizon": 1, "embargo": 1, "n_splits": 2, "train_ratio": 0.6,
              "validation_ratio": 0.2, "fee_bps": 5.0, "slippage_bps": 5.0,
              "starting_cash": 10000.0, "target_weight": 0.5, "max_volume_participation": 0.01,
              "min_abs_ic": 0.02, "max_abs_correlation": 0.95, "window_grid": [3, 5, 8],
              "max_neighbor_delta": 0.1, "code_version": _code_commit(), "annualization": 1}
    values["combination_weights"] = [0.5, 0.5]
    if set(overrides) - set(values):
        raise ValueError("unsupported validation policy fields")
    values.update(overrides)
    if values["phase"] not in {"validation", "test"} or values["version"] != "pilot-validation.v1":
        raise ValueError("unsupported validation policy phase or version")
    for name, low, high in (("min_samples", 4, 1000), ("label_horizon", 1, 20), ("embargo", 0, 20),
                            ("n_splits", 1, 4), ("annualization", 1, 366)):
        if type(values[name]) is not int or not low <= values[name] <= high:
            raise ValueError("validation integer control is out of range")
    bounds = {"train_ratio": (0.1, 0.8), "validation_ratio": (0.05, 0.5), "fee_bps": (0, 100),
              "slippage_bps": (0, 100), "starting_cash": (1, 100000000), "target_weight": (0.01, 0.9),
              "max_volume_participation": (0.00001, 1), "min_abs_ic": (0, 1),
              "max_abs_correlation": (0.5, 1), "max_neighbor_delta": (0, 1)}
    if any(isinstance(values[name], bool) or not isinstance(values[name], (int, float))
           or not math.isfinite(values[name]) or not low <= values[name] <= high for name, (low, high) in bounds.items()):
        raise ValueError("validation numeric controls must be finite and bounded")
    if values["train_ratio"] + values["validation_ratio"] >= 0.95:
        raise ValueError("validation policy must reserve a final holdout")
    grid = values["window_grid"]
    if (not isinstance(grid, (list, tuple)) or not 1 <= len(grid) <= 8
            or any(type(value) is not int or not 2 <= value <= 60 for value in grid)
            or len(set(grid)) != len(grid)):
        raise ValueError("declare one to eight bounded distinct windows")
    if (not isinstance(values["code_version"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", values["code_version"])
            or _unsafe_public_text(values["code_version"])):
        raise ValueError("validation code version must be explicit")
    values["window_grid"] = sorted(grid)
    if values["combination_weights"] != [0.5, 0.5]:
        raise ValueError("the initial pilot blend is frozen at equal train-standardized weights")
    return to_jsonable(values)


def factor_pair_diagnostic(left, right, *, min_samples=8, threshold=0.95):
    if not isinstance(left, pd.Series) or not isinstance(right, pd.Series):
        raise TypeError("factor correlation requires aligned numeric series")
    paired = pd.concat([left.rename("left"), right.rename("right")], axis=1).replace(
        [float("inf"), float("-inf")], float("nan")).dropna()
    value = None
    if len(paired) < min_samples:
        status = "INSUFFICIENT_SAMPLES"
    elif paired["left"].nunique() < 2 or paired["right"].nunique() < 2:
        status = "UNDEFINED_CONSTANT"
    else:
        value = float(paired["left"].rank().corr(paired["right"].rank()))
        status = "DUPLICATE" if abs(value) >= threshold else "DISTINCT"
    return {"status": status, "samples": len(paired), "correlation": value,
            "absolute_correlation": None if value is None else abs(value), "method": "spearman"}


@dataclass(frozen=True, slots=True, repr=False)
class FactorValidationRun:
    _json: str

    def __post_init__(self):
        value = json.loads(self._json)
        if (not isinstance(value, dict) or value.get("schema_version") != "factor-validation-run.v1"
                or value.get("fingerprint") != stable_digest({key: item for key, item in value.items() if key != "fingerprint"})
                or stable_digest(value["bindings"]) != value["binding_digest"]):
            raise ValueError("factor validation run integrity mismatch")
        if (stable_digest(value["source_policy"]) != value["bindings"]["source_policy_fingerprint"]
                or tuple(row["stage_id"] for row in value["source_policy"]["stages"]) != _STAGE_IDS
                or tuple(row["stage_id"] for row in value["steps"]) != _STAGE_IDS):
            raise ValueError("factor validation stage identity mismatch")
        for row in value["steps"]:
            if row["binding_digest"] != value["binding_digest"] or row["status"] not in _STAGE_STATUSES:
                raise ValueError("factor validation step binding mismatch")
            if row["artifact_ref"] is not None:
                document = value["artifacts"].get(row["artifact_ref"])
                if document is None or document["metrics"] != row["metrics"] or document["binding_digest"] != row["binding_digest"]:
                    raise ValueError("factor stage metrics differ from their computation artifact")
                artifact = Artifact.create(f"fv-{row['stage_id']}", "factor-validation-step", document,
                                            created_at=value["bindings"]["as_of"])
                if row["artifact_ref"] != f"artifact:{artifact.fingerprint}":
                    raise ValueError("factor stage artifact content digest mismatch")

    @classmethod
    def from_dict(cls, value):
        return cls(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))

    def to_dict(self):
        return json.loads(self._json)

    @property
    def fingerprint(self):
        return self.to_dict()["fingerprint"]


def _frames(panel):
    frame = panel.to_frame()
    return {asset: frame.xs(asset, level="asset").drop(columns=["asset", "timestamp"])
            for asset in frame["asset"].unique()}


def _oriented(spec):
    return spec.formula if spec.direction == "HIGHER" else f"negate({spec.formula})"


def _temporal(frames, policy):
    index = pd.DatetimeIndex(sorted({time for frame in frames.values() for time in frame.index}))
    ends = {time: [] for time in index}
    horizon = policy["label_horizon"]
    for frame in frames.values():
        for position, time in enumerate(frame.index):
            if position + horizon < len(frame):
                ends[time].append(frame.index[position + horizon])
    label_ends = pd.Series([max(ends[time]) if ends[time] else pd.NaT for time in index], index=index)
    split = purged_walk_forward(index, train_ratio=policy["train_ratio"], validation_ratio=policy["validation_ratio"],
        horizon=horizon, embargo=policy["embargo"], n_splits=policy["n_splits"], label_end_times=label_ends)
    return index, split


def _score(spec, frames, policy, scoring):
    evaluations, serial_estimates, aligned_scores = [], [], []
    for asset, frame in frames.items():
        budget = current_runtime_budget()
        if budget is not None:
            budget.charge_experiments(1)
        forward = frame["close"].shift(-policy["label_horizon"]) / frame["close"] - 1
        candidate = FactorCandidate(spec.factor_id, _oriented(spec), "frozen pilot hypothesis", "finathink-native",
                                    {"factor_spec_fingerprint": spec.fingerprint})
        result = evaluate_factor_candidate(candidate, frame, forward,
            {"shift_periods": 1, "min_samples": policy["min_samples"], "phase": policy["phase"],
             "oos": policy["phase"] == "test", "scoring_index": frame.index.intersection(scoring),
             "label_horizon": policy["label_horizon"], "decay_horizons": (1, 5, 20),
             "evaluation_start": None if scoring.empty else scoring[0],
             "evaluation_end": None if scoring.empty else scoring[-1],
             "cost_per_turnover": (policy["fee_bps"] + policy["slippage_bps"]) / 10000})
        evaluations.append({"asset": asset, "status": result.status.value,
            "ic": result.information_coefficient, "block_icir": result.information_ratio,
            "samples": result.sample_size, "coverage": result.coverage,
            "evaluation_fingerprint": result.fingerprint, "decay": {
                "horizons": list(result.decay.horizons), "ic": list(result.decay.information_coefficients)}})
        factor = evaluate_expression(_oriented(spec), frame).shift(1)
        pair = pd.concat([factor.rename("signal"), forward.rename("label")], axis=1).loc[
            frame.index.intersection(scoring)].dropna()
        autocorrelation = None
        if len(pair) > 3 and pair["signal"].std() > 0 and pair["label"].std() > 0:
            products = ((pair["signal"] - pair["signal"].mean()) / pair["signal"].std()
                        * (pair["label"] - pair["label"].mean()) / pair["label"].std())
            ac = products.autocorr(1) if products.nunique() > 1 else None
            if ac is not None and math.isfinite(ac):
                autocorrelation = float(ac)
                serial_estimates.append((autocorrelation, len(products) - 1))
        evaluations[-1]["product_lag1_autocorrelation"] = autocorrelation
        for time, row in pair.iterrows():
            aligned_scores.append({"timestamp": time.isoformat(), "asset": asset,
                                   "signal": float(row["signal"]), "label": float(row["label"])})
    valid = [row["ic"] for row in evaluations if row["ic"] is not None]
    cross_ics = []
    if aligned_scores:
        points = pd.DataFrame(aligned_scores)
        for _, group in points.groupby("timestamp", sort=True):
            if len(group) >= 3 and group["signal"].nunique() > 1 and group["label"].nunique() > 1:
                cross_ics.append(float(group["signal"].rank().corr(group["label"].rank())))
    ac = math.fsum(value * count for value, count in serial_estimates) / sum(count for _, count in serial_estimates) if serial_estimates else None
    return {"metric_basis": "IC_LABEL_DIAGNOSTIC_NOT_PORTFOLIO_PNL", "per_asset": evaluations,
            "mean_oriented_time_series_ic": None if not valid else math.fsum(valid) / len(valid),
            "mean_cross_sectional_spearman_ic": None if not cross_ics else math.fsum(cross_ics) / len(cross_ics),
            "cross_sectional_ic_days": len(cross_ics), "minimum_cross_section_assets": 3,
            "product_lag1_autocorrelation": None if ac is None or not math.isfinite(ac) else float(ac),
            "serial_scope": "WITHIN_ASSET_ONLY",
            "label_horizon_observations": policy["label_horizon"], "signal_delay_observations": 1}


class _FrozenSignalStrategy:
    strategy_id, version = "frozen-pilot-threshold", "v1"

    def __init__(self, signal, threshold, weight, active_from):
        self.signal, self.threshold, self.weight, self.active_from = signal, threshold, weight, active_from

    def generate(self, dataset):
        values = self.signal.reindex(dataset.frame.index)
        return pd.Series(np.where(values.notna() & (values >= self.threshold)
                                  & (values.index >= self.active_from), self.weight, 0.0), index=dataset.frame.index)


class FactorValidationGateway:
    """Trusted adapter to the existing paper engine; no model/network or tool grants."""

    def __init__(self, *, study_store=None, study_ids=None):
        self.study_store, self.study_ids = study_store, dict(study_ids or {})

    def authorize_test(self, candidate_fingerprint, bindings, policy):
        if self.study_store is None or set(self.study_ids) != set(bindings["universe"]["instruments"]):
            return False
        for asset, study_id in self.study_ids.items():
            row = self.study_store.get(study_id)
            spec = row["spec"]
            if (row["state"] not in {"TEST_RESERVED", "TESTING"} or row["test_key"] is None
                    or row["candidate_fingerprint"] != candidate_fingerprint
                    or spec["snapshot_ref"] != bindings["snapshot_ref"] or spec["instrument"] != asset
                    or spec["test_period"] != bindings["final_test_window"]):
                return False
        return True

    def backtest(self, spec, frames, policy, *, train_index, evaluation_index, snapshot_ref, secondary_spec=None):
        if any(not evaluation_index.isin(frame.index).all() for frame in frames.values()):
            raise ValueError("BLOCKED_DATA: portfolio calendars are not aligned")
        budget = current_runtime_budget()
        if budget is not None:
            budget.charge_experiments(len(frames))
        equity, benchmark, trades, quant_runs = {}, {}, [], []
        cash_per_asset = policy["starting_cash"] / len(frames)
        for asset, original in frames.items():
            frame = original.loc[original.index <= evaluation_index[-1]].copy()
            if frame["suspended"].isna().any():
                raise ValueError("BLOCKED_DATA: suspension availability is unknown")
            signal = evaluate_expression(_oriented(spec), frame)
            if secondary_spec is not None:
                secondary = evaluate_expression(_oriented(secondary_spec), frame)
                first_train = signal.loc[signal.index.intersection(train_index)].dropna()
                second_train = secondary.loc[secondary.index.intersection(train_index)].dropna()
                if first_train.std() <= 1e-12 or second_train.std() <= 1e-12:
                    raise ValueError("BLOCKED_DATA: blend training scale is undefined")
                signal = (policy["combination_weights"][0] * (signal - first_train.mean()) / first_train.std()
                          + policy["combination_weights"][1] * (secondary - second_train.mean()) / second_train.std())
            train = signal.loc[signal.index.intersection(train_index)].dropna()
            if len(train) < policy["min_samples"]:
                raise ValueError("BLOCKED_DATA: training threshold cannot be estimated")
            first = frame.index.get_indexer([evaluation_index[0]])[0]
            if first < 1:
                raise ValueError("BLOCKED_DATA: signal needs a preceding observable bar")
            strategy = _FrozenSignalStrategy(signal, float(train.median()), policy["target_weight"], frame.index[first - 1])
            columns = list(dict.fromkeys(["open", "high", "low", "close", "volume", *spec.required_fields,
                                         *(secondary_spec.required_fields if secondary_spec else ())]))
            if frame["suspended"].notna().all():
                columns.append("suspended")
            dataset = Dataset(frame[columns], Provenance("user-snapshot", f"snapshot:{snapshot_ref}", license="USER_SUPPLIED_NOT_VERIFIED"))
            config = BacktestConfig(cash_per_asset, policy["fee_bps"], policy["slippage_bps"],
                annualization=policy["annualization"], max_volume_participation=policy["max_volume_participation"])
            result = BacktestEngine().run(dataset, strategy, config)
            all_equity = pd.Series(dict(result.equity_curve), dtype=float)
            all_equity.index = pd.to_datetime(all_equity.index, utc=True)
            equity[asset] = all_equity.loc[evaluation_index]
            prices = frame.loc[evaluation_index, "close"]
            benchmark[asset] = cash_per_asset * prices / prices.iloc[0]
            trades.extend({"asset": asset, **trade.to_dict()} for trade in result.trades
                          if pd.Timestamp(trade.timestamp) in evaluation_index)
            quant_runs.append({"asset": asset, "dataset_fingerprint": result.dataset_fingerprint,
                "backtest_fingerprint": result.fingerprint, "engine_version": result.engine_version,
                "threshold": float(train.median()), "threshold_scope": "TRAIN_ONLY"})
        combined = pd.DataFrame(equity).sum(axis=1, min_count=len(equity))
        benchmark_equity = pd.DataFrame(benchmark).sum(axis=1, min_count=len(benchmark))
        returns = combined.pct_change(fill_method=None)
        returns.iloc[0] = combined.iloc[0] / policy["starting_cash"] - 1
        benchmark_returns = benchmark_equity.pct_change(fill_method=None).fillna(0.0)
        entries = [{"timestamp": time.isoformat(), "equity": float(value),
                    "fees": math.fsum(trade["fees"] for trade in trades if pd.Timestamp(trade["timestamp"]) == time),
                    "slippage": math.fsum(trade["slippage"] for trade in trades if pd.Timestamp(trade["timestamp"]) == time)}
                   for time, value in combined.items()]
        performance = compute_performance_report({"entries": entries, "returns": returns.tolist()},
            {"returns": benchmark_returns.tolist()}, annualization=policy["annualization"])
        return {"metric_basis": "ACTUAL_NET_LEDGER", "performance": performance.to_dict(), "entries": entries,
                "trades": trades, "fee_total": math.fsum(trade["fees"] for trade in trades),
                "slippage_total": math.fsum(trade["slippage"] for trade in trades), "quant_runs": quant_runs,
                "snapshot_ref": snapshot_ref, "fee_bps": policy["fee_bps"], "slippage_bps": policy["slippage_bps"],
                "execution": "previous_observation_signal_next_observed_close",
                "benchmark": "equal_initial_capital_gross_buy_and_hold", "annualization": policy["annualization"],
                "factor_spec_fingerprint": spec.fingerprint,
                "secondary_factor_spec_fingerprint": None if secondary_spec is None else secondary_spec.fingerprint}


def _baseline(spec):
    return next(value for value in pilot_factor_specs(spec.window) if value.factor_id == "finathink-return-dispersion")


def _factor_values(spec, frames):
    values = []
    for asset, frame in frames.items():
        signal = evaluate_expression(_oriented(spec), frame).shift(1)
        signal.index = pd.MultiIndex.from_arrays([[asset] * len(signal), signal.index], names=["asset", "timestamp"])
        values.append(signal)
    return pd.concat(values)


def _incremental(spec, baseline, frames, policy, train, heldout):
    if spec.factor_id == baseline.factor_id:
        return {"status": "BASELINE_SELF", "incremental_ic": None, "per_asset": []}
    records = []
    for asset, frame in frames.items():
        current = evaluate_expression(_oriented(spec), frame).shift(1)
        control = evaluate_expression(_oriented(baseline), frame).shift(1)
        paired = pd.concat([current.rename("candidate"), control.rename("baseline")], axis=1)
        fitting = paired.loc[paired.index.intersection(train)].dropna()
        if len(fitting) < policy["min_samples"] or fitting["baseline"].nunique() < 2:
            records.append({"asset": asset, "status": "INSUFFICIENT_TRAIN_BASELINE", "residual_ic": None})
            continue
        coefficients = np.linalg.lstsq(np.column_stack([np.ones(len(fitting)), fitting["baseline"]]),
                                       fitting["candidate"].to_numpy(), rcond=None)[0]
        residual = current - coefficients[0] - coefficients[1] * control
        labels = frame["close"].shift(-policy["label_horizon"]) / frame["close"] - 1
        scored = pd.concat([residual.rename("residual"), labels.rename("label")], axis=1).loc[
            paired.index.intersection(heldout)].dropna()
        value = None
        if len(scored) >= policy["min_samples"] and scored["residual"].nunique() > 1 and scored["label"].nunique() > 1:
            value = float(scored["residual"].rank().corr(scored["label"].rank()))
        records.append({"asset": asset, "status": "COMPLETE" if value is not None else "UNDEFINED",
                        "residual_ic": value, "intercept": float(coefficients[0]), "beta": float(coefficients[1]),
                        "fit_scope": "TRAIN_ONLY", "heldout_samples": len(scored)})
    available = [row["residual_ic"] for row in records if row["residual_ic"] is not None]
    return {"status": "COMPLETE" if available else "UNDEFINED", "per_asset": records,
            "incremental_ic": None if not available else math.fsum(available) / len(available),
            "baseline_factor_id": baseline.factor_id,
            "limitation": "Linear train-only residualization is descriptive, not causal independence or net-PnL attribution."}


def _stability(spec, frames, policy, index, split):
    training_ends = [split["label_ends"][position] for position in split["train"]]
    summary = {"horizon": split["horizon"], "embargo": split["embargo"],
               "train_samples": len(split["train"]), "validation_samples": len(split["validation"]),
               "max_training_label_end": max(training_ends).isoformat() if training_ends else None,
               "validation_start": index[split["train_end"]].isoformat(),
               "validation_end": index[split["validation_end"] - 1].isoformat(),
               "final_test": "HIDDEN" if policy["phase"] == "validation" else "RESERVED_FROZEN_TEST"}
    if policy["phase"] == "test":
        return {"split": summary, "parameter_surface": None, "ranking": [],
                "reason": "Final-test results never retune or rerank frozen parameters."}
    points, folds = [], []
    for window in policy["window_grid"]:
        alternate = next(value for value in pilot_factor_specs(window) if value.factor_id == spec.factor_id)
        values = []
        for number, fold in enumerate(split["folds"]):
            evaluation = _score(alternate, frames, policy, index[fold["validation"]])
            metric = evaluation["mean_oriented_time_series_ic"]
            values.append(metric)
            folds.append({"window": window, "fold": number, "ic": metric,
                          "training_samples": len(fold["train"]), "validation_samples": len(fold["validation"])})
        known = [value for value in values if value is not None]
        points.append({"parameters": {"window": window}, "metric": None if not known else math.fsum(known) / len(known),
                       "status": "VALID" if known else "MISSING", "passed": len(known) == len(values)})
    surface = compute_parameter_surface(points, axes={"window": policy["window_grid"]},
        min_metric=policy["min_abs_ic"], max_neighbor_delta=policy["max_neighbor_delta"])
    return {"split": summary, "folds": folds, "parameter_surface": surface.to_dict(),
            "ranking": [row["parameters"]["window"] for row in sorted(points,
                key=lambda row: (row["metric"] is None, -(row["metric"] or 0), row["parameters"]["window"]))]}


def run_factor_validation(spec, panel, policy, *, gateway) -> FactorValidationRun:
    if not isinstance(panel, FactorPanel):
        raise TypeError("typed factor panel required")
    policy = factor_validation_policy(**policy)
    definition = _policy_definition()
    source = panel.to_dict()
    frames = _frames(panel) if source["rows"] else {}
    bindings = {"snapshot_ref": panel.snapshot_ref, "attachment_ref": panel.attachment_ref,
        "panel_fingerprint": panel.fingerprint, "as_of": source["as_of"], "universe": source["universe"],
        "factor_id": spec.factor_id, "factor_version": spec.version, "factor_spec_fingerprint": spec.fingerprint,
        "formula": spec.formula, "implementation_fingerprint": spec.implementation_fingerprint,
        "execution_config": {"engine_version": BacktestEngine.engine_version, "signal_delay": 1,
            "fill": "next_observed_close", "allocation": "equal_initial_capital_per_asset",
            "target_weight": policy["target_weight"], "max_volume_participation": policy["max_volume_participation"]},
        "cost_bps": {"fee": policy["fee_bps"], "slippage": policy["slippage_bps"]},
        "benchmark": "equal_initial_capital_gross_buy_and_hold", "runtime_policy": policy,
        "source_policy_fingerprint": stable_digest(definition), "code_version": policy["code_version"],
        "train_window": None, "oos_window": None, "final_test_window": None,
        "final_test_usage": "HIDDEN" if policy["phase"] == "validation" else "REQUIRES_RESERVATION"}
    index = split = None
    if frames:
        try:
            index, split = _temporal(frames, policy)
            bindings.update(train_window=[index[0].isoformat(), index[split["train_end"] - 1].isoformat()],
                oos_window=[index[split["train_end"]].isoformat(), index[split["validation_end"] - 1].isoformat()],
                final_test_window=[index[split["validation_end"]].isoformat(), index[-1].isoformat()])
        except ValueError:
            pass
    neutral = {**bindings, "runtime_policy": {key: value for key, value in policy.items() if key != "phase"}}
    neutral.pop("final_test_usage")
    candidate_fingerprint = stable_digest(neutral)
    if policy["phase"] == "test":
        permitted = index is not None and hasattr(gateway, "authorize_test") and gateway.authorize_test(candidate_fingerprint, bindings, policy)
        bindings["final_test_usage"] = "RESERVED_FROZEN_TEST" if permitted else "NOT_AUTHORIZED"
    else:
        permitted = True
    binding_digest = stable_digest(bindings)
    steps = [{"stage_id": row["stage_id"], "name": row["name"], "status": "NOT_RUN", "reason": "UPSTREAM_NOT_RUN",
              "metrics": {}, "artifact_ref": None, "binding_digest": binding_digest} for row in definition["stages"]]
    artifacts = {}

    def record(position, status, metrics, reason=None):
        row = steps[position]
        document = to_jsonable({"stage_id": row["stage_id"], "status": status, "metrics": metrics,
                               "reason": reason, "binding_digest": binding_digest})
        artifact = Artifact.create(f"fv-{row['stage_id']}", "factor-validation-step", document, created_at=source["as_of"])
        reference = f"artifact:{artifact.fingerprint}"
        artifacts[reference] = artifact.payload
        row.update(status=status, reason=reason, metrics=document["metrics"], artifact_ref=reference)

    def finish(status):
        value = {"schema_version": "factor-validation-run.v1", "status": status, "bindings": bindings,
            "binding_digest": binding_digest, "candidate_fingerprint": candidate_fingerprint, "steps": steps,
            "artifacts": artifacts, "factor_spec": spec.to_dict(), "policy": policy, "source_policy": definition,
            "final_test_metrics": steps[2]["metrics"] if policy["phase"] == "test" and steps[2]["status"] == "COMPLETE" else None,
            "reproduction_scope": "FORMULA_FIXTURE" if source["data_mode"] in {"SAMPLE", "SYNTHETIC"} else "USER_DATA_EVALUATION",
            "requires_human_admission": True, "production_write": False, "paper_only": True,
            "financial_falsification": False,
            "limitations": [*source["limitations"], "Fixture calculations never reproduce upstream metrics.",
                "IC labels, tradable ledger returns and constant-slippage capacity scenarios are different quantities."]}
        value["fingerprint"] = stable_digest(value)
        return FactorValidationRun.from_dict(value)

    if not permitted or policy["code_version"] != _code_commit():
        return finish("BLOCKED_CONTROL")
    missing = panel.missing_inputs(spec.required_fields)
    if panel.status != "READY" or missing or split is None:
        record(0, "BLOCKED_DATA", {"missing_fields": list(missing), "issues": list(panel.issues),
                                   "rows": len(source["rows"])}, "MISSING_FIELDS_AVAILABILITY_OR_SPLIT")
        return finish("BLOCKED_DATA")
    try:
        resolve_factor_spec(spec)
        expected_units = factor_input_requirements(spec)
    except (ValueError, KeyError):
        record(1, "NOT_RUN", {}, "SEMANTIC_REJECTION")
        return finish("SEMANTIC_REJECTION")
    unit_gaps = [name for name, unit in expected_units.items() if panel.units.get(name) != unit]
    if unit_gaps:
        record(0, "BLOCKED_DATA", {"missing_fields": [], "unit_gaps": unit_gaps}, "UNIT_OR_DENOMINATOR_MISMATCH")
        return finish("BLOCKED_DATA")
    record(0, "COMPLETE", {"rows": len(source["rows"]), "assets": len(frames), "missing_fields": [],
                           "original_units": source["original_units"], "normalized_units": panel.units,
                           "data_license": "USER_DECLARATION_NOT_INDEPENDENTLY_VERIFIED"})
    phase_start = split["train_end"] if policy["phase"] == "validation" else split["validation_end"]
    phase_end = split["validation_end"] if policy["phase"] == "validation" else len(index)
    scoring = index[split[policy["phase"]]]
    raw_phase = index[phase_start:phase_end]
    # No final-holdout values enter any validation calculation or emitted statistic.
    bounded = {asset: frame.loc[frame.index < index[split["validation_end"]]] for asset, frame in frames.items()} if policy["phase"] == "validation" else frames
    try:
        for frame in bounded.values():
            evaluate_expression(spec.formula, frame)
        record(1, "COMPLETE", {"formula": spec.formula, "window": spec.window, "direction": spec.direction,
                               "missing_policy": spec.missing_policy, "std_ddof": 0,
                               "execution_primitive": "existing_bounded_dsl"})
        evaluation = _score(spec, bounded, policy, scoring)
        if all(row["ic"] is None for row in evaluation["per_asset"]):
            record(2, "BLOCKED_DATA", evaluation, "INSUFFICIENT_OR_CONSTANT_IC_PAIRS")
        else:
            record(2, "COMPLETE", evaluation)
        baseline = _baseline(spec)
        factor_values, baseline_values = _factor_values(spec, bounded), _factor_values(baseline, bounded)
        train_mask = factor_values.index.get_level_values("timestamp").isin(index[split["train"]])
        duplicate = factor_pair_diagnostic(factor_values.loc[train_mask], baseline_values.loc[train_mask],
            min_samples=policy["min_samples"], threshold=policy["max_abs_correlation"])
        record(3, "COMPLETE" if duplicate["correlation"] is not None else "BLOCKED_DATA", duplicate,
               None if duplicate["correlation"] is not None else duplicate["status"])
        try:
            backtest = gateway.backtest(spec, bounded, policy, train_index=index[split["train"]],
                                        evaluation_index=raw_phase, snapshot_ref=panel.snapshot_ref)
            if (backtest["snapshot_ref"] != panel.snapshot_ref or backtest["fee_bps"] != policy["fee_bps"]
                    or backtest["slippage_bps"] != policy["slippage_bps"]):
                raise ValueError("backtest evidence input/cost binding mismatch")
            record(4, "COMPLETE", backtest)
        except ValueError as exc:
            if str(exc).startswith("BLOCKED_DATA:"):
                record(4, "BLOCKED_DATA", {}, "EXECUTION_INPUTS_UNAVAILABLE")
            else:
                raise
        record(5, "COMPLETE", _stability(spec, bounded, policy, index, split))
        increment = _incremental(spec, baseline, bounded, policy, index[split["train"]], scoring)
        if spec.factor_id != baseline.factor_id and steps[4]["status"] == "COMPLETE":
            try:
                baseline_ledger = gateway.backtest(baseline, bounded, policy, train_index=index[split["train"]],
                    evaluation_index=raw_phase, snapshot_ref=panel.snapshot_ref)
                combination_ledger = gateway.backtest(spec, bounded, policy, train_index=index[split["train"]],
                    evaluation_index=raw_phase, snapshot_ref=panel.snapshot_ref, secondary_spec=baseline)
                for ledger in (baseline_ledger, combination_ledger):
                    if (ledger["snapshot_ref"] != panel.snapshot_ref or ledger["fee_bps"] != policy["fee_bps"]
                            or ledger["slippage_bps"] != policy["slippage_bps"]):
                        raise ValueError("ablation ledger scope mismatch")
                increment["portfolio_ablation"] = {"candidate_artifact_ref": steps[4]["artifact_ref"],
                    "baseline": baseline_ledger, "combination": combination_ledger,
                    "weights": policy["combination_weights"], "normalization": "TRAIN_ONLY"}
            except ValueError as exc:
                if str(exc).startswith("BLOCKED_DATA:"):
                    increment.update(status="UNDEFINED", portfolio_ablation=None)
                else:
                    raise
        record(6, "COMPLETE" if increment["status"] in {"COMPLETE", "BASELINE_SELF"} else "BLOCKED_DATA", increment)
        required_capacity = panel.units.get("volume") == "shares" and panel.units.get("amount") in {"CNY", "USD", "HKD", "EUR", "JPY", "GBP"}
        capacity = pd.concat([frame.loc[frame.index.intersection(raw_phase)] for frame in bounded.values()])
        if (not required_capacity or capacity["amount"].isna().any() or capacity["suspended"].isna().any()):
            record(7, "BLOCKED_DATA", {}, "CAPACITY_UNITS_OR_TRADABILITY_UNKNOWN")
        else:
            available = capacity.loc[~capacity["suspended"] & (capacity["volume"] > 0), "amount"]
            record(7, "COMPLETE", {"model": "declared_constant_slippage_and_volume_participation",
                "max_volume_participation": policy["max_volume_participation"], "tradable_rows": len(available),
                "minimum_amount_capacity": 0.0 if available.empty else float(available.min() * policy["max_volume_participation"]),
                "slippage_bps": policy["slippage_bps"], "impact_calibration": "NOT_VERIFIED",
                "limitation": "A participation bound is not proof of available live liquidity or calibrated market impact."})
        blocked = any(row["status"] == "BLOCKED_DATA" for row in steps[:8])
        oriented_ic = evaluation["mean_oriented_time_series_ic"]
        rejected = oriented_ic is None or oriented_ic < policy["min_abs_ic"] or duplicate["status"] == "DUPLICATE"
        record(8, "COMPLETE", {"decision": "OBSERVATION_ONLY" if blocked or rejected else "PENDING_HUMAN_RESEARCH_REVIEW",
            "source_metrics_inherited": False, "automatic_admission": False,
            "evaluation_outcome": "COMPLETED_WITH_REJECTION" if rejected else "COMPLETED_PENDING_REVIEW",
            "quality_threshold": policy["min_abs_ic"], "oriented_ic": evaluation["mean_oriented_time_series_ic"],
            "duplicate_with_baseline": duplicate["status"] == "DUPLICATE"})
        return finish("COMPLETE_WITH_BLOCKS" if blocked else "COMPLETE")
    except RuntimeLimitExceeded:
        raise  # Existing worker owns resource exhaustion/cancel semantics, not factor quality.
    except Exception:  # noqa: BLE001 - preserve prior evidence, never expose engine/material exceptions
        position = next((number for number in range(1, 9) if steps[number]["status"] == "NOT_RUN"), 8)
        record(position, "EXECUTION_ERROR", {}, "EXECUTION_ERROR")
        return finish("EXECUTION_ERROR")

"""Snapshot-bound factor study computation inside the existing spawn worker."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from itertools import pairwise

import pandas as pd

from finahinking.factors.dsl import evaluate_expression
from finahinking.factors.evaluation import evaluate_factor_candidate

from .contracts import stable_digest
from .factor_catalog_loader import get_research_recipe
from .factor_experiments import FactorExperimentSpec, _candidate_for, run_factor_experiments
from .observability import current_runtime_budget
from .quant_analytics import compute_parameter_surface
from .reports import compare_experiments
from .temporal_validation import purged_walk_forward

METHOD_VERSION = "factor-workbench.v1"


@dataclass(frozen=True, slots=True)
class ExperimentSummary:
    """Immutable JSON boundary shared by workspace, comparison and report."""
    _json: str

    @classmethod
    def build(cls, *, snapshot_ref, hypothesis, parameters, sample_period, split, cost, metrics, failure_reason, version, evidence_refs, experiment_id, conditions, phase):
        payload = {"snapshot_ref": snapshot_ref, "hypothesis": hypothesis, "parameters": parameters, "sample_period": sample_period, "split": split, "cost": cost, "metrics": metrics, "failure_reason": failure_reason, "version": version, "evidence_refs": evidence_refs, "experiment_id": experiment_id, "conditions": conditions, "phase": phase, "paper_only": True}
        payload["fingerprint"] = stable_digest(payload)
        return cls(json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False))

    def to_dict(self):
        return json.loads(self._json)


def study_frame(snapshot, instrument, as_of):
    from .snapshot_tools import SnapshotResearchTools

    if instrument not in snapshot.instruments or not snapshot.pit_available(SnapshotResearchTools._boundary(as_of)):
        raise ValueError("study snapshot has unknown/future availability or wrong instrument")
    rows = [item for item in snapshot.observations if item.instrument == instrument]
    if not 4 <= len(rows) <= 5000 or len({item.timestamp.date() for item in rows}) != len(rows):
        raise ValueError("study needs 4..5000 low-frequency observations")
    if any(previous.available_at > following.timestamp for previous, following in pairwise(rows)):
        raise ValueError("study data was unavailable at its historical execution boundary")
    return pd.DataFrame([{key: getattr(item, key) for key in ("open", "high", "low", "close", "volume")} for item in rows], index=pd.DatetimeIndex([item.timestamp for item in rows]))


def prepare_study(values, snapshot, *, policy_fingerprint):
    fields = {"idempotency_key", "snapshot_ref", "instrument", "as_of", "question", "recipe_id", "windows", "n_splits", "embargo", "label_horizon", "fee_bps", "slippage_bps", "min_abs_ic", "max_neighbor_delta"}
    if not isinstance(values, dict) or set(values) - fields:
        raise ValueError("unsupported study inputs; choose a controlled recipe")
    question = values.get("question")
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 2000:
        raise ValueError("study question must be nonempty and bounded")
    recipe = get_research_recipe(values.get("recipe_id"))
    windows = values.get("windows", [3, 5, 8])
    if not isinstance(windows, list) or not 1 <= len(windows) <= 8 or any(type(value) is not int or not 2 <= value <= 60 for value in windows) or len(set(windows)) != len(windows):
        raise ValueError("declare one to eight distinct integer windows in 2..60")
    windows = sorted(windows) if "{window}" in recipe.formula else [5]
    horizon, embargo, folds = values.get("label_horizon", 1), values.get("embargo", 1), values.get("n_splits", 3)
    if any(type(value) is not int or not lower <= value <= upper for value, lower, upper in ((horizon, 1, 20), (embargo, 0, 20), (folds, 1, 4))):
        raise ValueError("invalid study horizon, embargo or fold count")
    numeric = {key: values.get(key, default) for key, default in (("fee_bps", 5), ("slippage_bps", 5), ("min_abs_ic", 0.02), ("max_neighbor_delta", 0.1))}
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= (100 if key.endswith("bps") else 1) for key, value in numeric.items()):
        raise ValueError("study costs and thresholds must be finite and bounded")
    frame = study_frame(snapshot, values.get("instrument"), values.get("as_of"))
    if set(recipe.required_fields) - set(frame.columns):
        raise ValueError("BLOCKED_DATA: choose the method-guided study with explicit research fields and units")
    temporal = purged_walk_forward(frame.index, horizon=horizon, embargo=embargo, n_splits=folds)
    if any(len(fold["validation"]) < 8 for fold in temporal["folds"]) or len(temporal["test"]) < 8:
        raise ValueError("not enough samples after purge/embargo; prepare a longer snapshot")
    return {"version": METHOD_VERSION, "snapshot_ref": snapshot.fingerprint, "instrument": values["instrument"], "as_of": values["as_of"], "question": question.strip(), "recipe_id": recipe.recipe_id, "recipe_version": recipe.version, "windows": windows, "label_horizon": horizon, "embargo": embargo, "n_splits": folds, "split": {"train": 0.6, "validation": 0.2, "test": 0.2}, "sample_period": [frame.index[0].isoformat(), frame.index[-1].isoformat()], "test_period": [frame.index[temporal["validation_end"]].isoformat(), frame.index[-1].isoformat()], "cost": {"fee_bps": numeric["fee_bps"], "slippage_bps": numeric["slippage_bps"], "basis": "ex-post rank-turnover diagnostic, not portfolio net P&L"}, "min_abs_ic": numeric["min_abs_ic"], "max_neighbor_delta": numeric["max_neighbor_delta"], "policy_fingerprint": policy_fingerprint, "metric_basis": "OBSERVATION", "evaluation_units": len(windows) * (2 * folds + 1) + 2}


def _summary(record, spec, spec_ref, *, phase="VALIDATION", evaluation=None):
    evaluation = evaluation or record["evaluation"]
    # Comparability is tied to actual usable scoring timestamps, not counts.
    samples = record.get("sample_ids", [])
    conditions = {"dataset_fingerprint": spec["snapshot_ref"], "sample_period": spec["sample_period"], "scoring_index": stable_digest(samples), "split": {**spec["split"], "horizon": spec["label_horizon"], "embargo": spec["embargo"], "folds": spec["n_splits"], "phase": phase}, "cost": spec["cost"], "version": METHOD_VERSION, "metric_basis": "OBSERVATION", "policy_fingerprint": spec["policy_fingerprint"]}
    metrics = {key: evaluation.get(key) for key in ("information_coefficient", "information_ratio", "sample_size", "coverage", "turnover", "transaction_cost")}
    conditions["instrument"] = spec["instrument"]
    return ExperimentSummary.build(snapshot_ref=spec["snapshot_ref"], hypothesis=spec["question"], parameters=record["parameters"], sample_period=spec["sample_period"], split=conditions["split"], cost=spec["cost"], metrics=metrics, failure_reason=record.get("failure_reason"), version=METHOD_VERSION, evidence_refs=["factor-evidence:" + stable_digest({"input": spec_ref, "evaluation": evaluation["fingerprint"], "phase": phase})], experiment_id=record["experiment_id"], conditions=conditions, phase=phase).to_dict()


def compute_study(snapshot, study, *, phase, validation=None, research_fields_store=None, study_store=None):
    spec = study["spec"]
    if spec["version"] == "factor-workbench.suite.v1":
        from .factor_study_suite import compute_suite
        return compute_suite(snapshot,study,phase=phase,validation=validation,research_fields_store=research_fields_store,study_store=study_store)
    if spec["version"] == "factor-workbench.library.v1":
        from .factor_library_workbench import compute_library_study
        return compute_library_study(snapshot, study, phase=phase, validation=validation,
            research_fields_store=research_fields_store, study_store=study_store)
    if spec["version"] == "factor-workbench.pilot.v1":
        return _compute_method_study(snapshot, study, phase=phase, validation=validation,
                                     research_fields_store=research_fields_store, study_store=study_store)
    if spec["version"] != METHOD_VERSION or spec["snapshot_ref"] != snapshot.fingerprint:
        raise ValueError("study version or selected snapshot changed")
    frame = study_frame(snapshot, spec["instrument"], spec["as_of"])
    recipe = get_research_recipe(spec["recipe_id"])
    if recipe.version != spec["recipe_version"]:
        raise ValueError("study recipe version changed")
    forward = frame.close.pct_change(spec["label_horizon"], fill_method=None).shift(-spec["label_horizon"])
    dataset = {"frame": frame, "forward_return": forward}
    temporal = purged_walk_forward(frame.index, horizon=spec["label_horizon"], embargo=spec["embargo"], n_splits=spec["n_splits"])
    cost = (spec["cost"]["fee_bps"] + spec["cost"]["slippage_bps"]) / 10000
    if phase == "validation":
        experiment_spec = FactorExperimentSpec({"expression": [recipe.formula], "window": spec["windows"]}, max_experiments=8, split=spec["split"], n_splits=spec["n_splits"], label_horizon=spec["label_horizon"], embargo=spec["embargo"])
        result = run_factor_experiments(experiment_spec, dataset, {"min_samples": 8, "cost_per_turnover": cost}).to_dict()
        summaries = []
        for record in result["experiments"]:
            summaries.append(_summary(record, spec, study["spec_ref"]))
        comparison = compare_experiments([{"run_id": item["experiment_id"], "metrics": item["metrics"], "conditions": item["conditions"]} for item in summaries if item["failure_reason"] is None])
        ordered = sorted((record for record in result["experiments"] if record["failure_reason"] is None), key=lambda record: (-abs(record["ic"]), record["experiment_id"]))
        ranking_groups = [{"group_id": group["group_id"], "ranking": [record["experiment_id"] for record in ordered if record["experiment_id"] in group["run_ids"]]} for group in comparison["groups"]]
        result.update(study_id=study["study_id"], spec_ref=study["spec_ref"], snapshot_ref=snapshot.fingerprint, charter=spec, summaries=summaries, comparison=comparison, ranking_groups=ranking_groups, ranking=ranking_groups[0]["ranking"] if len(ranking_groups) == 1 else [], selected_candidate=None, test_evaluation=None, state="CANDIDATE_POOL" if ordered else "REJECTED", phase="VALIDATION")
        result["surfaces"] = []
        for group in ranking_groups:
            points = [{"parameters": {"window": item["parameters"]["window"]}, "metric": abs(item["evaluation"]["information_coefficient"]) if item["evaluation"]["information_coefficient"] is not None else None, "status": item["evaluation"]["status"]} for item in result["experiments"] if item["experiment_id"] in group["ranking"]]
            surface = compute_parameter_surface(points, axes={"window": spec["windows"]}, min_metric=spec["min_abs_ic"], max_neighbor_delta=spec["max_neighbor_delta"])
            result["surfaces"].append({"group_id": group["group_id"], **surface.to_dict()})
    elif phase == "test":
        if not validation or validation.get("spec_ref") != study["spec_ref"] or validation.get("snapshot_ref") != snapshot.fingerprint:
            raise ValueError("frozen test requires the original verified validation report")
        record = next(item for item in validation["experiments"] if item["experiment_id"] == study["candidate_id"])
        if record["digest"] != study["candidate_fingerprint"] or record["failure_reason"] is not None:
            raise ValueError("frozen candidate binding mismatch")
        number = int(record["experiment_id"].removeprefix("experiment-"))
        candidate = _candidate_for(record["parameters"], dataset, number)
        budget = current_runtime_budget()
        if budget is not None:
            budget.charge_experiments(1)
        evaluation = evaluate_factor_candidate(candidate, frame, forward, {"phase": "test", "oos": True, "shift_periods": 1, "label_horizon": spec["label_horizon"], "cost_per_turnover": cost, "min_samples": 8, "scoring_index": frame.index[temporal["test"]], "evaluation_start": frame.index[temporal["test"][0]], "evaluation_end": frame.index[temporal["test"][-1]], "as_of": frame.index[-1]})
        factor = evaluate_expression(candidate.expression, frame).shift(1)
        pairs = pd.concat([factor.rename("factor"), forward.rename("forward")], axis=1).loc[frame.index[temporal["test"]]].replace([float("inf"), float("-inf")], float("nan")).dropna()
        samples = [[timestamp.isoformat() for timestamp in pairs.index]]
        result = json.loads(json.dumps(validation))
        result.update(phase="TEST", state="TEST_EVALUATED", selected_candidate=study["candidate_id"], test_evaluation=evaluation.to_dict(), test_summary=_summary({**record, "failure_reason": None if evaluation.status.value == "VALID" else evaluation.status.value, "sample_ids": samples}, spec, study["spec_ref"], phase="TEST", evaluation=evaluation.to_dict()))
    else:
        raise ValueError("unknown study phase")
    result.update(paper_only=True, requires_human_admission=True, production_write=False)
    result["limitations"] = [*result["limitations"], "Validation ranking is descriptive; final-test metrics never re-rank candidates.", "OOS PASS means calculable held-out evidence, not profitable or production-admitted.", "Baseline portfolio returns and factor IC diagnostics are different quantities and are not compared as the same metric."]
    result["fingerprint"] = stable_digest(result)
    return result


def prepare_method_study(snapshot, attachment, *, instrument, as_of, question, factor_spec, method_binding,
                         policy_fingerprint):
    from dataclasses import asdict

    from .factor_inputs import UniverseSpec, build_factor_panel
    from .factor_proposals import FactorHypothesis
    from .factor_recipes import resolve_factor_spec
    from .factor_validation import _frames, _temporal, factor_validation_policy

    recipe = resolve_factor_spec(factor_spec)
    from .factor_families import family_for_spec

    audited_family = family_for_spec(factor_spec)
    market = "UNSPECIFIED" if attachment is None else attachment.market
    universe = UniverseSpec(market, (instrument,))
    panel = build_factor_panel(snapshot, attachment, as_of=as_of, universe=universe)
    policy = factor_validation_policy(window_grid=[3, 5, 8] if audited_family else sorted({3, factor_spec.window, 8}))
    index, split = _temporal(_frames(panel), policy)
    if len(split["validation"]) < 8 or len(split["test"]) < 8:
        raise ValueError("not enough observations for the declared pilot study")
    family = audited_family or ("liquidity" if {"turnover_rate", "amount"} & set(factor_spec.required_fields) else "volatility" if "dispersion" in factor_spec.factor_id else "mean_reversion")
    hypothesis = FactorHypothesis("method-hyp-" + stable_digest({"question": question, "spec": factor_spec.fingerprint})[:12],
        question, family, factor_spec.required_fields, factor_spec.window,
        "positive" if factor_spec.direction == "HIGHER" else "negative")
    return {"version": "factor-workbench.pilot.v1", "snapshot_ref": snapshot.fingerprint,
        "attachment_ref": None if attachment is None else attachment.fingerprint,
        "instrument": instrument, "as_of": as_of, "question": question, "recipe_id": recipe.recipe_id,
        "recipe_version": recipe.version, "factor_spec": factor_spec.to_dict(), "universe": universe.to_dict(),
        "hypothesis": asdict(hypothesis), "method_binding": method_binding, "validation_policy": policy,
        "baseline_factor_id": "finathink-return-dispersion", "baseline_window": 5 if audited_family else factor_spec.window,
        "parameterized": audited_family is None,
        "combination_plan": {"method": "train-standardized-equal-weight", "weights": [0.5, 0.5],
                             "directions": [factor_spec.direction, "LOWER"]},
        "windows": policy["window_grid"], "n_splits": policy["n_splits"], "embargo": policy["embargo"],
        "label_horizon": policy["label_horizon"], "policy_fingerprint": policy_fingerprint,
        "policy_ref": policy_fingerprint, "cost": {"fee_bps": policy["fee_bps"], "slippage_bps": policy["slippage_bps"]},
        "test_period": [index[split["validation_end"]].isoformat(), index[-1].isoformat()],
        "sample_period": [index[0].isoformat(), index[-1].isoformat()], "metric_basis": "OBSERVATION",
        "evaluation_units": len(policy["window_grid"]) * (2 * policy["n_splits"] + 1) + 4}


def _compute_method_study(snapshot, study, *, phase, validation, research_fields_store, study_store):
    from .asset_contracts import FactorSpec
    from .factor_inputs import UniverseSpec, build_factor_panel
    from .factor_validation import FactorValidationGateway, run_factor_validation
    from .reports import method_factor_activity

    spec = study["spec"]
    if spec["snapshot_ref"] != snapshot.fingerprint:
        raise ValueError("pilot study snapshot binding changed")
    attachment = None
    if spec["attachment_ref"] is not None:
        if research_fields_store is None:
            raise ValueError("pilot study requires the original field store")
        attachment = research_fields_store.load(spec["attachment_ref"])
    universe = UniverseSpec(spec["universe"]["market"], tuple(spec["universe"]["instruments"]))
    panel = build_factor_panel(snapshot, attachment, as_of=spec["as_of"], universe=universe)
    factor = FactorSpec(**{key: item for key, item in spec["factor_spec"].items() if key != "schema_version"})
    policy = {**spec["validation_policy"], "phase": phase}
    if phase == "test" and (not validation or validation.get("spec_ref") != study["spec_ref"]
                            or validation["experiments"][0]["digest"] != study["candidate_fingerprint"]):
        raise ValueError("pilot final test requires original frozen validation")
    gateway = FactorValidationGateway(study_store=study_store, study_ids={spec["instrument"]: study["study_id"]})
    run = run_factor_validation(factor, panel, policy, gateway=gateway).to_dict()
    steps = {item["stage_id"]: item for item in run["steps"]}
    metric = steps["information_coefficient"]["metrics"].get("mean_oriented_time_series_ic")
    incomplete = run["status"] in {"BLOCKED_DATA", "COMPLETE_WITH_BLOCKS", "BLOCKED_CONTROL", "SEMANTIC_REJECTION", "EXECUTION_ERROR"}
    failure = run["status"] if incomplete or metric is None else None
    experiment_id = factor.factor_id
    record = {"experiment_id": experiment_id, "digest": run["candidate_fingerprint"],
              "parameters": {"expression": factor.formula, "window": factor.window},
              "failure_reason": failure, "ic": metric, "ir": None,
              "evaluation": {"information_coefficient": metric, "status": run["status"]}}
    conditions = {"dataset_fingerprint": snapshot.fingerprint, "attachment_ref": spec["attachment_ref"],
        "factor_spec": factor.fingerprint, "cost": spec["cost"], "policy_fingerprint": spec["policy_fingerprint"],
        "code_version": policy["code_version"], "phase": phase, "scoring_window": run["bindings"]["oos_window"] if phase == "validation" else run["bindings"]["final_test_window"]}
    summary = {"experiment_id": experiment_id, "snapshot_ref": snapshot.fingerprint, "metrics": {"ic": metric},
               "conditions": conditions, "failure_reason": failure, "phase": phase.upper(),
               "parameters": record["parameters"], "evidence_refs": list(run["artifacts"]), "fingerprint": run["fingerprint"]}
    result = {"study_id": study["study_id"], "spec_ref": study["spec_ref"], "snapshot_ref": snapshot.fingerprint,
        "charter": spec, "validation_run": run, "phase": phase.upper(), "experiments": [record],
        "summaries": [summary], "ranking": [] if failure else [experiment_id], "ranking_groups": [],
        "selected_candidate": study.get("candidate_id"), "surfaces": [], "comparison": {"groups": [], "runs": []},
        "state": "BLOCKED" if incomplete else "CANDIDATE_POOL" if phase == "validation" else "TEST_EVALUATED",
        "test_evaluation": run["final_test_metrics"], "paper_only": True, "requires_human_admission": True,
        "production_write": False, "limitations": run["limitations"]}
    result["method_factor_activity"] = method_factor_activity(run, spec["method_binding"])
    if phase == "test":
        rejected = steps["archive_decision"]["metrics"].get("evaluation_outcome") == "COMPLETED_WITH_REJECTION"
        result["test_summary"] = {**summary, "failure_reason": failure or ("HELDOUT_REJECTION" if rejected else None)}
    result["fingerprint"] = stable_digest(result)
    return result

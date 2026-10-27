"""Real DSL evaluations, purged folds, failed trials and untouched holdouts."""

import math

import pandas as pd

from finahinking.research.factor_experiments import FactorExperimentSpec, run_factor_experiments


def dataset():
    index = pd.date_range("2025-01-01", periods=240, tz="UTC")
    close = pd.Series([100 + i * 0.03 + math.sin(i / 4) * 3 + math.cos(i / 9) for i in range(240)], index=index)
    return {"frame": pd.DataFrame({"close": close}), "forward_return": close.pct_change(fill_method=None).shift(-1)}


def test_rolling_validation_retains_fold_identities_and_all_declared_trials():
    spec = FactorExperimentSpec({"expression": ["return(close,3)", "return(missing,3)"]}, n_splits=2, label_horizon=3, embargo=2)
    data = dataset()
    data["forward_return"] = data["frame"].close.pct_change(3, fill_method=None).shift(-3)
    result = run_factor_experiments(spec, data, {"min_samples": 4, "cost_per_turnover": 0.001})
    records = [item.to_dict() for item in result.experiments]
    assert len(records) == 2 and len(records[0]["folds"]) == 2
    assert records[1]["evaluation"]["status"] == "INVALID"
    assert records[1]["failure_reason"]
    assert result.to_dict()["multiple_testing"]["family_size"] == 2
    assert result.ranking == ("experiment-0001",)
    assert all(fold["train_label_end"] < fold["validation_start"] for fold in records[0]["folds"])
    assert all(item["evaluation"]["oos_status"] != "PASS" for item in records)


def test_hidden_test_changes_cannot_change_validation_values_or_ranking():
    spec = FactorExperimentSpec({"expression": ["return(close,3)", "negate(return(close,3))"]}, n_splits=2, embargo=1)
    first = dataset()
    second = dataset()
    second["frame"].iloc[192:, 0] *= 7
    second["forward_return"] = second["frame"].close.pct_change(fill_method=None).shift(-1)
    left = run_factor_experiments(spec, first, {"min_samples": 4})
    right = run_factor_experiments(spec, second, {"min_samples": 4})
    assert left.ranking == right.ranking
    assert [item.ic for item in left.experiments] == [item.ic for item in right.experiments]
    assert all(item.evaluation.oos_information_coefficient is None for item in left.experiments)


def test_insufficient_samples_are_retained_but_never_ranked_as_successes():
    result = run_factor_experiments(FactorExperimentSpec({"expression": ["return(close,3)"]}, n_splits=2), dataset(), {"min_samples": 10000})
    assert len(result.experiments) == 1
    assert result.ranking == ()
    assert result.experiments[0].to_dict()["failure_reason"]


def test_same_counts_but_different_usable_dates_do_not_get_a_global_rank():
    data = dataset()
    data["frame"].iloc[145, 0] = float("nan")
    data["forward_return"] = data["frame"].close.pct_change(fill_method=None).shift(-1)
    result = run_factor_experiments(FactorExperimentSpec({"expression": ["return(close,3)", "return(close,8)"]}, n_splits=2), data, {"min_samples": 4})
    assert result.ranking == ()
    assert len(result.to_dict()["ranking_groups"]) == 2


def test_undefined_training_evidence_cannot_be_hidden_by_valid_validation():
    data = dataset()
    data["frame"].iloc[:144, 0] = 100.0
    data["forward_return"] = data["frame"].close.pct_change(fill_method=None).shift(-1)
    result = run_factor_experiments(FactorExperimentSpec({"expression": ["return(close,3)"]}), data, {"min_samples": 4})
    assert result.ranking == ()
    assert result.experiments[0].failure_reason

"""Hand-checked time boundaries and family-size accounting."""

import pandas as pd
import pytest

from finahinking.research import factor_experiments


def splits(index, **values):
    return factor_experiments.purged_walk_forward(index, **values)


def test_purge_removes_the_entire_label_horizon_and_embargo_gap():
    index = pd.date_range("2026-01-01", periods=40, tz="UTC")
    result = splits(index, horizon=3, embargo=2)
    assert result["train"] == list(range(19))
    assert result["validation"] == [24, 25, 26]
    assert result["test"] == [32, 33, 34, 35, 36]
    assert max(result["label_ends"][i] for i in result["train"]) < index[22]


def test_real_label_end_intervals_override_a_fixed_one_row_gap():
    index = pd.date_range("2026-01-01", periods=40, tz="UTC")
    ends = pd.Series(index + pd.Timedelta(days=1), index=index)
    ends.iloc[10] = index[28]
    ends.iloc[11] = pd.NaT
    result = splits(index, label_end_times=ends)
    assert 10 not in result["train"] and 11 not in result["train"]
    assert 12 in result["train"]


def test_expanding_folds_never_train_on_future_or_final_holdout_labels():
    index = pd.date_range("2026-01-01", periods=120, tz="UTC")
    result = splits(index, horizon=3, embargo=2, n_splits=2)
    assert [item["validation"] for item in result["folds"]] == [list(range(72, 79)), list(range(84, 91))]
    for fold in result["folds"]:
        assert max(result["label_ends"][i] for i in fold["train"]) < index[fold["validation_start"] - 2]
        assert not set(fold["train"] + fold["validation"]) & set(result["test"])


def test_unknown_or_backwards_label_intervals_are_not_scored():
    index = pd.date_range("2026-01-01", periods=40, tz="UTC")
    ends = pd.Series(index + pd.Timedelta(days=1), index=index)
    ends.iloc[10] = index[9]
    with pytest.raises(ValueError, match="label"):
        splits(index, label_end_times=ends)


@pytest.mark.parametrize("values", [{"horizon": 0}, {"embargo": -1}, {"n_splits": True}, {"train_ratio": 0.9, "validation_ratio": 0.2}])
def test_invalid_temporal_contracts_fail_closed(values):
    with pytest.raises(ValueError):
        splits(pd.date_range("2026-01-01", periods=40, tz="UTC"), **values)


def test_failed_experiments_still_count_in_bonferroni_family_size():
    assert factor_experiments.bonferroni_adjust([0.01, None, 0.2]) == [0.03, None, pytest.approx(0.6)]


def test_block_sign_test_uses_exact_two_sided_sign_randomization():
    # Four positive blocks: two of sixteen sign patterns have |sum| >= 4.
    assert factor_experiments.block_sign_pvalue([1.0] * 16, block_size=4) == 0.125
    assert factor_experiments.block_sign_pvalue([1.0, -1.0] * 8, block_size=4) == 1.0
    assert factor_experiments.block_sign_pvalue([1.0] * 3, block_size=4) is None

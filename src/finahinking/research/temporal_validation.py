"""Declared observation-time splits and conservative research diagnostics."""

from __future__ import annotations

import itertools
import math

import pandas as pd


def purged_walk_forward(index, *, train_ratio=0.6, validation_ratio=0.2, horizon=1, embargo=0, n_splits=1, label_end_times=None):
    """Past-only expanding folds; embargo is a gap before each held-out block.

    A training label must end strictly before the held-out start minus the
    declared gap. Validation labels also stay inside their own block. No
    future-side training exists, so no future-side embargo is implied.
    Durations are observations, not assumed equal calendar-day intervals.
    """
    index = pd.DatetimeIndex(index)
    if len(index) < 4 or not index.is_unique or not index.is_monotonic_increasing or index.hasnans:
        raise ValueError("time index must be ordered, unique and nonempty")
    if any(type(value) is not int or not lower <= value <= upper for value, lower, upper in ((horizon, 1, 252), (embargo, 0, 252), (n_splits, 1, 8))):
        raise ValueError("invalid label horizon, embargo or fold count")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value < 1 for value in (train_ratio, validation_ratio)) or train_ratio + validation_ratio >= 1:
        raise ValueError("split ratios must leave a final holdout")
    train_end = int(len(index) * train_ratio)
    validation_end = int(len(index) * (train_ratio + validation_ratio))
    if train_end < 1 or validation_end - train_end < n_splits or validation_end >= len(index):
        raise ValueError("not enough observations for the declared splits")
    if label_end_times is None:
        ends = [index[i + horizon] if i + horizon < len(index) else None for i in range(len(index))]
    else:
        if not isinstance(label_end_times, pd.Series) or not label_end_times.index.equals(index):
            raise ValueError("label end times must align with the feature index")
        ends = [None if pd.isna(value) else pd.Timestamp(value) for value in label_end_times]
        try:
            if any(end is not None and end <= index[i] for i, end in enumerate(ends)):
                raise ValueError("label intervals must end after their feature observation")
        except TypeError:
            raise ValueError("label timezones do not match the feature index") from None

    def eligible(start, end, gap=0):
        boundary = end - gap
        if boundary <= start:
            return []
        # All labels must finish by the final usable observation of this block.
        cutoff = index[boundary] if boundary < len(index) else None
        return [i for i in range(start, boundary) if ends[i] is not None and (ends[i] < cutoff if cutoff is not None else ends[i] <= index[-1])]

    folds = []
    for fold in range(n_splits):
        start = train_end + (validation_end - train_end) * fold // n_splits
        end = train_end + (validation_end - train_end) * (fold + 1) // n_splits
        folds.append({"train": eligible(0, start, embargo), "validation": eligible(start, end, embargo), "validation_start": start, "validation_end": end})
    return {"train": eligible(0, train_end, embargo), "validation": eligible(train_end, validation_end, embargo), "test": eligible(validation_end, len(index)), "folds": folds, "label_ends": ends, "train_end": train_end, "validation_end": validation_end, "horizon": horizon, "embargo": embargo}


def bonferroni_adjust(values):
    """All declared attempts, including failures/unknown p-values, count."""
    values = list(values)
    if any(value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1) for value in values):
        raise ValueError("p-values must be finite probabilities or unknown")
    return [None if value is None else min(1.0, value * len(values)) for value in values]


def block_sign_pvalue(products, *, block_size=4):
    """Exact two-sided sign randomization of at most twelve block sums.

    Validity assumes independent, sign-symmetric blocks under the null. This
    is a disclosed exploratory diagnostic, not proof of tradable profitability
    or an assertion that a financial series satisfies those assumptions.
    """
    values = list(products)
    if type(block_size) is not int or block_size < 1:
        raise ValueError("block size must be positive")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in values):
        return None
    size = max(block_size, math.ceil(len(values) / 12))
    blocks = [math.fsum(values[start:start + size]) for start in range(0, len(values) - size + 1, size)]
    if len(blocks) < 4 or not all(math.isfinite(value) for value in blocks):
        return None
    observed = abs(math.fsum(blocks))
    extreme = sum(abs(math.fsum(sign * value for sign, value in zip(signs, blocks))) >= observed - 1e-12 for signs in itertools.product((-1, 1), repeat=len(blocks)))
    return extreme / (2 ** len(blocks))

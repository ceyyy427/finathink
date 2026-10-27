from __future__ import annotations

import pandas as pd
import pytest

from finahinking.factors.dsl import evaluate_expression, parse_factor_expression
from finahinking.factors.mining import generate_candidates


def _frame() -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=8, freq="D", tz="UTC")
    return pd.DataFrame(
        {"close": [100, 101, 99, 102, 104, 103, 106, 108], "volume": [10, 12, 9, 15, 14, 16, 18, 17]},
        index=index,
    )


def test_parser_normalizes_expression_and_collects_dependencies() -> None:
    first = parse_factor_expression(" rank( rolling_mean( return(close, 1), 3 ) ) ", {"close"})
    second = parse_factor_expression("rank(rolling_mean(return(close,1),3))", {"close"})

    assert first.expression == "rank(rolling_mean(return(close,1),3))"
    assert first.fields == ("close",)
    assert first.operators == ("rank", "return", "rolling_mean")
    assert first.fingerprint == second.fingerprint


@pytest.mark.parametrize(
    "expression",
    ["__import__('os')", "eval(close)", "close + volume", "unknown(close, 3)", "rolling_mean(close, 1001)", "close('/etc/passwd')"],
)
def test_parser_rejects_unregistered_or_executable_expression(expression: str) -> None:
    with pytest.raises(ValueError):
        parse_factor_expression(expression, {"close", "volume"})


def test_evaluator_is_point_in_time_bounded_and_deterministic() -> None:
    frame = _frame()
    as_of = frame.index[5]
    result = evaluate_expression("rank(rolling_mean(return(close,1),3))", frame, as_of=as_of)

    assert result.index.max() == as_of
    assert result.index.is_monotonic_increasing
    assert result.equals(evaluate_expression("rank(rolling_mean(return(close,1),3))", frame, as_of=as_of))

    with pytest.raises(ValueError, match="availability"):
        evaluate_expression("close", frame, as_of=as_of, available_at={"close": as_of + pd.Timedelta(days=1)})


def test_candidate_generation_is_bounded_and_sorted() -> None:
    kwargs = {"field_catalog": ("close", "volume"), "operator_catalog": (), "max_candidates": 4}
    first = generate_candidates("mean reversion with volume", **kwargs)
    second = generate_candidates("mean reversion with volume", **kwargs)

    assert len(first) == 4
    assert [item.candidate_id for item in first] == sorted(item.candidate_id for item in first)
    assert [item.fingerprint for item in first] == [item.fingerprint for item in second]
    assert all("eval" not in item.expression for item in first)


@pytest.mark.parametrize("expression", ["rank(close)", "winsorize(close,0.2,0.8)"])
def test_historical_factor_values_cannot_change_when_future_rows_are_appended(expression: str) -> None:
    frame = _frame()
    prefix = evaluate_expression(expression, frame.iloc[:5])
    full = evaluate_expression(expression, frame)
    pd.testing.assert_series_equal(prefix, full.iloc[:5])


def test_feature_graph_temporal_nodes_and_lags_do_not_cross_asset_boundaries():
    from finahinking.p6_6.features import FeatureRegistry
    from finahinking.p6_6.models import FeatureDefinition, FeatureGraph, FeatureNode, FeatureVersion

    times = list(pd.date_range("2025-01-01", periods=3, tz="UTC")) * 2
    frame = pd.DataFrame({"asset": ["A"] * 3 + ["B"] * 3, "date": times,
                          "available_at": times, "close": [10, 11, 12, 100, 90, 81]})
    feature = FeatureVersion(FeatureDefinition("asset-return", "return", "grouped return", "return",
        "close.pct_change(1)", input_fields=("close",), window=1, lag=1))
    graph = FeatureGraph((FeatureNode("ret", feature),), ("ret",))
    result = FeatureRegistry().evaluate_graph(graph, frame)["ret"]
    first_day = pd.Timestamp("2025-01-01T00:00:00Z")
    second_day = pd.Timestamp("2025-01-02T00:00:00Z")
    assert result.loc[first_day].isna().all()
    assert result.loc[second_day].isna().all()
    assert sorted(result.loc[pd.Timestamp("2025-01-03T00:00:00Z")].tolist()) == pytest.approx([-0.1, 0.1])


def test_feature_graph_date_rank_is_same_day_not_historical_or_full_time():
    from finahinking.p6_6.features import FeatureRegistry
    from finahinking.p6_6.models import FeatureDefinition, FeatureVersion

    dates = list(pd.date_range("2025-01-01", periods=2, tz="UTC")) * 2
    frame = pd.DataFrame({"asset": ["A", "A", "B", "B"], "date": dates,
                          "available_at": dates, "close": [10, 500, 100, 20]})
    version = FeatureVersion(FeatureDefinition("date-rank", "rank", "same-day rank", "cross_sectional_rank",
        "rank(close)", input_fields=("close",), cross_sectional_scope="date"))
    actual = FeatureRegistry().evaluate(version, frame)
    assert actual.iloc[:2].tolist() == [0.5, 1.0]
    assert actual.iloc[2:].tolist() == [1.0, 0.5]
    prefix = frame.loc[frame["date"] == dates[0]]
    pd.testing.assert_series_equal(FeatureRegistry().evaluate(version, prefix), actual.iloc[:2])

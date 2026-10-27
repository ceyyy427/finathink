import pytest

from finahinking.research.quant_analytics import (
    compute_parameter_surface,
    compute_performance_report,
)


def test_parameter_surface_flags_can_exclude_but_not_manufacture_a_plateau():
    result = compute_parameter_surface([
        {"parameters": {"window": 1}, "metric": 1.0, "robust": True},
        {"parameters": {"window": 2}, "metric": 2.0, "robust": False},
        {"parameters": {"window": 3}, "metric": 3.0, "passed": True},
    ], axes={"window": [1, 2, 3]}, min_metric=0.5, max_neighbor_delta=3)
    assert [point["robust"] for point in result.points] == [False, False, False]
    assert result.robustness_regions == ()


def test_performance_groups_fill_rows_by_timestamp():
    report = compute_performance_report([
        {"timestamp": "2024-01-01", "equity": 100, "target_weight": 0.5},
        {"timestamp": "2024-01-01", "equity": 100, "target_weight": 0.5},
        {"timestamp": "2024-01-02", "equity": 110, "target_weight": 0.5},
        {"timestamp": "2024-01-02", "equity": 110, "target_weight": 0.5},
    ])
    assert report.metrics["observations"] == 1
    assert report.metrics["total_return"] == pytest.approx(0.1)
    assert report.metrics["turnover"] == 0


def test_performance_missing_equity_breaks_return_chain_and_invalid_costs_are_reported():
    report = compute_performance_report([
        {"timestamp": "1", "equity": 100, "fees": 1, "slippage": 0},
        {"timestamp": "2", "equity": float("nan"), "fees": "bad", "slippage": 0},
        {"timestamp": "3", "equity": 120, "fees": 1, "slippage": float("nan")},
    ])
    assert report.metrics["observations"] == 0
    assert report.metrics["costs"] is None
    assert report.metrics["slippage"] is None
    assert any("missing" in item for item in report.limitations)
    assert any("fees" in item or "slippage" in item for item in report.limitations)


def test_benchmark_accepts_direct_returns():
    report = compute_performance_report({"returns": [0.1, -0.1]}, benchmark={"returns": [0.02, 0.03]})
    assert report.benchmark["observations"] == 2
    assert report.metrics["benchmark_total_return"] == (1.02 * 1.03 - 1)

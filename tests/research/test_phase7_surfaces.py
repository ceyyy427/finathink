"""A finite metric or input ordering must not manufacture robustness."""

import pytest

from finahinking.research.quant_analytics import compute_parameter_surface


def point(window, metric, **values):
    return {"parameters": {"window": window}, "metric": metric, **values}


def test_finite_metrics_and_explicit_flags_do_not_establish_robustness_without_criteria():
    result = compute_parameter_surface([point(1, 0.9, robust=True), point(2, 0.91)])
    assert result.robustness_regions == ()
    assert not any(item["robust"] for item in result.points)


def test_regions_follow_declared_neighbors_and_threshold_not_input_order():
    records = [point(3, 0.1), point(1, 0.9), point(2, 0.95)]
    options = {"axes": {"window": [1, 2, 3]}, "min_metric": 0.8, "max_neighbor_delta": 0.1}
    result = compute_parameter_surface(records, **options)
    reordered = compute_parameter_surface(list(reversed(records)), **options)
    assert result.to_dict() == reordered.to_dict()
    assert len(result.robustness_regions) == 1
    assert result.robustness_regions[0]["parameter_bounds"] == {"window": [1, 2]}
    assert result.robustness_regions[0]["size"] == 2
    assert sum(item["robust"] for item in result.points) == 2


def test_missing_grid_point_cannot_bridge_a_robust_region():
    result = compute_parameter_surface([point(1, 0.9), point(3, 0.91)], axes={"window": [1, 2, 3]}, min_metric=0.8, max_neighbor_delta=0.1)
    assert result.robustness_regions == ()
    assert not any(item["robust"] for item in result.points)


def test_high_but_sharply_different_neighbors_are_not_a_stable_plateau():
    result = compute_parameter_surface([point(1, 0.9), point(2, 2.0)], axes={"window": [1, 2]}, min_metric=0.8, max_neighbor_delta=0.1)
    assert result.robustness_regions == ()


def test_duplicate_parameter_points_are_rejected_not_counted_as_neighbors():
    with pytest.raises(ValueError, match="duplicate"):
        compute_parameter_surface([point(1, 0.9), point(1, 0.95)], axes={"window": [1, 2]}, min_metric=0.8, max_neighbor_delta=0.1)


def test_diagonal_points_are_not_neighbors_in_a_two_dimensional_grid():
    result = compute_parameter_surface([
        {"parameters": {"window": 1, "cutoff": 0.1}, "metric": 0.9},
        {"parameters": {"window": 2, "cutoff": 0.2}, "metric": 0.91},
    ], axes={"window": [1, 2], "cutoff": [0.1, 0.2]}, min_metric=0.8, max_neighbor_delta=0.1)
    assert result.robustness_regions == ()


def test_criteria_change_is_bound_into_the_surface_fingerprint():
    records = [point(1, 0.9), point(2, 0.91)]
    options = {"axes": {"window": [1, 2]}, "min_metric": 0.8}
    first = compute_parameter_surface(records, **options, max_neighbor_delta=0.1)
    second = compute_parameter_surface(records, **options, max_neighbor_delta=0.2)
    assert first.fingerprint != second.fingerprint

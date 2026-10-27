from finahinking.research.reports import compare_experiments


def run(name, **changes):
    conditions = {"dataset_fingerprint": "a" * 64, "instrument": "AAA", "sample_period": ["2026-01-01", "2026-06-30"], "scoring_index": "b" * 64, "split": {"train": 0.6, "validation": 0.2, "test": 0.2}, "cost": {"fee_bps": 5, "slippage_bps": 5}, "version": "test-evaluator.v1", "metric_basis": "OBSERVATION", **changes}
    return {"run_id": name, "inputs": {}, "metrics": {"ic": 0.2 if name == "a" else 0.4}, "conditions": conditions}


def test_same_conditions_show_server_computed_differences_without_a_winner():
    result = compare_experiments([run("a"), run("b")])
    assert len(result["groups"]) == 1
    assert result["groups"][0]["differences"] == [{"run_id": "b", "reference_run_id": "a", "metrics": {"ic": 0.2}}]
    assert "winner" not in result


def test_different_data_cost_sample_or_methods_are_not_directly_compared():
    result = compare_experiments([
        run("a"), run("b", cost={"fee_bps": 10, "slippage_bps": 5}),
        run("c", dataset_fingerprint="c" * 64), run("d", scoring_index="d" * 64),
        run("e", split={"train": 0.5, "validation": 0.25, "test": 0.25}),
    ])
    assert len(result["groups"]) == 5
    assert all(group["differences"] == [] for group in result["groups"])


def test_missing_conditions_are_explicitly_unknown_not_assumed_equal():
    result = compare_experiments([{"run_id": "old-a", "metrics": {"ic": 0.2}}, {"run_id": "old-b", "metrics": {"ic": 0.4}}])
    assert result["groups"] == []
    assert all(row["comparability"] == "UNKNOWN" for row in result["runs"])

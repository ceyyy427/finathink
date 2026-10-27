from __future__ import annotations

import pandas as pd
import pytest

from finahinking.factors.mining import FactorCandidate
from finahinking.p6_6.workbench import ResearchCharter
from finahinking.research.factor_loop import FactorResearchState, run_factor_research


def _charter(max_experiments: int = 4) -> ResearchCharter:
    return ResearchCharter(
        charter_id="factor-charter",
        research_question="Does price strength survive costs?",
        hypothesis_scope="bounded momentum and mean reversion",
        dataset_reference="fixture-v1",
        data_split={"train": 0.6, "validation": 0.2, "test": 0.2},
        evaluation_metrics=("ic", "icir", "turnover"),
        hard_constraints={"shift_periods": 1, "paper_only": True},
        allowed_primitives=("input", "return", "rolling", "rank"),
        max_experiments=max_experiments,
        iteration_budget=max_experiments,
    )


def _dataset(points=24) -> dict[str, object]:
    index = pd.date_range("2024-01-01", periods=points, freq="D", tz="UTC")
    close = pd.Series([100 + i + (i % 3) for i in range(len(index))], index=index, dtype=float)
    return {"frame": pd.DataFrame({"close": close}, index=index), "forward_return": close.pct_change().shift(-1)}


def _candidate(candidate_id: str, expression: str = "rank(close)") -> FactorCandidate:
    return FactorCandidate(candidate_id, expression, "price strength", "fixture", {"direction": "positive"})


def test_factor_research_hides_oos_and_retains_each_attempt() -> None:
    # Control-flow coverage needs enough validation blocks for a genuine ICIR.
    run = run_factor_research(_charter(), (_candidate("candidate-1"), _candidate("candidate-2", "negate(rank(close))")), _dataset(120))

    assert run.state is FactorResearchState.CANDIDATE_POOL
    assert len(run.rounds) == 2
    assert all(item.evaluation.oos_status == "HIDDEN" for item in run.rounds)
    assert run.test_evaluation is None
    assert run.fingerprint


def test_factor_research_budget_is_bounded_and_freeze_controls_test_access() -> None:
    data = _dataset(120)
    run = run_factor_research(_charter(max_experiments=1), (_candidate("candidate-1"), _candidate("candidate-2")), data)
    admitted = next(item for item in run.rounds if item.admission.status == "ADMITTED")

    with pytest.raises(ValueError, match="frozen"):
        run.evaluate_test(data)
    frozen = run.freeze(admitted.candidate.candidate_id)
    tested = frozen.evaluate_test(data)

    assert len(tested.rounds) == 1
    assert tested.state is FactorResearchState.TEST_EVALUATED
    assert tested.test_evaluation is not None
    with pytest.raises(ValueError, match="once-only"):
        tested.evaluate_test(data)


def test_test_period_mutation_cannot_change_validation_evidence() -> None:
    data = _dataset()
    first = run_factor_research(_charter(), (_candidate("candidate-1", "return(close,1)"),), data)
    modified = {"frame": data["frame"].copy(), "forward_return": data["forward_return"].copy()}
    modified["frame"].iloc[19:, 0] = [400, 10, 700, 20, 800]
    modified["forward_return"].iloc[19:] = [2, -3, 4, -5, 6]
    second = run_factor_research(_charter(), (_candidate("candidate-1", "return(close,1)"),), modified)
    assert first.rounds[0].evaluation.to_dict() == second.rounds[0].evaluation.to_dict()

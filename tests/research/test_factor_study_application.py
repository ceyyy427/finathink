"""Trusted spawn + existing report publication, not an in-request mock evaluator."""

import time
from dataclasses import replace

import pytest

from finahinking.p8_2.data_sources import FixtureMarketDataSource
from finahinking.research.application_service import ResearchApplicationService


def wait_study(service, study_id, phase):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        result = service.factor_study(study_id)
        job = result.get(f"{phase}_job")
        if job and job["status"] in {"completed", "failed", "cancelled"}:
            assert job["status"] == "completed", job
            return result
        time.sleep(0.04)
    pytest.fail("factor study did not reach a terminal state")


def values(reference, **changes):
    return {"idempotency_key": "study-one", "snapshot_ref": reference, "instrument": "AAA", "as_of": "2026-10-01", "question": "研究均值回归在不同窗口下的稳定性", "recipe_id": "close_reversal", "windows": [3, 5, 8], "n_splits": 2, "embargo": 1, "label_horizon": 1, "fee_bps": 5, "slippage_bps": 5, **changes}


def test_declare_validate_freeze_once_test_review_and_restart_share_snapshot_and_reports(tmp_path):
    service = ResearchApplicationService(tmp_path)
    reference = service.snapshots.save(FixtureMarketDataSource(points=240).snapshot("AAA"))
    service.start()
    try:
        created = service.create_factor_study(values(reference))
        study_id = created["study_id"]
        validation = wait_study(service, study_id, "validation")
        assert validation["state"] == "VALIDATED"
        result = validation["validation"]
        assert result["snapshot_ref"] == reference and len(result["summaries"]) == 3
        assert result["test_evaluation"] is None
        assert all(item["snapshot_ref"] == reference and item["evidence_refs"] for item in result["summaries"])
        selected = result["ranking"][0]
        service.freeze_factor_study(study_id, selected)
        service.test_factor_study(study_id, "one-final-test")
        tested = wait_study(service, study_id, "test")
        assert tested["state"] == "TEST_EVALUATED"
        assert tested["test_result"]["selected_candidate"] == selected
        assert tested["test_result"]["test_evaluation"]["oos_status"] == "PASS"
        with pytest.raises(ValueError):
            service.test_factor_study(study_id, "different-final-test")
        service.review_factor_study(study_id, "REJECT", "local-reviewer")
        assert service.factor_study(study_id)["state"] == "REVIEWED"
        assert service.runs.count_runs() == 2
    finally:
        service.close()
    restarted = ResearchApplicationService(tmp_path)
    try:
        result = restarted.factor_study(study_id)
        assert result["review_decision"] == "REJECT"
        assert result["test_job"]["report_status"] == "AVAILABLE"
    finally:
        restarted.close()


def test_new_question_or_cost_cannot_reuse_consumed_holdout(tmp_path):
    service = ResearchApplicationService(tmp_path)
    reference = service.snapshots.save(FixtureMarketDataSource(points=240).snapshot("AAA"))
    service.start()
    try:
        one = service.create_factor_study(values(reference))
        validation = wait_study(service, one["study_id"], "validation")
        service.freeze_factor_study(one["study_id"], validation["validation"]["ranking"][0])
        service.test_factor_study(one["study_id"], "once")
        two = service.create_factor_study(values(reference, idempotency_key="study-two", fee_bps=10))
        second = wait_study(service, two["study_id"], "validation")
        service.freeze_factor_study(two["study_id"], second["validation"]["ranking"][0])
        with pytest.raises(ValueError, match="overlap|consum"):
            service.test_factor_study(two["study_id"], "try-again")
    finally:
        service.close()


def test_short_or_future_snapshots_and_arbitrary_expressions_are_not_admitted(tmp_path):
    service = ResearchApplicationService(tmp_path)
    reference = service.snapshots.save(FixtureMarketDataSource(points=20).snapshot("AAA"))
    try:
        with pytest.raises(ValueError):
            service.create_factor_study(values(reference))
        reference = service.snapshots.save(FixtureMarketDataSource(points=240).snapshot("AAA"))
        with pytest.raises(ValueError):
            service.create_factor_study(values(reference, as_of="2000-01-01"))
        with pytest.raises((ValueError, KeyError)):
            service.create_factor_study(values(reference, expression="__import__('os')"))
        assert service.list_jobs()["total"] == 0
    finally:
        service.close()


def test_failed_final_test_is_visible_and_cannot_be_approved_as_a_proposal(tmp_path):
    service = ResearchApplicationService(tmp_path)
    original = FixtureMarketDataSource(points=240).snapshot("AAA")
    rows = original.observations
    snapshot = replace(original, observations=(*rows[:192], *(replace(row, open=100, close=100, high=101, low=99) for row in rows[192:])))
    reference = service.snapshots.save(snapshot)
    service.start()
    try:
        created = service.create_factor_study(values(reference))
        study = wait_study(service, created["study_id"], "validation")
        service.freeze_factor_study(study["study_id"], study["validation"]["ranking"][0])
        service.test_factor_study(study["study_id"], "only-test")
        result = wait_study(service, study["study_id"], "test")
        assert result["test_result"]["test_summary"]["failure_reason"]
        with pytest.raises(ValueError):
            service.review_factor_study(study["study_id"], "APPROVE_FOR_RESEARCH", "reviewer")
        service.review_factor_study(study["study_id"], "REJECT", "reviewer")
    finally:
        service.close()


@pytest.mark.parametrize("recipe_id", ["turnover_mean", "turnover_dispersion", "amihud_ratio"])
def test_new_optional_field_recipes_do_not_turn_missing_legacy_inputs_into_factor_rejection(tmp_path, recipe_id):
    service = ResearchApplicationService(tmp_path)
    reference = service.snapshots.save(FixtureMarketDataSource(points=240).snapshot("AAA"))
    try:
        with pytest.raises(ValueError, match="BLOCKED_DATA"):
            service.create_factor_study(values(reference, recipe_id=recipe_id))
        assert service.list_jobs()["total"] == 0
        assert service.factor_studies.list() == []
    finally:
        service.close()

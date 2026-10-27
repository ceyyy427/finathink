import concurrent.futures

import pytest

from finahinking.research import factor_pipeline
from finahinking.research.job_queue import JobQueue


def store(tmp_path, scope="local"):
    return factor_pipeline.FactorStudyStore(JobQueue(tmp_path / "jobs.sqlite3"), scope_id=scope)


def spec(**changes):
    return {"snapshot_ref": "a" * 64, "instrument": "AAA", "question": "研究均值回归", "test_period": ["2026-07-01T00:00:00Z", "2026-07-30T00:00:00Z"], **changes}


def frozen(target, study_id, **changes):
    target.create(study_id, spec(**changes))
    target.set_job(study_id, "validation", "job-validate-" + study_id)
    target.complete(study_id, "validation", "job-validate-" + study_id, succeeded=True)
    return target.freeze(study_id, "experiment-0001", "b" * 64)


def test_repeated_test_touch_is_durable_and_idempotent_only_for_the_same_owner_key(tmp_path):
    target = store(tmp_path)
    frozen(target, "study-a")
    first = target.reserve_test("study-a", "one-test")
    restarted = store(tmp_path)
    assert restarted.reserve_test("study-a", "one-test")["spec_ref"] == first["spec_ref"]
    with pytest.raises(ValueError, match="once|consum|reserved"):
        restarted.reserve_test("study-a", "another-test")


def test_changing_question_cost_or_snapshot_does_not_unlock_the_same_test_window(tmp_path):
    target = store(tmp_path)
    frozen(target, "study-a")
    target.reserve_test("study-a", "once-a")
    frozen(target, "study-b", snapshot_ref="c" * 64, question="另一个问题", cost={"fee_bps": 10})
    with pytest.raises(ValueError, match="overlap|consum"):
        target.reserve_test("study-b", "once-b")


def test_overlapping_but_not_identical_test_windows_are_also_contaminated(tmp_path):
    target = store(tmp_path)
    frozen(target, "study-a")
    target.reserve_test("study-a", "once-a")
    frozen(target, "study-b", test_period=["2026-07-20T00:00:00Z", "2026-08-20T00:00:00Z"])
    with pytest.raises(ValueError, match="overlap|consum"):
        target.reserve_test("study-b", "once-b")


def test_concurrent_claims_cannot_both_consume_one_holdout(tmp_path):
    target = store(tmp_path)
    frozen(target, "study-a")
    frozen(target, "study-b")

    def claim(name):
        try:
            store(tmp_path).reserve_test(name, name)
            return "claimed"
        except ValueError:
            return "blocked"

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim, ["study-a", "study-b"])) == ["blocked", "claimed"]


def test_dataset_binding_and_scope_cannot_be_mutated_or_read_across_studies(tmp_path):
    target = store(tmp_path)
    first = target.create("study-a", spec())
    with pytest.raises(ValueError, match="immutable|conflict"):
        target.create("study-a", spec(snapshot_ref="b" * 64))
    changed = target.get("study-a")
    changed["spec"]["snapshot_ref"] = "c" * 64
    assert target.get("study-a")["spec_ref"] == first["spec_ref"]
    with pytest.raises(KeyError):
        store(tmp_path, "other").get("study-a")


def test_unvalidated_or_refrozen_candidates_cannot_touch_test_and_failed_test_stays_consumed(tmp_path):
    target = store(tmp_path)
    target.create("study-a", spec())
    with pytest.raises(ValueError, match="validated|validation"):
        target.freeze("study-a", "experiment-0001", "b" * 64)
    frozen(target, "study-b")
    target.reserve_test("study-b", "once")
    with pytest.raises(ValueError):
        target.freeze("study-b", "experiment-0002", "c" * 64)
    target.set_job("study-b", "test", "job-test")
    target.complete("study-b", "test", "job-test", succeeded=False)
    with pytest.raises(ValueError):
        target.reserve_test("study-b", "another-test")


def test_job_completion_and_review_require_matching_owner_and_finished_test(tmp_path):
    target = store(tmp_path)
    frozen(target, "study-a")
    with pytest.raises(ValueError):
        target.review("study-a", "APPROVE_FOR_RESEARCH", "reviewer")
    target.reserve_test("study-a", "once")
    target.set_job("study-a", "test", "job-test")
    with pytest.raises(ValueError):
        target.complete("study-a", "test", "foreign-job", succeeded=True)
    target.complete("study-a", "test", "job-test", succeeded=True)
    reviewed = target.review("study-a", "REJECT", "reviewer")
    assert reviewed["review_decision"] == "REJECT" and reviewed["production_write"] is False
    with pytest.raises(ValueError):
        target.review("study-a", "REJECT", "different-reviewer")

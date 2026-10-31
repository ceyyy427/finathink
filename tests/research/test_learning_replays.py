import pytest

from finahinking.research.application_service import ResearchApplicationService
from finahinking.research.learning_replays import LearningReplayService
from tests.research.test_application_service import wait_for
from tests.research.test_phase8_learning_flow import decide, inputs, settle


def prepared(app):
    source, ref, evidence = inputs(app)
    approved = decide(app, settle(app, source, evidence))
    return {"review_id": approved["review_id"], "snapshot_ref": ref, "as_of": approved["available_as_of"]}


def test_pair_executes_two_bound_policies_and_reopens_without_new_jobs(tmp_path):
    app = ResearchApplicationService(tmp_path)
    try:
        values = prepared(app)
        pairs = LearningReplayService(app)
        pair = pairs.create(values)
        for child in pair["children"].values():
            assert wait_for(app, child["job_id"])["status"] == "completed"
        result = pairs.get(pair["pair_id"])
        assert result["status"] == "AVAILABLE" and result["comparison_kind"] == "RETROSPECTIVE"
        assert result["metrics"] and all(row["delta"] == pytest.approx(row["right"]-row["left"]) for row in result["metrics"])
        for side in ("left", "right"):
            request = app._request(app.queue.get(result["children"][side]["job_id"]))
            assert request.snapshot_ref == values["snapshot_ref"]
            assert request.research_plan.validation_spec["model_config_ref"] == "offline"
            assert request.previous_policy_fingerprint == result["binding"][f"{side}_policy_ref"]
        count = app.list_jobs()["total"]
        assert pairs.create(values)["pair_id"] == pair["pair_id"]
        assert app.list_jobs()["total"] == count
        other = ResearchApplicationService(tmp_path, scope_id="other")
        try:
            with pytest.raises(KeyError):
                LearningReplayService(other).get(pair["pair_id"])
        finally:
            other.close()
    finally:
        app.close()
    reopened = ResearchApplicationService(tmp_path)
    try:
        assert LearningReplayService(reopened).get(pair["pair_id"])["status"] == "AVAILABLE"
        assert reopened.list_jobs()["total"] == count
        entry = reopened.runs.get_run(result["children"]["right"]["run_id"])
        (entry["bundle"] / "complete_report.html").write_text("broken")
        damaged = LearningReplayService(reopened).get(pair["pair_id"])
        assert damaged["status"] == "BLOCKED" and damaged["metrics"] == []
    finally:
        reopened.close()


def test_half_enqueue_recovers_only_explicitly_and_code_change_blocks(tmp_path, monkeypatch):
    import finahinking.research.learning_replays as module

    app = ResearchApplicationService(tmp_path)
    try:
        values = prepared(app)
        pairs = LearningReplayService(app)
        original = app.submit
        calls = []
        def crash_after_first(body, **kwargs):
            calls.append(body)
            if len(calls) == 2:
                raise OSError("simulated shutdown")
            return original(body, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(app, "submit", crash_after_first)
            with pytest.raises(OSError):
                pairs.create(values)
        pair = pairs.list()[0]
        assert pair["status"] == "INCOMPLETE" and set(pair["children"]) == {"left"}
        count = app.list_jobs()["total"]
        assert pairs.get(pair["pair_id"])["status"] == "INCOMPLETE"
        assert app.list_jobs()["total"] == count
        with monkeypatch.context() as patch:
            patch.setattr(module, "execution_identity", lambda policy: {"source_digest": "changed"})
            with pytest.raises(ValueError, match="代码"):
                pairs.resume(pair["pair_id"])
        assert app.list_jobs()["total"] == count
    finally:
        app.close()
    reopened = ResearchApplicationService(tmp_path)
    try:
        pairs = LearningReplayService(reopened)
        result = pairs.resume(pair["pair_id"])
        assert len(result["children"]) == 2 and reopened.list_jobs()["total"] == count+1
        reopened.start()
        for child in result["children"].values():
            wait_for(reopened, child["job_id"])
        assert pairs.get(pair["pair_id"])["status"] == "AVAILABLE"
    finally:
        reopened.close()


def test_cancelled_child_is_not_a_successful_pair(tmp_path):
    app = ResearchApplicationService(tmp_path)
    try:
        values = prepared(app)
        app.close()
        app = ResearchApplicationService(tmp_path)
        pairs = LearningReplayService(app)
        result = pairs.create(values)
        app.cancel(result["children"]["right"]["job_id"])
        result = pairs.get(result["pair_id"])
        assert result["status"] == "BLOCKED" and result["metrics"] == []
    finally:
        app.close()

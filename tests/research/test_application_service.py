"""Real spawn acceptance of the user-facing, snapshot-bound research service."""

import time
from dataclasses import replace
from stat import S_IMODE

import pytest

from finahinking.p8_2.data_sources import FixtureMarketDataSource
from finahinking.research.application_service import (
    ResearchApplicationService,
    verify_research_receipt,
)
from finahinking.research.contracts import stable_digest
from finahinking.research.job_queue import JobStatus
from finahinking.research.runtime_protocol import WorkerMessage


@pytest.fixture
def service(tmp_path):
    instance = ResearchApplicationService(tmp_path / "artifacts", scope_id="local-session")
    try:
        yield instance
    finally:
        instance.close()


def payload(service, *, key="request-1", snapshot=None, budget=None):
    selected = snapshot or FixtureMarketDataSource(points=20).snapshot("AAA")
    return {
        "idempotency_key": key, "snapshot_ref": service.snapshots.save(selected),
        "question": "Inspect the documented momentum baseline with declared costs",
        "instrument": "AAA", "as_of": "2026-10-01", "model_config_ref": "offline",
        **({"budget": budget} if budget is not None else {}),
    }


def wait_for(service, job_id, *, statuses=("completed", "failed", "cancelled"), timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = service.get_job(job_id)
        if status["status"] in statuses:
            return status
        time.sleep(0.05)
    pytest.fail(f"research job did not reach {statuses}: {status}")


def test_double_submit_queues_one_reference_bound_request(service):
    body = payload(service)
    first = service.submit(body)
    second = service.submit(body)
    assert first["job_id"] == second["job_id"]
    assert service.list_jobs()["total"] == 1
    record = service.queue.get(first["job_id"])
    request = service.queue.load_request_snapshot(record.task_ref)
    assert request.snapshot_ref == body["snapshot_ref"]
    assert request.research_plan.validation_spec["scope_id"] == "local-session"
    with service.queue._connect() as db:
        stored = db.execute("SELECT payload FROM request_snapshots").fetchone()[0]
    assert '"observations"' not in stored and '"records"' not in stored
    with pytest.raises(ValueError, match="bound|idempotency"):
        service.submit({**body, "question": "A different research question"})


def test_durable_job_inputs_are_owner_private(service):
    assert S_IMODE(service.queue.path.stat().st_mode) == 0o600


def test_changed_policy_cannot_silently_replay_under_new_rules(service):
    from finahinking.research.runtime_service import RuntimeLimits

    original = service.submit(payload(service))
    request = service.queue.load_request_snapshot(service.queue.get(original["job_id"]).task_ref)
    service.cancel(original["job_id"])
    stale = replace(request, run_id="stale-policy", research_plan=replace(request.research_plan, validation_spec={**request.research_plan.validation_spec, "policy_fingerprint": "0" * 64}))
    job = service.runtime.submit(stale, "stale-policy", limits=RuntimeLimits(**request.research_plan.validation_spec["budget"]))
    service.start()
    result = wait_for(service, job.job_id)
    assert result["status"] == "failed" and result["failure_kind"] == "POLICY_CHANGED"
    assert result["attempts"] == 1 and result["report_url"] is None


def test_two_spawned_jobs_publish_distinct_verified_reports_and_survive_restart(service):
    a = FixtureMarketDataSource(points=20).snapshot("AAA")
    b = replace(a, observations=tuple(
        replace(item, close=100 - index, high=max(item.open, 100 - index) + 1, low=min(item.open, 100 - index) - 1)
        for index, item in enumerate(a.observations)
    ))
    first = service.submit(payload(service, key="a", snapshot=a))
    second = service.submit(payload(service, key="b", snapshot=b))
    service.start()
    outcomes = [wait_for(service, job["job_id"]) for job in (first, second)]
    assert [job["status"] for job in outcomes] == ["completed", "completed"]
    assert outcomes[0]["snapshot_ref"] == a.fingerprint
    assert outcomes[1]["snapshot_ref"] == b.fingerprint
    assert outcomes[0]["metrics"]["total_return"] > 0
    assert outcomes[1]["metrics"]["total_return"] == 0
    assert all(job["report_status"] == "AVAILABLE" and job["report_url"] for job in outcomes)
    assert service.runs.count_runs() == 2
    for job in outcomes:
        entry = service.runs.get_run(job["run_id"])
        html = (entry["bundle"] / "complete_report.html").read_text()
        assert job["snapshot_ref"] in html
        assert "Total return" in html
    record_a, record_b = [service.queue.get(job["job_id"]) for job in outcomes]
    with pytest.raises(ValueError, match="identity|bound"):
        verify_research_receipt(service.root, service.queue, record_a, record_b.result_ref)
    service.close()
    reopened = ResearchApplicationService(service.root, scope_id="local-session")
    try:
        assert reopened.get_job(first["job_id"])["report_url"] == outcomes[0]["report_url"]
        assert reopened.runs.count_runs() == 2
        assert reopened.get_job(second["job_id"])["attempts"] == 1
    finally:
        reopened.close()


def test_cancelled_queue_item_cannot_publish_or_be_revived_by_double_submit(service):
    body = payload(service)
    job = service.submit(body)
    assert service.cancel(job["job_id"])["status"] == "cancelled"
    service.start()
    assert wait_for(service, job["job_id"])["status"] == "cancelled"
    assert service.submit(body)["status"] == "cancelled"
    assert service.runs.count_runs() == 0


def test_byte_budget_failure_has_a_public_reason_and_no_report(service):
    job = service.submit(payload(service, budget={"max_bytes": 1}))
    service.start()
    outcome = wait_for(service, job["job_id"])
    assert outcome["status"] == "failed"
    assert outcome["failure_kind"] == "RESOURCE_LIMIT"
    assert outcome["report_url"] is None
    assert service.runs.count_runs() == 0


def test_artifact_output_counts_toward_the_same_byte_budget(service):
    job = service.submit(payload(service, budget={"max_bytes": 30000}))
    service.start()
    outcome = wait_for(service, job["job_id"])
    assert outcome["status"] == "failed"
    assert outcome["failure_kind"] == "RESOURCE_LIMIT"
    assert outcome["report_url"] is None
    assert service.runs.count_runs() == 0


def test_unknown_availability_stops_at_the_data_stage(service):
    original = FixtureMarketDataSource().snapshot("AAA")
    snapshot = replace(original, observations=tuple(replace(item, available_at=None) for item in original.observations))
    job = service.submit(payload(service, snapshot=snapshot))
    service.start()
    outcome = wait_for(service, job["job_id"])
    assert outcome["status"] == "failed"
    assert outcome["failure_kind"] == "DATA_UNAVAILABLE"
    assert outcome["failure_stage"] == "DATA_CHECKED"
    assert outcome["attempts"] == 1
    assert outcome["report_url"] is None


def test_reserved_job_is_restored_after_application_restart(service):
    job = service.submit(payload(service))
    service.close()
    reopened = ResearchApplicationService(service.root, scope_id="local-session")
    try:
        reopened.start()
        outcome = wait_for(reopened, job["job_id"])
        assert outcome["status"] == "completed"
        assert reopened.runs.count_runs() == 1
    finally:
        reopened.close()


def test_scope_cannot_observe_another_sessions_jobs(service):
    job = service.submit(payload(service))
    other = ResearchApplicationService(service.root, scope_id="another-session")
    try:
        assert other.list_jobs()["total"] == 0
        with pytest.raises(KeyError):
            other.get_job(job["job_id"])
    finally:
        other.close()


@pytest.mark.parametrize("changes", [
    {"budget": {"max_bytes": None}}, {"budget": {"max_wall_seconds": 301}},
    {"budget": {"unknown_limit": 1}}, {"model_config_ref": "private-provider"},
    {"snapshot_ref": "a" * 64}, {"question": "api_key=never-persist-this"},
])
def test_invalid_admission_does_not_create_a_job(service, changes):
    with pytest.raises((ValueError, FileNotFoundError)):
        service.submit({**payload(service), **changes})
    assert service.list_jobs()["total"] == 0


def test_supervisor_rejects_a_result_without_a_verified_publication(service):
    job = service.submit(payload(service))
    record = service.queue.claim("proof-test", job_id=job["job_id"])
    message = WorkerMessage("RESULT", record.job_id, {
        "invocation_digest": stable_digest("proof-test"), "status": "completed",
        "result_ref": "research-result:" + "a" * 64, "artifact_digest": "a" * 64,
    })
    service.runtime._supervisor._publish_terminal(record, message)
    assert service.queue.get(record.job_id).status is JobStatus.FAILED
    assert service.runs.count_runs() == 0


def test_owner_publishes_history_without_a_client_poll(service):
    job = service.submit(payload(service))
    service.start()
    deadline = time.monotonic() + 20
    while service.queue.get(job["job_id"]).status not in {JobStatus.COMPLETED, JobStatus.FAILED} and time.monotonic() < deadline:
        time.sleep(0.05)
    assert service.queue.get(job["job_id"]).status is JobStatus.COMPLETED
    deadline = time.monotonic() + 2
    while not service.runs.count_runs() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert service.runs.count_runs() == 1


def test_running_job_can_be_cancelled_without_publication(service):
    snapshot = FixtureMarketDataSource(points=256).snapshot("AAA")
    job = service.submit({**payload(service, snapshot=snapshot), "as_of": snapshot.as_of.date().isoformat()})
    service.start()
    wait_for(service, job["job_id"], statuses=("running",))
    service.cancel(job["job_id"])
    outcome = wait_for(service, job["job_id"])
    assert outcome["status"] == "cancelled" and outcome["report_url"] is None
    assert service.runs.count_runs() == 0


def test_terminated_owner_replays_an_active_job_with_a_new_attempt(service):
    snapshot = FixtureMarketDataSource(points=256).snapshot("AAA")
    job = service.submit({**payload(service, snapshot=snapshot), "as_of": snapshot.as_of.date().isoformat()})
    service.start()
    wait_for(service, job["job_id"], statuses=("running",))
    owner = service.runtime._supervisor._process
    owner.terminate()
    owner.join(timeout=3)
    service.close()
    resumed = ResearchApplicationService(service.root, scope_id="local-session")
    try:
        resumed.start()
        outcome = wait_for(resumed, job["job_id"])
        assert outcome["status"] == "completed", outcome
        assert outcome["attempts"] == 2
        assert outcome["snapshot_ref"] == snapshot.fingerprint
        assert resumed.runs.count_runs() == 1
    finally:
        resumed.close()


def test_policy_reference_is_not_a_hash_of_user_resource_budgets(service):
    first = service.submit(payload(service, key="small-budget", budget={"max_wall_seconds": 20}))
    second = service.submit(payload(service, key="large-budget", budget={"max_wall_seconds": 60}))
    requests = [service.queue.load_request_snapshot(service.queue.get(item["job_id"]).task_ref) for item in (first, second)]
    fingerprints = [request.research_plan.validation_spec["policy_fingerprint"] for request in requests]
    assert fingerprints[0] == fingerprints[1]
    assert requests[0].config_digest != requests[1].config_digest


def test_default_runtime_uses_a_selected_snapshot_instead_of_an_empty_gateway(service):
    from finahinking.research.runtime_service import ResearchRuntimeService, RuntimeLimits

    reserved = service.submit(payload(service))
    request = service.queue.load_request_snapshot(service.queue.get(reserved["job_id"]).task_ref)
    service.cancel(reserved["job_id"])
    request = replace(request, run_id="default-snapshot-runtime")
    runtime = ResearchRuntimeService(service.queue, autostart=False)
    try:
        job = runtime.submit(request, "default-snapshot", limits=RuntimeLimits(**request.research_plan.validation_spec["budget"]))
        runtime.start_supervisor()
        outcome = wait_for(service, job.job_id)
        assert outcome["status"] == "completed", outcome
        assert outcome["report_status"] == "AVAILABLE"
        assert outcome["metrics"]["total_return"] > 0
    finally:
        runtime.stop_supervisor()

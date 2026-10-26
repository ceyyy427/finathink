import json

from finahinking.research.application_service import ResearchApplicationService
from tests.research.grounded_worker_peer import install_peer
from tests.research.test_application_service import payload, wait_for
from tests.research.test_context_bundle import material
from tests.research.test_grounded_application import configured

__all__ = ["configured"]


def submit(service, **changes):
    return service.submit({**payload(service), "model_config_ref": "desk", "allow_model_calls": True, "materials": [material()], **changes})


def test_spawn_reconstructs_authorized_material_and_publishes_grounded_report(configured):
    service, _ = configured
    install_peer(service)
    job = submit(service)
    service.start()
    outcome = wait_for(service, job["job_id"])
    assert outcome["status"] == "completed", outcome
    study = outcome["model_research"]
    assert len(study["calls"]) == 3 and len(study["roles"]) == 5
    assert all(item["usage"]["total_units"] == 160 for item in study["calls"])
    assert study["synthesis"]["hypotheses"] == ["Test the interpretation out of sample."]
    wire = [json.loads(line) for line in (service.root / "peer-wire.jsonl").read_text().splitlines()]
    assert {json.loads(item["messages"][1]["content"])["role"] for item in wire} == {"technical", "news", "research"}
    assert outcome["metrics"]["total_return"] > 0 and outcome["report_status"] == "AVAILABLE"
    assert "factory opening" not in str(outcome) and "test-only-model-value" not in str(outcome)
    restarted = ResearchApplicationService(service.root)
    try:
        assert restarted.get_job(job["job_id"])["model_research"] == study
    finally:
        restarted.close()


def test_context_tampering_is_rejected_before_any_outbound_attempt(configured):
    service, _ = configured
    install_peer(service)
    job = submit(service)
    request = service.queue.load_request_snapshot(service.queue.get(job["job_id"]).task_ref)
    ref = request.research_plan.validation_spec["context_input_ref"]
    (service.root / "model-inputs" / f"{ref}.json").write_text("{}")
    service.start()
    outcome = wait_for(service, job["job_id"])
    assert outcome["failure_kind"] == "CONTEXT_INTEGRITY_FAILED" and outcome["attempts"] == 1
    assert not (service.root / "peer-wire.jsonl").exists()


def test_parallel_calls_cannot_escape_the_persisted_budget(configured):
    service, _ = configured
    install_peer(service)
    job = submit(service, budget={"max_provider_calls": 1})
    service.start()
    outcome = wait_for(service, job["job_id"])
    assert outcome["status"] == "failed" and outcome["failure_kind"] == "RESOURCE_LIMIT", outcome
    assert outcome["attempts"] == 1 and outcome["report_url"] is None
    assert len((service.root / "peer-wire.jsonl").read_text().splitlines()) == 1


def test_restarting_in_offline_mode_blocks_a_previously_authorized_queued_call(configured):
    service, _ = configured
    job = submit(service)
    offline = ResearchApplicationService(service.root, allow_model_calls=False)
    try:
        offline.start()
        outcome = wait_for(offline, job["job_id"])
        assert outcome["failure_kind"] == "OUTBOUND_DISABLED" and outcome["report_url"] is None
    finally:
        offline.close()


def test_missing_credential_is_reported_as_configuration_failure_without_network(configured, monkeypatch):
    service, _ = configured
    monkeypatch.delenv("FINAHINK_TEST_KEY", raising=False)
    job = submit(service)
    service.start()
    outcome = wait_for(service, job["job_id"])
    assert outcome["failure_kind"] == "PROVIDER_NOT_CONFIGURED" and outcome["attempts"] == 1

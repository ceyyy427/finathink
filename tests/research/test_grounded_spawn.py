import json
import time

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


def test_failed_model_attempts_keep_sanitized_durable_call_details(configured):
    service, _ = configured
    install_peer(service)
    job = submit(service, question="force-provider-failure", materials=[])
    service.start()
    outcome = wait_for(service, job["job_id"])
    assert outcome["status"] == "failed" and outcome["report_url"] is None
    study = outcome["model_research"]
    assert len(study["calls"]) == 1 and study["calls"][0]["role"] == "technical"
    assert study["calls"][0]["status"] == "SCHEMA_ERROR" and study["calls"][0]["dispatched"]
    assert study["calls"][0]["usage"]["total_units"] == 160
    restarted = ResearchApplicationService(service.root)
    try:
        assert restarted.get_job(job["job_id"])["model_research"]["calls"] == study["calls"]
    finally:
        restarted.close()
    assert "test-only-model-value" not in str(study) and "models.acme.com" not in str(study)


def test_cancelled_paid_attempts_are_unknown_not_zero_after_worker_termination(configured):
    service, _ = configured
    install_peer(service)
    job = submit(service, question="hold-model-request", materials=[])
    service.start()
    deadline = time.monotonic() + 15
    while not (service.root / "peer-wire.jsonl").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert (service.root / "peer-wire.jsonl").exists()
    service.cancel(job["job_id"])
    outcome = wait_for(service, job["job_id"])
    assert outcome["status"] == "cancelled"
    calls = outcome["model_research"]["calls"]
    assert len(calls) == 1 and calls[0]["dispatched"] and calls[0]["status"] == "UNKNOWN"
    assert calls[0]["usage"] is None and outcome["model_research"]["usage_status"] == "UNKNOWN"


def test_optional_learning_byte_exhaustion_prevents_any_publication(configured):
    service, _ = configured
    install_peer(service)
    job = submit(service, question="oversized-learning-case", materials=[material(role="learning")], budget={"max_bytes": 200000})
    service.start()
    outcome = wait_for(service, job["job_id"])
    assert outcome["status"] == "failed" and outcome["failure_kind"] == "RESOURCE_LIMIT"
    assert outcome["report_url"] is None and service.runs.list_runs() == []

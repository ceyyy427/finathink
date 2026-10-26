"""Online admission and the real workflow, with only the remote model doubled."""

import json

import pytest

from finahinking.research.application_service import ResearchApplicationService
from finahinking.research.provider_config import ProviderConfig, ProviderConfigStore
from finahinking.research.provider_status import ProviderCredentialRef
from tests.research.test_application_service import payload
from tests.research.test_context_bundle import material


@pytest.fixture
def configured(tmp_path):
    configs = ProviderConfigStore()
    configs.save(ProviderConfig("desk", "openai_compatible", "gpt-4o", endpoint="https://models.acme.com/v1", credential_ref=ProviderCredentialRef("desk", env_var="FINAHINK_TEST_KEY")))
    service = ResearchApplicationService(tmp_path / "artifacts", provider_configs=configs)
    try:
        yield service, configs
    finally:
        service.close()


def test_online_submission_freezes_private_inputs_without_resolving_credentials(configured):
    service, configs = configured
    body = {**payload(service), "model_config_ref": "desk", "allow_model_calls": True, "materials": [material()]}
    job = service.submit(body)
    assert job["execution_mode"] == "provider-grounded" and job["model_config_ref"] == "desk"
    assert job["budget"]["max_attempts"] == 1 and job["budget"]["max_provider_calls"] == 6
    request = service.queue.load_request_snapshot(service.queue.get(job["job_id"]).task_ref)
    assert "factory opening" not in str(request) and "models.acme.com" not in str(request)
    ref = request.research_plan.validation_spec["context_input_ref"]
    stored = json.loads((service.root / "model-inputs" / f"{ref}.json").read_text())
    assert stored["materials"][0]["summary"] == "The company announced a factory opening."
    assert stored["provider"]["model"] == "gpt-4o"
    assert service.submit(body)["job_id"] == job["job_id"]
    configs.remove("desk")
    assert stored["provider"]["model"] == "gpt-4o"  # frozen consent; cancellation revokes a queued job
    assert service.get_job(job["job_id"])["job_id"] == job["job_id"]


@pytest.mark.parametrize("changes", [
    {"allow_model_calls": False}, {"allow_model_calls": "true"},
    {"budget": {"max_attempts": 2}}, {"materials": [material(available_at="2027-01-01T00:00:00Z")]},
])
def test_unconsented_replayed_or_future_context_is_not_enqueued(configured, changes):
    service, _ = configured
    with pytest.raises((TypeError, ValueError)):
        service.submit({**payload(service), "model_config_ref": "desk", "allow_model_calls": True, **changes})
    assert service.list_jobs()["total"] == 0


def test_offline_application_rejects_all_outbound_model_admission(tmp_path):
    service = ResearchApplicationService(tmp_path / "artifacts", allow_model_calls=False)
    try:
        with pytest.raises(ValueError):
            service.submit({**payload(service), "model_config_ref": "desk", "allow_model_calls": True})
    finally:
        service.close()


def test_grounded_workflow_keeps_missing_roles_visible_and_numeric_engine_authoritative():
    from finahinking.research.analyst_runtime import AnalystPool, GroundedResearchManager
    from finahinking.research.contracts import ResearchState
    from finahinking.research.snapshot_tools import SnapshotResearchTools
    from finahinking.research.workflow import ResearchOrchestrator
    from tests.research.test_grounded_driver import setup_driver

    req, driver, peer = setup_driver(materials=(material(),))
    tools = SnapshotResearchTools(driver.snapshot)
    result = ResearchOrchestrator().run(req, driver, tools, analyst_pool=AnalystPool(), manager=GroundedResearchManager(driver))
    assert result.state.current_state is ResearchState.LEARNING_RECORDED
    statuses = {report.role: report.status for report in result.state.analyst_reports}
    assert statuses == {"fundamentals": "INSUFFICIENT_DATA", "technical": "READY", "sentiment": "INSUFFICIENT_DATA", "news": "READY", "learning": "INSUFFICIENT_DATA"}
    assert len(peer.calls) == 3 and result.decision.paper_only
    assert tools.analysis["dataset_fingerprint"] == req.snapshot_ref and tools.analysis["factor"]["latest_value"] > 0


def test_online_report_persists_and_displays_synthesis_without_private_inputs(tmp_path):
    from finahinking.research.analyst_runtime import AnalystPool, GroundedResearchManager
    from finahinking.research.reports import ReportBundleWriter
    from finahinking.research.snapshot_tools import SnapshotResearchTools
    from finahinking.research.workflow import ResearchOrchestrator
    from tests.research.test_grounded_driver import setup_driver

    req, driver, _ = setup_driver()
    tools = SnapshotResearchTools(driver.snapshot)
    result = ResearchOrchestrator().run(req, driver, tools, analyst_pool=AnalystPool(), manager=GroundedResearchManager(driver))
    tools.analysis["model_research"] = {"execution_mode": "provider-grounded", "calls": driver.call_records, "synthesis": driver.synthesis_summary,
                                       "roles": [{"role": report.role, "status": report.status, "claims": list(report.claims)} for report in result.state.analyst_reports]}
    manifest = ReportBundleWriter().write(result, tmp_path / "reports", quant_analytics=tools.analysis)
    assert manifest.source_snapshot["model_research"]["synthesis"]["hypotheses"] == ["Test the interpretation out of sample."]
    html = (tmp_path / "reports" / req.run_id / "complete_report.html").read_text()
    assert "Test the interpretation out of sample." in html and "INSUFFICIENT_DATA" in html
    assert "fake-local-value" not in html and "models.acme.com" not in html
    from finahinking.research.ui import research_view_model

    assert research_view_model(result.state, manifest)["mode"] == "USER_API"

from __future__ import annotations

import sqlite3
from datetime import date

from finahinking.local_app import LocalAppConfig, LocalApplication
from finahinking.research.contracts import (
    AgentReport,
    ReportManifest,
    ResearchRunState,
    ResearchState,
)
from finahinking.research.reports import ReportBundleWriter
from finahinking.research.ui import research_report_url, research_view_model


def make_state() -> ResearchRunState:
    return ResearchRunState(
        run_id="run-ui",
        current_state=ResearchState.LEARNING_RECORDED,
        as_of=date(2026, 10, 1),
        state_history=(ResearchState.RECEIVED, ResearchState.LEARNING_RECORDED),
        analyst_reports=(AgentReport("technical", "READY", evidence_refs=("artifact:t",), limitations=("offline",)),),
        decision_eligible=True,
    )


def make_manifest() -> ReportManifest:
    return ReportManifest(
        run_id="run-ui",
        schema_version="research-report.v1",
        files={"complete_report.html": "digest"},
        source_snapshot={"as_of": "2026-10-01"},
        created_at="2026-10-01T00:00:00+00:00",
    )


def test_view_model_is_redacted_sorted_and_paper_only() -> None:
    model = research_view_model(make_state(), make_manifest())
    assert model["mode"] == "OFFLINE"
    assert model["paper_only"] is True
    assert model["as_of"] == "2026-10-01"
    assert model["analysts"][0]["role"] == "technical"
    assert model["missing_analysts"] == ["fundamentals", "learning", "news", "sentiment"]
    assert research_report_url("run-ui", "complete") == "/research/run-ui/report/complete"


def test_report_url_rejects_path_traversal_and_unknown_section() -> None:
    for run_id, section in (("../secret", "complete"), ("run-ui", "../secret"), ("run-ui", "unknown")):
        try:
            research_report_url(run_id, section)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe report URL was accepted")


def test_local_app_exposes_read_only_status_and_report_routes(tmp_path) -> None:
    app = LocalApplication(LocalAppConfig(db_path=":memory:", offline=True), connection=sqlite3.connect(":memory:"))
    state = make_state()
    result_type = type("Result", (), {"state": state, "events": (), "decision": None, "manifest": None})
    result = result_type()
    manifest = ReportBundleWriter().write(result, app.artifact_root / "reports")
    app.register_research_run(result, manifest)

    status, content_type, payload = app.route("GET", "/research/run-ui/status")
    assert (status, content_type) == (200, "application/json")
    assert payload["paper_only"] is True
    page_status, page_type, page = app.route("GET", "/research/run-ui")
    assert page_status == 200 and page_type.startswith("text/html")
    assert "PAPER-ONLY" in page
    report_status, report_type, report = app.route("GET", "/research/run-ui/report/complete")
    assert report_status == 200 and report_type.startswith("text/html")
    assert "Finathink research report" in report
    missing_status, _, missing = app.route("GET", "/research/missing/status")
    assert missing_status == 404
    assert missing["error"] == "research run not found"


def test_local_app_exposes_server_owned_workbench_payload_read_only() -> None:
    app = LocalApplication(LocalAppConfig(db_path=":memory:", offline=True), connection=sqlite3.connect(":memory:"))
    status, content_type, payload = app.route("GET", "/api/research/series")
    assert (status, content_type) == (200, "application/json")
    assert payload["workbench"]["paper_only"] is True
    assert payload["workbench"]["provenance"]["dataset_fingerprint"] == payload["dataset"]["fingerprint"]
    workbench_status, workbench_type, workbench_payload = app.route("GET", "/api/research/workbench")
    assert (workbench_status, workbench_type) == (200, "application/json")
    assert workbench_payload["run_id"] == payload["workbench"]["run_id"]
    rejected, rejected_type, rejected_payload = app.route("POST", "/api/research/workbench", body={})
    assert rejected == 405
    assert rejected_type == "application/json"
    assert "error" in rejected_payload


def test_local_app_exposes_secret_free_provider_status_and_settings_page(monkeypatch) -> None:
    secret = "should-never-be-rendered"
    monkeypatch.setenv("FINAHINK_USER_API_KEY", secret)
    app = LocalApplication(LocalAppConfig(db_path=":memory:", offline=True), connection=sqlite3.connect(":memory:"))
    status, content_type, payload = app.route("GET", "/api/research/providers")
    assert (status, content_type) == (200, "application/json")
    encoded = str(payload)
    assert secret not in encoded
    user = next(item for item in payload["providers"] if item["provider"] == "user-compatible")
    assert user["configured"] is True
    rejected, _, error = app.route("POST", "/api/research/providers", body={})
    assert rejected == 405
    assert "read-only" in error["error"]
    page_status, page_type, page = app.route("GET", "/settings/providers")
    assert page_status == 200 and page_type.startswith("text/html")
    assert 'name="credential_env"' in page and 'name="api_key"' not in page
    assert secret not in page


def test_local_app_renders_first_class_workbench_page() -> None:
    app = LocalApplication(LocalAppConfig(db_path=":memory:", offline=True), connection=sqlite3.connect(":memory:"))
    status, content_type, page = app.route("GET", "/workbench")
    assert status == 200 and content_type.startswith("text/html")
    assert "Factor strategy workbench" in page
    assert "PAPER-ONLY" in page
    assert "/api/research/workbench" in page
    assert "Factor to execution flow" in page
    assert "<table" in page


def test_research_page_links_to_the_standalone_workbench() -> None:
    app = LocalApplication(LocalAppConfig(db_path=":memory:", offline=True), connection=sqlite3.connect(":memory:"))
    status, _, page = app.route("GET", "/research")
    assert status == 200
    assert "/workbench" in page

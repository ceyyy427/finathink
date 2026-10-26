"""Public research admission is CSRF-protected and never spawns from HTTP."""

import json

import pytest

from finahinking.data.user_api import TransportResponse
from finahinking.data.user_api_contracts import DataConnectionConfig
from finahinking.local_app import LocalAppConfig, LocalApplication
from finahinking.p8_2.data_sources import FixtureMarketDataSource


@pytest.fixture
def app(tmp_path):
    application = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "app.sqlite"), offline=True))
    try:
        yield application
    finally:
        application.close()


def body(app):
    ref = app.research_service.snapshots.save(FixtureMarketDataSource(points=20).snapshot("AAA"))
    return {"_csrf": app.csrf_token, "idempotency_key": "public-1", "snapshot_ref": ref, "instrument": "AAA", "as_of": "2026-10-01", "question": "Evaluate the cost-aware momentum baseline", "model_config_ref": "offline"}


def test_public_job_admission_is_idempotent_and_inert(app):
    values = body(app)
    assert app.route("POST", "/api/research/jobs", body={**values, "_csrf": "wrong"})[0] == 403
    status, _, first = app.route("POST", "/api/research/jobs", body=values)
    assert status == 202
    assert first["status"] == "queued"
    assert app.route("POST", "/api/research/jobs", body=values)[2]["job_id"] == first["job_id"]
    assert app.research_service.runtime._supervisor._process is None
    assert app.route("GET", "/api/research/jobs")[2]["total"] == 1
    assert app.route("GET", f'/api/research/jobs/{first["job_id"]}')[2]["snapshot_ref"] == values["snapshot_ref"]
    assert app.route("POST", f'/api/research/jobs/{first["job_id"]}/cancel', body={})[0] == 403
    assert app.route("POST", f'/api/research/jobs/{first["job_id"]}/cancel', body={"_csrf": app.csrf_token})[2]["status"] == "cancelled"


def test_submission_errors_are_safe_and_leave_no_job(app):
    values = body(app)
    for changed in ({"snapshot_ref": "a" * 64}, {"budget": {"max_bytes": None}}, {"question": "api_key=private-value"}, {"model_config_ref": "other"}):
        status, _, payload = app.route("POST", "/api/research/jobs", body={**values, **changed})
        assert status == 422
        assert "private-value" not in str(payload)
    assert app.route("GET", "/api/research/jobs")[2]["total"] == 0
    assert app.route("GET", "/api/research/jobs/unknown")[0] == 404
    assert app.route("GET", "/api/research/jobs?limit=bad")[0] == 422


def test_explicit_sample_preparation_does_not_submit_or_invent_a_user_source(app):
    assert app.route("GET", "/api/research/snapshots")[2]["snapshots"] == []
    assert app.route("POST", "/api/research/snapshots/sample", body={"instrument": "AAA"})[0] == 403
    code, _, snapshot = app.route("POST", "/api/research/snapshots/sample", body={"instrument": "AAA", "_csrf": app.csrf_token})
    assert code == 201 and snapshot["mode"] == "SAMPLE"
    assert snapshot["snapshot_ref"] in {item["snapshot_ref"] for item in app.route("GET", "/api/research/snapshots")[2]["snapshots"]}
    assert app.route("GET", "/api/research/jobs")[2]["total"] == 0


def test_workspace_has_shell_forms_empty_loading_and_recovery_regions(app):
    code, _, html = app.route("GET", "/research/jobs")
    assert code == 200
    for marker in ('data-run-workspace', 'data-run-submit', 'data-run-progress', 'data-run-error', 'data-run-empty', 'data-run-cancel', 'data-snapshot-sample', 'data-snapshot-capture', 'aria-live="polite"', 'finathink-research.js', 'skip-link'):
        assert marker in html
    assert app.research_service.runtime._supervisor._process is None


def test_explicit_connection_capture_is_available_to_job_admission(app):
    calls = []
    rows = [{"instrument": "AAA", "timestamp": f"2026-01-0{day}T00:00:00Z", "available_at": f"2026-01-0{day}T01:00:00Z", "open": 100, "high": 103, "low": 99, "close": 102, "volume": 1000} for day in range(2, 7)]

    class Transport:
        def request(self, url, *, headers, timeout, allow_redirects=False):
            calls.append(url)
            return TransportResponse(200, {"content-type": "application/json"}, json.dumps(rows).encode())

    app._data_transport = Transport()
    config = DataConnectionConfig("feed", "User feed", "https://93.184.216.34/prices", None, "no_auth", {key: key for key in rows[0]})
    app._data_connections.save(config)
    assert not calls
    code, _, result = app.route("POST", "/api/research/snapshots/capture", body={"_csrf": app.csrf_token, "connection_id": "feed", "instrument": "AAA", "start": "2026-01-01", "end": "2026-01-10", "as_of": "2026-01-10"})
    assert code == 201 and result["mode"] == "CAPTURED" and len(calls) == 1
    code, _, job = app.route("POST", "/api/research/jobs", body={"_csrf": app.csrf_token, "idempotency_key": "captured", "snapshot_ref": result["snapshot_ref"], "instrument": "AAA", "as_of": "2026-01-10", "question": "Inspect the momentum baseline", "model_config_ref": "offline"})
    assert code == 202 and job["snapshot_ref"] == result["snapshot_ref"]
    assert len(calls) == 1  # Submission reconstructs refs, never refetches.

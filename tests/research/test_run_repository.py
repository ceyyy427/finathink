"""A report inventory survives restarts without trusting modified bundles."""

from dataclasses import replace
from pathlib import Path

import pytest

from finahinking.local_app import LocalAppConfig, LocalApplication
from finahinking.research.contracts import (
    AgentReport,
    ResearchRunResult,
    ResearchRunState,
    ResearchState,
    RunEvent,
)
from finahinking.research.reports import ReportBundleWriter
from finahinking.research.run_repository import RunRepository


def completed(root: Path, run_id="persisted-run", *, include_events=True):
    state = ResearchRunState(
        run_id, ResearchState.LEARNING_RECORDED, as_of="2020-01-01",
        state_history=(ResearchState.RECEIVED, ResearchState.LEARNING_RECORDED),
        analyst_reports=(AgentReport("technical", "OFFLINE", evidence_refs=("artifact:local",)),),
        decision_eligible=False,
    )
    events = (
        RunEvent(f"{run_id}:received", run_id, ResearchState.RECEIVED, "workflow", "2026-10-15T12:00:00Z", "a" * 64),
        RunEvent(f"{run_id}:done", run_id, ResearchState.LEARNING_RECORDED, "workflow", "2026-10-15T12:01:00Z", "b" * 64, metadata={"dataset_fingerprint": "a" * 64}),
    )
    result = ResearchRunResult(state, events if include_events else ())
    manifest = ReportBundleWriter().write(result, root)
    return result, manifest


def repository(tmp_path, *, scope="local"):
    return RunRepository(tmp_path / "index.sqlite3", report_root=tmp_path / "reports", scope_id=scope)


def test_restart_restores_typed_state_reports_and_dataset_binding(tmp_path):
    result, manifest = completed(tmp_path / "reports")
    repository(tmp_path).record_result(result, manifest)
    entry = repository(tmp_path).get_run("persisted-run")
    assert entry["state"].current_state is ResearchState.LEARNING_RECORDED
    assert entry["state"].as_of.isoformat() == "2020-01-01"
    assert entry["dataset_fingerprint"] == "a" * 64
    assert entry["created_at"] == "2026-10-15T12:00:00+00:00"
    assert entry["report_status"] == "AVAILABLE"
    assert entry["bundle"] == tmp_path / "reports" / "persisted-run"


def test_registered_result_is_idempotent_but_cannot_be_rebound(tmp_path):
    repo = repository(tmp_path)
    result, manifest = completed(tmp_path / "reports")
    repo.record_result(result, manifest)
    repo.record_result(result, manifest)
    assert repo.count_runs() == 1
    changed = replace(result, state=replace(result.state, failure_message="different result"))
    with pytest.raises(ValueError, match="bound|immutable"):
        repo.record_result(changed, manifest)


@pytest.mark.parametrize("mutation,wanted", [("missing", "MISSING"), ("content", "CORRUPT"), ("manifest", "CORRUPT"), ("symlink", "CORRUPT")])
def test_missing_or_modified_reports_remain_listed_with_explicit_state(tmp_path, mutation, wanted):
    result, manifest = completed(tmp_path / "reports")
    repo = repository(tmp_path)
    repo.record_result(result, manifest)
    bundle = tmp_path / "reports" / "persisted-run"
    report = bundle / "complete_report.html"
    if mutation == "missing":
        report.unlink()
    elif mutation == "content":
        report.write_text("modified")
    elif mutation == "manifest":
        path = bundle / "manifest.json"
        path.write_text(path.read_text().replace('"run_id": "persisted-run"', '"run_id": "another-run"'))
    else:
        outside = tmp_path / "outside.html"
        outside.write_bytes(report.read_bytes())
        report.unlink()
        report.symlink_to(outside)
    assert repo.get_run("persisted-run")["report_status"] == wanted
    assert repo.list_runs()[0]["report_status"] == wanted


def test_repository_filters_orders_pages_and_isolates_session_scope(tmp_path):
    repo = repository(tmp_path)
    for number in range(3):
        result, manifest = completed(tmp_path / "reports", f"run-{number}")
        repo.record_result(result, manifest, created_at=f"2026-10-{10 + number}T00:00:00Z", started_at=f"2026-10-{10 + number}T00:00:01Z", finished_at=f"2026-10-{10 + number}T00:00:02Z")
    rows = repo.list_runs(limit=1, offset=1, sort_by="created_at", order="desc", state="LEARNING_RECORDED")
    assert [row["run_id"] for row in rows] == ["run-1"]
    assert repository(tmp_path, scope="another-session").count_runs() == 0
    with pytest.raises(KeyError):
        repository(tmp_path, scope="another-session").get_run("run-1")
    with pytest.raises(ValueError):
        repo.list_runs(sort_by="created_at; DELETE FROM runs")
    with pytest.raises(ValueError):
        repo.list_runs(limit=0)


def test_invalid_run_or_manifest_binding_cannot_enter_inventory(tmp_path):
    result, manifest = completed(tmp_path / "reports")
    repo = repository(tmp_path)
    with pytest.raises(ValueError, match="run|manifest"):
        repo.record_result(result, replace(manifest, run_id="another-run"))
    assert repo.count_runs() == 0


def test_application_restart_keeps_overview_history_comparison_and_report(tmp_path):
    config = LocalAppConfig(db_path=str(tmp_path / "local.sqlite3"), offline=True)
    app = LocalApplication(config)
    result, manifest = completed(app.artifact_root / "reports")
    app.register_research_run(result, manifest)
    app.close()
    reopened = LocalApplication(config)
    try:
        overview = reopened.overview()
        assert overview["metrics"]["research_runs"] == 1
        assert any(row["date"] == "2026-10-15" and row["research"] == 1 for row in overview["activity_series"])
        assert not any(row["date"] == "2020-01-01" for row in overview["activity_series"])
        assert reopened.research_comparison()["runs"][0]["run_id"] == "persisted-run"
        status, _, history = reopened.route("GET", "/api/research/runs?limit=1")
        assert status == 200 and history["total"] == 1
        status, _, report = reopened.route("GET", "/research/persisted-run/report/complete")
        assert status == 200 and "Finathink 研究报告" in report
    finally:
        reopened.close()


def test_application_refuses_corrupt_report_bytes_but_keeps_history(tmp_path):
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "local.sqlite3")))
    result, manifest = completed(app.artifact_root / "reports")
    app.register_research_run(result, manifest)
    (app.artifact_root / "reports" / "persisted-run" / "complete_report.html").write_text("do not serve corrupted bytes")
    try:
        status, _, payload = app.route("GET", "/research/persisted-run/status")
        assert status == 200 and payload["report_status"] == "CORRUPT"
        assert payload["report_links"] == {}
        status, _, payload = app.route("GET", "/research/persisted-run/report/complete")
        assert status == 409 and payload["report_status"] == "CORRUPT"
    finally:
        app.close()


def test_missing_execution_start_is_not_invented_from_report_creation(tmp_path):
    result, manifest = completed(tmp_path / "reports", include_events=False)
    repo = repository(tmp_path)
    repo.record_result(result, manifest)
    assert repo.get_run("persisted-run")["started_at"] is None


@pytest.mark.parametrize("metadata", [{"strategy_version": "api_key=private-value"}, {"policy_fingerprint": "https://private.example/config"}])
def test_index_metadata_accepts_only_public_version_references(tmp_path, metadata):
    result, manifest = completed(tmp_path / "reports")
    with pytest.raises(ValueError):
        repository(tmp_path).record_result(result, manifest, **metadata)


def test_manifest_must_bind_the_actual_state_before_initial_registration(tmp_path):
    result, manifest = completed(tmp_path / "reports")
    changed = replace(result, state=replace(result.state, decision_eligible=True))
    with pytest.raises(ValueError, match="bound|state"):
        repository(tmp_path).record_result(changed, manifest)

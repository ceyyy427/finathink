from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime

from finahinking.research.contracts import (
    AgentReport,
    FailureKind,
    ResearchRunResult,
    ResearchRunState,
    ResearchState,
    RunEvent,
)
from finahinking.research.reports import ReportBundleWriter, verify_report_bundle


def test_pre_theme_report_keeps_original_bytes_manifest_and_creation_date(tmp_path):
    result = _result()
    writer = ReportBundleWriter()
    writer.write(result, tmp_path)
    bundle = tmp_path / result.state.run_id
    report = bundle / "complete_report.html"
    report.write_text('<!doctype html><html lang="en"><title>Original legacy publication</title><body>Original legacy publication</body></html>')
    path = bundle / "manifest.json"
    payload = json.loads(path.read_text())
    payload["files"]["complete_report.html"] = hashlib.sha256(report.read_bytes()).hexdigest()
    for key in ("report_template", "report_input_digests", "method_factor_snapshot"):
        payload["source_snapshot"].pop(key, None)
    path.write_text(json.dumps(payload))
    original = {item.name: item.read_bytes() for item in (path, report)}
    replay = writer.write(result, tmp_path)
    assert replay.created_at.isoformat() == payload["created_at"]
    assert {item.name: item.read_bytes() for item in (path, report)} == original
    assert verify_report_bundle(path).ok
from finahinking.research.ui import research_runtime_view_model


def _result(state: ResearchState = ResearchState.RISK_REVIEW, *, failure_kind: FailureKind | None = None) -> ResearchRunResult:
    run_state = ResearchRunState(
        run_id="tree-run",
        current_state=state,
        as_of=date(2026, 10, 9),
        state_history=(ResearchState.RECEIVED, ResearchState.DATA_CHECKED, state),
        analyst_reports=(AgentReport("technical", "READY", evidence_refs=("artifact:price",)),),
        failure_kind=failure_kind,
        decision_eligible=False,
    )
    event = RunEvent(
        "event-tree", "tree-run", state, "workflow", datetime(2026, 10, 9, tzinfo=UTC), "digest",
        metadata={
            "experiments": [{"experiment_id": "exp-1", "status": "COMPLETE", "metrics": {"ic": 0.1}}],
            "risk_attribution": {"status": "RECORDED", "factors": [{"name": "volatility", "contribution": 0.2}]},
            "learning_history": [{"card_id": "learn-1", "status": "RECORDED"}],
        },
    )
    return ResearchRunResult(state=run_state, events=(event,))


def test_write_runtime_tree_publishes_navigable_runtime_pages_and_consistent_digests(tmp_path):
    writer = ReportBundleWriter(output_root=tmp_path / "reports")
    manifest = writer.write_runtime_tree(_result())
    bundle = tmp_path / "reports" / "tree-run"
    for relative in ("experiments/index.html", "risk_attribution/index.html", "learning_history/index.html"):
        assert relative in manifest.files
        assert (bundle / relative).exists()
        assert __import__("hashlib").sha256((bundle / relative).read_bytes()).hexdigest() == manifest.files[relative]
    runtime_html = (bundle / "risk_attribution/index.html").read_text(encoding="utf-8")
    assert "/research/tree-run/report/complete" in runtime_html
    assert "/research/tree-run/report/experiments" in runtime_html
    assert verify_report_bundle(bundle / "manifest.json").ok


def test_runtime_tree_marks_missing_stages_explicitly(tmp_path):
    result = _result(ResearchState.ANALYSTS_RUNNING)
    manifest = ReportBundleWriter(output_root=tmp_path / "reports").write_runtime_tree(result)
    bundle = tmp_path / "reports" / "tree-run"
    for relative in ("experiments/index.html", "risk_attribution/index.html", "learning_history/index.html"):
        html = (bundle / relative).read_text(encoding="utf-8")
        assert "PENDING" in html or "BLOCKED" in html
    model = research_runtime_view_model("tree-run", state=result.state, manifest=manifest, events=result.events)
    assert "experiments" in model and "risk_attribution" in model and "learning_history" in model


def test_runtime_tree_marks_active_stage_current_even_when_state_history_includes_it(tmp_path):
    result = _result(ResearchState.RISK_REVIEW)
    manifest = ReportBundleWriter(output_root=tmp_path / "reports").write_runtime_tree(result)
    bundle = tmp_path / "reports" / "tree-run"
    risk_html = (bundle / "risk_attribution/index.html").read_text(encoding="utf-8")
    assert "Status: <strong>CURRENT</strong>" in risk_html
    model = research_runtime_view_model("tree-run", state=result.state, manifest=manifest, events=result.events)
    assert model["stage_status"]["risk_attribution"] == "CURRENT"


def test_runtime_tree_blocks_all_runtime_stages_for_data_unavailable_with_blocking_evidence(tmp_path):
    for state, failure_kind in (
        (ResearchState.DATA_UNAVAILABLE, FailureKind.DATA_UNAVAILABLE),
        (ResearchState.NO_DATA_AVAILABLE, FailureKind.NO_DATA_AVAILABLE),
    ):
        result = _result(state, failure_kind=failure_kind)
        manifest = ReportBundleWriter(output_root=tmp_path / state.value).write_runtime_tree(result)
        bundle = tmp_path / state.value / "tree-run"
        for section in ("experiments", "risk_attribution", "learning_history"):
            payload = manifest.source_snapshot[section]
            assert payload["status"] == "BLOCKED"
            assert payload["failure_kind"] == failure_kind.value
            assert f"state:{state.value}" in payload["blocked_evidence"]
            assert f"failure_kind:{failure_kind.value}" in payload["blocked_evidence"]
            assert "Status: <strong>BLOCKED</strong>" in (bundle / section / "index.html").read_text(encoding="utf-8")


def test_runtime_payload_is_redacted_and_schema_versioned():
    result = _result()
    model = research_runtime_view_model(
        result.state,
        manifest={"run_id": "tree-run", "schema_version": "research-report.v1", "files": {}, "source_snapshot": {}},
        events=result.events,
    )
    assert model["schema_version"] == 3
    assert model["experiments"][0]["experiment_id"] == "exp-1"
    assert model["stage_status"]["risk"] == "CURRENT"
    assert model["stage_status"]["risk_attribution"] == "CURRENT"
    assert "api_key" not in json.dumps(model).lower()

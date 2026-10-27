from __future__ import annotations

import json
from datetime import UTC, datetime

from finahinking.research.contracts import (
    AgentReport,
    DecisionCard,
    ResearchRunResult,
    ResearchRunState,
    ResearchState,
    RunEvent,
)
from finahinking.research.reports import (
    ReportBundleWriter,
    render_section_html,
    verify_report_bundle,
)


def result_with_claim(claim: str = "fixture claim") -> ResearchRunResult:
    report = AgentReport(
        role="fundamentals",
        status="READY",
        claims=(claim,),
        evidence_refs=("artifact:fundamental",),
        limitations=("offline fixture",),
        model_ref="offline/fixture-v1",
    )
    state = ResearchRunState(
        run_id="run-report",
        current_state=ResearchState.LEARNING_RECORDED,
        state_history=(ResearchState.RECEIVED, ResearchState.LEARNING_RECORDED),
        analyst_reports=(report,),
        decision_eligible=True,
    )
    event = RunEvent(
        event_id="event-1",
        run_id="run-report",
        state=ResearchState.RECEIVED,
        actor="workflow",
        timestamp=datetime(2026, 10, 1, tzinfo=UTC),
        payload_digest="digest",
    )
    decision = DecisionCard(
        action="PAPER-ONLY research allocation",
        weights={"ETF:SPY": 1.0},
        rationale="fixture rationale",
        evidence_refs=("artifact:fundamental",),
        limitations=("offline fixture",),
        eligible=True,
    )
    return ResearchRunResult(state=state, events=(event,), decision=decision)


def test_writer_creates_required_bundle_and_verifier_accepts_it(tmp_path) -> None:
    manifest = ReportBundleWriter().write(result_with_claim(), tmp_path / "reports")
    bundle = tmp_path / "reports" / "run-report"

    assert (bundle / "complete_report.html").exists()
    assert (bundle / "manifest.json").exists()
    assert (bundle / "activity.jsonl").exists()
    for role in ("fundamentals", "technical", "sentiment", "news", "learning"):
        assert (bundle / "1_analysts" / f"{role}.html").exists()
    for section in ("2_evidence", "3_research", "4_quant", "5_risk", "6_paper_decision"):
        assert (bundle / section / "index.html").exists()
    assert "complete_report.html" in manifest.files
    verification = verify_report_bundle(bundle / "manifest.json")
    assert verification.ok is True
    assert verification.errors == ()


def test_new_reports_are_themed_offline_and_old_publications_are_immutable(tmp_path):
    import hashlib

    result = result_with_claim()
    writer = ReportBundleWriter()
    manifest = writer.write(result, tmp_path, quant_analytics={"metrics": {"unknown": None}})
    path = tmp_path / result.state.run_id / "complete_report.html"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    assert "#f5f8fd" in path.read_text() and "Content-Security-Policy" in path.read_text()
    assert writer.write(result, tmp_path).files == manifest.files
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_model_and_news_text_are_html_escaped_and_not_executable() -> None:
    rendered = render_section_html(
        "News <script>alert(1)</script>",
        {"claim": '<img src=x onerror="alert(2)">', "url": "https://evil.invalid"},
        ("evidence:&lt;1",),
        ("<script>bad</script>",),
    )
    assert "<script>" not in rendered
    assert "onerror=" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "&lt;img" in rendered


def test_manifest_detects_tampering_and_activity_is_jsonl(tmp_path) -> None:
    ReportBundleWriter().write(result_with_claim(), tmp_path / "reports")
    bundle = tmp_path / "reports" / "run-report"
    with (bundle / "complete_report.html").open("a", encoding="utf-8") as handle:
        handle.write("tampered")
    verification = verify_report_bundle(bundle / "manifest.json")
    assert verification.ok is False
    assert any("complete_report.html" in error for error in verification.errors)
    events = [json.loads(line) for line in (bundle / "activity.jsonl").read_text(encoding="utf-8").splitlines()]
    assert events[0]["run_id"] == "run-report"


def test_report_does_not_copy_secrets_or_absolute_paths(tmp_path) -> None:
    result = result_with_claim("model says api_key=secret endpoint=https://secret.invalid /Users/mac/private")
    ReportBundleWriter().write(result, tmp_path / "reports")
    bundle = tmp_path / "reports" / "run-report"
    content = "\n".join(path.read_text(encoding="utf-8") for path in bundle.rglob("*.html"))
    assert "api_key=secret" not in content
    assert "endpoint=https://secret.invalid" not in content
    assert "/Users/mac/private" not in content


"""Evidence-first summaries distinguish unfinished work and immutable results."""
import hashlib

import pytest

from finahinking.research.application_service import ResearchApplicationService
from finahinking.research.reports import ReportBundleWriter
from tests.research.test_application_service import payload, service, wait_for
from tests.research.test_reports import result_with_claim

__all__ = ["service"]


def job(**changes):
    return {"job_id": "job-one", "run_id": "research-one", "status": "queued", "state": "RECEIVED",
            "snapshot_ref": "a" * 64, "instrument": "AAA", "as_of": "2026-10-01", "data_mode": "SAMPLE",
            "metrics": {}, "stages": [{"name": "DATA_CHECKED", "status": "PENDING"}],
            "report_status": "UNAVAILABLE", "execution_mode": "local-deterministic", **changes}


def explain(value):
    from finahinking.research.explanations import research_explanation
    return research_explanation(value)


@pytest.mark.parametrize("status, term", [("queued", "排队"), ("running", "运行"), ("retryable", "重试"), ("cancelled", "取消")])
def test_incomplete_jobs_do_not_acquire_results_or_evidence(status, term):
    summary = explain(job(status=status, retry_at="2026-10-27T00:00:00Z"))
    assert term in summary["headline"]
    assert summary["evidence"] == []
    assert summary["outcome"] == "NOT_READY"
    assert summary["next_steps"]
    assert summary["timeline"][0]["fact"] == "尚未记录本阶段结果。"


def test_timeout_has_actionable_copy_without_claiming_financial_failure():
    summary = explain(job(status="failed", failure_kind="TIMEOUT", failure_stage="QUANT_VALIDATION"))
    assert "超时" in summary["headline"]
    assert "预算" in " ".join(summary["next_steps"])
    assert summary["outcome"] == "BLOCKED"
    assert "无效因子" not in str(summary)


def test_bad_publication_with_stale_metrics_is_not_success():
    summary = explain(job(status="completed", report_status="INVALID", failure_kind="PUBLICATION_INVALID", metrics={"total_return": 5}))
    assert summary["outcome"] == "BLOCKED"
    assert summary["evidence"] == []
    assert "校验" in summary["headline"]


def test_completed_summary_keeps_zero_separate_from_missing_and_preserves_disagreement():
    summary = explain(job(status="completed", report_status="AVAILABLE", metrics={"total_return": 0, "max_drawdown": None}, risk_portfolio={"execution_status": "PENDING_NEXT_OBSERVATION"}, model_research={"synthesis": {"disagreements": ["新闻与技术观点相反"]}, "roles": [{"role": "news", "status": "INSUFFICIENT_DATA"}]}))
    assert summary["outcome"] == "AVAILABLE"
    assert "等待" in summary["conclusion"] and "下一" in summary["conclusion"]
    metrics = {item["key"]: item["value"] for item in summary["evidence"] if "key" in item}
    assert metrics == {"total_return": 0, "max_drawdown": None}
    assert summary["disagreements"] == ["新闻与技术观点相反"]
    assert any("news" in item for item in summary["conditions"])
    assert all(item["href"].startswith("/research/research-one/report/complete#") for item in summary["evidence"])
    assert any("SAMPLE" in condition for condition in summary["conditions"])


def test_html_is_escaped_and_offline_evidence_links_stay_inside_document():
    from finahinking.research.explanations import render_explanation_html
    summary = explain(job(status="completed", report_status="AVAILABLE", metrics={"total_return": 0}, model_research={"synthesis": {"disagreements": ["<img src=x>"], "hypotheses": ["password=private-material"]}}))
    text = render_explanation_html(summary, offline=True)
    assert '<img src=x>' not in text
    assert 'href="#quant-metrics"' in text
    assert "private-material" not in text
    assert "结论" in text and "下一步" in text


def test_new_offline_summary_and_existing_bytes_are_immutable(tmp_path):
    result = result_with_claim()
    writer = ReportBundleWriter()
    writer.write(result, tmp_path, quant_analytics={"metrics": {"total_return": 0}, "data_mode": "SAMPLE"})
    path = tmp_path / result.state.run_id / "complete_report.html"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    assert 'data-research-explanation' in path.read_text()
    assert 'href="#quant-metrics"' in path.read_text()
    writer.write(result, tmp_path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_reopen_preserves_explanation_evidence_and_cancellation(service):
    created = service.submit(payload(service))
    assert created["explanation"]["outcome"] == "NOT_READY"
    service.start()
    done = wait_for(service, created["job_id"])
    assert done["explanation"]["outcome"] == "AVAILABLE"
    report = service.runs.get_run(done["run_id"])["bundle"] / "complete_report.html"
    digest = hashlib.sha256(report.read_bytes()).hexdigest()
    service.close()
    reopened = ResearchApplicationService(service.root, scope_id=service.scope_id)
    try:
        again = reopened.get_job(done["job_id"])
        assert again["explanation"] == done["explanation"]
        assert hashlib.sha256(report.read_bytes()).hexdigest() == digest
        pending = reopened.submit(payload(reopened, key="cancel-me"))
        cancelled = reopened.cancel(pending["job_id"])
        assert cancelled["explanation"]["outcome"] == "NOT_READY"
        assert cancelled["snapshot_ref"] == done["snapshot_ref"]
        assert reopened.list_jobs()["total"] == 2
        assert "checkpoint:" not in str(cancelled["explanation"])
    finally:
        reopened.close()


@pytest.mark.parametrize("study, anchor", [({"study_id": "s1"}, "factor-study"), ({"validation_run": {}}, "identity")])
def test_factor_evidence_uses_the_actual_report_template_anchor(study, anchor):
    summary = explain(job(status="completed", report_status="AVAILABLE", factor_study=study))
    assert summary["evidence"] == [{"label": "冻结章程与因子验证报告", "href": f"/research/research-one/report/complete#{anchor}"}]


def test_started_analyst_checkpoint_is_not_a_claim_of_finished_analysis():
    summary = explain(job(status="cancelled", stages=[{"name": "ANALYSTS_RUNNING", "status": "COMPLETE"}]))
    assert "完成" not in summary["timeline"][0]["fact"]
    assert "标记" in summary["timeline"][0]["fact"]

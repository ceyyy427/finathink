"""Queue/spawn acceptance. Only the configured provider's DNS and TLS peer are controlled."""
from __future__ import annotations

import io
import json
import os
import sqlite3
import time
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from finahinking.research.application_service import (
    ResearchApplicationService,
    verify_research_receipt,
)
from finahinking.research.contracts import stable_digest
from finahinking.research.model_workbench import ModelWorkbench
from finahinking.research.runtime_service import ResearchRuntimeService
from tests.research.test_application_service import wait_for
from tests.research.test_model_workbench import approval, workbench

__all__ = ["workbench"]


def copy_journal(source, destination):
    with closing(sqlite3.connect(source)) as original, closing(sqlite3.connect(destination)) as restored:
        original.backup(restored)
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def run_controlled_stage(invocation, checkpoint_ref, publish_checkpoint):
    from finahinking.research.application_service import run_local_research_stage
    from finahinking.research.provider_transport import ProviderHttpTransport

    root = Path(os.environ["FINATHINK_QUEUE_PATH"]).parent
    fault = (root / "controlled-fault").read_text()
    if fault == "preflight-backup":
        from finahinking.research.model_call_journal import ModelCallJournal

        begin_call = ModelCallJournal.begin

        def snapshot_prepared(journal, record):
            call_id = begin_call(journal, record)
            copy_journal(journal.path, root / "earlier.sqlite3")
            return call_id

        ModelCallJournal.begin = snapshot_prepared
        os.environ.pop("FINAHINK_TEST_KEY")
    if fault in {"timeout-before-dispatch-backup", "timeout-after-analysts", "hold-before-dispatch-backup", "hold-after-analysts"}:
        from finahinking.research.model_call_journal import ModelCallJournal

        if fault in {"timeout-before-dispatch-backup", "hold-before-dispatch-backup"}:
            original_dispatch = ModelCallJournal.dispatched

            def snapshot_before_dispatch(journal, call_id, request_bytes):
                copy_journal(journal.path, root / "earlier.sqlite3")
                return original_dispatch(journal, call_id, request_bytes)

            ModelCallJournal.dispatched = snapshot_before_dispatch
        else:
            original_begin = ModelCallJournal.begin

            def snapshot_before_manager(journal, record):
                if record["role"] == "research":
                    copy_journal(journal.path, root / "earlier.sqlite3")
                return original_begin(journal, record)

            ModelCallJournal.begin = snapshot_before_manager

    class Connection:
        sock = None

        def request(self, method, path, body, headers):
            assert method == "POST" and path.endswith("/chat/completions")
            assert headers["Authorization"] == "Bearer only-in-controlled-test"
            self.wire = json.loads(body)
            self.context = json.loads(self.wire["messages"][1]["content"])
            (root / f"wire-{self.context['role']}.json").write_bytes(body)

        def getresponse(self):
            if fault in {"timeout", "timeout-before-dispatch-backup"} or (
                    fault == "timeout-after-analysts" and self.context["role"] == "research"):
                raise TimeoutError()
            if fault in {"hold", "hold-before-dispatch-backup"} or (
                    fault == "hold-after-analysts" and self.context["role"] == "research"):
                time.sleep(30)
            source = self.context["materials"][0]
            if isinstance(source["summary"], str):
                claim = source["summary"]
            else:
                row = source["summary"]["observations"][-1]
                close = row["close"] if fault != "wrong-value" else -999
                claim = f"{row['timestamp'][:10]} close = {close}"
            if fault == "generic":
                claim = "Review this observation."
            supplied = [source["evidence_ref"]] if fault != "other-run" else ["evidence:" + "0" * 64]
            if fault == "mixed-references":
                claim = next(item["summary"] for item in self.context["materials"] if isinstance(item["summary"], str))
                supplied = [item["evidence_ref"] for item in self.context["materials"]]
            content = {"status": "READY", "claims": [claim], "evidence_refs": supplied,
                       "limitations": ["Controlled protocol exercise."]}
            if self.context["role"] == "research":
                content.update(hypotheses=["Test again on a separate historical period."], disagreements=[])
            usage = {"prompt_tokens": 40, "completion_tokens": 8, "total_tokens": 48}
            if fault == "partial-usage":
                usage = {"prompt_tokens": 40}
            response = io.BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(content)}, "finish_reason": "stop"}],
                                               "usage": usage}).encode())
            response.status, response.headers = 200, {}
            return response

        def close(self):
            pass

    ProviderHttpTransport._resolve = lambda self, host: ("127.0.0.1" if fault == "dns" else "1.1.1.1",)
    ProviderHttpTransport._connect = lambda self, host, address, timeout: Connection()
    return run_local_research_stage(invocation, checkpoint_ref, publish_checkpoint)


def launch(workbench, adapter_kind, fault="", **draft):
    app = workbench.service
    if adapter_kind == "deepseek_compatible":
        old = app.provider_configs.get("desk")
        app.provider_configs.remove("desk")
        app.provider_configs.save(replace(old, adapter_kind=adapter_kind, model="deepseek-chat",
                                          role_models={"technical": "deepseek-reasoner"}))
    (app.root / "controlled-fault").write_text(fault)
    app.runtime = ResearchRuntimeService(app.queue, stage_runners={"local_research": run_controlled_stage}, autostart=False)
    values = {"snapshot_ref": "sample", "question": "Record one observable fact.", "model_config_ref": "desk", **draft}
    preview = workbench.preview(values)
    assert preview["status"] == "READY", preview
    job_id = workbench.authorize(approval(preview))["job_id"]
    app.start()
    return preview, job_id


@pytest.mark.parametrize(("kind", "output_limit"), [
    ("openai_compatible", "max_completion_tokens"), ("deepseek_compatible", "max_tokens"),
])
def test_real_worker_matches_preview_wire_and_published_claims_survive_restart(workbench, kind, output_limit):
    app = workbench.service
    material = {"role": "news", "instrument": "SYNTH", "summary": "Synthetic company delayed its launch.",
                "source_label": "Controlled case", "observed_at": "2026-05-01T10:00:00Z",
                "available_at": "2026-05-01T14:00:00Z", "authorized": True}
    preview, job_id = launch(workbench, kind, materials=[material])
    result = wait_for(app, job_id)
    assert result["status"] == "completed", result
    evidence = workbench.evidence(job_id)
    assert evidence["evidence_issues"] == [], evidence
    assert evidence["protocol_status"] == "ACCEPTED" and evidence["semantic_status"] == "SOURCE_MATCHED", evidence
    assert evidence["dispatch_status"] == "SENT" and evidence["usage"]["total_units"] == 144
    assert evidence["report_url"] and evidence["semantic_summary"]["matched_claims"] == 3
    publication = app.runs.get_run(evidence["run_id"])
    assert publication["manifest"]["source_snapshot"]["model_workbench"]["usage"]["total_units"] == 144
    html = (publication["bundle"] / "complete_report.html").read_text()
    assert "模型调用与来源核对" in html and "未调用" in html and "字面事实匹配" in html
    for role in ("technical", "news", "research"):
        frozen = next(r for r in preview["roles"] if r["role"] == role)
        receipt = next(c for c in evidence["calls"] if c["role"] == role)
        wire = json.loads((app.root / f"wire-{role}.json").read_text())
        context = json.loads(wire["messages"][1]["content"])
        assert wire[output_limit] == frozen["output_limit"][output_limit] == 1024
        assert wire["model"] == frozen["model"] == receipt["model"]
        assert wire["messages"][0]["content"] == frozen["system_instructions"]
        assert receipt["context_fingerprint"] == stable_digest(context)
        if role == "research":
            assert len(context["observations"]) == 5
            assert {**context, "observations": []} == frozen["context"]
        else:
            assert context == frozen["context"] and receipt["context_fingerprint"] == frozen["context_fingerprint"]
    assert not (app.root / "wire-learning.json").exists()
    assert next(r for r in evidence["roles"] if r["role"] == "learning")["material_status"] == "INSUFFICIENT_DATA"
    restarted = ResearchApplicationService(app.root)
    try:
        history = ModelWorkbench(restarted)
        assert history.evidence(job_id) == evidence
        assert history.authorize(approval(preview))["job_id"] == job_id
        assert history.settings()["history"][0]["job_id"] == job_id
    finally:
        restarted.close()
    assert "only-in-controlled-test" not in str(evidence) and "models.acme.com" not in str(evidence)


@pytest.mark.parametrize("kind", ["openai_compatible", "deepseek_compatible"])
@pytest.mark.parametrize(("fault", "dispatch", "usage", "protocol", "semantic", "outcome"), [
    ("timeout", "SENT", "UNKNOWN", "REJECTED", "NO_EVIDENCE", "failed"),
    ("dns", "NOT_SENT", "NOT_CALLED", "NOT_CALLED", "NO_EVIDENCE", "failed"),
    ("other-run", "SENT", "RECORDED", "REJECTED", "NO_EVIDENCE", "failed"),
    ("partial-usage", "SENT", "UNKNOWN", "ACCEPTED", "SOURCE_MATCHED", "completed"),
    ("wrong-value", "SENT", "RECORDED", "ACCEPTED", "REVIEW_REQUIRED", "completed"),
    ("generic", "SENT", "RECORDED", "ACCEPTED", "REVIEW_REQUIRED", "completed"),
])
def test_compatible_facts_from_actual_runtime(workbench, kind, fault, dispatch, usage, protocol, semantic, outcome):
    preview, job_id = launch(workbench, kind, fault)
    result = wait_for(workbench.service, job_id)
    evidence = workbench.evidence(job_id)
    assert (result["status"], evidence["dispatch_status"], evidence["usage_status"],
            evidence["protocol_status"], evidence["semantic_status"]) == (outcome, dispatch, usage, protocol, semantic), evidence
    assert evidence["attempts"] == 1 and workbench.authorize(approval(preview))["job_id"] == job_id
    if usage == "UNKNOWN":
        assert evidence["usage"]["total_units"] is None
        if outcome == "completed":
            html = (workbench.service.runs.get_run(evidence["run_id"])["bundle"] / "complete_report.html").read_text()
            assert "服务端报告的总用量为 未知" in html
    if fault == "wrong-value":
        assert evidence["roles"][1]["semantic_checks"][0]["status"] == "SOURCE_MISMATCH"
    if outcome == "failed":
        assert not evidence["report_url"] and len(evidence["calls"]) == 1


@pytest.mark.parametrize("kind", ["openai_compatible", "deepseek_compatible"])
def test_cancel_inflight_unknown_usage_and_foreign_late_publication_cannot_fill_it(workbench, kind):
    app = workbench.service
    first, first_id = launch(workbench, kind)
    assert wait_for(app, first_id)["status"] == "completed"
    (app.root / "wire-technical.json").unlink()
    (app.root / "controlled-fault").write_text("hold")
    second = workbench.preview({"snapshot_ref": "sample", "question": "Changed inputs.", "model_config_ref": "desk"})
    second_id = workbench.authorize(approval(second))["job_id"]
    assert second["preview_digest"] != first["preview_digest"]
    deadline = time.monotonic() + 20
    while not (app.root / "wire-technical.json").exists() and time.monotonic() < deadline:
        time.sleep(.05)
    assert (app.root / "wire-technical.json").exists()
    # Even while the new attempt is running, a valid earlier result cannot be attached to it.
    old_ref = app.queue.get(first_id).result_ref
    with pytest.raises(ValueError):
        verify_research_receipt(app.root, app.queue, app.queue.get(second_id), old_ref)
    app.cancel(second_id)
    assert wait_for(app, second_id)["status"] == "cancelled"
    evidence = workbench.evidence(second_id)
    assert (evidence["dispatch_status"], evidence["usage_status"], evidence["protocol_status"], evidence["semantic_status"]) == (
        "SENT", "UNKNOWN", "UNKNOWN", "NO_EVIDENCE")
    assert evidence["calls"][0]["dispatched"] and evidence["calls"][0]["usage"] is None
    assert evidence["report_url"] is None and workbench.authorize(approval(second))["status"] == "cancelled"
    with pytest.raises(ValueError):
        verify_research_receipt(app.root, app.queue, app.queue.get(second_id), old_ref)


def test_conflicting_call_receipt_suppresses_research_evidence_and_report(workbench):
    import sqlite3

    app = workbench.service
    _, job_id = launch(workbench, "openai_compatible")
    assert wait_for(app, job_id)["status"] == "completed"
    assert workbench.evidence(job_id)["semantic_status"] == "SOURCE_MATCHED"
    with sqlite3.connect(app.root / "model-calls.sqlite3") as db:
        db.execute("UPDATE model_calls SET context_fingerprint=? WHERE role='technical'", ("0" * 64,))
    evidence = workbench.evidence(job_id)
    assert evidence["protocol_status"] == evidence["semantic_status"] == "CONFLICT"
    assert not evidence["report_url"] and "CALL_JOURNAL_CONFLICT" in evidence["evidence_issues"]
    assert all(not role["claims"] for role in evidence["roles"])


def test_changed_preview_receipt_blocks_worker_before_any_wire_request(workbench):
    app = workbench.service
    preview = workbench.preview({"snapshot_ref": "sample", "model_config_ref": "desk", "question": "Inspect the last close."})
    job_id = workbench.authorize(approval(preview))["job_id"]
    (app.root / "model-previews" / (preview["preview_ref"] + ".json")).write_text("{}")
    (app.root / "controlled-fault").write_text("")
    app.runtime = ResearchRuntimeService(app.queue, stage_runners={"local_research": run_controlled_stage}, autostart=False)
    app.start()
    assert wait_for(app, job_id)["failure_kind"] == "CONTEXT_INTEGRITY_FAILED"
    evidence = workbench.evidence(job_id)
    assert not list(app.root.glob("wire-*.json")) and evidence["semantic_status"] == "NO_EVIDENCE"
    assert "FROZEN_INPUT_INVALID" in evidence["evidence_issues"]


def test_corrupt_usage_journal_fails_closed_without_suppressing_queue_history(workbench):
    import sqlite3

    app = workbench.service
    _, job_id = launch(workbench, "openai_compatible")
    assert wait_for(app, job_id)["status"] == "completed"
    with sqlite3.connect(app.root / "model-calls.sqlite3") as db:
        db.execute("UPDATE model_calls SET usage=? WHERE role='technical'", ('{"unapproved":"untrusted-content"}',))
    evidence = workbench.evidence(job_id)
    assert evidence["semantic_status"] in {"NO_EVIDENCE", "CONFLICT"}
    assert evidence["dispatch_status"] == evidence["usage_status"] == "UNKNOWN"
    assert evidence["usage"]["total_units"] is None and "untrusted-content" not in str(evidence)
    assert workbench.settings()["history"][0]["job_id"] == job_id


@pytest.mark.parametrize("provider", ["offline", "desk"])
def test_preexisting_discovery_publication_reopens_with_its_frozen_knowledge(workbench, provider):
    app = workbench.service
    (app.root / "controlled-fault").write_text("")
    app.runtime = ResearchRuntimeService(app.queue, stage_runners={"local_research": run_controlled_stage}, autostart=False)
    sample = app.create_strategy_sample()
    values = {"contract": sample["contract"], "attachment_refs": sample["attachment_refs"],
              "question": "等权组合能降低集中吗？", "model_config_ref": provider}
    blueprint = app.preview_discovery(values)
    job = app.run_discovery({**values, "idempotency_key": "prior-discovery", "authorized": True,
                             "allow_model_calls": provider != "offline", "blueprint_fingerprint": blueprint["fingerprint"]})["job"]
    app.start()
    result = wait_for(app, job["job_id"])
    assert result["status"] == "completed", result
    evidence = workbench.evidence(job["job_id"])
    assert evidence["evidence_issues"] == [] and evidence["report_url"] == result["report_url"], evidence
    assert evidence["consent_status"] == "LEGACY_SUBMISSION"
    assert job["job_id"] in {row["job_id"] for row in workbench.settings()["history"]}
    if provider == "desk":
        assert evidence["protocol_status"] == "ACCEPTED" and evidence["semantic_status"] == "SOURCE_MATCHED"
        assert evidence["calls"][0]["context_fingerprint"] == next(
            r for r in evidence["roles"] if r["role"] == evidence["calls"][0]["role"])["context_fingerprint"]


@pytest.mark.parametrize(("field", "claim_value", "observed", "market_status", "claim_status"), [
    ("close", 99, 101, "SOURCE_MISMATCH", "REVIEW_REQUIRED"),
    ("volume", 200, 220, "SOURCE_MISMATCH", "REVIEW_REQUIRED"),
    ("close", 101, 101, "SOURCE_MATCHED", "SOURCE_MATCHED"),
])
def test_each_cited_summary_and_snapshot_fact_remains_visible(workbench, field, claim_value, observed, market_status, claim_status):
    from finahinking.p8_2.data_sources import FixtureMarketDataSource

    app = workbench.service
    snapshot = FixtureMarketDataSource(points=12).snapshot("MIX")
    last = replace(snapshot.observations[-1], open=100, high=102, low=98, close=101, volume=220, amount=22220)
    snapshot = replace(snapshot, observations=(*snapshot.observations[:-1], last))
    claim = f"{last.timestamp.date()} {field} = {claim_value}"
    summary = {"role": "technical", "instrument": "MIX", "summary": claim, "source_label": "Conflicting field note",
               "observed_at": last.timestamp.isoformat(), "available_at": last.available_at.isoformat(), "authorized": True}
    preview, job_id = launch(workbench, "openai_compatible", "mixed-references",
                             snapshot_ref=app.snapshots.save(snapshot), materials=[summary])
    assert wait_for(app, job_id)["status"] == "completed"
    frozen = next(v for v in preview["roles"] if v["role"] == "technical")["materials"]
    quote = next(v for v in frozen if isinstance(v["summary"], str))
    market = next(v for v in frozen if isinstance(v["summary"], dict))
    assert market["summary"]["observations"][-1][field] == observed and quote["summary"] == claim
    expected = {quote["evidence_ref"]: "SOURCE_MATCHED", market["evidence_ref"]: market_status}
    evidence = workbench.evidence(job_id)
    assert evidence["protocol_status"] == "ACCEPTED"
    assert evidence["semantic_status"] == claim_status
    assert evidence["semantic_summary"]["matched_claims"] == (0 if market_status == "SOURCE_MISMATCH" else 2)
    publication = app.runs.get_run(evidence["run_id"])
    for roles in (evidence["roles"], publication["manifest"]["source_snapshot"]["model_workbench"]["roles"]):
        for role in [r for r in roles if r["role"] in {"technical", "research"}]:
            assert role["semantic_status"] == claim_status
            check, = role["semantic_checks"]
            assert check["status"] == claim_status and set(check["evidence_refs"]) == set(expected)
            assert {c["evidence_ref"]: c["status"] for c in check["citation_checks"]} == expected
    html = (publication["bundle"] / "complete_report.html").read_text()
    assert '<summary>逐条引用核对</summary>' in html
    market_label = "与所引来源不符" if market_status == "SOURCE_MISMATCH" else "字面事实匹配"
    assert f'<code>{market["evidence_ref"]}</code></td><td>{market_label}</td>' in html
    assert f'<code>{quote["evidence_ref"]}</code></td><td>字面事实匹配</td>' in html


@pytest.mark.parametrize(("fault", "charged", "confirmed"), [
    ("timeout", 1, 0), ("timeout-before-dispatch-backup", 1, 0), ("timeout-after-analysts", 2, 1),
])
def test_older_valid_journal_cannot_turn_an_actual_timeout_into_no_dispatch(workbench, fault, charged, confirmed):
    from finahinking.research.model_call_journal import ModelCallJournal

    app = workbench.service
    path = app.root / "model-calls.sqlite3"
    backup = app.root / "earlier.sqlite3"
    if fault == "timeout":
        first = ModelCallJournal(path, job_id="other-job", run_id="other-run", request_digest="a" * 64,
                                 attempt=1, scope_id=app.scope_id)
        first.begin({"role": "technical", "model": "old-model", "context_bytes": 12, "context_fingerprint": "b" * 64})
        copy_journal(path, backup)
    _, job_id = launch(workbench, "openai_compatible", fault)
    assert wait_for(app, job_id)["status"] == "failed"
    app.close()
    original = workbench.evidence(job_id)
    assert original["dispatched_calls"] == charged and original["calls"][-1]["status"] == "TIMEOUT"
    copy_journal(backup, path)
    reopened = ResearchApplicationService(app.root)
    try:
        job = reopened.get_job(job_id)
        study = job["model_research"]
        evidence = ModelWorkbench(reopened).evidence(job_id)
        assert study["aggregate_counts"]["provider_calls"] == charged
        assert study["journal_status"] == "INCOMPLETE"
        for accounting in (study, evidence):
            assert accounting["dispatch_status"] == accounting["usage_status"] == "UNKNOWN"
            assert accounting["dispatched_calls"] is None and accounting["known_dispatched_calls"] == confirmed
            assert accounting["usage"]["total_units"] is None and accounting["usage"]["known_total_units"] == confirmed * 48
        assert evidence["protocol_status"] == "UNKNOWN" and evidence["semantic_status"] == "NO_EVIDENCE"
        assert "CALL_JOURNAL_INCOMPLETE" in evidence["evidence_issues"]
        by_role = {r["role"]: r for r in evidence["roles"]}
        missing = "research" if confirmed else "technical"
        assert by_role[missing]["dispatch_status"] == by_role[missing]["usage_status"] == "UNKNOWN"
        assert by_role["fundamentals"]["dispatch_status"] == "NOT_SENT"
        if confirmed:
            assert by_role["technical"]["dispatch_status"] == "SENT"
            assert by_role["technical"]["usage_status"] == "RECORDED"
        if fault == "timeout-before-dispatch-backup":
            assert study["calls"][0]["dispatched"] is None and study["calls"][0]["status"] == "UNKNOWN"
    finally:
        reopened.close()


def test_reconciled_dns_failure_preserves_proven_zero_after_a_budget_charge(workbench):
    _, job_id = launch(workbench, "openai_compatible", "dns")
    study = wait_for(workbench.service, job_id)["model_research"]
    assert study["aggregate_counts"]["provider_calls"] == 1
    assert study["calls"][0]["status"] == "TRANSPORT" and study["calls"][0]["dispatched"] is False
    assert study["journal_status"] == "AVAILABLE" and study["dispatch_status"] == "NOT_SENT"
    assert study["dispatched_calls"] == study["known_dispatched_calls"] == study["usage"]["total_units"] == 0
    evidence = workbench.evidence(job_id)
    assert evidence["dispatch_status"] == "NOT_SENT" and evidence["usage_status"] == "NOT_CALLED"


def test_a_returned_workers_zero_budget_confirms_a_retained_prepared_marker(workbench):
    app = workbench.service
    _, job_id = launch(workbench, "openai_compatible", "preflight-backup")
    job = wait_for(app, job_id)
    assert job["status"] == "failed" and job["runtime_metrics"]["provider_calls"] == 0
    assert "local_research" in job["runtime_metrics"]["stage_durations"]
    assert job["model_research"]["calls"][0]["status"] == "NOT_CONFIGURED"
    app.close()
    copy_journal(app.root / "earlier.sqlite3", app.root / "model-calls.sqlite3")
    evidence = workbench.evidence(job_id)
    assert evidence["dispatch_status"] == "NOT_SENT" and evidence["usage_status"] == "NOT_CALLED"
    assert evidence["calls"][0]["status"] == "NOT_SENT" and evidence["calls"][0]["dispatched"] is False
    assert not list(app.root.glob("wire-*.json"))


@pytest.mark.parametrize(("fault", "role", "confirmed"), [
    ("hold-before-dispatch-backup", "technical", 0), ("hold-after-analysts", "research", 1),
])
def test_cancelled_workers_default_zero_does_not_confirm_restored_receipts(workbench, fault, role, confirmed):
    app = workbench.service
    _, job_id = launch(workbench, "openai_compatible", fault)
    wire = app.root / f"wire-{role}.json"
    deadline = time.monotonic() + 20
    while not wire.exists() and time.monotonic() < deadline:
        time.sleep(.05)
    assert wire.exists()
    app.cancel(job_id)
    assert wait_for(app, job_id)["status"] == "cancelled"
    app.close()
    before = workbench.evidence(job_id)
    assert before["dispatch_status"] == "SENT" and before["usage_status"] == "UNKNOWN"
    assert any(c["role"] == role and c["dispatched"] is True for c in before["calls"])
    copy_journal(app.root / "earlier.sqlite3", app.root / "model-calls.sqlite3")
    job = app.get_job(job_id)
    assert job["attempts"] == 1 and job["runtime_metrics"]["provider_calls"] == 0
    assert not job["runtime_metrics"]["stage_durations"]
    assert job["model_research"]["usage_status"] == "UNKNOWN"
    after = workbench.evidence(job_id)
    assert after["dispatched_calls"] is None and after["known_dispatched_calls"] == confirmed
    assert after["usage"]["known_total_units"] == confirmed * 48 and after["usage"]["total_units"] is None
    assert next(r for r in after["roles"] if r["role"] == role)["dispatch_status"] == "UNKNOWN"
    if not confirmed:
        assert after["dispatch_status"] == "UNKNOWN" and after["calls"][0]["dispatched"] is None

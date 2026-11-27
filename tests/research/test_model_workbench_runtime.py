"""Queue/spawn acceptance. Only the configured provider's DNS and TLS peer are controlled."""
from __future__ import annotations

import io
import json
import os
import time
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


def run_controlled_stage(invocation, checkpoint_ref, publish_checkpoint):
    from finahinking.research.application_service import run_local_research_stage
    from finahinking.research.provider_transport import ProviderHttpTransport

    root = Path(os.environ["FINATHINK_QUEUE_PATH"]).parent
    fault = (root / "controlled-fault").read_text()

    class Connection:
        sock = None

        def request(self, method, path, body, headers):
            assert method == "POST" and path.endswith("/chat/completions")
            assert headers["Authorization"] == "Bearer only-in-controlled-test"
            self.wire = json.loads(body)
            self.context = json.loads(self.wire["messages"][1]["content"])
            (root / f"wire-{self.context['role']}.json").write_bytes(body)

        def getresponse(self):
            if fault == "timeout":
                raise TimeoutError()
            if fault == "hold":
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

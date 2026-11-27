"""Single-use dispatch, evidence review and restart through a scoped research job."""

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest

from finahinking.research.application_service import ResearchApplicationService
from finahinking.research.asset_contracts import MethodRouteRequest
from finahinking.research.codex_workbench import CodexWorkbench, CodexWorkbenchError
from finahinking.research.contracts import stable_digest
from finahinking.research.method_handoff import MethodHandoffPackage
from tests.research.test_application_service import payload
from tests.research.test_method_handoff import artifact_for, response_for


@pytest.fixture
def app(tmp_path):
    service = ResearchApplicationService(tmp_path, scope_id="owner", allow_model_calls=True)
    yield service
    service.close()


def handoff(app, job=None, purpose="factor-mining", **options):
    if job is None:
        job = app.submit(payload(app, **options))["job_id"]
    ref = app.export_method_handoff(job, MethodRouteRequest(purposes=(purpose,)))["handoff_ref"]
    return job, ref


def approval(preview, **limits):
    return {"authorized": True, "handoff_ref": preview["handoff_ref"],
            "package_digest": preview["package_digest"], "limits": {**preview["limits"], **limits}}


def dispatched(app, **limits):
    desk = CodexWorkbench(app)
    job, ref = handoff(app)
    preview = desk.preview(job, ref)
    op = desk.authorize(job, approval(preview, **limits))["operation_id"]
    return desk, job, ref, op, desk.claim(job, op, {})


def result(packet, *, output=True):
    package = MethodHandoffPackage.from_dict(packet["package"])
    answer = response_for(package)
    if output:
        answer["output_artifacts"] = [artifact_for(package)]
    return {"lease_token": packet["lease_token"], "response": answer}


def test_scoped_single_use_dispatch_and_evidence_survive_restart(app):
    desk, job, ref, op, packet = dispatched(app)
    assert packet["package"]["envelope"]["tool_names"] == [] and packet["package"]["envelope"]["paper_only"]
    assert packet["limits"]["max_provider_calls"] == 1
    assert packet["usage"]["usage_state"] == "UNKNOWN"
    assert packet["lease_token"] not in json.dumps(desk.list(job))
    assert packet["lease_token"] not in json.dumps(desk.status(job, op))
    with app.queue._connect() as db:
        assert packet["lease_token"] not in repr(tuple(db.execute("SELECT * FROM codex_handoffs").fetchone()))
    with pytest.raises(CodexWorkbenchError, match="领取"):
        desk.claim(job, op, {})
    answer = result(packet)
    assert app.accept_method_handoff(job, ref, answer["response"])["outcome"]["failure_kind"] == "EXECUTOR_OWNS_HANDOFF"
    status = desk.complete(job, op, answer)
    assert status["status"] == "COMPLETED" and status["protocol_status"] == "READY"
    assert status["semantic_status"] == "TYPED_EVIDENCE_VERIFIED"
    assert status["evidence_refs"] == answer["response"]["result"]["evidence_refs"]
    assert status["artifact_types"] == ["ResearchPlan"]
    assert status["usage"] == {"provider_calls": 1, "bytes_used": packet["packet_bytes"] + len(
        json.dumps(answer["response"], ensure_ascii=False, separators=(",", ":")).encode()), "usage_state": "UNKNOWN"}
    reopened = ResearchApplicationService(app.root, scope_id="owner")
    other = ResearchApplicationService(app.root, scope_id="another")
    try:
        restored = CodexWorkbench(reopened)
        assert restored.list(job)["handoffs"] == [status]
        assert restored.complete(job, op, answer) == status
        answer["response"]["output_artifacts"] = []
        with pytest.raises(CodexWorkbenchError) as conflict:
            restored.complete(job, op, answer)
        assert conflict.value.status_code == 409 and conflict.value.code == "RESULT_CONFLICT"
        assert reopened.method_handoff_artifacts(job, ref)[0]["artifact_ref"] == status["artifact_refs"][0]
        foreign = CodexWorkbench(other)
        for action in (lambda: foreign.list(job), lambda: foreign.status(job, op),
                       lambda: foreign.preview(job, ref), lambda: foreign.cancel(job, op, {}),
                       lambda: foreign.complete(job, op, answer)):
            with pytest.raises(KeyError):
                action()
        other_job = reopened.submit(payload(reopened, key="different-parent"))["job_id"]
        with pytest.raises(KeyError):
            restored.preview(other_job, ref)
        with pytest.raises(KeyError):
            restored.status(other_job, op)
    finally:
        reopened.close()
        other.close()


@pytest.mark.parametrize("change", ["no-typed-output", "no-evidence", "empty-plan", "factor-proposal"])
def test_semantic_review_is_separate_from_protocol(app, change):
    desk, job, _, op, packet = dispatched(app)
    answer = result(packet, output=change != "no-typed-output")
    if change == "no-evidence":
        answer["response"]["result"]["evidence_refs"] = []
    if change == "empty-plan":
        artifact = answer["response"]["output_artifacts"][0]
        artifact["payload"]["hypotheses"] = []
        artifact["content_digest"] = stable_digest(artifact["payload"])
    if change == "factor-proposal":
        answer["response"]["output_artifacts"] = [artifact_for(MethodHandoffPackage.from_dict(packet["package"]), "FactorProposal")]
    accepted = desk.complete(job, op, answer)
    assert accepted["protocol_status"] == "READY"
    assert accepted["semantic_status"] == ("TYPED_EVIDENCE_VERIFIED" if change == "factor-proposal" else "INSUFFICIENT_EVIDENCE")


@pytest.mark.parametrize("change", ["wrong-input", "foreign-evidence", "non-paper", "byte-limit", "wrong-package", "null"])
def test_invalid_worker_result_is_terminal_without_manual_publication(app, change):
    desk = CodexWorkbench(app)
    job, ref = handoff(app)
    preview = desk.preview(job, ref)
    limits = {"max_bytes": preview["packet_bytes"] + 8} if change == "byte-limit" else {}
    op = desk.authorize(job, approval(preview, **limits))["operation_id"]
    packet = desk.claim(job, op, {})
    answer = result(packet)
    if change == "wrong-input":
        answer["response"]["output_artifacts"][0]["input_digest"] = "different"
    if change == "foreign-evidence":
        answer["response"]["result"]["evidence_refs"] = ["artifact:unrelated"]
    if change == "non-paper":
        answer["response"]["result"]["paper_only"] = False
    if change == "wrong-package":
        answer["response"]["package_digest"] = "0" * 64
    if change == "null":
        answer["response"] = None
    with pytest.raises(CodexWorkbenchError) as unauthorized:
        desk.complete(job, op, {**answer, "lease_token": "0" * 64})
    assert unauthorized.value.status_code == 403 and desk.status(job, op)["status"] == "DISPATCHED"
    invalid = desk.complete(job, op, answer)
    assert invalid["status"] == invalid["protocol_status"] == "REJECTED"
    assert invalid["semantic_status"] == "NOT_EVALUATED" and invalid["failure_kind"]
    assert invalid["usage"]["bytes_used"] <= packet["limits"]["max_bytes"]
    assert desk.complete(job, op, answer) == invalid
    assert app.method_handoff_artifacts(job, ref) == ()
    assert not list((app._method_store().root / "worker-returns").glob("*.json"))


def test_manual_legacy_index_and_offline_mode(app):
    desk = CodexWorkbench(app)
    job, ref = handoff(app)
    # Phase 12 handoffs had the file and external row, but no parent column.
    with app.queue._connect() as db:
        db.execute("DELETE FROM codex_handoffs")
    app.allow_model_calls = False
    assert desk.list(job)["handoffs"][0]["handoff_ref"] == ref
    preview = desk.preview(job, ref)
    assert not preview["eligible"] and preview["reason_code"] == "OFFLINE_MODE"
    with pytest.raises(CodexWorkbenchError) as offline:
        desk.authorize(job, approval(preview))
    assert offline.value.status_code == 403
    package = MethodHandoffPackage.from_dict(preview["package"])
    answer = response_for(package)
    answer["output_artifacts"] = [artifact_for(package)]
    assert app.accept_method_handoff(job, ref, answer)["outcome"]["status"] == "READY"
    app.allow_model_calls = True
    manual = desk.list(job)["handoffs"][0]
    assert manual["status"] == "MANUAL" and manual["semantic_status"] == "TYPED_EVIDENCE_VERIFIED"
    with pytest.raises(CodexWorkbenchError) as published:
        desk.authorize(job, approval(preview))
    assert published.value.code == "MANUAL_RESULT_EXISTS"


@pytest.mark.parametrize("action", ["cancel-before", "cancel-after", "parent-cancel", "authorization-expiry",
                                    "lease-expiry", "snapshot-changed", "worker-timeout", "worker-failed"])
def test_terminal_or_expired_grant_cannot_reopen(app, action):
    desk = CodexWorkbench(app)
    job, ref = handoff(app)
    now = [app.queue._clock()]
    app.queue._clock = lambda: now[0]
    preview = desk.preview(job, ref)
    grant = approval(preview, max_wall_seconds=2)
    op = desk.authorize(job, grant)["operation_id"]
    assert desk.authorize(job, grant)["operation_id"] == op
    packet = None if action in {"cancel-before", "authorization-expiry"} else desk.claim(job, op, {})
    expected = {"parent-cancel": "CANCELLED", "worker-timeout": "TIMED_OUT", "worker-failed": "FAILED",
                "snapshot-changed": "STALE"}.get(action, "CANCELLED" if action.startswith("cancel") else "TIMED_OUT")
    if action.startswith("cancel"):
        assert desk.cancel(job, op, {}) == desk.cancel(job, op, {})
    elif action == "parent-cancel":
        app.cancel(job)
    elif action.endswith("expiry"):
        now[0] = desk.status(job, op)["lease_until"]
    elif action == "snapshot-changed":
        app.snapshots._path(preview["package"]["context"]["dataset_fingerprint"]).write_text("{}")
    else:
        fail = {"lease_token": packet["lease_token"], "failure_kind": "EXECUTOR_TIMEOUT" if action == "worker-timeout" else "EXECUTOR_FAILED"}
        assert desk.fail(job, op, fail) == desk.fail(job, op, fail)
    closed = desk.status(job, op)
    assert closed["status"] == expected
    assert closed["usage"]["usage_state"] == ("NOT_SENT" if packet is None else "UNKNOWN")
    assert desk.authorize(job, grant)["operation_id"] == op
    with pytest.raises(CodexWorkbenchError):
        desk.claim(job, op, {})
    if packet:
        with pytest.raises(CodexWorkbenchError):
            desk.complete(job, op, result(packet))
    assert app._method_store().artifacts(ref) == ()


def test_durable_return_intent_finishes_after_crash_without_redispatch(app, monkeypatch):
    desk, job, ref, op, packet = dispatched(app)
    answer = result(packet)

    def interrupted(*args):
        raise RuntimeError("interrupted after recording the on-time return")

    monkeypatch.setattr(desk, "_publish", interrupted)
    with pytest.raises(RuntimeError, match="interrupted"):
        desk.complete(job, op, answer)
    assert app._method_store().artifacts(ref) == ()
    restarted = ResearchApplicationService(app.root, scope_id="owner", allow_model_calls=True)
    try:
        restarted.queue._clock = lambda: packet["lease_until"] + 600
        recovered = CodexWorkbench(restarted)
        status = recovered.cancel(job, op, {})
        assert status["status"] == "COMPLETED" and status["semantic_status"] == "TYPED_EVIDENCE_VERIFIED"
        assert recovered.complete(job, op, answer) == status
        with pytest.raises(CodexWorkbenchError):
            recovered.claim(job, op, {})
    finally:
        restarted.close()


def _race(*functions):
    barrier = Barrier(len(functions))

    def invoke(fn):
        barrier.wait(timeout=10)
        try:
            return fn()
        except CodexWorkbenchError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=len(functions)) as pool:
        return list(pool.map(invoke, functions))


def test_authorize_claim_and_cancel_race_across_connections(app):
    desks = [CodexWorkbench(app), CodexWorkbench(app)]
    job, ref = handoff(app)
    grant = approval(desks[0].preview(job, ref))
    ops = _race(*(lambda d=d: d.authorize(job, grant) for d in desks))
    assert ops[0]["operation_id"] == ops[1]["operation_id"]
    op = ops[0]["operation_id"]
    claims = _race(*(lambda d=d: d.claim(job, op, {}) for d in desks))
    assert "ALREADY_DISPATCHED" in claims
    packet = next(item for item in claims if isinstance(item, dict))
    answer = result(packet)
    results = _race(lambda: desks[0].complete(job, op, answer), lambda: desks[1].cancel(job, op, {}))
    status = desks[0].status(job, op)
    assert status["status"] in {"COMPLETED", "CANCELLED"}
    assert status["usage"]["provider_calls"] == 1
    if status["status"] == "COMPLETED":
        assert all(item == status for item in results)
        assert len(app.method_handoff_artifacts(job, ref)) == 1
    else:
        assert "OPERATION_CLOSED" in results and not app.method_handoff_artifacts(job, ref)


def test_manual_callback_and_explicit_authorization_race(app):
    desk = CodexWorkbench(app)
    job, ref = handoff(app)
    preview = desk.preview(job, ref)
    grant = approval(preview)
    response = response_for(MethodHandoffPackage.from_dict(preview["package"]))
    decisions = _race(lambda: desk.authorize(job, grant),
                      lambda: app.accept_method_handoff(job, ref, response)["outcome"]["status"])
    row = desk.list(job)["handoffs"][0]
    if row["operation_id"]:
        assert "REJECTED" in decisions and row["status"] == "AUTHORIZED"
        assert app._method_store()._receipt(ref) is None
    else:
        assert set(decisions) == {"READY", "MANUAL_RESULT_EXISTS"} and row["status"] == "MANUAL"


def test_shared_budget_across_handoffs_and_local_worker(app):
    desk = CodexWorkbench(app)
    job, first = handoff(app)
    _, second = handoff(app, job, "backtest")
    assert first != second
    preview = desk.preview(job, first)
    with pytest.raises(CodexWorkbenchError):
        desk.authorize(job, {**approval(preview), "package_digest": "0" * 64})
    with pytest.raises(CodexWorkbenchError):
        desk.authorize(job, {**approval(preview), "authorized": False})
    with pytest.raises(CodexWorkbenchError):
        desk.authorize(job, approval(preview, max_bytes=preview["limits"]["max_bytes"] + 1))
    grants = _race(*(lambda p=p: desk.authorize(job, approval(p))
                     for p in (preview, desk.preview(job, second))))
    assert "BUDGET_EXHAUSTED" in grants
    op = next(item for item in grants if isinstance(item, dict))["operation_id"]
    invocation = app.runtime._supervisor._make_handle(app.queue.get(job)).invocation
    assert invocation.budget["max_provider_calls"] == 0
    assert invocation.budget["max_bytes"] < app.queue.get(job).runtime_budget["max_bytes"]
    assert invocation.timeout_seconds <= 60
    desk.cancel(job, op, {})
    remaining = second if desk.status(job, op)["handoff_ref"] == first else first
    assert desk.preview(job, remaining)["eligible"]
    app.queue.save_metrics(SimpleNamespace(run_id=job, provider_calls=1, bytes_used=0))
    assert desk.preview(job, remaining)["reason_code"] == "BUDGET_EXHAUSTED"


def test_offline_restart_cannot_disclose_claim_and_changed_approvals_conflict(app):
    desk = CodexWorkbench(app)
    job, ref = handoff(app)
    grant = approval(desk.preview(job, ref))
    op = desk.authorize(job, grant)["operation_id"]
    with pytest.raises(CodexWorkbenchError) as rebound:
        desk.authorize(job, {**grant, "limits": {**grant["limits"], "max_bytes": grant["limits"]["max_bytes"] - 1}})
    assert rebound.value.code == "ALREADY_AUTHORIZED"
    offline = ResearchApplicationService(app.root, scope_id="owner", allow_model_calls=False)
    try:
        with pytest.raises(CodexWorkbenchError) as error:
            CodexWorkbench(offline).claim(job, op, {})
        assert error.value.status_code == 403
    finally:
        offline.close()
    assert desk.status(job, op)["usage"]["provider_calls"] == 0


def test_response_cannot_cross_package_or_modify_a_new_job(app):
    desk, job, _, op, packet = dispatched(app)
    new_job, new_ref = handoff(app, key="new-job")
    other_op = desk.authorize(new_job, approval(desk.preview(new_job, new_ref)))["operation_id"]
    other = desk.claim(new_job, other_op, {})
    with pytest.raises(KeyError):
        desk.complete(new_job, op, result(packet))
    with pytest.raises(CodexWorkbenchError):
        desk.complete(new_job, other_op, result(packet))
    old_response = copy.deepcopy(result(packet)["response"])
    rejected = desk.complete(new_job, other_op, {"lease_token": other["lease_token"], "response": old_response})
    assert rejected["status"] == "REJECTED" and rejected["failure_kind"] == "PACKAGE_DIGEST_MISMATCH"
    assert desk.complete(job, op, result(packet))["status"] == "COMPLETED"


def test_durable_budget_tightening_blocks_unspent_or_outstanding_grants(app):
    desk = CodexWorkbench(app)
    job, ref = handoff(app)
    grant = approval(desk.preview(job, ref))
    op = desk.authorize(job, grant)["operation_id"]
    app.queue.configure_limits(job, max_attempts=1, budget={"max_bytes": 1})
    assert desk.status(job, op)["failure_kind"] == "LIMITS_CHANGED"
    with pytest.raises(CodexWorkbenchError):
        desk.claim(job, op, {})
    assert desk.status(job, op)["usage"]["provider_calls"] == 0


def test_completed_parent_with_remaining_budget_can_authorize_later(app):
    from tests.research.test_application_service import wait_for

    job, ref = handoff(app)
    app.start()
    finished = wait_for(app, job)
    assert finished["status"] == "completed"
    record = app.queue.get(job)
    now = record.runtime_budget["deadline_at"] + 86400
    app.queue._clock = lambda: now
    desk = CodexWorkbench(app)
    preview = desk.preview(job, ref)
    assert preview["eligible"]
    assert MethodHandoffPackage(preview["packet_json"]).package_digest == preview["package_digest"]
    grant = desk.authorize(job, approval(preview))
    packet = desk.claim(job, grant["operation_id"], {})
    assert now < packet["lease_until"] <= now + grant["limits"]["max_wall_seconds"]
    assert desk.complete(job, grant["operation_id"], result(packet))["protocol_status"] == "READY"
    assert app.get_job(job)["report_url"] == finished["report_url"]


def test_result_publication_recovers_after_crash_between_receipt_and_queue(app, monkeypatch):
    desk, job, ref, op, packet = dispatched(app)
    real_record = app.queue.record_external_result
    answer = result(packet)

    def interrupted(*args, **kwargs):
        raise RuntimeError("after immutable receipt, before queue write")

    monkeypatch.setattr(app.queue, "record_external_result", interrupted)
    with pytest.raises(RuntimeError, match="immutable receipt"):
        desk.complete(job, op, answer)
    monkeypatch.setattr(app.queue, "record_external_result", real_record)
    # The ordinary legacy reader can still reconcile this exact authorized receipt.
    assert app.get_method_handoff(job, ref)["status"] == "READY"
    assert desk.cancel(job, op, {})["status"] == "COMPLETED"
    assert desk.complete(job, op, answer)["artifact_types"] == ["ResearchPlan"]


def test_manual_result_cannot_write_after_parent_is_cancelled(app):
    desk = CodexWorkbench(app)
    job, ref = handoff(app)
    package = MethodHandoffPackage.from_dict(desk.preview(job, ref)["package"])
    app.cancel(job)
    rejected = app.accept_method_handoff(job, ref, response_for(package))
    assert rejected["outcome"]["failure_kind"] == "PARENT_CANCELLED" and rejected["artifacts"] == []


def test_low_level_bridge_cannot_publish_past_a_worker_reservation(app):
    from finahinking.research.codex_bridge import CodexBridge

    desk, job, ref, op, packet = dispatched(app)
    package = MethodHandoffPackage.from_dict(packet["package"])
    bridge = CodexBridge(queue=app.queue)
    raw = result(packet)["response"]["result"]
    for _ in range(2):
        response = bridge.record_external_result(package.envelope, raw)
        assert response.status == "REJECTED" and response.failure_kind == "EXECUTOR_OWNS_HANDOFF"
    assert desk.status(job, op)["status"] == "DISPATCHED"
    assert app.method_handoff_artifacts(job, ref) == ()


def test_tightening_a_claimed_wall_budget_fences_late_result(app):
    now = [app.queue._clock()]
    app.queue._clock = lambda: now[0]
    desk, job, _, op, packet = dispatched(app, max_wall_seconds=10)
    now[0] += 2
    app.queue.configure_limits(job, max_attempts=1, budget={"max_wall_seconds": 1})
    with pytest.raises(CodexWorkbenchError):
        desk.complete(job, op, result(packet))
    status = desk.status(job, op)
    assert status["status"] == "TIMED_OUT" and status["lease_until"] == status["claimed_at"] + 1

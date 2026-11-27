"""Explicit, single-use worker authorization for a frozen method handoff."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
import sqlite3
from contextlib import contextmanager

from .contracts import stable_digest
from .method_handoff import (
    ProtectedFactorHandoff,
    _evidence_members,
    _json,
    _normalized_response,
    accept_method_result,
)

_ACTIVE = {"AUTHORIZED", "DISPATCHED"}
_MAX_RESULT_BYTES = 262144  # method-result.v1
_MAX_REVIEW_BYTES = 262144  # entire typed artifact array including its evidence and provenance
_AUTHORIZATION_SECONDS = 300
_FAILURES = {"EXECUTOR_FAILED": "FAILED", "EXECUTOR_TIMEOUT": "TIMED_OUT",
             "INVALID_EXECUTOR_RESPONSE": "REJECTED"}
_REASONS = {
    "INVALID_REQUEST": "请使用此操作规定的字段和数值。",
    "AUTHORIZATION_REQUIRED": "请先预览冻结材料与预算，再明确授权一次执行。",
    "OFFLINE_MODE": "当前服务仅限离线研究。启用外部模型调用后可授权 Codex 执行。",
    "RESEARCH_RUNNING": "本地研究正在使用任务预算。研究完成后可查看余量并授权。",
    "PARENT_CANCELLED": "所属研究任务已取消。",
    "PARENT_FAILED": "所属研究任务已失败，请查看任务记录。",
    "STALE_INPUTS": "冻结的任务、数据、方法或材料已改变。请重新预览并创建交接。",
    "BUDGET_EXHAUSTED": "研究任务剩余的调用次数、字节或运行时间不足。",
    "USAGE_UNAVAILABLE": "原研究尝试的资源记录缺失，无法确认可用额度。",
    "LIMITS_CHANGED": "请求的预算超过当前可用额度。请重新预览预算。",
    "MANUAL_RESULT_EXISTS": "此交接已接受手工回传，可查看原始证据。",
    "ALREADY_AUTHORIZED": "此交接已有执行授权，可查看原授权与状态。",
    "ALREADY_DISPATCHED": "执行器已领取此交接。请查看原领取记录与状态。",
    "AUTHORIZATION_EXPIRED": "授权已过期，执行器未领取这次交接。",
    "LEASE_EXPIRED": "执行器的回传期限已到，此交接不会重复派发。",
    "OPERATION_CLOSED": "此执行已结束，请查看已保存的状态。",
    "INVALID_LEASE": "执行凭据与此交接不匹配。",
    "RESULT_CONFLICT": "已保存的回传与本次内容不同。",
    "BYTE_LIMIT": "回传超出已授权的总字节数。",
    "CANCELLED_BY_USER": "用户已取消此次执行。",
    "RESULT_UNAVAILABLE": "执行收据或其冻结产物未通过完整性校验。",
    "FROZEN_TEST_WINDOW": "当前因子任务仍保护样本外窗口；完成冻结测试后才可外发。",
}


class CodexWorkbenchError(ValueError):
    def __init__(self, code, status_code=409):
        self.code = code
        self.status_code = status_code
        super().__init__(_REASONS[code])


def _fields(values, keys):
    if not isinstance(values, dict) or set(values) != set(keys):
        raise CodexWorkbenchError("INVALID_REQUEST", 422)


def _limits(values):
    _fields(values, ("max_provider_calls", "max_bytes", "max_wall_seconds"))
    seconds = values["max_wall_seconds"]
    if (type(values["max_provider_calls"]) is not int or values["max_provider_calls"] != 1
            or type(values["max_bytes"]) is not int or values["max_bytes"] <= 0
            or type(seconds) not in (int, float) or not 0 < seconds <= 300 or not math.isfinite(seconds)):
        raise CodexWorkbenchError("INVALID_REQUEST", 422)
    return values


def _lease_digest(lease):
    if not isinstance(lease, str) or len(lease) != 64:
        raise CodexWorkbenchError("INVALID_LEASE", 403)
    return hashlib.sha256(lease.encode()).hexdigest()


def _review(receipt, outputs, package):
    if receipt is None:
        return "NOT_EVALUATED", [], []
    result = receipt["outcome"]
    refs = result["evidence_refs"]
    allowed = set(package.context.evidence_index)
    usable = result["status"] in {"READY", "ANALYZED"} and bool(refs and outputs)
    artifacts = []
    for record, output in zip(receipt["artifacts"], outputs, strict=True):
        payload = output["payload"]
        cited = set()
        _evidence_members(payload, allowed, cited)
        if not cited.issubset(refs):
            usable = False
        if output["artifact_type"] == "ResearchPlan" and (not payload["hypotheses"] or not any(
                payload[key] for key in ("validation_spec", "required_datasets", "factor_ids"))):
            usable = False
        artifacts.append({**record, "schema_version": output["schema_version"], "payload": payload,
                          "evidence_refs": sorted(cited) if cited else refs,
                          "evidence_scope": "ARTIFACT" if cited else "RESULT"})
    return ("TYPED_EVIDENCE_VERIFIED" if usable else "INSUFFICIENT_EVIDENCE", refs, artifacts)


class CodexWorkbench:
    def __init__(self, service):
        self.service = service
        self.queue = service.queue
        self.store = service._method_store()

    def _job(self, job_id):
        return self.service._method_request(self.queue.get(job_id))

    @contextmanager
    def _write(self):
        with self.queue._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except CodexWorkbenchError:
                # A rejected late call may have observed the first durable
                # timeout, cancellation or stale-input transition.
                db.commit()
                raise

    @staticmethod
    def _row(db, job_id, operation_id):
        if not isinstance(operation_id, str) or not re.fullmatch(r"codex-op-[0-9a-f]{32}", operation_id):
            raise KeyError("执行引用无效")
        row = db.execute("SELECT * FROM codex_handoffs WHERE job_id=? AND operation_id=?",
                         (job_id, operation_id)).fetchone()
        if row is None:
            raise KeyError("该研究任务没有此执行记录")
        return row

    def _resolve(self, job_id, reference):
        store, package = self.service._bound_method_handoff(job_id, reference)
        store.bind_job(job_id, reference, package)
        return package

    def _current(self, row):
        reference = "method-handoff:" + row["envelope_digest"]
        try:
            _, package = self.service._bound_method_handoff(row["job_id"], reference, current=True)
            if package.package_digest != row["package_digest"]:
                raise ValueError("package digest")
            return package
        except (KeyError, ValueError, TypeError, OSError) as exc:
            raise CodexWorkbenchError("STALE_INPUTS") from exc

    def _backfill(self, job_id, request):
        # Older exports have a scoped private file and a digest-only external
        # row, but no parent id. Adopt only a verified matching frozen request.
        with self.queue._connect() as db:
            rows = db.execute("""SELECT e.envelope_digest FROM external_dispatches e
                                 LEFT JOIN codex_handoffs c ON e.envelope_digest=c.envelope_digest
                                 WHERE c.envelope_digest IS NULL AND e.task_id LIKE 'method-%'""").fetchall()
        for row in rows:
            digest = row["envelope_digest"]
            if self.store._path("method-handoffs", digest).exists():
                reference = "method-handoff:" + digest
                try:
                    package = self.store.load(reference)
                    if package.context.to_dict()["run_id"] == request.run_id:
                        self._resolve(job_id, reference)
                except (KeyError, ValueError, TypeError, OSError):
                    continue

    def list(self, job_id):
        try:
            request = self._job(job_id)
        except ProtectedFactorHandoff as exc:
            return {"job_id": job_id, "handoffs": [], "status": exc.code, "reason": str(exc)}
        self._backfill(job_id, request)
        with self.queue._connect() as db:
            rows = db.execute("""SELECT * FROM codex_handoffs WHERE job_id=?
                                 ORDER BY authorized_at DESC,envelope_digest""", (job_id,)).fetchall()
        return {"job_id": job_id, "handoffs": [
            self.status(job_id, row["operation_id"]) if row["operation_id"] else self._summary(row)
            for row in rows]}

    def _summary(self, row, *, include_artifacts=False):
        reference = "method-handoff:" + row["envelope_digest"]
        status = row["status"]
        failure = row["failure_kind"]
        receipt = package = None
        semantic, refs, artifacts = "NOT_EVALUATED", [], []
        if status in {"AWAITING_AUTHORIZATION", "COMPLETED"}:
            try:
                package = self.store.load(reference)
                receipt, outputs = self.store.verified_result(reference)
                if status == "COMPLETED" and (receipt is None or receipt["response_digest"] != row["callback_digest"]):
                    raise ValueError("worker receipt missing")
                semantic, refs, artifacts = _review(receipt, outputs, package)
            except (KeyError, ValueError, TypeError, OSError):
                receipt = None
                status, failure = "STALE", "RESULT_UNAVAILABLE"
        if receipt is not None and row["operation_id"] is None:
            status = "MANUAL"
        protocol = receipt["outcome"]["status"] if receipt is not None else "EXTERNAL_HANDOFF_REQUIRED"
        if receipt is not None:
            failure = receipt["outcome"]["failure_kind"]
            if failure and not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", failure):
                failure = "METHOD_REPORTED_FAILURE"
        if status == "REJECTED" and row["claimed_at"] is not None:
            protocol = "REJECTED"
        sent = row["claimed_at"] is not None
        summary = {"job_id": row["job_id"], "handoff_ref": reference, "package_digest": row["package_digest"],
                "status": status, "operation_id": row["operation_id"], "failure_kind": failure,
                "protocol_status": protocol, "semantic_status": semantic, "evidence_refs": refs,
                "artifact_types": [item["artifact_type"] for item in artifacts],
                "artifact_refs": [item["artifact_ref"] for item in artifacts], "paper_only": True,
                "packet_bytes": row["packet_bytes"], "limits": self._grant(row),
                "authorized_at": row["authorized_at"], "claimed_at": row["claimed_at"],
                "lease_until": row["lease_until"], "finished_at": row["finished_at"],
                "usage": {"provider_calls": int(sent), "bytes_used": (
                    row["packet_bytes"] + row["response_bytes"] if sent else 0),
                    "usage_state": "UNKNOWN" if sent else "NOT_SENT"}}
        if include_artifacts:
            review_status = "VERIFIED" if artifacts else "NOT_RETURNED"
            review_failure = None
            if status == "STALE" and failure == "RESULT_UNAVAILABLE":
                review_status, review_failure = "UNAVAILABLE", failure
            elif len(json.dumps(artifacts, ensure_ascii=False, allow_nan=False).encode()) > _MAX_REVIEW_BYTES:
                # Return the whole verified pair or neither body.
                artifacts = []
                review_status, review_failure = "UNAVAILABLE", "ARTIFACT_PREVIEW_LIMIT"
            summary.update(artifacts=artifacts, artifact_review_status=review_status,
                           artifact_review_failure=review_failure)
        return summary

    @staticmethod
    def _grant(row):
        if row["operation_id"] is None:
            return None
        return {"max_provider_calls": 1, "max_bytes": row["max_bytes"],
                "max_wall_seconds": row["max_wall_seconds"]}

    def _available(self, db, row, request, *, exclude=None):
        parent = self.queue._must_row(db, row["job_id"])
        budget = json.loads(parent["runtime_budget"])
        frozen = request.research_plan.validation_spec["budget"]
        # Before the supervisor starts, tighten against both the original
        # request and any subsequent queue-level resource reductions.
        caps = {k: min(frozen[k], budget.get(k, frozen[k])) for k in
                ("max_provider_calls", "max_bytes", "max_wall_seconds")}
        now = self.queue._clock()
        external = self.queue.codex_budget(db, row["job_id"], now, exclude=exclude)
        local = self.queue.load_metrics(row["job_id"]) or {}
        local_calls = local.get("provider_calls", 0)
        local_bytes = local.get("bytes_used", 0)
        usage_unavailable = bool(parent["attempts"] and not local and parent["status"] != "running")
        journal_path = self.service.root / "model-calls.sqlite3"
        if request.research_plan.validation_spec.get("execution_mode") == "provider-grounded":
            from .model_call_journal import ModelCallJournal

            if journal_path.exists() or journal_path.is_symlink():
                try:
                    records = ModelCallJournal.read_existing(journal_path, job_id=row["job_id"], run_id=request.run_id,
                        request_digest=stable_digest(request), scope_id=self.service.scope_id)
                    sent = [call for call in records if call["dispatched"]]
                    if local_calls > len(sent) or (parent["attempts"] and not records and parent["status"] != "running"):
                        usage_unavailable = True
                    local_calls = max(local_calls, len(sent))
                    local_bytes = max(local_bytes, sum(call["request_bytes"] for call in sent))
                except (ValueError, TypeError, OSError, sqlite3.Error):
                    usage_unavailable = True
            elif parent["attempts"] or local_calls:
                usage_unavailable = True
        local_time = sum(local.get("stage_durations", {}).values())
        calls = caps["max_provider_calls"] - local_calls - external["provider_calls"]
        byte_count = caps["max_bytes"] - local_bytes - external["bytes_used"]
        seconds = caps["max_wall_seconds"] - local_time - external["wall_seconds"]
        deadline = budget.get("deadline_at") if parent["status"] != "completed" else None
        if deadline is not None:
            start = row["claimed_at"] if exclude and row["claimed_at"] is not None else now
            seconds = min(seconds, deadline - start)
        maximum = {"max_provider_calls": max(0, min(1, calls)),
                   "max_bytes": max(0, min(row["packet_bytes"] + _MAX_RESULT_BYTES, byte_count)),
                   "max_wall_seconds": max(0, min(300, seconds))}
        reason = None
        if usage_unavailable:
            reason = "USAGE_UNAVAILABLE"
        elif not self.service.allow_model_calls:
            reason = "OFFLINE_MODE"
        elif parent["cancel_requested"] or parent["status"] == "cancelled":
            reason = "PARENT_CANCELLED"
        elif parent["status"] == "failed":
            reason = "PARENT_FAILED"
        elif parent["status"] == "running":
            reason = "RESEARCH_RUNNING"
        elif (study := request.research_plan.validation_spec.get("factor_study")) and (
                study["phase"] != "test" or parent["status"] != "completed"):
            reason = "FROZEN_TEST_WINDOW"
        elif calls < 1 or byte_count <= row["packet_bytes"] or seconds < 1:
            reason = "BUDGET_EXHAUSTED"
        return maximum, reason, deadline

    def preview(self, job_id, handoff_ref):
        request = self._job(job_id)
        package = self._resolve(job_id, handoff_ref)
        digest = self.store._digest(handoff_ref)
        with self.queue._connect() as db:
            row = db.execute("SELECT * FROM codex_handoffs WHERE job_id=? AND envelope_digest=?", (job_id, digest)).fetchone()
            maximum, reason, _ = self._available(db, row, request)
        budget_status = "USAGE_UNAVAILABLE" if reason == "USAGE_UNAVAILABLE" else "AVAILABLE"
        summary = (self.status(job_id, row["operation_id"], include_artifacts=True) if row["operation_id"]
                   else self._summary(row, include_artifacts=True))
        if row["operation_id"]:
            reason = "ALREADY_AUTHORIZED"
        elif summary["status"] == "MANUAL":
            reason = "MANUAL_RESULT_EXISTS"
        elif summary["status"] == "STALE":
            reason = summary["failure_kind"]
        elif reason is None:
            try:
                self._current(row)
            except CodexWorkbenchError as exc:
                reason = exc.code
        limits = summary["limits"] or {**maximum, "max_wall_seconds": min(60, int(maximum["max_wall_seconds"]))}
        return {**summary, "package": package.to_dict(), "packet_json": package.to_json(), "eligible": reason is None,
                "reason_code": reason, "reason": _REASONS.get(reason), "limits": limits, "budget_status": budget_status}

    def authorize(self, job_id, values):
        _fields(values, ("handoff_ref", "package_digest", "limits", "authorized"))
        if values["authorized"] is not True:
            raise CodexWorkbenchError("AUTHORIZATION_REQUIRED", 403)
        limits = _limits(values["limits"])
        reference = values["handoff_ref"]
        request = self._job(job_id)
        package = self._resolve(job_id, reference)
        if not self.service.allow_model_calls:
            raise CodexWorkbenchError("OFFLINE_MODE", 403)
        if package.package_digest != values["package_digest"]:
            raise CodexWorkbenchError("STALE_INPUTS")
        with self._write() as db:
            row = db.execute("SELECT * FROM codex_handoffs WHERE job_id=? AND envelope_digest=?",
                             (job_id, self.store._digest(reference))).fetchone()
            if row["operation_id"]:
                if self._grant(row) != limits:
                    raise CodexWorkbenchError("ALREADY_AUTHORIZED")
                operation = row["operation_id"]
            else:
                if self.store._receipt(reference) is not None:
                    raise CodexWorkbenchError("MANUAL_RESULT_EXISTS")
                self._current(row)
                maximum, reason, deadline = self._available(db, row, request)
                if reason:
                    raise CodexWorkbenchError(reason, 403 if reason == "OFFLINE_MODE" else 409)
                if (any(limits[k] > maximum[k] for k in limits)
                        or limits["max_bytes"] <= row["packet_bytes"]):
                    raise CodexWorkbenchError("LIMITS_CHANGED")
                now = self.queue._clock()
                expires = min(now + _AUTHORIZATION_SECONDS, deadline) if deadline is not None else now + _AUTHORIZATION_SECONDS
                operation = "codex-op-" + secrets.token_hex(16)
                db.execute("""UPDATE codex_handoffs SET operation_id=?,status='AUTHORIZED',max_bytes=?,
                              max_wall_seconds=?,authorized_at=?,lease_until=? WHERE envelope_digest=?""",
                           (operation, limits["max_bytes"], limits["max_wall_seconds"], now, expires, row["envelope_digest"]))
        return self.status(job_id, operation)

    def _refresh(self, db, row):
        status = row["status"]
        if status == "RETURNING":
            self._publish(db, row)
        elif status in _ACTIVE:
            now = self.queue._clock()
            parent = self.queue._must_row(db, row["job_id"])
            if parent["cancel_requested"] or parent["status"] == "cancelled":
                self._finish(db, row, "CANCELLED", "PARENT_CANCELLED", now)
            elif parent["status"] == "failed":
                self._finish(db, row, "FAILED", "PARENT_FAILED", now)
            elif now >= row["lease_until"]:
                reason = "AUTHORIZATION_EXPIRED" if status == "AUTHORIZED" else "LEASE_EXPIRED"
                self._finish(db, row, "TIMED_OUT", reason, row["lease_until"])
            else:
                try:
                    self._current(row)
                except CodexWorkbenchError:
                    self._finish(db, row, "STALE", "STALE_INPUTS", now)
                else:
                    maximum, budget_reason, deadline = self._available(db, row, self._job(row["job_id"]), exclude=row["operation_id"])
                    now = self.queue._clock()
                    until = min(row["lease_until"], deadline) if deadline is not None else row["lease_until"]
                    if status == "DISPATCHED":
                        until = min(until, row["claimed_at"] + maximum["max_wall_seconds"])
                    if until < row["lease_until"]:
                        db.execute("UPDATE codex_handoffs SET lease_until=? WHERE operation_id=?", (until, row["operation_id"]))
                    if now >= until:
                        reason = "AUTHORIZATION_EXPIRED" if status == "AUTHORIZED" else "LEASE_EXPIRED"
                        self._finish(db, row, "TIMED_OUT", reason, until)
                    elif budget_reason == "USAGE_UNAVAILABLE":
                        self._finish(db, row, "REJECTED", budget_reason, now)
                    elif maximum["max_provider_calls"] < 1 or maximum["max_bytes"] < row["max_bytes"]:
                        self._finish(db, row, "REJECTED", "LIMITS_CHANGED", now)
        return self._row(db, row["job_id"], row["operation_id"])

    def _finish(self, db, row, status, reason, now, *, callback_digest=None, response_bytes=0):
        db.execute("""UPDATE codex_handoffs SET status=?,failure_kind=?,finished_at=?,callback_digest=?,
                      response_bytes=? WHERE operation_id=?""",
                   (status, reason, now, callback_digest, response_bytes, row["operation_id"]))
        if status != "RETURNING":
            terminal = "cancelled" if status == "CANCELLED" else "failed"
            digest = stable_digest(reason)
            external = db.execute("SELECT job_id FROM jobs WHERE task_type='external' AND task_digest=?",
                                  (row["envelope_digest"],)).fetchone()
            if external:
                db.execute("UPDATE jobs SET status=?,last_error_digest=?,updated_at=? WHERE job_id=?",
                           (terminal, digest, now, external["job_id"]))
                self.queue._event(db, external["job_id"], terminal, now, reason_digest=digest)

    def status(self, job_id, operation_id, *, include_artifacts=False):
        self._job(job_id)
        with self._write() as db:
            row = self._refresh(db, self._row(db, job_id, operation_id))
        return self._summary(row, include_artifacts=include_artifacts)

    def claim(self, job_id, operation_id, values):
        _fields(values, ())
        self._job(job_id)
        with self._write() as db:
            row = self._refresh(db, self._row(db, job_id, operation_id))
            if not self.service.allow_model_calls:
                raise CodexWorkbenchError("OFFLINE_MODE", 403)
            if row["status"] != "AUTHORIZED":
                raise CodexWorkbenchError("ALREADY_DISPATCHED" if row["status"] == "DISPATCHED" else "OPERATION_CLOSED")
            package = self.store.load("method-handoff:" + row["envelope_digest"])
            now = self.queue._clock()
            if now >= row["lease_until"]:
                self._finish(db, row, "TIMED_OUT", "AUTHORIZATION_EXPIRED", row["lease_until"])
                raise CodexWorkbenchError("AUTHORIZATION_EXPIRED")
            maximum, _, _ = self._available(db, row, self._job(job_id), exclude=operation_id)
            until = now + min(row["max_wall_seconds"], maximum["max_wall_seconds"])
            if until <= now:
                self._finish(db, row, "TIMED_OUT", "AUTHORIZATION_EXPIRED", now)
                raise CodexWorkbenchError("AUTHORIZATION_EXPIRED")
            lease = secrets.token_hex(32)
            db.execute("""UPDATE codex_handoffs SET status='DISPATCHED',claimed_at=?,lease_until=?,lease_digest=?
                          WHERE operation_id=?""", (now, until, _lease_digest(lease), operation_id))
            row = self._row(db, job_id, operation_id)
        return {**self._summary(row), "package": package.to_dict(), "lease_token": lease}

    def cancel(self, job_id, operation_id, values):
        _fields(values, ())
        self._job(job_id)
        with self._write() as db:
            row = self._refresh(db, self._row(db, job_id, operation_id))
            if row["status"] in _ACTIVE:
                self._finish(db, row, "CANCELLED", "CANCELLED_BY_USER", self.queue._clock())
            row = self._row(db, job_id, operation_id)
        return self._summary(row)

    def _worker(self, db, job_id, operation_id, lease):
        row = self._row(db, job_id, operation_id)
        if not hmac.compare_digest(row["lease_digest"] or "", _lease_digest(lease)):
            raise CodexWorkbenchError("INVALID_LEASE", 403)
        return self._refresh(db, row)

    @staticmethod
    def _replay(row, digest):
        if row["status"] == "DISPATCHED":
            return False
        if row["callback_digest"] is None:
            raise CodexWorkbenchError("OPERATION_CLOSED")
        if row["callback_digest"] != digest:
            raise CodexWorkbenchError("RESULT_CONFLICT")
        return True

    def fail(self, job_id, operation_id, values):
        _fields(values, ("lease_token", "failure_kind"))
        reason = values["failure_kind"]
        if not isinstance(reason, str) or reason not in _FAILURES:
            raise CodexWorkbenchError("INVALID_REQUEST", 422)
        self._job(job_id)
        with self._write() as db:
            row = self._worker(db, job_id, operation_id, values["lease_token"])
            digest = stable_digest({"executor_failure": reason})
            if not self._replay(row, digest):
                self._finish(db, row, _FAILURES[reason], reason, self.queue._clock(), callback_digest=digest)
            row = self._row(db, job_id, operation_id)
        return self._summary(row)

    def complete(self, job_id, operation_id, values):
        _fields(values, ("lease_token", "response"))
        self._job(job_id)
        with self._write() as db:
            row = self._worker(db, job_id, operation_id, values["lease_token"])
            if row["callback_digest"] is None and row["status"] != "DISPATCHED":
                raise CodexWorkbenchError("OPERATION_CLOSED")
            self._return(db, row, values["response"])
        return self.status(job_id, operation_id)

    def _return(self, db, row, response):
        reference = "method-handoff:" + row["envelope_digest"]
        package = self.store.load(reference)
        try:
            encoded = _json(response).encode()
        except (ValueError, TypeError, OverflowError, RecursionError):
            raise CodexWorkbenchError("INVALID_REQUEST", 422) from None
        room = row["max_bytes"] - row["packet_bytes"]
        size = len(encoded)
        normalized = None
        if size > min(room, _MAX_RESULT_BYTES):
            reason = "BYTE_LIMIT"
        else:
            outcome = accept_method_result(package, response, allowed_evidence_refs=package.context.evidence_index)
            reason = outcome.failure_kind if outcome.status in {"REJECTED", "EXTERNAL_HANDOFF_REQUIRED"} else None
            if reason is None:
                normalized = _normalized_response(package, response)
        digest = (stable_digest(normalized) if normalized is not None
                  else hashlib.sha256(b"rejected:" + encoded).hexdigest())
        if self._replay(row, digest):
            return
        if normalized is not None:
            # The immutable private input plus the queue intent form a
            # recoverable publication. A crash before the intent commits
            # leaves no visible method receipt. Once it commits recovery
            # needs only the validated local bytes, even after lease expiry.
            self.store._save(self.store._path("worker-returns", digest), normalized)
        now = self.queue._clock()
        if now >= row["lease_until"]:
            if normalized is not None:
                self.store._path("worker-returns", digest).unlink(missing_ok=True)
            self._finish(db, row, "TIMED_OUT", "LEASE_EXPIRED", row["lease_until"])
            raise CodexWorkbenchError("LEASE_EXPIRED")
        self._finish(db, row, "REJECTED" if normalized is None else "RETURNING", reason, now,
                     callback_digest=digest, response_bytes=min(room, size))

    def _publish(self, db, row):
        reference = "method-handoff:" + row["envelope_digest"]
        digest = row["callback_digest"]
        try:
            package = self.store.load(reference)
            result = self.store._read(self.store._path("worker-returns", digest))
            if package.package_digest != row["package_digest"] or stable_digest(result) != digest:
                raise ValueError("worker result binding")
            outcome = accept_method_result(package, result, allowed_evidence_refs=package.context.evidence_index)
            if outcome.status in {"REJECTED", "EXTERNAL_HANDOFF_REQUIRED"}:
                raise ValueError("worker result schema")
            saved = self.store.publish_validated(db, reference, package, result, outcome)
            if saved != outcome:
                raise ValueError("worker receipt conflict")
        except (KeyError, ValueError, TypeError, OSError) as exc:
            raise CodexWorkbenchError("RESULT_UNAVAILABLE") from exc
        db.execute("UPDATE codex_handoffs SET status='COMPLETED' WHERE operation_id=?", (row["operation_id"],))

"""Durable, sanitized model-attempt accounting independent of report success."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_UNITS = frozenset({"input_units", "generated_units", "total_units"})
_INITIALIZE_TIMEOUT = 30.0
_OUTCOMES = frozenset({"READY", "FAILED", "SCHEMA_ERROR", "NOT_CONFIGURED", "TIMEOUT", "NON_JSON", "HTTP_ERROR", "TRANSPORT", "CAPABILITY_REJECTED", "RESOURCE_LIMIT", "STREAM_INTERRUPTED", "UNSUPPORTED_STREAM", "RESPONSE_TOO_LARGE"})
_BEFORE_PROVIDER = frozenset({"OUTBOUND_DISABLED", "SNAPSHOT_INTEGRITY_FAILED", "CONTEXT_INTEGRITY_FAILED",
                              "POLICY_CHANGED", "REQUEST_UNAVAILABLE", "WORKER_START_FAILED"})


def model_call_usage(calls, *, incomplete=False):
    totals = [(call["usage"] or {}).get("total_units") for call in calls if call["dispatched"] is True]
    subtotal = sum(value for value in totals if value is not None)
    missing = totals.count(None)
    return {"known_total_units": subtotal, "unknown_calls": None if incomplete else missing,
            "total_units": None if incomplete or missing else subtotal,
            "status": "UNKNOWN" if incomplete or missing else "RECORDED" if totals else "NOT_CALLED"}


def reconcile_model_calls(calls, journal_status, *, attempts, failure, metrics, published_calls=None):
    """Reconcile scoped receipts with the worker's independent budget counters and publication."""
    charged = metrics.get("provider_calls", 0)
    # Each grounded adapter invocation permits one attempt. DNS and TLS happen
    # after the budget charge and before the dispatch marker.
    accounted = sum(call["dispatched"] or call["status"] in {"TRANSPORT", "TIMEOUT", "STREAM_INTERRUPTED"} for call in calls)
    sent_ids = {call["call_id"] for call in calls if call["dispatched"]}
    call_ids = {call["call_id"] for call in calls}
    archive_gap = any(not isinstance(call, dict) or call.get("call_id") not in (
        sent_ids if call.get("dispatched") else call_ids) for call in published_calls or ())
    # A supervisor may persist default zero counters when a worker is killed.
    # A returned local stage or a known pre-provider gate certifies a real zero.
    zero_proven = not charged and not published_calls and (
        not attempts or failure in _BEFORE_PROVIDER or "local_research" in metrics.get("stage_durations", {}))
    gap = charged > accounted or archive_gap or (attempts and not calls and not zero_proven)
    issue = None
    if journal_status == "UNAVAILABLE" and not zero_proven:
        issue = "CALL_JOURNAL_UNAVAILABLE"
    elif gap:
        issue = "CALL_JOURNAL_INCOMPLETE"
        journal_status = "INCOMPLETE"
    if issue:
        # A restored PREPARED row predates a possible dispatch. Preserve the
        # observation without representing its missing outcome as a known zero.
        calls = [{**call, "observed_status": "PREPARED", "dispatched": None, "status": "UNKNOWN"}
                 if not call["dispatched"] and call["status"] in {"PREPARED", "NOT_SENT"} else call for call in calls]
    usage = model_call_usage(calls, incomplete=issue is not None)
    return {"calls": calls, "journal_status": journal_status, "journal_issue": issue,
            "dispatch_status": "UNKNOWN" if issue else "SENT" if sent_ids else "NOT_SENT",
            "known_dispatched_calls": len(sent_ids), "dispatched_calls": None if issue else len(sent_ids),
            "usage_status": "NOT_STARTED" if usage["status"] == "NOT_CALLED" else usage["status"], "usage": usage}


class ModelCallJournal:
    def __init__(self, path, *, job_id, run_id, request_digest, attempt, scope_id):
        if not all(isinstance(value, str) and value for value in (job_id, run_id, scope_id)) or not _DIGEST.fullmatch(request_digest) or type(attempt) is not int or attempt < 1:
            raise ValueError("model journal binding is invalid")
        self.path = Path(path)
        self.binding = (scope_id, job_id, request_digest)
        self.run_id, self.attempt = run_id, attempt
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise ValueError("model journal must not be a symlink")
        descriptor = os.open(self.path, os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        deadline = time.monotonic() + _INITIALIZE_TIMEOUT
        while True:
            try:
                with self._connect(timeout=max(0, deadline - time.monotonic())) as db:
                    # File existence does not establish completed initialization.
                    # Concurrent first creators can also race on the WAL switch;
                    # SQLite may return BUSY immediately despite busy_timeout.
                    db.execute("PRAGMA journal_mode=WAL")
                    db.execute("""CREATE TABLE IF NOT EXISTS model_calls (
                        call_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, job_id TEXT NOT NULL,
                        request_digest TEXT NOT NULL, run_id TEXT NOT NULL, attempt INTEGER NOT NULL,
                        role TEXT NOT NULL, model TEXT NOT NULL, context_fingerprint TEXT NOT NULL,
                        context_bytes INTEGER NOT NULL, started_at TEXT NOT NULL, dispatched_at TEXT,
                        status TEXT NOT NULL, request_bytes INTEGER, usage TEXT, duration_ms INTEGER
                    )""")
                break
            except sqlite3.OperationalError as error:
                remaining = deadline - time.monotonic()
                code = getattr(error, "sqlite_errorcode", 0) & 0xFF
                if code not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} or remaining <= 0:
                    raise
                # Reopen after rollback/close instead of retrying a stale lock.
                time.sleep(min(0.05, remaining))

    @contextmanager
    def _connect(self, *, timeout=30):
        db = sqlite3.connect(self.path, timeout=timeout)
        db.row_factory = sqlite3.Row
        try:
            db.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
            with db:
                yield db
        finally:
            db.close()

    def begin(self, record):
        from .provider_config import ALLOWED_ROLES, _model_allowed

        if not isinstance(record, dict) or set(record) != {"role", "model", "context_fingerprint", "context_bytes"}:
            raise ValueError("model journal accepts only sanitized metadata")
        if record["role"] not in ALLOWED_ROLES or not _model_allowed("openai_compatible", record["model"]) or not isinstance(record["context_fingerprint"], str) or not _DIGEST.fullmatch(record["context_fingerprint"]) or type(record["context_bytes"]) is not int or not 0 < record["context_bytes"] <= 131072:
            raise ValueError("model journal metadata is invalid")
        call_id = "call-" + uuid.uuid4().hex
        with self._connect() as db:
            db.execute("INSERT INTO model_calls (call_id,scope_id,job_id,request_digest,run_id,attempt,role,model,context_fingerprint,context_bytes,started_at,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (call_id, *self.binding, self.run_id, self.attempt, record["role"], record["model"], record["context_fingerprint"], record["context_bytes"], datetime.now(UTC).isoformat(), "PREPARED"))
        return call_id

    def dispatched(self, call_id, request_bytes):
        if type(request_bytes) is not int or request_bytes < 0:
            raise ValueError("model request byte count is invalid")
        with self._connect() as db:
            changed = db.execute("UPDATE model_calls SET dispatched_at=?,request_bytes=?,status='DISPATCHED' WHERE call_id=? AND scope_id=? AND job_id=? AND request_digest=? AND run_id=? AND attempt=? AND status='PREPARED'",
                                 (datetime.now(UTC).isoformat(), request_bytes, call_id, *self.binding, self.run_id, self.attempt)).rowcount
        if changed != 1:
            raise ValueError("model call does not match an open attempt")

    @staticmethod
    def _usage(value):
        if not isinstance(value, dict) or set(value) - _UNITS or any(type(item) is not int or not 0 <= item <= 10**9 for item in value.values()):
            raise ValueError("model usage metadata is invalid")
        return json.dumps(value, sort_keys=True) if value else None

    def record_usage(self, call_id, usage):
        encoded = self._usage(usage)
        with self._connect() as db:
            changed = db.execute("UPDATE model_calls SET usage=? WHERE call_id=? AND scope_id=? AND job_id=? AND request_digest=? AND run_id=? AND attempt=? AND status='DISPATCHED'", (encoded, call_id, *self.binding, self.run_id, self.attempt)).rowcount
        if changed != 1:
            raise ValueError("model usage does not match an active attempt")

    def finish(self, call_id, outcome):
        if not isinstance(outcome, dict) or set(outcome) != {"status", "usage", "duration_ms"} or outcome["status"] not in _OUTCOMES or type(outcome["duration_ms"]) is not int or outcome["duration_ms"] < 0:
            raise ValueError("model outcome metadata is invalid")
        usage = self._usage(outcome["usage"])
        with self._connect() as db:
            changed = db.execute("UPDATE model_calls SET status=?,usage=COALESCE(?,usage),duration_ms=? WHERE call_id=? AND scope_id=? AND job_id=? AND request_digest=? AND run_id=? AND attempt=? AND status IN ('PREPARED','DISPATCHED')",
                                 (outcome["status"], usage, outcome["duration_ms"], call_id, *self.binding, self.run_id, self.attempt)).rowcount
        if changed != 1:
            raise ValueError("model outcome does not match an open attempt")

    def records(self, *, terminal=False):
        with self._connect() as db:
            return self._read(db, self.binding, self.run_id, terminal=terminal)

    @classmethod
    def read_existing(cls, path, *, scope_id, job_id, run_id, request_digest, terminal=False):
        """Reading a job must never initialize, repair or replace its journal."""
        path = Path(path)
        if path.is_symlink():
            raise ValueError("model journal must not be a symlink")
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            return cls._read(db, (scope_id, job_id, request_digest), run_id, terminal=terminal)

    @classmethod
    def _read(cls, db, binding, run_id, *, terminal):
        from .provider_config import ALLOWED_ROLES, _model_allowed

        rows = db.execute("SELECT * FROM model_calls WHERE scope_id=? AND job_id=? AND request_digest=? ORDER BY started_at,call_id", binding)
        records = []
        for row in rows:
            status = row["status"]
            if (row["run_id"] != run_id or row["role"] not in ALLOWED_ROLES or not _model_allowed("openai_compatible", row["model"])
                    or status not in _OUTCOMES | {"PREPARED", "DISPATCHED"}
                    or not re.fullmatch(r"call-[0-9a-f]{32}", row["call_id"]) or not _DIGEST.fullmatch(row["context_fingerprint"])
                    or type(row["attempt"]) is not int or row["attempt"] < 1 or type(row["context_bytes"]) is not int
                    or not 0 < row["context_bytes"] <= 131072):
                raise ValueError("model journal contains invalid metadata")
            for key in ("request_bytes", "duration_ms"):
                if row[key] is not None and (type(row[key]) is not int or row[key] < 0):
                    raise ValueError("model journal contains invalid counts")
            for key in ("started_at", "dispatched_at"):
                if (key == "started_at" or row[key] is not None) and datetime.fromisoformat(row[key]).tzinfo is None:
                    raise ValueError("model journal contains an invalid timestamp")
            usage = json.loads(row["usage"]) if row["usage"] is not None else None
            if usage is not None:
                cls._usage(usage)
            if terminal and status in {"PREPARED", "DISPATCHED"}:
                status = "UNKNOWN" if row["dispatched_at"] else "NOT_SENT"
            records.append({key: row[key] for key in ("call_id", "role", "model", "attempt", "context_fingerprint", "context_bytes", "started_at", "request_bytes", "duration_ms")} |
                           {"status": status, "dispatched": row["dispatched_at"] is not None, "usage": usage})
        return records

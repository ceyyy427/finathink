"""Durable, sanitized model-attempt accounting independent of report success."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_UNITS = frozenset({"input_units", "generated_units", "total_units"})
_OUTCOMES = frozenset({"READY", "FAILED", "SCHEMA_ERROR", "NOT_CONFIGURED", "TIMEOUT", "NON_JSON", "HTTP_ERROR", "TRANSPORT", "CAPABILITY_REJECTED", "RESOURCE_LIMIT", "STREAM_INTERRUPTED", "UNSUPPORTED_STREAM", "RESPONSE_TOO_LARGE"})


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
        is_new = not self.path.exists()
        descriptor = os.open(self.path, os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        with self._connect() as db:
            if is_new:
                db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS model_calls (
                call_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, job_id TEXT NOT NULL,
                request_digest TEXT NOT NULL, run_id TEXT NOT NULL, attempt INTEGER NOT NULL,
                role TEXT NOT NULL, model TEXT NOT NULL, context_fingerprint TEXT NOT NULL,
                context_bytes INTEGER NOT NULL, started_at TEXT NOT NULL, dispatched_at TEXT,
                status TEXT NOT NULL, request_bytes INTEGER, usage TEXT, duration_ms INTEGER
            )""")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=30000")
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
            changed = db.execute("UPDATE model_calls SET dispatched_at=?,request_bytes=?,status='DISPATCHED' WHERE call_id=? AND scope_id=? AND job_id=? AND request_digest=? AND status='PREPARED'",
                                 (datetime.now(UTC).isoformat(), request_bytes, call_id, *self.binding)).rowcount
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
            changed = db.execute("UPDATE model_calls SET usage=? WHERE call_id=? AND scope_id=? AND job_id=? AND request_digest=? AND status='DISPATCHED'", (encoded, call_id, *self.binding)).rowcount
        if changed != 1:
            raise ValueError("model usage does not match an active attempt")

    def finish(self, call_id, outcome):
        if not isinstance(outcome, dict) or set(outcome) != {"status", "usage", "duration_ms"} or outcome["status"] not in _OUTCOMES or type(outcome["duration_ms"]) is not int or outcome["duration_ms"] < 0:
            raise ValueError("model outcome metadata is invalid")
        usage = self._usage(outcome["usage"])
        with self._connect() as db:
            changed = db.execute("UPDATE model_calls SET status=?,usage=COALESCE(?,usage),duration_ms=? WHERE call_id=? AND scope_id=? AND job_id=? AND request_digest=? AND status IN ('PREPARED','DISPATCHED')",
                                 (outcome["status"], usage, outcome["duration_ms"], call_id, *self.binding)).rowcount
        if changed != 1:
            raise ValueError("model outcome does not match an open attempt")

    def records(self, *, terminal=False):
        with self._connect() as db:
            rows = db.execute("SELECT * FROM model_calls WHERE scope_id=? AND job_id=? AND request_digest=? ORDER BY started_at,call_id", self.binding).fetchall()
        records = []
        for row in rows:
            status = row["status"]
            if terminal and status in {"PREPARED", "DISPATCHED"}:
                status = "UNKNOWN" if row["dispatched_at"] else "NOT_SENT"
            records.append({key: row[key] for key in ("call_id", "role", "model", "attempt", "context_fingerprint", "context_bytes", "started_at", "request_bytes", "duration_ms")} |
                           {"status": status, "dispatched": row["dispatched_at"] is not None, "usage": json.loads(row["usage"]) if row["usage"] else None})
        return records

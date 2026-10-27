"""Durable, session-scoped research inventory with verified report references."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .contracts import (
    _TERMINAL_STATES,
    ResearchRunState,
    ResearchState,
    _public_identifier,
    stable_digest,
    to_jsonable,
)
from .reports import MAX_MANIFEST_BYTES, redact_public_payload, verify_report_bundle
from .run_store import _decode_event, _decode_state

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SORTS = frozenset({"created_at", "started_at", "finished_at", "as_of", "run_id"})


def _run_id(value):
    if not isinstance(value, str) or not _RUN_ID.fullmatch(value):
        raise ValueError("research run id is invalid")
    return value


def _time(value):
    if value is None:
        return None
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(parsed, datetime) or parsed.utcoffset() is None:
        raise ValueError("execution time must be timezone-aware")
    return parsed.astimezone(UTC).isoformat()


class RunRepository:
    def __init__(self, path: str | Path, *, report_root: str | Path, scope_id: str = "local") -> None:
        if not isinstance(scope_id, str) or not scope_id or len(scope_id) > 128:
            raise ValueError("research scope is invalid")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.report_root = Path(report_root)
        self.report_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.scope_id = scope_id
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS research_run_index (
                scope_id TEXT NOT NULL, run_id TEXT NOT NULL, state TEXT NOT NULL,
                as_of TEXT, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
                payload TEXT NOT NULL, record_digest TEXT NOT NULL,
                PRIMARY KEY(scope_id, run_id))""")
            db.execute("CREATE INDEX IF NOT EXISTS research_run_date ON research_run_index(scope_id, created_at, run_id)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _bundle(self, relative):
        path = self.report_root / relative
        if Path(relative).is_absolute() or ".." in Path(relative).parts or not path.resolve().is_relative_to(self.report_root.resolve()):
            raise ValueError("report bundle is outside the approved report root")
        return path

    def _report_status(self, payload):
        try:
            bundle = self._bundle(payload["bundle_ref"])
            path = bundle / "manifest.json"
            if not path.is_file():
                return "MISSING"
            if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent.is_relative_to(self.report_root)):
                return "CORRUPT"
            if path.stat().st_size > MAX_MANIFEST_BYTES:
                return "CORRUPT"
            actual = json.loads(path.read_text(encoding="utf-8"))
            if stable_digest(actual) != payload["manifest_digest"] or actual.get("run_id") != payload["run_id"]:
                return "CORRUPT"
            for relative in actual.get("files", {}):
                child = bundle / relative
                if child.is_symlink() or not child.resolve().is_relative_to(bundle.resolve()):
                    return "CORRUPT"
                if not child.is_file():
                    return "MISSING"
            return "AVAILABLE" if verify_report_bundle(path).ok else "CORRUPT"
        except (OSError, ValueError, TypeError, KeyError):
            return "CORRUPT"

    def record_result(self, result, manifest, *, bundle=None, created_at=None, started_at=None, finished_at=None, strategy_version=None, policy_fingerprint=None):
        if not isinstance(result.state, ResearchRunState):
            raise TypeError("research result requires a typed state")
        run_id = _run_id(result.state.run_id)
        admitted = redact_public_payload(manifest)
        if not isinstance(admitted, dict) or admitted.get("run_id") != run_id:
            raise ValueError("manifest run id does not match result")
        source = admitted.get("source_snapshot")
        if not isinstance(source, dict) or source.get("state_digest") != stable_digest(result.state):
            raise ValueError("result is not bound to manifest state")
        for value, name in ((strategy_version, "strategy_version"), (policy_fingerprint, "policy_fingerprint")):
            if value is not None:
                _public_identifier(value, name)
        directory = Path(bundle) if bundle is not None else self.report_root / run_id
        try:
            relative = directory.absolute().relative_to(self.report_root.absolute()).as_posix()
        except ValueError as exc:
            raise ValueError("report bundle is outside the approved report root") from exc
        events = tuple(getattr(result, "events", ()) or ())
        if any(event.run_id != run_id for event in events):
            raise ValueError("research events belong to another run")
        created = _time(created_at or (events[0].timestamp if events else admitted["created_at"]))
        started = _time(started_at or (events[0].timestamp if events else None))
        finished = _time(finished_at or (admitted["created_at"] if result.state.current_state in _TERMINAL_STATES else None))
        if (started and started < created) or (finished and finished < (started or created)):
            raise ValueError("research execution times are out of order")
        payload = {
            "run_id": run_id, "scope_id": self.scope_id,
            "state": to_jsonable(result.state), "events": to_jsonable(events), "manifest": admitted,
            "manifest_digest": stable_digest(admitted), "bundle_ref": relative,
            "created_at": created, "started_at": started, "finished_at": finished,
            "dataset_fingerprint": admitted.get("source_snapshot", {}).get("dataset_fingerprint"),
            "strategy_version": strategy_version, "policy_fingerprint": policy_fingerprint,
        }
        if self._report_status(payload) != "AVAILABLE":
            raise ValueError("report manifest integrity check failed")
        digest = stable_digest(payload)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT record_digest FROM research_run_index WHERE scope_id=? AND run_id=?", (self.scope_id, run_id)).fetchone()
            if existing is not None:
                if existing["record_digest"] != digest:
                    raise ValueError("research run is already bound to an immutable result")
                return
            db.execute("""INSERT INTO research_run_index
                (scope_id, run_id, state, as_of, created_at, started_at, finished_at, payload, record_digest)
                VALUES (?,?,?,?,?,?,?,?,?)""", (
                    self.scope_id, run_id, result.state.current_state.value,
                    result.state.as_of.isoformat() if result.state.as_of else None,
                    created, started, finished, json.dumps(payload, ensure_ascii=False, sort_keys=True), digest,
                ))

    def _entry(self, row):
        try:
            payload = json.loads(row["payload"])
            if stable_digest(payload) != row["record_digest"] or payload["scope_id"] != self.scope_id or payload["run_id"] != row["run_id"]:
                raise ValueError("run index integrity check failed")
            state = _decode_state(payload["state"], row["run_id"])
            if state.current_state.value != row["state"] or any(payload[key] != row[key] for key in ("created_at", "started_at", "finished_at")):
                raise ValueError("run index metadata integrity check failed")
            return {
                **{key: payload.get(key) for key in ("run_id", "created_at", "started_at", "finished_at", "dataset_fingerprint", "strategy_version", "policy_fingerprint")},
                "state": state, "manifest": payload["manifest"],
                "events": tuple(_decode_event(event) for event in payload["events"]),
                "bundle": self._bundle(payload["bundle_ref"]), "report_status": self._report_status(payload),
            }
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError("research index record is corrupt") from exc

    def get_run(self, run_id):
        run_id = _run_id(run_id)
        with self._connect() as db:
            row = db.execute("SELECT * FROM research_run_index WHERE scope_id=? AND run_id=?", (self.scope_id, run_id)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._entry(row)

    def list_runs(self, *, limit=50, offset=0, state=None, sort_by="created_at", order="desc"):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200 or isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("research pagination is invalid")
        if sort_by not in _SORTS or order not in {"asc", "desc"}:
            raise ValueError("research sort is invalid")
        code = ResearchState(state).value if state is not None else None
        where = "scope_id=?" + (" AND state=?" if code else "")
        values = [self.scope_id] + ([code] if code else [])
        with self._connect() as db:
            rows = db.execute(f"SELECT * FROM research_run_index WHERE {where} ORDER BY {sort_by} {order}, run_id {order} LIMIT ? OFFSET ?", (*values, limit, offset)).fetchall()
        return [self._entry(row) for row in rows]

    def count_runs(self, *, state=None):
        code = ResearchState(state).value if state is not None else None
        where = "scope_id=?" + (" AND state=?" if code else "")
        with self._connect() as db:
            return db.execute(f"SELECT COUNT(*) FROM research_run_index WHERE {where}", (self.scope_id, code) if code else (self.scope_id,)).fetchone()[0]

    def list_run_refs(self, *, limit=200, offset=0):
        """Bounded identities let projections isolate a single corrupt record."""
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
            raise ValueError("research pagination is invalid")
        with self._connect() as db:
            return tuple(row[0] for row in db.execute(
                "SELECT run_id FROM research_run_index WHERE scope_id=? ORDER BY created_at DESC, run_id DESC LIMIT ? OFFSET ?",
                (self.scope_id, limit, offset)))

    def counts_by_state(self):
        with self._connect() as db:
            return {row["state"]: row["count"] for row in db.execute("SELECT state, COUNT(*) AS count FROM research_run_index WHERE scope_id=? GROUP BY state", (self.scope_id,))}

    def activity_by_day(self):
        with self._connect() as db:
            return [{"date": row["day"], "count": row["count"]} for row in db.execute("SELECT substr(created_at,1,10) AS day, COUNT(*) AS count FROM research_run_index WHERE scope_id=? GROUP BY day ORDER BY day", (self.scope_id,))]

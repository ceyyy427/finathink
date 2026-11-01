"""Charter/test control state in the existing queue database, not run facts.

Published observations remain in RunRepository/report manifests. This store
owns immutable charters, explicit freezes, and atomic once-only test claims.
"""

from __future__ import annotations

import json
import re
from contextlib import nullcontext

import pandas as pd

from .contracts import stable_digest
from .job_queue import _ref


class FactorStudyStore:
    def __init__(self, queue, *, scope_id):
        self.queue, self.scope_id = queue, scope_id
        with queue._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS factor_studies (
                scope_id TEXT NOT NULL, study_id TEXT NOT NULL, spec TEXT NOT NULL,
                spec_ref TEXT NOT NULL, state TEXT NOT NULL, validation_job_id TEXT,
                test_job_id TEXT, candidate_id TEXT, candidate_fingerprint TEXT,
                test_key TEXT, review_decision TEXT, reviewer_id TEXT,
                PRIMARY KEY(scope_id,study_id))""")
            if "validation_key" not in {row["name"] for row in db.execute("PRAGMA table_info(factor_studies)")}:
                db.execute("ALTER TABLE factor_studies ADD COLUMN validation_key TEXT")
            db.execute("""CREATE TABLE IF NOT EXISTS factor_test_claims (
                scope_id TEXT NOT NULL, study_id TEXT NOT NULL, instrument TEXT NOT NULL,
                start_ns INTEGER NOT NULL, end_ns INTEGER NOT NULL,
                PRIMARY KEY(scope_id,study_id))""")

    def _decode(self, row):
        if row is None:
            raise KeyError("study not found")
        payload = dict(row)
        payload["spec"] = json.loads(payload["spec"])
        if stable_digest({"scope_id": self.scope_id, "spec": payload["spec"]}) != payload["spec_ref"]:
            raise ValueError("study immutable input integrity failure")
        payload.pop("scope_id")
        payload["validation_key"] = payload["validation_key"] or payload["study_id"]
        payload["production_write"] = False
        return payload

    def _row(self, db, study_id):
        return db.execute("SELECT * FROM factor_studies WHERE scope_id=? AND study_id=?", (self.scope_id, _ref(study_id, "study_id"))).fetchone()

    def get(self, study_id):
        with self.queue._connect() as db:
            return self._decode(self._row(db, study_id))

    def list(self, limit=50):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("invalid study limit")
        with self.queue._connect() as db:
            return [self._decode(row) for row in db.execute("SELECT * FROM factor_studies WHERE scope_id=? ORDER BY rowid DESC LIMIT ?", (self.scope_id, limit)).fetchall()]

    def create(self, study_id, spec, *, validation_key=None):
        study_id = _ref(study_id, "study_id")
        validation_key = _ref(validation_key or study_id, "validation_key")
        if not isinstance(spec, dict) or not re.fullmatch(r"[0-9a-f]{64}", spec.get("snapshot_ref", "")):
            raise ValueError("study snapshot reference is invalid")
        reference = stable_digest({"scope_id": self.scope_id, "spec": spec})
        with self.queue._connect() as db:
            db.execute("INSERT OR IGNORE INTO factor_studies (scope_id,study_id,spec,spec_ref,state,validation_key) VALUES (?,?,?,?,'DECLARED',?)", (self.scope_id, study_id, json.dumps(spec, ensure_ascii=False, sort_keys=True, allow_nan=False), reference, validation_key))
            row = self._decode(self._row(db, study_id))
            if row["spec_ref"] != reference or row["validation_key"] != validation_key:
                raise ValueError("immutable study idempotency conflict")
        return self.get(study_id)

    def set_job(self, study_id, phase, job_id):
        if phase not in {"validation", "test"}:
            raise ValueError("invalid study phase")
        _ref(job_id, "job_id")
        with self.queue._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._decode(self._row(db, study_id))
            key = f"{phase}_job_id"
            allowed = {"DECLARED", "VALIDATING"} if phase == "validation" else {"TEST_RESERVED", "TESTING"}
            if row[key] is not None:
                if row[key] != job_id:
                    raise ValueError("study phase already owns another job")
            elif row["state"] not in allowed:
                raise ValueError("study phase cannot start from this state")
            else:
                db.execute(f"UPDATE factor_studies SET {key}=?,state=? WHERE scope_id=? AND study_id=?", (job_id, "VALIDATING" if phase == "validation" else "TESTING", self.scope_id, study_id))
        return self.get(study_id)

    def complete(self, study_id, phase, job_id, *, succeeded):
        if phase not in {"validation", "test"}:
            raise ValueError("invalid study phase")
        with self.queue._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._decode(self._row(db, study_id))
            if row[f"{phase}_job_id"] != job_id:
                raise ValueError("study completion belongs to another job")
            running = "VALIDATING" if phase == "validation" else "TESTING"
            target = ("VALIDATED" if succeeded else "VALIDATION_FAILED") if phase == "validation" else ("TEST_EVALUATED" if succeeded else "TEST_FAILED")
            if row["state"] == running:
                db.execute("UPDATE factor_studies SET state=? WHERE scope_id=? AND study_id=?", (target, self.scope_id, study_id))
        return self.get(study_id)

    def freeze(self, study_id, candidate_id, candidate_fingerprint):
        _ref(candidate_id, "candidate_id")
        if not isinstance(candidate_fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", candidate_fingerprint):
            raise ValueError("frozen candidate fingerprint is invalid")
        with self.queue._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._decode(self._row(db, study_id))
            if row["state"] == "FROZEN" and row["candidate_id"] == candidate_id and row["candidate_fingerprint"] == candidate_fingerprint:
                pass
            elif row["state"] != "VALIDATED":
                raise ValueError("candidate freeze requires completed validation")
            else:
                db.execute("UPDATE factor_studies SET candidate_id=?,candidate_fingerprint=?,state='FROZEN' WHERE scope_id=? AND study_id=?", (candidate_id, candidate_fingerprint, self.scope_id, study_id))
        return self.get(study_id)

    def reserve_test(self, study_id, key):
        _ref(key, "test_key")
        with self.queue._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._decode(self._row(db, study_id))
            if row["test_key"] is not None:
                if row["test_key"] != key:
                    raise ValueError("test is once-only and already reserved/consumed")
            else:
                if row["state"] != "FROZEN":
                    raise ValueError("test requires a frozen candidate")
                start, end = [pd.Timestamp(value) for value in row["spec"]["test_period"]]
                if any(pd.isna(value) or value.tzinfo is None for value in (start, end)) or start >= end:
                    raise ValueError("test period requires ordered timezone-aware boundaries")
                instruments = row["spec"].get("panel_contract", {}).get("instrument_ids", [row["spec"]["instrument"]])
                instrument = row["spec"]["instrument"]
                # A panel holdout consumes each asset, including overlap with legacy studies.
                for asset in instruments:
                    conflict = db.execute("SELECT 1 FROM factor_test_claims WHERE scope_id=? AND instrument=? AND start_ns<=? AND end_ns>=? LIMIT 1", (self.scope_id, asset, end.value, start.value)).fetchone()
                    if conflict is not None:
                        raise ValueError("overlapping test labels were already consumed")
                for asset in instruments:
                    claim_id = study_id if asset == instrument else study_id + ":" + asset
                    db.execute("INSERT INTO factor_test_claims VALUES (?,?,?,?,?)", (self.scope_id, claim_id, asset, start.value, end.value))
                db.execute("UPDATE factor_studies SET test_key=?,state='TEST_RESERVED' WHERE scope_id=? AND study_id=?", (key, self.scope_id, study_id))
        return self.get(study_id)

    def review(self, study_id, decision, reviewer_id, *, connection=None):
        if decision not in {"APPROVE_FOR_RESEARCH", "REJECT"}:
            raise ValueError("explicit research review decision is required")
        _ref(reviewer_id, "reviewer_id")
        with (nullcontext(connection) if connection is not None else self.queue._connect()) as db:
            if connection is None:
                db.execute("BEGIN IMMEDIATE")
            row = self._decode(self._row(db, study_id))
            if row["state"] not in {"TEST_EVALUATED", "REVIEWED"} or row["review_decision"] not in {None, decision} or row["reviewer_id"] not in {None, reviewer_id}:
                raise ValueError("review requires a completed test and is immutable")
            db.execute("UPDATE factor_studies SET state='REVIEWED',review_decision=?,reviewer_id=? WHERE scope_id=? AND study_id=?", (decision, reviewer_id, self.scope_id, study_id))
            return self._decode(self._row(db, study_id))

"""Append-only paper decision evidence in the existing research queue database."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping

from finahinking.data.panel_contracts import CURRENCIES, instrument, panel_time, reference

from .contracts import stable_digest
from .job_queue import _ref

ORIGINS = ("AUTO_SCAN", "QUANT_BASELINE", "USER", "MODEL", "PUBLIC_METHOD")
STAGES = ("WATCH", "CANDIDATE", "DECISION")
HORIZONS = (1, 5, 20, 60)
_ENTRY_FIELDS = {
    "pool_ref", "instrument", "decision_origin", "stage", "decision_at", "available_at",
    "policy_ref", "snapshot_ref", "signal_fingerprint", "source_ref",
}
_MAX_ENTRIES = 2048


def _basis(snapshot):
    provenance = snapshot.provenance
    units = provenance.get("units", {})
    return {
        "price_basis": provenance.get("adjustment", "UNKNOWN"),
        "currency": units.get("price_currency") if isinstance(units, Mapping) else None,
        "connection_id": provenance.get("connection_id"),
        "provider": provenance.get("provider"),
        "data_mode": snapshot.mode,
    }


def _eligible(row, cutoff):
    return row is not None and row.suspended is False and row.volume > 0 and row.available_at_time(cutoff)


class ResearchPoolStore:
    """Persist source associations, frozen cohorts and comparable mark receipts."""

    def __init__(self, queue, *, scope_id):
        self.queue = queue
        self.scope_id = _ref(scope_id, "scope_id")
        with queue._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS phase26_pool_entries (
                    scope_id TEXT NOT NULL, entry_ref TEXT NOT NULL, sample_ref TEXT NOT NULL,
                    pool_ref TEXT NOT NULL, decision_at TEXT NOT NULL, origin TEXT NOT NULL,
                    stage INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY (scope_id, entry_ref)
                );
                CREATE INDEX IF NOT EXISTS phase26_pool_decisions
                    ON phase26_pool_entries(scope_id, pool_ref, decision_at);
                CREATE INDEX IF NOT EXISTS phase26_pool_samples
                    ON phase26_pool_entries(scope_id, sample_ref, origin, stage);
                CREATE TABLE IF NOT EXISTS phase26_pool_cohorts (
                    scope_id TEXT NOT NULL, cohort_ref TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY (scope_id, cohort_ref)
                );
                CREATE TABLE IF NOT EXISTS phase26_pool_calendars (
                    scope_id TEXT NOT NULL, cohort_ref TEXT NOT NULL, ordinal INTEGER NOT NULL,
                    target_at TEXT NOT NULL, PRIMARY KEY (scope_id, cohort_ref, ordinal)
                );
                CREATE TABLE IF NOT EXISTS phase26_pool_marks (
                    scope_id TEXT NOT NULL, cohort_ref TEXT NOT NULL, horizon INTEGER NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY (scope_id, cohort_ref, horizon)
                );
            """)

    def record_pool_entry(self, values, *, snapshot):
        """Accept a server-bound decision and its original price snapshot."""
        if not isinstance(values, Mapping) or set(values) != _ENTRY_FIELDS:
            raise ValueError("invalid research pool entry")
        entry = {key: _ref(values[key], key) for key in _ENTRY_FIELDS - {"decision_at", "available_at"}}
        instrument(entry["instrument"])
        reference(entry["signal_fingerprint"])
        if entry["decision_origin"] not in ORIGINS or entry["stage"] not in STAGES:
            raise ValueError("invalid decision origin or stage")
        if entry["snapshot_ref"] != snapshot.fingerprint:
            raise ValueError("entry snapshot binding does not match")
        decision = panel_time(values["decision_at"])
        available = panel_time(values["available_at"])
        if available > decision:
            raise ValueError("entry must be available at decision time")
        entry.update(decision_at=decision.isoformat(), available_at=available.isoformat())

        rows = [row for row in snapshot.observations
                if row.instrument == entry["instrument"] and row.timestamp <= decision]
        baseline = rows[-1] if rows else None
        cutoff = min(decision, available, snapshot.as_of)
        if not _eligible(baseline, cutoff):
            raise ValueError("decision lacks an eligible baseline price available at decision time")
        entry.update(
            baseline_at=baseline.timestamp.isoformat(),
            baseline_close=baseline.close,
            baseline_ref=baseline.fingerprint,
            basis=_basis(snapshot),
        )
        economic_fields = ("pool_ref", "instrument", "decision_at", "signal_fingerprint")
        entry["sample_ref"] = stable_digest({
            "scope_id": self.scope_id, **{key: entry[key] for key in economic_fields},
        })
        entry["entry_ref"] = stable_digest(entry)
        return self._append_entry(entry)

    def _append_entry(self, entry):
        scope = self.scope_id
        stage = STAGES.index(entry["stage"])
        with self.queue._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM phase26_pool_entries WHERE scope_id=? AND entry_ref=?",
                          (scope, entry["entry_ref"])).fetchone():
                return entry
            peers = db.execute(
                """SELECT origin, stage, payload FROM phase26_pool_entries
                   WHERE scope_id=? AND sample_ref=? ORDER BY rowid""",
                (scope, entry["sample_ref"]),
            ).fetchall()
            if peers:
                original = json.loads(peers[0]["payload"])
                fields = ("policy_ref", "baseline_at", "baseline_close", "basis")
                if any(entry[key] != original[key] for key in fields):
                    raise ValueError("economic sample baseline and policy are immutable")
            previous = max((peer["stage"] for peer in peers
                            if peer["origin"] == entry["decision_origin"]), default=None)
            if previous is not None and not previous <= stage <= previous + 1:
                raise ValueError("origin stage may advance only one step")
            count = db.execute(
                """SELECT count(*) FROM phase26_pool_entries
                   WHERE scope_id=? AND pool_ref=? AND decision_at=?""",
                (scope, entry["pool_ref"], entry["decision_at"]),
            ).fetchone()[0]
            if count >= _MAX_ENTRIES:
                raise ValueError("research pool decision exceeds the entry limit")
            db.execute(
                "INSERT INTO phase26_pool_entries VALUES (?,?,?,?,?,?,?,?)",
                (scope, entry["entry_ref"], entry["sample_ref"], entry["pool_ref"],
                 entry["decision_at"], entry["decision_origin"], stage, json.dumps(entry)),
            )
        return entry

    def list_entries(self, pool_ref, *, limit=100, offset=0):
        _ref(pool_ref, "pool_ref")
        if type(limit) is not int or type(offset) is not int or not 1 <= limit <= 200 or offset < 0:
            raise ValueError("invalid research pool pagination")
        with self.queue._connect() as db:
            rows = db.execute(
                """SELECT payload FROM phase26_pool_entries WHERE scope_id=? AND pool_ref=?
                   ORDER BY rowid DESC LIMIT ? OFFSET ?""",
                (self.scope_id, pool_ref, limit, offset),
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def freeze_cohort(self, pool_ref, *, decision_at):
        _ref(pool_ref, "pool_ref")
        decision_at = panel_time(decision_at).isoformat()
        identity = {"scope_id": self.scope_id, "pool_ref": pool_ref, "decision_at": decision_at}
        cohort_ref = stable_digest(identity)
        with self.queue._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT payload FROM phase26_pool_cohorts WHERE scope_id=? AND cohort_ref=?",
                (self.scope_id, cohort_ref),
            ).fetchone()
            if existing:
                return json.loads(existing["payload"])
            entries = db.execute(
                """SELECT payload FROM phase26_pool_entries
                   WHERE scope_id=? AND pool_ref=? AND decision_at=? AND stage=?
                   ORDER BY rowid""",
                (self.scope_id, pool_ref, decision_at, STAGES.index("DECISION")),
            ).fetchall()
            if not entries:
                raise ValueError("no decisions available for this cohort")
            members = {}
            for entry in entries:
                record = json.loads(entry["payload"])
                sample = members.setdefault(record["sample_ref"], {
                    "sample_ref": record["sample_ref"], "instrument": record["instrument"],
                    "signal_fingerprint": record["signal_fingerprint"],
                    "policy_ref": record["policy_ref"], "snapshot_ref": record["snapshot_ref"],
                    "baseline_at": record["baseline_at"], "baseline_close": record["baseline_close"],
                    "basis": record["basis"], "origins": [], "entry_refs": [],
                })
                sample["entry_refs"].append(record["entry_ref"])
                if record["decision_origin"] not in sample["origins"]:
                    sample["origins"].append(record["decision_origin"])
            samples = list(members.values())
            first = samples[0]
            if any(sample[key] != first[key] for sample in samples
                   for key in ("policy_ref", "basis", "baseline_at")):
                raise ValueError("cohort decisions must share baseline time, policy and price basis")
            if len(samples) > 256:
                raise ValueError("research cohort exceeds the sample limit")
            cohort = {
                **identity, "cohort_ref": cohort_ref, "samples": samples, "status": "FROZEN",
                "paper_only": True, "horizon_basis": "COMMON_OBSERVATION_TIMESTAMPS",
            }
            cohort["fingerprint"] = stable_digest(cohort)
            db.execute("INSERT INTO phase26_pool_cohorts VALUES (?,?,?)",
                       (self.scope_id, cohort_ref, json.dumps(cohort)))
        return cohort

    def get_cohort(self, cohort_ref):
        reference(cohort_ref)
        with self.queue._connect() as db:
            row = db.execute(
                "SELECT payload FROM phase26_pool_cohorts WHERE scope_id=? AND cohort_ref=?",
                (self.scope_id, cohort_ref),
            ).fetchone()
        if row is None:
            raise KeyError("research cohort not found")
        cohort = json.loads(row["payload"])
        if stable_digest({key: value for key, value in cohort.items() if key != "fingerprint"}) != cohort["fingerprint"]:
            raise ValueError("research cohort integrity check failed")
        return cohort

    def observe_pool(self, cohort_ref, snapshot, horizons=HORIZONS):
        if (not isinstance(horizons, (list, tuple)) or not 1 <= len(horizons) <= len(HORIZONS)
                or any(type(h) is not int or h not in HORIZONS for h in horizons)
                or len(set(horizons)) != len(horizons)):
            raise ValueError("use distinct horizons selected from 1, 5, 20 and 60")
        cohort = self.get_cohort(cohort_ref)
        assets = {sample["instrument"] for sample in cohort["samples"]}
        decision = panel_time(cohort["decision_at"])
        grid = {(row.instrument, row.timestamp): row for row in snapshot.observations
                if row.instrument in assets and row.timestamp <= snapshot.as_of}
        # Use the shared union of observed times. A missing or suspended asset
        # at a frozen target is a coverage gap and cannot advance the horizon.
        times = sorted({timestamp for _, timestamp in grid if timestamp > decision})
        results = []
        with self.queue._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            reason = self._baseline_failure(cohort, snapshot, grid)
            if reason is None and not self._extend_calendar(db, cohort_ref, times):
                reason = "FROZEN_OBSERVATION_GRID_MISMATCH"
            for horizon in sorted(horizons):
                results.append(self._observe_horizon(db, cohort, snapshot, grid, horizon, reason))
        return {
            "cohort_ref": cohort_ref, "horizons": results, "paper_only": True,
            "return_basis": "UNADJUSTED_CLOSE_CHANGE_EXCLUDING_ACTIONS_AND_COSTS",
            "settlement_eligible": False, "policy_updates": [],
            "review_suggestions": [
                {"kind": "REVIEW_OBSERVATION", "cohort_ref": cohort_ref,
                 "horizon": row["horizon"], "observation_ref": row["fingerprint"]}
                for row in results if row["status"] == "READY"
            ],
        }

    @staticmethod
    def _baseline_failure(cohort, snapshot, grid):
        basis = _basis(snapshot)
        if (basis != cohort["samples"][0]["basis"] or basis["price_basis"] != "RAW"
                or basis["currency"] not in CURRENCIES):
            return "INCOMPATIBLE_PRICE_BASIS"
        decision = panel_time(cohort["decision_at"])
        for sample in cohort["samples"]:
            baseline = grid.get((sample["instrument"], panel_time(sample["baseline_at"])))
            if not _eligible(baseline, decision) or baseline.close != sample["baseline_close"]:
                return "FROZEN_BASELINE_MISMATCH"
        return None

    def _extend_calendar(self, db, cohort_ref, times):
        """Freeze the observed prefix so different horizons cannot pick different grids."""
        key = (self.scope_id, cohort_ref)
        calendar = [row["target_at"] for row in db.execute(
            """SELECT target_at FROM phase26_pool_calendars
               WHERE scope_id=? AND cohort_ref=? ORDER BY ordinal""", key,
        )]
        incoming = [stamp.isoformat() for stamp in times[:max(HORIZONS)]]
        overlap = min(len(incoming), len(calendar))
        if incoming[:overlap] != calendar[:overlap]:
            return False
        db.executemany(
            "INSERT INTO phase26_pool_calendars VALUES (?,?,?,?)",
            ((*key, index, stamp) for index, stamp in enumerate(incoming, 1) if index > len(calendar)),
        )
        return True

    def _observe_horizon(self, db, cohort, snapshot, grid, horizon, reason):
        key = (self.scope_id, cohort["cohort_ref"], horizon)
        existing = db.execute(
            "SELECT payload FROM phase26_pool_marks WHERE scope_id=? AND cohort_ref=? AND horizon=?",
            key,
        ).fetchone()
        if existing:
            result = json.loads(existing["payload"])
            if stable_digest({k: v for k, v in result.items() if k != "fingerprint"}) != result["fingerprint"]:
                raise ValueError("research observation integrity check failed")
            return result
        target_row = db.execute(
            "SELECT target_at FROM phase26_pool_calendars WHERE scope_id=? AND cohort_ref=? AND ordinal=?",
            key,
        ).fetchone()
        failure = {
            "horizon": horizon, "status": "NEEDS_DATA", "samples": [],
            "target_at": None if target_row is None else target_row["target_at"],
        }
        if reason:
            return {**failure, "reason": reason}
        if target_row is None:
            return {**failure, "reason": "INSUFFICIENT_OBSERVATIONS"}
        target = panel_time(target_row["target_at"])
        cutoff = min(target, snapshot.as_of)
        marks = []
        for sample in cohort["samples"]:
            mark = grid.get((sample["instrument"], target))
            if not _eligible(mark, cutoff):
                return {**failure, "reason": "INCOMPLETE_OR_SUSPENDED_COMMON_MARK"}
            price_return = mark.close / sample["baseline_close"] - 1
            if not math.isfinite(price_return):
                return {**failure, "reason": "NONFINITE_PRICE_RETURN"}
            marks.append({
                "sample_ref": sample["sample_ref"], "instrument": sample["instrument"],
                "mark_ref": mark.fingerprint, "mark_close": mark.close, "price_return": price_return,
                "origins": sample["origins"],
            })
        result = {
            **failure, "status": "READY", "samples": marks, "snapshot_ref": snapshot.fingerprint,
            "observed_as_of": snapshot.as_of.isoformat(), "unique_sample_count": len(marks),
        }
        result["fingerprint"] = stable_digest(result)
        db.execute("INSERT INTO phase26_pool_marks VALUES (?,?,?,?)", (*key, json.dumps(result)))
        return result

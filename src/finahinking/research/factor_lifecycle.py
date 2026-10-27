"""Scope-bound review events in the existing queue, with derived lifecycle views."""

from __future__ import annotations

import json
import re
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from .contracts import _public_identifier, stable_digest

_HASH = re.compile(r"^[0-9a-f]{64}$")
_SCOPE = ("factor_ref", "market", "universe_ref", "purpose", "policy_fingerprint")
_FINAL = {"APPROVE_FOR_RESEARCH", "REJECT"}


@dataclass(frozen=True, slots=True)
class FactorUseDecision:
    factor_ref: str
    market: str
    universe_ref: str
    purpose: str
    policy_fingerprint: str
    evaluation_fingerprint: str
    reviewer: str
    decision: str
    idempotency_key: str
    run_ref: str

    def __post_init__(self):
        for key in ("factor_ref", "universe_ref", "policy_fingerprint", "evaluation_fingerprint"):
            value = getattr(self, key)
            if not isinstance(value, str) or not _HASH.fullmatch(value):
                raise ValueError("factor use requires complete fingerprints")
        for key in ("market", "reviewer", "idempotency_key", "run_ref"):
            object.__setattr__(self, key, _public_identifier(getattr(self, key), key))
        if self.market != self.market.upper() or len(self.market) > 32:
            raise ValueError("factor use market must be explicit")
        if self.purpose not in {"FORMULA_RESEARCH", "PAPER_RESEARCH", "PAPER_SCREENING"}:
            raise ValueError("factor use purpose is unsupported")
        if self.decision not in {*_FINAL, "OBSERVE", "MONITOR", "RETIRE"}:
            raise ValueError("explicit factor use decision is required")

    def to_dict(self):
        return asdict(self)


def _match(decision, evidence):
    values = evidence.to_dict()
    row = next((row for row in values["local_runs"] if row.get("evaluation_fingerprint") == decision.evaluation_fingerprint
                and row["run_ref"] == decision.run_ref), None)
    if row is None or not row.get("definition_matches") or row.get("factor_ref") != decision.factor_ref:
        raise ValueError("factor use requires the exact verified definition and evaluation")
    universe = row["scope"]["universe"]
    if (universe["market"] != decision.market or stable_digest(universe) != decision.universe_ref
            or row["policy_fingerprint"] != decision.policy_fingerprint):
        raise ValueError("factor use scope does not match evaluation")
    if row["reproduction_scope"] == "FORMULA_FIXTURE" and decision.purpose != "FORMULA_RESEARCH":
        raise ValueError("formula fixture cannot approve investment research use")
    if decision.decision in _FINAL:
        if (row["status"] != "COMPLETE" or row["phase"] != "test" or not row.get("settled")
                or len(row["stages"]) != 9 or any(stage["status"] != "COMPLETE" for stage in row["stages"])):
            raise ValueError("factor use review requires a completed frozen final test")
        if (decision.decision == "APPROVE_FOR_RESEARCH"
                and row["local_metric"]["archive_decision"].get("decision") != "PENDING_HUMAN_RESEARCH_REVIEW"):
            raise ValueError("observation-only or rejected evidence cannot be approved")
    return row


class FactorUseStore:
    def __init__(self, queue, *, scope_id):
        self.queue = queue
        self.scope_id = _public_identifier(scope_id, "scope_id")
        with queue._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS factor_use_events (
                scope_id TEXT NOT NULL, event_ref TEXT NOT NULL, factor_ref TEXT NOT NULL,
                use_scope TEXT NOT NULL, evaluation_fingerprint TEXT NOT NULL, decision TEXT NOT NULL,
                payload TEXT NOT NULL, payload_digest TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(scope_id,event_ref))""")
            db.execute("CREATE INDEX IF NOT EXISTS factor_use_scope ON factor_use_events(scope_id,use_scope)")

    def _decode(self, row):
        try:
            return self._verified(row)
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError("factor use event integrity failure") from exc

    def _verified(self, row):
        payload = json.loads(row["payload"])
        decision = FactorUseDecision(**payload)
        scope = stable_digest({key: payload[key] for key in _SCOPE})
        event_ref = "factor-use-" + stable_digest([self.scope_id, decision.idempotency_key])
        if (row["scope_id"] != self.scope_id or row["event_ref"] != event_ref or row["use_scope"] != scope
                or row["factor_ref"] != decision.factor_ref or row["evaluation_fingerprint"] != decision.evaluation_fingerprint
                or row["decision"] != decision.decision or row["payload_digest"] != stable_digest([self.scope_id, payload])):
            raise ValueError("factor use event integrity failure")
        return {"event_ref": event_ref, "sequence": row["sequence"], "scope_id": self.scope_id,
                "created_at": row["created_at"], "decision": payload}

    def list(self, *, factor_ref=None, limit=200):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("factor use pagination is invalid")
        if factor_ref is not None and (not isinstance(factor_ref, str) or not _HASH.fullmatch(factor_ref)):
            raise ValueError("factor use reference is invalid")
        where = "scope_id=?" + (" AND factor_ref=?" if factor_ref else "")
        with self.queue._connect() as db:
            rows = db.execute(f"SELECT rowid AS sequence,* FROM factor_use_events WHERE {where} ORDER BY rowid DESC LIMIT ?",
                              (self.scope_id, *((factor_ref,) if factor_ref else ()), limit)).fetchall()
            return [self._decode(row) for row in reversed(rows)]

    def record(self, decision, *, evidence, connection=None):
        if not isinstance(decision, FactorUseDecision):
            raise TypeError("typed explicit use review is required")
        if decision.decision != "RETIRE":
            _match(decision, evidence)
        payload = decision.to_dict()
        event_ref = "factor-use-" + stable_digest([self.scope_id, decision.idempotency_key])
        use_scope = stable_digest({key: payload[key] for key in _SCOPE})
        with (nullcontext(connection) if connection is not None else self.queue._connect()) as db:
            if connection is None:
                db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT rowid AS sequence,* FROM factor_use_events WHERE scope_id=? AND (event_ref=? OR use_scope=?) ORDER BY rowid",
                              (self.scope_id, event_ref, use_scope)).fetchall()
            history = [self._decode(row) for row in rows]
            for old in history:
                previous = old["decision"]
                if old["event_ref"] == event_ref:
                    if previous != payload:
                        raise ValueError("factor use idempotency conflict")
                    return old
                if (decision.decision in _FINAL and previous["decision"] in _FINAL
                        and previous["evaluation_fingerprint"] == decision.evaluation_fingerprint):
                    if {key: value for key, value in previous.items() if key != "idempotency_key"} != {
                            key: value for key, value in payload.items() if key != "idempotency_key"}:
                        raise ValueError("factor evaluation already has an immutable review")
                    return old
            active = next((row["decision"] for row in reversed(history)
                           if row["decision"]["decision"] in {*_FINAL, "MONITOR", "RETIRE"}), None)
            if decision.decision in {"MONITOR", "RETIRE"} and (active is None or active["decision"] not in {"APPROVE_FOR_RESEARCH", "MONITOR"}):
                raise ValueError("monitoring or retirement requires an active scoped approval")
            created = datetime.now(UTC).isoformat()
            db.execute("INSERT INTO factor_use_events VALUES (?,?,?,?,?,?,?,?,?)", (
                self.scope_id, event_ref, decision.factor_ref, use_scope, decision.evaluation_fingerprint, decision.decision,
                json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True), stable_digest([self.scope_id, payload]), created))
            row = db.execute("SELECT rowid AS sequence,* FROM factor_use_events WHERE scope_id=? AND event_ref=?", (self.scope_id, event_ref)).fetchone()
            return self._decode(row)


@dataclass(frozen=True, slots=True)
class FactorLifecycleView:
    _json: str

    def to_dict(self):
        return json.loads(self._json)


def project_factor_lifecycle(evidence, decisions) -> FactorLifecycleView:
    values = evidence.to_dict()
    local = [row for row in values["local_runs"] if row.get("definition_matches")]
    scopes = {}
    allowed = {row.get("factor_ref") for row in local}
    if values["factor_ref"] != values["factor_id"]:
        allowed = {values["factor_ref"]}
    history = sorted((row for row in decisions if row["decision"]["factor_ref"] in allowed),
                     key=lambda row: row["sequence"])
    for event in history:
        decision = FactorUseDecision(**event["decision"])
        key = stable_digest({name: event["decision"][name] for name in _SCOPE})
        try:
            if decision.decision != "RETIRE":
                _match(decision, evidence)
            state = {"APPROVE_FOR_RESEARCH": "RESEARCH_APPROVED", "REJECT": "REJECTED", "OBSERVE": "OBSERVATION",
                     "MONITOR": "MONITORING", "RETIRE": "RETIRED"}[decision.decision]
        except (ValueError, KeyError, TypeError):
            state = "OBSERVATION"
        scopes[key] = {**{name: event["decision"][name] for name in _SCOPE}, "state": state,
                       "evaluation_fingerprint": decision.evaluation_fingerprint, "event_ref": event["event_ref"]}
    states = {scope["state"] for scope in scopes.values()}
    state = next((value for value in ("MONITORING", "RESEARCH_APPROVED", "OBSERVATION", "REJECTED", "RETIRED") if value in states), None)
    if state is None:
        state = "VALIDATING" if any(row["status"] == "COMPLETE" for row in local) else (
            "IMPLEMENTED" if values["definition"].get("recipe_id") else
            "DEFINED" if values["definition"].get("definition_status") != "UNRESOLVED" else "SOURCE_ONLY")
    return FactorLifecycleView(json.dumps({"schema_version": "factor-lifecycle.v1", "factor_id": values["factor_id"],
        "state": state, "use_scopes": list(scopes.values()), "history": history,
        "financial_falsification": False, "production_write": False,
        "limitations": ["Approval is restricted to its exact formula, market, universe, purpose and policy.",
                        "A formula fixture cannot approve paper screening or real investment use."]}, ensure_ascii=False, sort_keys=True))


def use_is_approved(view, **scope):
    if set(scope) != set(_SCOPE):
        return False
    return any(all(item[key] == value for key, value in scope.items()) and item["state"] in {"RESEARCH_APPROVED", "MONITORING"}
               for item in view["use_scopes"])

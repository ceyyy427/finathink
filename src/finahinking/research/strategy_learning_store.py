"""Immutable strategy audit, decision and replay evidence in the local queue database."""

from .contracts import stable_digest
from .learning_review import _decode, _json


class StrategyLearningStore:
    def __init__(self, app):
        self.queue, self.scope = app.queue, app.scope_id
        with self.queue._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS strategy_learning_records (
                    scope_id TEXT NOT NULL, kind TEXT NOT NULL, record_id TEXT NOT NULL,
                    payload TEXT NOT NULL, payload_digest TEXT NOT NULL,
                    PRIMARY KEY(scope_id,kind,record_id));
                CREATE TABLE IF NOT EXISTS strategy_learning_claims (
                    scope_id TEXT NOT NULL, sample_ref TEXT NOT NULL, proposal_id TEXT NOT NULL,
                    PRIMARY KEY(scope_id,sample_ref));
            """)

    def find(self, db, kind, key):
        row = db.execute(
            "SELECT * FROM strategy_learning_records WHERE scope_id=? AND kind=? AND record_id=?",
            (self.scope, kind, key),
        ).fetchone()
        if row is None:
            return None
        payload = _decode(row)
        if payload["scope_id"] != self.scope or payload["record_id"] != key or payload["kind"] != kind:
            raise ValueError("策略学习记录身份校验失败")
        return payload["value"]

    def get(self, kind, key):
        with self.queue._connect() as db:
            value = self.find(db, kind, key)
        if value is None:
            raise KeyError("策略学习记录不存在")
        return value

    def put(self, db, kind, key, value):
        prior = self.find(db, kind, key)
        if prior is not None:
            if prior != value:
                raise ValueError("策略学习证据已冻结，写入内容与原记录不同")
            return prior
        payload = {"scope_id": self.scope, "kind": kind, "record_id": key, "value": value}
        db.execute("INSERT INTO strategy_learning_records VALUES (?,?,?,?,?)",
                   (self.scope, kind, key, _json(payload), stable_digest(payload)))
        return value

    def list(self, kind):
        with self.queue._connect() as db:
            rows = db.execute(
                "SELECT record_id FROM strategy_learning_records WHERE scope_id=? AND kind=? ORDER BY rowid DESC LIMIT 100",
                (self.scope, kind),
            ).fetchall()
            return [self.find(db, kind, row[0]) for row in rows]

    def claimed(self, sample_refs):
        with self.queue._connect() as db:
            return {row[0] for row in db.execute(
                """SELECT c.sample_ref FROM strategy_learning_claims c JOIN json_each(?) r ON r.value=c.sample_ref
                   WHERE c.scope_id=?""", (_json(sorted(sample_refs)), self.scope),
            )}

    def claim(self, db, sample_refs, proposal_id):
        for ref in sample_refs:
            prior = db.execute(
                "SELECT proposal_id FROM strategy_learning_claims WHERE scope_id=? AND sample_ref=?",
                (self.scope, ref),
            ).fetchone()
            if prior is not None and prior[0] != proposal_id:
                raise ValueError("经济窗口已经进入另一提案，请读取已有审核或观察新的成熟窗口")
            db.execute("INSERT OR IGNORE INTO strategy_learning_claims VALUES (?,?,?)",
                       (self.scope, ref, proposal_id))

"""A rebuildable SQLite FTS5 projection of reviewed public knowledge only."""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
import unicodedata
from dataclasses import asdict
from pathlib import Path

from .knowledge_contracts import (
    DOMAINS,
    KnowledgeItem,
    KnowledgeSource,
    identifier,
    references,
    text,
)
from .knowledge_examples import evaluate_example

INDEX_VERSION = "knowledge-fts5-bigram.v1"
# Fixed before the held-out evaluation: title, aliases, summary, explanation.
FIELD_WEIGHTS = (6.0, 4.0, 2.0, 1.0)
CACHE_APPLICATION_ID = 0x46544B42
_META_SQL = "CREATE TABLE knowledge_meta (fingerprint TEXT NOT NULL)"
_FTS_SQL = "CREATE VIRTUAL TABLE knowledge_fts USING fts5(item_id UNINDEXED, title, aliases, summary, explanation, tokenize='unicode61')"
_CACHE_TABLES = {"knowledge_meta", "knowledge_fts", "knowledge_fts_data", "knowledge_fts_idx", "knowledge_fts_content", "knowledge_fts_docsize", "knowledge_fts_config"}


class KnowledgeIndexUnavailable(RuntimeError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def tokenize(value):
    normalized = unicodedata.normalize("NFKC", value).casefold()
    tokens = []
    for word in re.findall(r"[\u3400-\u9fff]+|[a-z0-9]+|[\u0370-\u03ff]+", normalized):
        if "\u3400" <= word[0] <= "\u9fff" and len(word) > 1:
            tokens.extend(word[i:i + 2] for i in range(len(word) - 1))
        else:
            tokens.append(word)
    return tokens


def _records(rows, key, limit):
    if not isinstance(rows, (list, tuple)) or len(rows) > limit:
        raise ValueError("invalid bounded knowledge collection")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError("invalid knowledge record")
        value = identifier(row.get(key))
        if value in result:
            raise ValueError("duplicate knowledge record")
        result[value] = json.loads(_json(row))
    return result


class KnowledgeIndex:
    @classmethod
    def from_catalogs(cls, basic, advanced, assets, content, *, index_path=None):
        self = cls()
        self._lock = threading.RLock()
        if (not isinstance(content, dict) or content.get("schema_version") != "quant-knowledge.v1"
                or set(content) - {"schema_version", "version", "items", "sources", "formulas", "misconceptions", "examples", "paths"}):
            raise ValueError("invalid public knowledge bundle")
        text(content.get("version"), "bundle version", 128)
        raw_items = _records(content.get("items"), "canonical_id", 2000)
        self._items = {key: KnowledgeItem.from_dict(value) for key, value in raw_items.items()}
        raw_sources = _records(content.get("sources"), "source_ref", 1000)
        try:
            self._sources = {key: KnowledgeSource(**value).to_dict() for key, value in raw_sources.items()}
        except TypeError as exc:
            raise ValueError("invalid source contract") from exc
        for source in self._sources.values():
            ref = assets.get_resource(source["license_resource_id"])
            if ref.kind != "license":
                raise ValueError("knowledge citation needs a license record")
            assets.read_text(ref.resource_id)
        self._formulas = _records(content.get("formulas"), "formula_id", 2000)
        self._counterexamples = _records(content.get("misconceptions"), "counterexample_id", 2000)
        self._examples = _records(content.get("examples"), "example_id", 2000)
        self._paths = _records(content.get("paths"), "path_id", 256)
        self._validate_public_records()
        self._methods = {s.skill_id: asdict(s) for s in assets.skills()}
        self._aliases, self._legacy, self._terms = {}, {}, {}
        for key, item in self._items.items():
            self._alias(key, key)
        for key, item in self._items.items():
            for alias in item.aliases:
                self._alias(alias, key)
            for refs, targets in ((item.prerequisites, self._items), (item.source_refs, self._sources),
                                  (item.formula_refs, self._formulas), (item.counterexample_refs, self._counterexamples),
                                  (item.example_refs, self._examples), (item.method_refs, self._methods),
                                  (item.code_refs, self._sources)):
                if not set(refs) <= targets.keys():
                    raise ValueError(f"unknown reference in knowledge {key}")
            if item.review_status == "REVIEWED" and any(self._items[p].review_status != "REVIEWED" for p in item.prerequisites):
                raise ValueError("reviewed knowledge depends on an unreviewed prerequisite")
        self._check_cycles()
        for name, catalog, mapping in (("p7_5", basic, basic.canonical_ids()), ("p8_2b", advanced, advanced.canonical_ids())):
            for old, canonical in mapping.items():
                if canonical not in self._items:
                    raise ValueError("legacy knowledge must retain a canonical target")
                self._alias(old, canonical)
                self._alias(f"{name}:{old}", canonical)
                self._legacy.setdefault(canonical, []).append({"catalog": name, "item_id": old, "fingerprint": catalog.fingerprint})
        for path in basic.learning_paths:
            if path.path_id in self._paths:
                raise ValueError("legacy learning path cannot be overwritten")
            self._paths[path.path_id] = {"path_id": path.path_id, "title": path.title,
                "description": path.description, "item_ids": list(path.concept_ids),
                "path_type": path.path_type, "legacy": True}
        for path in self._paths.values():
            text(path.get("title"), "path title", 256)
            ids = references(path.get("item_ids"), "path items")
            if len(ids) < 2 or not set(ids) <= self._items.keys():
                raise ValueError("learning path requires known knowledge items")
            if not path.get("legacy"):
                positions = {key: n for n, key in enumerate(ids)}
                if any(parent in positions and positions[parent] >= positions[key] for key in ids for parent in self._items[key].prerequisites):
                    raise ValueError("learning path places a prerequisite after its dependent")
        for row in self._counterexamples.values():
            for name in ("claim", "correction", "scenario"):
                text(row.get(name), "counterexample")
            if not set(references(row.get("source_refs"), "counterexample sources")) <= self._sources.keys():
                raise ValueError("unknown counterexample source")
        # Every public input, including the old catalogs and file-rights records,
        # is part of the projection version. No personal DB is consulted.
        self.fingerprint = _digest({"index": INDEX_VERSION, "weights": FIELD_WEIGHTS,
            "basic": basic.fingerprint, "advanced": advanced.fingerprint,
            "assets": assets.knowledge_fingerprint(), "content": content})
        self._projection = []
        for key, item in sorted(self._items.items()):
            if item.review_status != "REVIEWED":
                continue
            context = " ".join((item.explanation, item.application, item.example_text, *item.assumptions, *item.tags,
                *(self._counterexamples[c]["correction"] for c in item.counterexample_refs)))
            values = (item.title, " ".join((key, *item.aliases)), item.summary, context)
            tokens = [tokenize(v) for v in values]
            self._terms[key] = dict(zip(("title", "aliases", "summary", "explanation"), map(set, tokens)))
            self._projection.append((key, *(" ".join(t) for t in tokens)))
        self._open(index_path)
        return self

    def _validate_public_records(self):
        contracts = (
            (self._formulas, {"formula_id", "expression", "meaning", "source_refs"}, ("expression", "meaning")),
            (self._counterexamples, {"counterexample_id", "claim", "correction", "scenario", "source_refs"}, ("claim", "correction", "scenario")),
            (self._examples, {"example_id", "item_id", "title", "operation", "inputs", "expected", "derivation", "source_refs", "tolerance"}, ("title", "derivation")),
            (self._paths, {"path_id", "title", "description", "item_ids", "path_type", "practice", "legacy"}, ("title", "description", "practice")),
        )
        for rows, fields, texts in contracts:
            for row in rows.values():
                if set(row) != fields:
                    raise ValueError("unknown or missing public knowledge record field")
                for field in texts:
                    text(row[field], field)
                if "source_refs" in fields and not set(references(row["source_refs"], "record sources")) <= self._sources.keys():
                    raise ValueError("unknown public knowledge source")
        for row in self._examples.values():
            if row["item_id"] not in self._items or row["example_id"] not in self._items[row["item_id"]].example_refs:
                raise ValueError("example must be linked to its canonical item")
            if type(row["tolerance"]) not in (float, int) or not 0 < row["tolerance"] <= 1e-8:
                raise ValueError("example tolerance is invalid")
            expected, actual = row["expected"], evaluate_example(row)
            if isinstance(expected, list) != isinstance(actual, list):
                raise ValueError("example expected shape differs")
            left = expected if isinstance(expected, list) else [expected]
            right = actual if isinstance(actual, list) else [actual]
            if len(left) != len(right) or any(type(a) not in (int, float) or not math.isfinite(a) or not math.isclose(a, b, rel_tol=row["tolerance"], abs_tol=row["tolerance"]) for a, b in zip(left, right)):
                raise ValueError("example differs from the authored expected value")
        for row in self._paths.values():
            if row["legacy"] is not False or row["path_type"] not in {"learning", "research"}:
                raise ValueError("new paths cannot impersonate legacy paths")

    def _alias(self, alias, canonical):
        text(alias, "alias", 256)
        alias = unicodedata.normalize("NFKC", alias).casefold()
        if alias in self._aliases and self._aliases[alias] != canonical:
            raise ValueError("canonical knowledge alias conflict")
        self._aliases[alias] = canonical

    def _check_cycles(self):
        visiting, visited = set(), set()

        def visit(key):
            if key in visiting:
                raise ValueError("knowledge prerequisite cycle")
            if key in visited:
                return
            visiting.add(key)
            for parent in self._items[key].prerequisites:
                visit(parent)
            visiting.remove(key)
            visited.add(key)

        for key in self._items:
            visit(key)

    def _open(self, path):
        if path is not None:
            path = Path(path)
            if path.is_symlink() or any(p.is_symlink() for p in path.parents):
                raise ValueError("knowledge projection path cannot be a symlink")
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path) if path is not None else ":memory:", timeout=30, check_same_thread=False)
        try:
            self._db.execute("BEGIN IMMEDIATE")
            schema = self._db.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
            app_id = self._db.execute("PRAGMA application_id").fetchone()[0]
            if schema:
                normalize = lambda sql: " ".join((sql or "").split()).casefold()
                definitions = {name: sql for _, name, sql in schema}
                if (app_id != CACHE_APPLICATION_ID or set(definitions) != _CACHE_TABLES
                        or any(kind != "table" for kind, _, _ in schema)
                        or normalize(definitions.get("knowledge_meta")) != normalize(_META_SQL)
                        or normalize(definitions.get("knowledge_fts")) != normalize(_FTS_SQL)):
                    raise ValueError("knowledge projection cannot share a private or foreign database")
            else:
                if app_id not in {0, CACHE_APPLICATION_ID}:
                    raise ValueError("knowledge projection cannot share a private or foreign database")
                self._db.execute(_META_SQL)
                self._db.execute(_FTS_SQL)
                self._db.execute(f"PRAGMA application_id = {CACHE_APPLICATION_ID}")
            prior = self._db.execute("SELECT fingerprint FROM knowledge_meta").fetchall()
            stored = self._db.execute("SELECT * FROM knowledge_fts ORDER BY item_id LIMIT 2001").fetchall()
            if prior != [(self.fingerprint,)] or stored != self._projection:
                self._db.execute("DELETE FROM knowledge_fts")
                self._db.executemany("INSERT INTO knowledge_fts VALUES (?,?,?,?,?)", self._projection)
                self._db.execute("DELETE FROM knowledge_meta")
                self._db.execute("INSERT INTO knowledge_meta VALUES (?)", (self.fingerprint,))
            self._db.commit()
        except Exception as exc:
            self._db.rollback()
            self._db.close()
            if isinstance(exc, sqlite3.OperationalError) and "no such module: fts5" in str(exc).lower():
                raise KnowledgeIndexUnavailable("当前 Python 的 SQLite 未启用 FTS5。请使用含 FTS5 的 Python 安装后重试。") from exc
            raise

    def close(self):
        with self._lock:
            self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def resolve(self, item_id):
        if not isinstance(item_id, str) or len(item_id) > 256:
            raise KeyError("knowledge item not found")
        key = self._aliases.get(unicodedata.normalize("NFKC", item_id).casefold())
        if key not in self._items or self._items[key].review_status != "REVIEWED":
            raise KeyError("knowledge item not found")
        item = self._items[key]
        result = {**item.to_dict(), "source_ref_ids": list(item.source_refs),
            "source_refs": [self._sources[s] for s in item.source_refs],
            "formulas": [self._formulas[s] for s in item.formula_refs],
            "counterexamples": [self._counterexamples[s] for s in item.counterexample_refs],
            "examples": [self._examples[s] for s in item.example_refs],
            "methods": [self._methods[s] for s in item.method_refs],
            "legacy_refs": self._legacy.get(key, []), "catalog_fingerprint": self.fingerprint}
        return json.loads(_json(result))

    def search(self, query, filters=None, limit=5):
        if not isinstance(query, str) or len(query) > 200 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("检索文字最多200字，每次最多100项。")
        filters = filters or {}
        if (not isinstance(filters, dict) or set(filters) - {"domain", "kind", "claim_kind", "source_id"}
                or any(not isinstance(v, str) or len(v) > 128 for v in filters.values())):
            raise ValueError("知识检索筛选条件无效。")
        if filters.get("domain") and filters["domain"] not in DOMAINS:
            raise ValueError("知识主题域无效。")
        allowed = [key for key, item in self._items.items() if item.review_status == "REVIEWED" and all(
            not value or (value in item.source_refs if name == "source_id" else getattr(item, name) == value)
            for name, value in filters.items())]
        tokens = sorted(set(tokenize(query)))[:128]
        if not query.strip():
            selected = sorted(allowed, key=lambda k: (self._items[k].domain, k))[:limit]
        elif not tokens or not allowed:
            selected = []
        else:
            match = " OR ".join('"' + token + '"' for token in tokens)
            slots = ",".join("?" for _ in allowed)
            with self._lock:
                self._db.execute("BEGIN")
                try:
                    if self._db.execute("SELECT fingerprint FROM knowledge_meta").fetchall() != [(self.fingerprint,)]:
                        raise KnowledgeIndexUnavailable("公开知识缓存已由另一版本重建，请重新启动本地应用后检索。")
                    selected = [row[0] for row in self._db.execute(
                        "SELECT item_id FROM knowledge_fts WHERE knowledge_fts MATCH ? "
                        f"AND item_id IN ({slots}) ORDER BY bm25(knowledge_fts,0,?,?,?,?),item_id LIMIT ?",
                        (match, *allowed, *FIELD_WEIGHTS, limit))]
                    self._db.commit()
                except Exception:
                    self._db.rollback()
                    raise
        return [{**self.resolve(key), "matched_fields": [name for name, terms in self._terms[key].items() if set(tokens) & terms]} for key in selected]

    def path(self, path_id):
        if path_id not in self._paths:
            raise KeyError("learning path not found")
        return json.loads(_json(self._paths[path_id]))

    def paths(self):
        return [self.path(key) for key in sorted(self._paths)]

    def export(self):
        return {"schema_version": "quant-knowledge-export.v1", "fingerprint": self.fingerprint,
            "items": [self.resolve(key) for key in sorted(self._items) if self._items[key].review_status == "REVIEWED"],
            "paths": self.paths(), "sources": json.loads(_json(list(self._sources.values())))}

"""Audited source changes create new records; historic sources stay addressable."""
from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from .contracts import stable_digest
from .job_queue import _ref
from .source_contracts import SourceSnapshot
from .source_intake import audit_source


def compare_sources(old: SourceSnapshot, new: SourceSnapshot, *, objects=()):
    if (old.source_id, old.repository) != (new.source_id, new.repository):
        raise ValueError("来源快照必须属于同一仓库")
    before = {row.path: row.sha256 for row in old.artifacts}
    after = {row.path: row.sha256 for row in new.artifacts}
    added, removed = after.keys() - before.keys(), before.keys() - after.keys()
    changed = {path for path in before.keys() & after.keys() if before[path] != after[path]}
    notices = set(old.license_paths) | set(new.license_paths)
    rights = (added | changed | removed) & notices
    if old.license_observed != new.license_observed:
        rights |= notices
    affected = {
        row["object_id"] for row in objects for source in row["evidence"]["source_refs"]
        if source.get("repository") == old.repository
        and (rights or source.get("path") in changed | removed)
    }
    return {"source_id": old.source_id, "repository": old.repository, "from_commit": old.commit,
            "to_commit": new.commit, "added": sorted(added), "changed": sorted(changed),
            "removed": sorted(removed), "rights_changed": sorted(rights),
            "affected_items": sorted(affected),
            "review_required": old != new}


def _encoded(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class SourceVersionStore:
    def __init__(self, artifact_root):
        self.path = Path(artifact_root) / "source-versions.sqlite3"

    def _connect(self, *, readonly=False):
        if self.path.is_symlink():
            raise ValueError("来源审阅记录路径无效")
        if readonly:
            return sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.touch(mode=0o600, exist_ok=True)
        return sqlite3.connect(self.path)

    def stage(self, old, new, *, old_checkout, new_checkout):
        difference = compare_sources(old, new)
        if not difference["review_required"]:
            raise ValueError("两个快照没有可审阅的变化")
        audits = [audit_source(root, snapshot) for root, snapshot in ((old_checkout, old), (new_checkout, new))]
        if any(row["status"] != "VERIFIED" for row in audits):
            raise ValueError("提交、字节、许可证文件或逐文件清单未通过验证")
        old_notices, new_notices = ({row["path"]: row["sha256"] for row in a["license_artifacts"]} for a in audits)
        difference["rights_changed"] = sorted(set(difference["rights_changed"]) | {
            path for path in old_notices.keys() | new_notices.keys() if old_notices.get(path) != new_notices.get(path)})
        from .asset_catalog import ResearchAssetCatalog
        from .discovery_planner import open_knowledge
        from .knowledge_workspace_page import WorkspaceCatalog

        with open_knowledge(ResearchAssetCatalog.packaged()) as knowledge:
            workspace = WorkspaceCatalog.packaged(knowledge)
        paths = None if difference["rights_changed"] else set(difference["removed"]) | set(difference["changed"])
        difference["affected_items"] = workspace.source_objects(old.repository, paths=paths)
        payload = {"schema_version": "source-update.v1", "old": asdict(old), "new": asdict(new),
                   "catalog_ref": workspace.fingerprint, "repository_objects": workspace.source_objects(old.repository),
                   "difference": difference, "audits": audits}
        ref = stable_digest(payload)
        with closing(self._connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS source_updates (
                    ref TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS source_update_reviews (
                    ref TEXT PRIMARY KEY REFERENCES source_updates(ref),
                    decision TEXT NOT NULL, reviewer TEXT NOT NULL, rationale TEXT NOT NULL);
            """)
            db.execute("INSERT OR IGNORE INTO source_updates VALUES (?,?)", (ref, _encoded(payload)))
        return ref

    @staticmethod
    def _decode(row):
        ref, raw, decision, reviewer, rationale = row
        payload = json.loads(raw)
        if stable_digest(payload) != ref:
            raise ValueError("来源快照摘要失配")
        return {"ref": ref, **payload, "decision": decision or "PENDING",
                "reviewer": reviewer, "rationale": rationale}

    @staticmethod
    def _rows(db):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_update_reviews'").fetchone():
            return ()
        return db.execute("""SELECT u.ref, u.payload, r.decision, r.reviewer, r.rationale
            FROM source_updates u LEFT JOIN source_update_reviews r ON r.ref=u.ref
            ORDER BY r.rowid, u.rowid""")

    def records(self):
        if not self.path.exists():
            return []
        with closing(self._connect(readonly=True)) as db:
            return [self._decode(row) for row in self._rows(db)]

    def review(self, ref, *, decision, reviewer, rationale):
        _ref(reviewer, "reviewer")
        if (decision not in {"APPROVE_REFERENCE", "WITHDRAW", "REJECT"}
                or not isinstance(rationale, str) or not 1 <= len(rationale.strip()) <= 600):
            raise ValueError("来源变更需要审阅人、明确结论和说明")
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            rows = [self._decode(row) for row in self._rows(db)]
            selected = next((row for row in rows if row["ref"] == ref), None)
            if selected is None:
                raise KeyError(ref)
            if selected["decision"] != "PENDING":
                if (selected["decision"], selected["reviewer"], selected["rationale"]) != (decision, reviewer, rationale):
                    raise ValueError("该版本的审阅记录已冻结")
                return selected
            active = [row for row in rows if row["decision"] in {"APPROVE_REFERENCE", "WITHDRAW"}
                      and row["new"]["repository"] == selected["new"]["repository"]]
            if decision != "REJECT" and active and active[-1]["new"] != selected["old"]:
                raise ValueError("审核基线已改变，请重新比较当前版本")
            db.execute("INSERT INTO source_update_reviews VALUES (?,?,?,?)", (ref, decision, reviewer, rationale))
            return {**selected, "decision": decision, "reviewer": reviewer, "rationale": rationale}

def source_policy(artifact_root, refs):
    """Only reviewed new versions can make a pinned source unavailable."""
    history = [row for row in SourceVersionStore(artifact_root).records()
               if row["decision"] in {"APPROVE_REFERENCE", "WITHDRAW"}]
    blocked, versions = set(), set()
    for ref in refs:
        repo, path, sha = ref.get("repository"), ref.get("path"), ref.get("sha256")
        chain = [row for row in history if row["new"]["repository"] == repo]
        if not chain:
            continue
        versions.update(row["ref"] for row in chain)
        latest = chain[-1]
        current = {row["path"]: row["sha256"] for row in latest["new"]["artifacts"]}
        pinned = next((i for i, row in enumerate(chain) if row["new"]["commit"] == ref.get("commit")), -1)
        newer = chain[pinned + 1:]
        changed = any(path in row["difference"]["changed"] or path in row["difference"]["removed"]
                      or row["difference"]["rights_changed"] or row["decision"] == "WITHDRAW" for row in newer)
        if (latest["decision"] == "WITHDRAW" or changed or (path in current and current[path] != sha)):
            blocked.add(f"{latest['new']['source_id']}:{path}")
    return {"versions": sorted(versions), "blocked": sorted(blocked)}


def research_source_refs(kind, target):
    if kind == "FACTOR":
        from .factor_library import FactorLibrary

        library = FactorLibrary()
        ids = set(library.get(target)["source_card_ids"])
        return [row for row in library.cards() if row["card_id"] in ids]
    from .strategy_catalog import StrategyCatalog

    catalog = StrategyCatalog()
    return [*catalog.template(target)["source_refs"], *(ref for rule in catalog.risk_rules() for ref in rule["source_refs"])]


def require_active_research_source(artifact_root, kind, target):
    if not SourceVersionStore(artifact_root).path.exists():
        return
    decision = source_policy(artifact_root, research_source_refs(kind, target))
    if decision["blocked"]:
        raise ValueError("此研究引用了已撤回或须复核的来源，请查看来源审阅")


def _load_snapshot(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("来源清单超过限制")
    data = json.loads(raw)
    return SourceSnapshot.from_record(data, module_map=data["module_map"])


def main():
    parser = argparse.ArgumentParser(description="离线核对和审阅来源变更")
    parser.add_argument("--artifact-root", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage")
    for arg in ("old", "new", "old-checkout", "new-checkout"):
        stage.add_argument("--" + arg, type=Path, required=True)
    review = commands.add_parser("review")
    review.add_argument("ref")
    review.add_argument("--decision", choices=("APPROVE_REFERENCE", "WITHDRAW", "REJECT"), required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--rationale", required=True)
    commands.add_parser("list")
    args = parser.parse_args()
    store = SourceVersionStore(args.artifact_root)
    if args.command == "stage":
        result = {"ref": store.stage(_load_snapshot(args.old), _load_snapshot(args.new),
                                      old_checkout=args.old_checkout, new_checkout=args.new_checkout)}
    elif args.command == "review":
        result = store.review(args.ref, decision=args.decision, reviewer=args.reviewer, rationale=args.rationale)
    else:
        result = store.records()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

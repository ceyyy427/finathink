"""Read pinned Git metadata and bounded source bytes without importing upstream code."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path

from .source_contracts import (
    MAX_ARTIFACT_BYTES,
    MAX_AUDIT_BYTES,
    MAX_SOURCE_FILES,
    SourceSnapshot,
    source_path,
)


class SourceAuditError(ValueError):
    pass


def module_key(path: str) -> str:
    parts = source_path(path).split("/")
    return "/".join(parts[:2]) if parts[0] == "QUANTAXIS" and len(parts) > 1 else parts[0]


def _git(root, *args):
    # Git reads objects only: never checkout, filters, hooks, submodules or network.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1",
               GIT_OPTIONAL_LOCKS="0", LC_ALL="C")
    argv = ["git", "--no-pager", "-c", "core.fsmonitor=false", "-c",
            f"core.hooksPath={os.devnull}", "-C", str(root), *args]
    try:
        with tempfile.TemporaryFile() as out:
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.DEVNULL, env=env, timeout=15, check=False)
            if result.returncode or out.tell() > MAX_ARTIFACT_BYTES:
                raise SourceAuditError("GIT_METADATA_UNAVAILABLE")
            out.seek(0)
            return out.read(MAX_ARTIFACT_BYTES + 1)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SourceAuditError("GIT_METADATA_UNAVAILABLE") from exc


def _origin(value):
    value = value.strip().removesuffix(".git").rstrip("/")
    if value.startswith("git@github.com:"):
        value = "https://github.com/" + value.removeprefix("git@github.com:")
    return value.casefold()


def _tree(root, commit):
    tree = {}
    for record in _git(root, "ls-tree", "-r", "-z", "--full-tree", commit).split(b"\0"):
        if not record:
            continue
        try:
            header, path = record.decode("utf-8").split("\t", 1)
            mode, kind, oid = header.split(" ")
            source_path(path)
            if not re.fullmatch(r"[0-9a-f]{40}", oid) or path in tree:
                raise ValueError("invalid tree")
        except (ValueError, UnicodeError) as exc:
            raise SourceAuditError("INVALID_GIT_TREE") from exc
        tree[path] = {"mode": mode, "kind": kind, "oid": oid}
        if len(tree) > MAX_SOURCE_FILES:
            raise SourceAuditError("SOURCE_FILE_LIMIT")
    return tree


def _read(root, path):
    node = root
    for part in source_path(path).split("/"):
        node = node / part
        if node.is_symlink():
            raise SourceAuditError("UNSAFE_PATH")
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(node, flags), "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise SourceAuditError("UNSAFE_PATH")
            if info.st_size > MAX_ARTIFACT_BYTES:
                raise SourceAuditError("ARTIFACT_TOO_LARGE")
            raw = stream.read(MAX_ARTIFACT_BYTES + 1)
            if len(raw) > MAX_ARTIFACT_BYTES:
                raise SourceAuditError("ARTIFACT_TOO_LARGE")
            return raw
    except OSError as exc:
        raise SourceAuditError("MISSING_SOURCE_FILE") from exc


def _is_notice(path):
    return bool(re.match(r"^(license|licence|copying|notice)([._-].*)?$",
                         Path(path).name, re.IGNORECASE))


def _license_candidates(path, notices):
    parents = [str(p) for p in Path(path).parents]
    return sorted((p for p in notices if str(Path(p).parent) in parents),
                  key=lambda p: (-len(Path(p).parts), p))


def audit_source(root: Path, snapshot: SourceSnapshot) -> dict:
    if not isinstance(snapshot, SourceSnapshot):
        raise TypeError("SourceSnapshot required")
    result = {
        "schema_version": "source-audit.v1", "source_id": snapshot.source_id,
        "repository": snapshot.repository, "commit": snapshot.commit,
        "capture_date": snapshot.capture_date, "status": "BLOCKED",
        "execution_policy": "SOURCE_TEXT_ONLY", "rights_status": "REVIEW_REQUIRED",
        "verification_scope": "COMMITTED_INVENTORY_AND_LOCKED_ARTIFACTS",
        "license_observed": snapshot.license_observed, "tracked_file_count": 0,
        "modules": [], "artifacts": [], "license_artifacts": [], "errors": [],
    }

    def error(code, path=None):
        result["errors"].append({"code": code, "path": path})

    root = Path(root)
    try:
        if root.is_symlink() or (root / ".git").is_symlink():
            raise SourceAuditError("UNSAFE_PATH")
        root = root.resolve(strict=True)
        if Path(_git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve() != root:
            raise SourceAuditError("NOT_REPOSITORY_ROOT")
        if _git(root, "rev-parse", "HEAD").decode().strip() != snapshot.commit:
            raise SourceAuditError("COMMIT_MISMATCH")
        if _origin(_git(root, "remote", "get-url", "origin").decode()) != _origin(
            snapshot.repository
        ):
            raise SourceAuditError("REPOSITORY_MISMATCH")
        tree = _tree(root, snapshot.commit)
    except (SourceAuditError, OSError, UnicodeError) as exc:
        error(str(exc) if isinstance(exc, SourceAuditError) else "SOURCE_UNAVAILABLE")
        return result
    result["tracked_file_count"] = len(tree)
    if len(tree) != snapshot.tracked_file_count:
        error("INVENTORY_COUNT_MISMATCH")
    expected_modules = {module_key(p) for p in tree}
    declared_modules = {m.module for m in snapshot.module_map}
    for key in sorted(expected_modules ^ declared_modules):
        error("MODULE_DISPOSITION_MISSING_OR_STALE", key)
    for module in snapshot.module_map:
        if any(p not in tree or module_key(p) != module.module for p in module.source_paths):
            error("MODULE_REFERENCE_MISMATCH", module.module)
        result["modules"].append(asdict(module))
    notices = {p for p in tree if _is_notice(p)}
    declared = {a.path: a for a in snapshot.artifacts}
    total = 0
    for path in sorted(set(declared) | notices):
        entry = {"path": path, "status": "BLOCKED",
                 "license_candidates": _license_candidates(path, notices)}
        try:
            info = tree.get(path)
            if not info:
                raise SourceAuditError("UNTRACKED_ARTIFACT")
            if info["mode"] not in {"100644", "100755"} or info["kind"] != "blob":
                raise SourceAuditError("UNSAFE_PATH")
            raw = _read(root, path)
            total += len(raw)
            if total > MAX_AUDIT_BYTES:
                raise SourceAuditError("AUDIT_BYTE_LIMIT")
            sha = hashlib.sha256(raw).hexdigest()
            artifact = declared.get(path)
            if artifact and (sha != artifact.sha256 or len(raw) != artifact.byte_size):
                raise SourceAuditError("HASH_MISMATCH")
            # Compute the Git blob identity without cat-file or applying source filters.
            blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            if blob != info["oid"]:
                raise SourceAuditError("COMMIT_CONTENT_MISMATCH")
            header = raw[:8192].decode("utf-8", errors="replace")
            embedded = [line.strip()[:300] for line in header.splitlines()
                        if re.search(r"SPDX-License-Identifier:|copyright|all rights reserved|"
                                     r"commons clause", line, re.IGNORECASE)][:16]
            entry.update(sha256=sha, byte_size=len(raw), git_blob=info["oid"],
                         embedded_rights=embedded, status="VERIFIED")
        except (SourceAuditError, ValueError) as exc:
            code = str(exc) if isinstance(exc, SourceAuditError) else "UNSAFE_PATH"
            error(code, path)
            entry["error"] = code
        if path in declared:
            result["artifacts"].append(entry)
        if path in notices:
            result["license_artifacts"].append(dict(entry))
        if total > MAX_AUDIT_BYTES:
            break
    result["status"] = "BLOCKED" if result["errors"] else "VERIFIED"
    return result

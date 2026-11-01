"""Immutable declarations for a bounded, offline source inspection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit

MAX_SOURCE_FILES = 20000
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_AUDIT_BYTES = 64 * 1024 * 1024
DISPOSITIONS = frozenset({"ADAPTED", "VALIDATION_PENDING", "REFERENCE_ONLY",
                          "EVIDENCE_MISSING", "OUT_OF_SCOPE"})


def source_path(value: str) -> str:
    if (not isinstance(value, str) or not value or len(value) > 1024
            or any(ord(c) < 32 for c in value) or "\\" in value or ":" in value
            or any(p in {"", ".", "..", ".git"} for p in value.split("/"))):
        raise ValueError("invalid source path")
    return value


def _text(value, label, maximum=2048):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(c) < 32 for c in value)):
        raise ValueError(f"invalid {label}")
    return value


def _hash(value, size, label):
    if not isinstance(value, str) or not re.fullmatch(rf"[0-9a-f]{{{size}}}", value):
        raise ValueError(f"invalid {label}")
    return value


@dataclass(frozen=True, slots=True)
class SourceArtifact:
    path: str
    sha256: str
    byte_size: int

    def __post_init__(self):
        source_path(self.path)
        _hash(self.sha256, 64, "sha256")
        if type(self.byte_size) is not int or not 0 <= self.byte_size <= MAX_ARTIFACT_BYTES:
            raise ValueError("invalid artifact size")


@dataclass(frozen=True, slots=True)
class ModuleDisposition:
    module: str
    disposition: str
    reason: str
    target: str
    phase: int
    source_paths: tuple[str, ...]
    dependencies: tuple[str, ...] = ()

    def __post_init__(self):
        source_path(self.module)
        if self.disposition not in DISPOSITIONS or type(self.phase) is not int:
            raise ValueError("invalid module disposition")
        if not 21 <= self.phase <= 28:
            raise ValueError("invalid target phase")
        for key in ("reason", "target"):
            _text(getattr(self, key), key)
        for key in ("source_paths", "dependencies"):
            values = getattr(self, key)
            if not isinstance(values, (tuple, list)) or len(values) > 128:
                raise ValueError("invalid module references")
            converted = tuple(source_path(v) if key == "source_paths"
                              else _text(v, "dependency") for v in values)
            object.__setattr__(self, key, converted)
        if not self.source_paths:
            raise ValueError("module requires source paths")


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    source_id: str
    repository: str
    commit: str
    capture_date: str
    artifacts: tuple[SourceArtifact, ...]
    license_paths: tuple[str, ...]
    tracked_file_count: int
    module_map: tuple[ModuleDisposition, ...]
    license_observed: str = "UNDETERMINED"

    def __post_init__(self):
        if not isinstance(self.source_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", self.source_id
        ):
            raise ValueError("invalid source_id")
        _text(self.repository, "repository", 512)
        url = urlsplit(self.repository)
        if (url.scheme != "https" or url.netloc != "github.com" or url.query or url.fragment
                or not re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", url.path)):
            raise ValueError("repository must be a canonical GitHub HTTPS URL")
        _hash(self.commit, 40, "commit")
        if not isinstance(self.capture_date, str) or date.fromisoformat(
            self.capture_date
        ).isoformat() != self.capture_date:
            raise ValueError("invalid capture_date")
        _text(self.license_observed, "license observation", 128)
        if type(self.tracked_file_count) is not int or not (
            0 < self.tracked_file_count <= MAX_SOURCE_FILES
        ):
            raise ValueError("invalid tracked_file_count")
        for key, kind, limit in (("artifacts", SourceArtifact, 2048),
                                 ("module_map", ModuleDisposition, MAX_SOURCE_FILES)):
            values = getattr(self, key)
            if not isinstance(values, (tuple, list)) or not 0 < len(values) <= limit:
                raise ValueError(f"invalid {key}")
            if not all(isinstance(v, kind) for v in values):
                raise ValueError(f"invalid {key}")
            object.__setattr__(self, key, tuple(values))
        if sum(a.byte_size for a in self.artifacts) > MAX_AUDIT_BYTES:
            raise ValueError("source audit byte budget exceeded")
        for values in ([a.path for a in self.artifacts], [m.module for m in self.module_map]):
            if len(set(values)) != len(values):
                raise ValueError("duplicate source path or module")
        if not isinstance(self.license_paths, (list, tuple)) or not self.license_paths:
            raise ValueError("license paths required")
        object.__setattr__(self, "license_paths", tuple(source_path(p) for p in self.license_paths))
        if not set(self.license_paths) <= {a.path for a in self.artifacts}:
            raise ValueError("license paths must be locked artifacts")

    @classmethod
    def from_record(cls, record, *, module_map):
        if not isinstance(record, dict):
            raise TypeError("source record must be an object")
        try:
            return cls(
                source_id=record["source_id"], repository=record["repository"],
                commit=record["commit"], capture_date=record["capture_date"],
                artifacts=tuple(SourceArtifact(a["path"], a["sha256"], a["bytes"])
                                for a in record["artifacts"]),
                license_paths=tuple(record["license_paths"] if "license_paths" in record
                                    else [record["license_path"]]),
                tracked_file_count=record["tracked_file_count"],
                module_map=tuple(ModuleDisposition(**m) for m in module_map),
                license_observed=record.get("license_observed", "UNDETERMINED"),
            )
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError("invalid source declaration") from exc

"""Public, versioned knowledge definitions; no personal state or execution rights."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, fields
from datetime import date
from urllib.parse import quote

DOMAINS = {
    "data-pit": "数据与时点", "returns-market": "收益与市场", "statistics": "统计方法",
    "technical-volume": "技术与价量", "fundamentals": "财务分析", "factor-validation": "因子检验",
    "strategy-portfolio": "策略与组合", "risk-review": "风险与复盘",
}
KINDS = {"CONCEPT", "METHOD", "FACTOR", "STRATEGY", "RISK", "CASE"}
CLAIMS = {"DEFINITION", "MATHEMATICAL", "EMPIRICAL", "ENGINEERING"}
LEVELS = {"SOURCE_REPORTED", "DEFINITION_CHECKED"}
ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")


def text(value, label, maximum=6000):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(c) < 32 and c not in "\n\t" for c in value)):
        raise ValueError(f"invalid knowledge {label}")
    return value


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError("invalid knowledge identifier")
    return value


def references(values, label, maximum=64, *, labels=False):
    if not isinstance(values, (list, tuple)) or len(values) > maximum:
        raise ValueError(f"invalid knowledge {label}")
    result = tuple(text(v, label, 256) if labels else identifier(v) for v in values)
    if len(set(result)) != len(result):
        raise ValueError(f"duplicate knowledge {label}")
    return result


@dataclass(frozen=True, slots=True)
class KnowledgeItem:
    item_id: str
    canonical_id: str
    version: str
    kind: str
    title: str
    domain: str
    summary: str
    explanation: str
    application: str
    example_text: str
    prerequisites: tuple[str, ...]
    assumptions: tuple[str, ...]
    required_fields: tuple[str, ...]
    formula_refs: tuple[str, ...]
    code_refs: tuple[str, ...]
    source_refs: tuple[str, ...]
    claim_kind: str
    evidence_refs: tuple[str, ...]
    evidence_level: str
    counterexample_refs: tuple[str, ...]
    method_refs: tuple[str, ...]
    review_status: str
    aliases: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    example_refs: tuple[str, ...] = ()

    def __post_init__(self):
        identifier(self.item_id)
        if self.item_id != self.canonical_id:
            raise ValueError("translations must share one canonical knowledge item")
        for name in ("version", "title", "summary", "explanation", "application", "example_text"):
            text(getattr(self, name), name)
        if self.domain not in DOMAINS or self.kind not in KINDS or self.claim_kind not in CLAIMS:
            raise ValueError("invalid knowledge classification")
        if self.review_status not in {"REVIEWED", "DRAFT"} or self.evidence_level not in LEVELS:
            raise ValueError("invalid knowledge review or evidence level")
        # Definitions and hand-worked examples cannot certify an empirical claim.
        if self.claim_kind == "EMPIRICAL" and self.evidence_level != "SOURCE_REPORTED":
            raise ValueError("empirical knowledge requires actual research evidence")
        if self.evidence_refs:
            raise ValueError("static teaching content cannot declare user-run evidence")
        names = ("prerequisites", "assumptions", "required_fields", "formula_refs", "code_refs",
                 "source_refs", "evidence_refs", "counterexample_refs", "method_refs", "aliases", "tags", "example_refs")
        for name in names:
            object.__setattr__(self, name, references(getattr(self, name), name,
                labels=name in {"assumptions", "aliases", "tags"}))
        if not all((self.assumptions, self.required_fields, self.source_refs, self.counterexample_refs)):
            raise ValueError("knowledge requires conditions, fields, sources and counterexamples")

    @classmethod
    def from_dict(cls, record):
        if not isinstance(record, dict) or set(record) - {f.name for f in fields(cls)}:
            raise ValueError("unknown public knowledge field")
        try:
            return cls(**record)
        except TypeError as exc:
            raise ValueError("incomplete public knowledge contract") from exc

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, slots=True)
class KnowledgeSource:
    source_ref: str
    title: str
    repository: str
    commit: str
    path: str
    sha256: str
    license_resource_id: str
    capture_date: str
    start_line: int
    end_line: int
    relation: str
    scope_note: str

    def __post_init__(self):
        from .source_contracts import source_path

        identifier(self.source_ref)
        identifier(self.license_resource_id)
        text(self.title, "source title", 256)
        text(self.scope_note, "source scope")
        if not re.fullmatch(r"https://github.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository):
            raise ValueError("knowledge sources require a fixed repository")
        source_path(self.path)
        if not re.fullmatch(r"[0-9a-f]{40}", self.commit) or not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("knowledge source digest is invalid")
        if date.fromisoformat(self.capture_date).isoformat() != self.capture_date:
            raise ValueError("knowledge source date is invalid")
        if (type(self.start_line) is not int or type(self.end_line) is not int
                or not 1 <= self.start_line <= self.end_line <= 100000):
            raise ValueError("knowledge source line range is invalid")
        if self.relation not in {"IMPLEMENTATION_REFERENCE", "DEFINITION_REFERENCE", "RELATED_WORKFLOW"}:
            raise ValueError("knowledge source relation is invalid")

    def to_dict(self):
        return {**asdict(self), "url": f"{self.repository}/blob/{self.commit}/{quote(self.path, safe='/')}#L{self.start_line}-L{self.end_line}"}

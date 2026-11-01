"""Immutable reference metadata, never execution or investment-use approval."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date


class AssetLoadError(ValueError):
    """A resource failure is distinct from a failed financial hypothesis."""

    def __init__(self, code: str, resource_id: str):
        self.code = code
        self.resource_id = resource_id
        super().__init__(f"{code}: {resource_id}")


@dataclass(frozen=True, slots=True)
class ResourceRef:
    resource_id: str
    path: str | None
    sha256: str | None
    byte_size: int | None
    availability: str
    kind: str
    author: str
    license_id: str | None
    license_ref: str | None
    version: str
    source_url: str | None = None
    source_path: str | None = None
    source_sha256: str | None = None
    derivation: str = "ORIGINAL"
    rights_ref: str | None = None


@dataclass(frozen=True, slots=True)
class RightsMaterial:
    resource_id: str
    sha256: str

    def __post_init__(self):
        if (not isinstance(self.resource_id, str) or not self.resource_id
                or len(self.resource_id) > 160 or not isinstance(self.sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", self.sha256)):
            raise ValueError("invalid rights material")


@dataclass(frozen=True, slots=True)
class FileRightsReview:
    review_id: str
    resource_id: str
    resource_sha256: str
    license_id: str
    license_refs: tuple[RightsMaterial, ...]
    notice_refs: tuple[RightsMaterial, ...]
    decision: str
    scope: str
    reviewed_by: str
    reviewed_on: str
    rationale: str
    source_url: str | None = None
    source_path: str | None = None
    source_sha256: str | None = None

    def __post_init__(self):
        for name in ("review_id", "resource_id", "license_id", "reviewed_by", "rationale"):
            value = getattr(self, name)
            if (not isinstance(value, str) or not value.strip() or len(value) > 2048
                    or any(ord(c) < 32 for c in value)):
                raise ValueError("invalid rights review text")
        if self.scope != "FILE_BYTES_ONLY" or self.decision not in {
            "DISTRIBUTION_APPROVED", "REFERENCE_ONLY", "BLOCKED",
        }:
            raise ValueError("invalid rights decision scope")
        if (not isinstance(self.reviewed_on, str)
                or date.fromisoformat(self.reviewed_on).isoformat() != self.reviewed_on):
            raise ValueError("invalid review date")
        if not isinstance(self.resource_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.resource_sha256
        ):
            raise ValueError("invalid reviewed hash")
        for name in ("source_url", "source_path", "source_sha256"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value
                                      or len(value) > 2048):
                raise ValueError("invalid reviewed provenance")
        if self.source_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", self.source_sha256):
            raise ValueError("invalid reviewed source hash")
        for name in ("license_refs", "notice_refs"):
            values = getattr(self, name)
            if not isinstance(values, (list, tuple)) or len(values) > 64:
                raise ValueError("invalid rights references")
            items = tuple(v if isinstance(v, RightsMaterial) else RightsMaterial(**v)
                          for v in values)
            if len({v.resource_id for v in items}) != len(items):
                raise ValueError("duplicate rights reference")
            object.__setattr__(self, name, items)
        if not self.license_refs:
            raise ValueError("review requires license evidence")


@dataclass(frozen=True, slots=True)
class SkillDescriptor:
    skill_id: str
    name: str
    version: str
    tags: tuple[str, ...]
    required_inputs: tuple[str, ...]
    output_types: tuple[str, ...]
    main_resource: str
    required_resources: tuple[str, ...]
    optional_resources: tuple[str, ...]
    policy_ref: str
    boundaries: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvidenceClaim:
    claim_id: str
    subject_id: str
    evidence_status: str
    metric_name: str
    metric_value: float | str | None
    metric_units: str
    definition: str
    scope: str
    source_resource_ids: tuple[str, ...]
    local_run_ref: str | None = None
    reproduction_scope: str | None = None


@dataclass(frozen=True, slots=True)
class LessonRecord:
    lesson_id: str
    title: str
    tags: tuple[str, ...]
    failure_type: str
    summary: str
    source_resource_ids: tuple[str, ...]
    mitigation: str
    regression_check: str
    revisit_when: str
    evidence_status: str = "SOURCE_REPORTED"
    is_financial_falsification: bool = False


@dataclass(frozen=True, slots=True)
class AssetInventory:
    skill_count: int
    packaged_resource_count: int
    missing_resource_count: int
    upstream_only_resource_count: int
    unlicensed_resource_count: int
    unique_definition_count: int
    alias_count: int
    claim_count: int
    lesson_count: int
    locally_reproduced_claim_count: int
    financial_falsification_count: int
    source_factor_count: int = 0
    unresolved_definition_count: int = 0


@dataclass(frozen=True, slots=True)
class FactorEvidenceView:
    """An immutable projection of existing facts, not an approval or new metric."""

    _json: str

    def to_dict(self):
        import json

        return json.loads(self._json)

    @property
    def fingerprint(self):
        from .contracts import stable_digest

        return stable_digest(self.to_dict())


def _route_values(value, name, maximum):
    from .contracts import _unsafe_public_text

    if not isinstance(value, (tuple, list)) or len(value) > maximum or any(
        not isinstance(item, str) or not item.strip() or len(item) > 128
        or _unsafe_public_text(item) for item in value
    ):
        raise ValueError(f"{name} requires bounded, non-sensitive text labels")
    return tuple(sorted({item.strip().casefold() for item in value}))


@dataclass(frozen=True, slots=True)
class MethodRouteRequest:
    purposes: tuple[str, ...] = ()
    factor_families: tuple[str, ...] = ()
    market: str = "UNSPECIFIED"
    available_fields: tuple[str, ...] = ()
    public_profiles: tuple[str, ...] = ()

    def __post_init__(self):
        from .contracts import _unsafe_public_text

        for name, maximum in (("purposes", 16), ("factor_families", 16),
                              ("available_fields", 128), ("public_profiles", 6)):
            object.__setattr__(self, name, _route_values(getattr(self, name), name, maximum))
        if (not isinstance(self.market, str) or not self.market.strip()
                or len(self.market) > 64 or _unsafe_public_text(self.market)):
            raise ValueError("market requires a bounded, non-sensitive label")
        object.__setattr__(self, "market", self.market.strip().upper())


@dataclass(frozen=True, slots=True)
class MethodSelection:
    request_digest: str
    status: str
    skill_ids: tuple[str, ...]
    reasons: tuple[tuple[str, str], ...]
    required_resources: tuple[ResourceRef, ...]
    optional_resources: tuple[ResourceRef, ...]
    missing_inputs: tuple[str, ...] = ()
    unsupported: tuple[str, ...] = ()
    method_fingerprints: tuple[tuple[str, str], ...] = ()

    @property
    def fingerprint(self):
        from dataclasses import asdict

        from .contracts import stable_digest

        return stable_digest({"schema_version": "method-selection.v1", **asdict(self)})


@dataclass(frozen=True, slots=True)
class FactorSpec:
    """Definition/provenance only; execution resolves an existing trusted recipe."""

    factor_id: str
    version: str
    formula: str
    required_fields: tuple[str, ...]
    units: str
    direction: str
    window: int
    frequency: str
    availability: str
    missing_policy: str
    implementation_ref: str
    implementation_fingerprint: str
    source_resource_ids: tuple[str, ...] = ()

    def __post_init__(self):
        import re

        from finahinking.factors.dsl import parse_factor_expression

        from .contracts import _unsafe_public_text

        for name in ("factor_id", "version", "formula", "units", "implementation_ref"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 512 or _unsafe_public_text(value):
                raise ValueError("factor definition requires bounded non-sensitive metadata")
        for name in ("required_fields", "source_resource_ids"):
            values = getattr(self, name)
            if (not isinstance(values, (tuple, list)) or len(values) > 32
                    or any(not isinstance(value, str) or not value or len(value) > 160 or _unsafe_public_text(value) for value in values)):
                raise ValueError("factor definition collections must be immutable text references")
            object.__setattr__(self, name, tuple(sorted(set(values))))
        if (type(self.window) is not int or not 1 <= self.window <= 252 or self.direction not in {"HIGHER", "LOWER"}
                or self.frequency != "daily" or self.availability != "available-at-or-before-signal"
                or self.missing_policy != "propagate"):
            raise ValueError("unsupported factor timing or missing-value semantics")
        if (not isinstance(self.implementation_fingerprint, str)
                or not re.fullmatch(r"[0-9a-f]{64}", self.implementation_fingerprint)
                or not re.fullmatch(r"(?:recipe|feature):[A-Za-z0-9_.:-]+@[A-Za-z0-9_.:-]+", self.implementation_ref)):
            raise ValueError("factor definition requires a versioned implementation binding")
        parsed = parse_factor_expression(self.formula, self.required_fields)
        if parsed.fields != self.required_fields:
            raise ValueError("factor definition fields disagree with its formula")
        object.__setattr__(self, "formula", parsed.expression)

    def to_dict(self):
        from dataclasses import asdict

        from .contracts import to_jsonable

        return {"schema_version": "factor-spec.v1", **to_jsonable(asdict(self))}

    @property
    def fingerprint(self):
        from .contracts import stable_digest

        return stable_digest(self.to_dict())

"""Immutable reference metadata, never execution or investment-use approval."""

from __future__ import annotations

from dataclasses import dataclass


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

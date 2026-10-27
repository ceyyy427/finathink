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

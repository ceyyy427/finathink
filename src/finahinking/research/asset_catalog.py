"""One bounded, hash-checked, read-only authority for installed research assets."""

from __future__ import annotations

import hashlib
import json
import math
import re
from importlib.resources import files
from pathlib import Path, PurePosixPath

from .asset_contracts import (
    AssetInventory,
    AssetLoadError,
    EvidenceClaim,
    FileRightsReview,
    LessonRecord,
    ResourceRef,
    SkillDescriptor,
)

_HASH = re.compile(r"[0-9a-f]{64}")
_SKILL_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_AVAILABILITY = {"PACKAGED", "UPSTREAM_ONLY", "MISSING", "UNLICENSED"}


class ResearchAssetCatalog:
    def __init__(self, root):
        self._root = root
        manifest = self._json(self._read_path("provenance/upstream-lock.json", 524288), "manifest")
        self._schema = manifest.get("schema_version")
        if self._schema not in {"research-assets.v1", "research-assets.v2"}:
            raise AssetLoadError("INVALID_MANIFEST", "manifest")
        self._resources = {}
        try:
            for item in manifest["resources"]:
                if not self._text_fields(
                    item, ("resource_id", "availability", "kind", "author", "version"),
                    ("path", "sha256", "license_id", "license_ref", "source_url",
                     "source_path", "source_sha256", "derivation", "rights_ref"),
                ):
                    raise AssetLoadError("INVALID_MANIFEST", "manifest")
                if item.get("byte_size") is not None and (
                    type(item["byte_size"]) is not int or item["byte_size"] < 0
                ):
                    raise AssetLoadError("INVALID_SIZE", "manifest")
                ref = ResourceRef(**item)
                if not ref.resource_id or ref.resource_id in self._resources:
                    raise AssetLoadError("DUPLICATE_RESOURCE", str(ref.resource_id))
                if ref.availability not in _AVAILABILITY:
                    raise AssetLoadError("INVALID_AVAILABILITY", ref.resource_id)
                if ref.availability == "PACKAGED":
                    self._path(ref.path)
                    if not isinstance(ref.sha256, str) or not _HASH.fullmatch(ref.sha256):
                        raise AssetLoadError("INVALID_HASH", ref.resource_id)
                    if type(ref.byte_size) is not int or ref.byte_size < 0:
                        raise AssetLoadError("INVALID_SIZE", ref.resource_id)
                elif ref.path is not None:
                    raise AssetLoadError("INVALID_PATH", ref.resource_id)
                self._resources[ref.resource_id] = ref
        except (KeyError, TypeError) as exc:
            raise AssetLoadError("INVALID_MANIFEST", "manifest") from exc
        self._rights = self._load_rights(manifest)
        self._skills = self._load_skills()

    def _load_rights(self, manifest):
        if self._schema == "research-assets.v1":
            if manifest.get("rights_reviews") or any(r.rights_ref for r in self._resources.values()):
                raise AssetLoadError("INVALID_RIGHTS", "manifest")
            return {}
        reviews = manifest.get("rights_reviews")
        if not isinstance(reviews, list) or len(reviews) > 4096:
            raise AssetLoadError("INVALID_RIGHTS", "manifest")
        result = {}
        try:
            for payload in reviews:
                review = FileRightsReview(**payload)
                if review.review_id in result:
                    raise ValueError("duplicate review")
                result[review.review_id] = review
        except (TypeError, ValueError, KeyError) as exc:
            raise AssetLoadError("INVALID_RIGHTS", "manifest") from exc
        return result

    @classmethod
    def packaged(cls) -> ResearchAssetCatalog:
        return cls(files("finahinking").joinpath("_package_data", "research_assets"))

    @staticmethod
    def _text_fields(item, required, optional=()):
        """Type hints alone cannot make JSON-derived scalar fields immutable."""
        if not isinstance(item, dict):
            return False
        if any(not isinstance(item.get(name), str) or not item[name].strip()
               for name in required):
            return False
        return all(item.get(name) is None or (
            isinstance(item[name], str) and bool(item[name].strip())
        ) for name in optional)

    @staticmethod
    def _json(text, resource_id):
        try:
            value = json.loads(text)
            if not isinstance(value, dict):
                raise TypeError("object required")
            return value
        except (RecursionError, ValueError, TypeError) as exc:
            raise AssetLoadError("INVALID_JSON", resource_id) from exc

    def _path(self, relative):
        if (not isinstance(relative, str) or not relative or "\\" in relative
                or ":" in relative or "\x00" in relative):
            raise AssetLoadError("INVALID_PATH", "resource")
        parts = relative.split("/")
        if PurePosixPath(relative).is_absolute() or any(p in {"", ".", ".."} for p in parts):
            raise AssetLoadError("INVALID_PATH", "resource")
        node = self._root
        if isinstance(node, Path) and node.is_symlink():
            raise AssetLoadError("INVALID_PATH", "resource")
        for part in parts:
            node = node.joinpath(part)
            if isinstance(node, Path) and node.is_symlink():
                raise AssetLoadError("INVALID_PATH", "resource")
        return node

    def _read_path(self, relative, max_bytes):
        node = self._path(relative)
        try:
            with node.open("rb") as stream:
                raw = stream.read(max_bytes + 1)
        except (OSError, ValueError) as exc:
            raise AssetLoadError("MISSING_RESOURCE", "resource") from exc
        if len(raw) > max_bytes:
            raise AssetLoadError("TOO_LARGE", "resource")
        return raw

    def get_resource(self, resource_id: str) -> ResourceRef:
        try:
            return self._resources[resource_id]
        except (KeyError, TypeError) as exc:
            raise AssetLoadError("UNKNOWN_RESOURCE", str(resource_id)) from exc

    def resources(self) -> tuple[ResourceRef, ...]:
        return tuple(self._resources[key] for key in sorted(self._resources))

    def source_intake_summary(self):
        doc = self._json(self.read_text("source-intake-summary"), "source-intake-summary")
        sources = doc.get("sources")
        if (doc.get("schema_version") != "source-intake-summary.v1"
                or not isinstance(sources, list) or len(sources) > 100):
            raise AssetLoadError("INVALID_SOURCE_SUMMARY", "source-intake-summary")
        seen = set()
        for item in sources:
            if not self._text_fields(item, ("source_id", "title", "repository", "commit",
                                           "capture_date", "license_observed", "license_resource_id")):
                raise AssetLoadError("INVALID_SOURCE_SUMMARY", "source-intake-summary")
            if (item["source_id"].lower() in seen
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", item["source_id"])
                    or item.get("source_status") != "VERIFIED"
                    or not re.fullmatch(r"[0-9a-f]{40}", item["commit"])
                    or any(type(item.get(k)) is not int or item[k] < 0 for k in (
                        "tracked_file_count", "verified_artifact_count", "module_count"))
                    or not isinstance(item.get("modules"), list)
                    or len(item["modules"]) != item["module_count"]):
                raise AssetLoadError("INVALID_SOURCE_SUMMARY", "source-intake-summary")
            seen.add(item["source_id"].lower())
            references = item.get("reference_resource_ids")
            if (not isinstance(references, list) or len(references) > 64
                    or not all(isinstance(r, str) and r for r in references)):
                raise AssetLoadError("INVALID_SOURCE_SUMMARY", "source-intake-summary")
            for module in item["modules"]:
                if (not self._text_fields(module, ("name", "disposition"))
                        or type(module.get("phase")) is not int or not 21 <= module["phase"] <= 28
                        or module["disposition"] not in {"ADAPTED", "VALIDATION_PENDING",
                            "REFERENCE_ONLY", "EVIDENCE_MISSING", "OUT_OF_SCOPE"}):
                    raise AssetLoadError("INVALID_SOURCE_SUMMARY", "source-intake-summary")
            for rid in [item["license_resource_id"], *references]:
                self.read_text(rid)
        return doc

    def _check_rights(self, ref):
        if self._schema == "research-assets.v1":
            # Historical v1 catalogs keep their MIT-only read contract.
            if ref.license_id != "MIT" or not ref.license_ref or not ref.author:
                raise AssetLoadError("LICENSE_MISSING", ref.resource_id)
            license_ref = self.get_resource(ref.license_ref)
            if license_ref.kind != "license":
                raise AssetLoadError("LICENSE_MISSING", ref.resource_id)
            self._checked_bytes(license_ref, 65536)
            return
        pending, seen = [ref], set()
        while pending:
            current = pending.pop()
            if current.resource_id in seen:
                continue
            seen.add(current.resource_id)
            review = self._approved_review(current)
            for materials, kind in ((review.license_refs, "license"),
                                    (review.notice_refs, "notice")):
                for material in materials:
                    reference = self.get_resource(material.resource_id)
                    if reference.kind != kind or reference.sha256 != material.sha256:
                        raise AssetLoadError("RIGHTS_EVIDENCE_MISMATCH", current.resource_id)
                    self._checked_bytes(reference, 65536)
                    pending.append(reference)

    def _approved_review(self, ref):
        review = self._rights.get(ref.rights_ref)
        if (review is None or review.decision != "DISTRIBUTION_APPROVED"
                or review.resource_id != ref.resource_id or review.resource_sha256 != ref.sha256
                or review.license_id != ref.license_id or ref.license_id not in {"MIT", "Apache-2.0"}
                or ref.license_ref not in {m.resource_id for m in review.license_refs}
                or any(getattr(review, name) != getattr(ref, name)
                       for name in ("source_url", "source_path", "source_sha256"))):
            raise AssetLoadError("RIGHTS_NOT_APPROVED", ref.resource_id)
        # Finathink also retains an explicit attribution for Apache sources without
        # an upstream NOTICE. It is labelled as ours, never fabricated as upstream.
        if ref.license_id == "Apache-2.0" and not review.notice_refs:
            raise AssetLoadError("NOTICE_MISSING", ref.resource_id)
        return review

    def _checked_bytes(self, ref, max_bytes):
        if ref.availability != "PACKAGED":
            code = "LICENSE_MISSING" if ref.availability == "UNLICENSED" else "MISSING_RESOURCE"
            raise AssetLoadError(code, ref.resource_id)
        raw = self._read_path(ref.path, max_bytes)
        if hashlib.sha256(raw).hexdigest() != ref.sha256 or len(raw) != ref.byte_size:
            raise AssetLoadError("HASH_MISMATCH", ref.resource_id)
        return raw

    def read_text(self, resource_id: str, *, max_bytes: int = 65536) -> str:
        if type(max_bytes) is not int or not 1 <= max_bytes <= 1048576:
            raise AssetLoadError("INVALID_LIMIT", str(resource_id))
        ref = self.get_resource(resource_id)
        if ref.availability == "PACKAGED":
            self._check_rights(ref)
        raw = self._checked_bytes(ref, max_bytes)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AssetLoadError("INVALID_ENCODING", resource_id) from exc

    def _load_skills(self):
        index = self._json(self.read_text("skill-index"), "skill-index")
        if index.get("schema_version") != "research-methods.v1":
            raise AssetLoadError("INVALID_INDEX", "skill-index")
        result = {}
        tuple_fields = ("tags", "required_inputs", "output_types", "required_resources",
                        "optional_resources", "boundaries")
        try:
            for row in index["skills"]:
                if not self._text_fields(
                    row, ("skill_id", "name", "version", "main_resource", "policy_ref")
                ):
                    raise AssetLoadError("INVALID_DESCRIPTOR", "skill-index")
                item = dict(row)
                for name in tuple_fields:
                    if not isinstance(item[name], list) or not all(
                        isinstance(value, str) and value for value in item[name]
                    ):
                        raise AssetLoadError("INVALID_DESCRIPTOR", "skill-index")
                    item[name] = tuple(item[name])
                skill = SkillDescriptor(**item)
                if not _SKILL_ID.fullmatch(skill.skill_id) or skill.skill_id in result:
                    raise AssetLoadError("INVALID_DESCRIPTOR", "skill-index")
                for resource_id in (skill.main_resource, skill.policy_ref,
                                    *skill.required_resources, *skill.optional_resources):
                    self.get_resource(resource_id)
                result[skill.skill_id] = skill
        except (KeyError, TypeError) as exc:
            raise AssetLoadError("INVALID_DESCRIPTOR", "skill-index") from exc
        return result

    def skills(self) -> tuple[SkillDescriptor, ...]:
        return tuple(self._skills[key] for key in sorted(self._skills))

    def get_skill(self, skill_id: str) -> SkillDescriptor:
        try:
            return self._skills[skill_id]
        except KeyError as exc:
            raise AssetLoadError("UNKNOWN_SKILL", skill_id) from exc

    def resources_for(self, skill_id: str) -> tuple[ResourceRef, ...]:
        skill = self.get_skill(skill_id)
        ids = dict.fromkeys((skill.main_resource, skill.policy_ref,
                             *skill.required_resources, *skill.optional_resources))
        return tuple(self.get_resource(resource_id) for resource_id in ids)

    def _records(self, resource_id, record_type, tuple_fields):
        result = []
        seen = set()
        try:
            for line in self.read_text(resource_id).splitlines():
                if not line.strip():
                    continue
                item = self._json(line, resource_id)
                if record_type is EvidenceClaim:
                    valid = self._text_fields(
                        item, ("claim_id", "subject_id", "evidence_status", "metric_name",
                               "metric_units", "definition", "scope"),
                        ("local_run_ref", "reproduction_scope"),
                    )
                else:
                    valid = self._text_fields(
                        item, ("lesson_id", "title", "failure_type", "summary", "mitigation",
                               "regression_check", "revisit_when"), ("evidence_status",),
                    )
                if not valid:
                    raise AssetLoadError("INVALID_EVIDENCE", resource_id)
                for name in tuple_fields:
                    if not isinstance(item[name], list) or not all(
                        isinstance(value, str) and value for value in item[name]
                    ):
                        raise AssetLoadError("INVALID_EVIDENCE", resource_id)
                    item[name] = tuple(item[name])
                record = record_type(**item)
                identity = record.claim_id if record_type is EvidenceClaim else record.lesson_id
                if not isinstance(identity, str) or not identity or identity in seen:
                    raise AssetLoadError("INVALID_EVIDENCE", resource_id)
                seen.add(identity)
                if record.evidence_status not in {
                    "SOURCE_REPORTED", "IMPLEMENTATION_LOCATED", "DEPENDENCY_MISSING",
                    "CONFLICTING_EVIDENCE",
                } or not record.source_resource_ids:
                    raise AssetLoadError("INVALID_EVIDENCE", resource_id)
                for source_id in record.source_resource_ids:
                    self.get_resource(source_id)
                if record_type is EvidenceClaim:
                    # Packaged source text cannot mint a local execution receipt.
                    if record.local_run_ref is not None or record.reproduction_scope is not None:
                        raise AssetLoadError("INVALID_EVIDENCE", resource_id)
                    if any(not isinstance(value, str) or not value for value in (
                        record.subject_id, record.metric_name, record.metric_units,
                        record.definition, record.scope,
                    )):
                        raise AssetLoadError("INVALID_EVIDENCE", resource_id)
                    value = record.metric_value
                    if isinstance(value, bool) or (
                        isinstance(value, (float, int)) and not math.isfinite(value)
                    ) or not isinstance(value, (float, int, str, type(None))):
                        raise AssetLoadError("INVALID_EVIDENCE", resource_id)
                elif record.is_financial_falsification is not False:
                    # Local hypothesis rejection is joined from audited runs later,
                    # not inferred from a summary or an unavailable original report.
                    raise AssetLoadError("INVALID_EVIDENCE", resource_id)
                result.append(record)
        except (KeyError, OverflowError, TypeError) as exc:
            raise AssetLoadError("INVALID_EVIDENCE", resource_id) from exc
        return tuple(result)

    def claims_for(self, subject_id: str) -> tuple[EvidenceClaim, ...]:
        claims = self._records("source-claims", EvidenceClaim, ("source_resource_ids",))
        return tuple(sorted((claim for claim in claims if claim.subject_id == subject_id),
                            key=lambda claim: claim.claim_id))

    def lessons_for(self, tags: tuple[str, ...]) -> tuple[LessonRecord, ...]:
        lessons = self._records("lessons", LessonRecord, ("source_resource_ids", "tags"))
        wanted = set(tags)
        return tuple(sorted((lesson for lesson in lessons if not wanted or wanted & set(lesson.tags)),
                            key=lambda lesson: lesson.lesson_id))

    def _definitions(self):
        document = self._json(self.read_text("source-definitions"), "source-definitions")
        if document.get("schema_version") != "source-definitions.v1":
            raise AssetLoadError("INVALID_DEFINITION", "source-definitions")
        try:
            definitions = document["definitions"]
            aliases = document["aliases"]
            if not isinstance(definitions, list) or not isinstance(aliases, list):
                raise AssetLoadError("INVALID_DEFINITION", "source-definitions")
            identities = set()
            for item in definitions:
                identity = item["definition_id"]
                if not isinstance(identity, str) or not identity or identity in identities:
                    raise AssetLoadError("INVALID_DEFINITION", "source-definitions")
                identities.add(identity)
                if not item["name"] or not item["source_id"] or not item["source_resource_ids"]:
                    raise AssetLoadError("INVALID_DEFINITION", identity)
                for resource_id in item["source_resource_ids"]:
                    self.get_resource(resource_id)
                state = item["definition_status"]
                if state not in {"FORMULA_LOCATED", "UNRESOLVED"} or (
                    state == "FORMULA_LOCATED" and not item.get("formula")
                ) or (state == "UNRESOLVED" and item.get("formula") is not None):
                    raise AssetLoadError("INVALID_DEFINITION", identity)
            alias_ids = set()
            for item in aliases:
                identity = item["alias_id"]
                if (not isinstance(identity, str) or not identity or identity in alias_ids
                        or identity in identities or item["canonical_id"] not in identities):
                    raise AssetLoadError("INVALID_DEFINITION", "source-definitions")
                alias_ids.add(identity)
        except (KeyError, TypeError) as exc:
            raise AssetLoadError("INVALID_DEFINITION", "source-definitions") from exc
        return definitions, aliases

    def inventory(self) -> AssetInventory:
        claims = self._records("source-claims", EvidenceClaim, ("source_resource_ids",))
        lessons = self.lessons_for(())
        definitions, aliases = self._definitions()
        counts = {state: sum(r.availability == state for r in self._resources.values())
                  for state in _AVAILABILITY}
        return AssetInventory(
            skill_count=len(self._skills), packaged_resource_count=counts["PACKAGED"],
            missing_resource_count=counts["MISSING"],
            upstream_only_resource_count=counts["UPSTREAM_ONLY"],
            unlicensed_resource_count=counts["UNLICENSED"],
            unique_definition_count=sum(d["definition_status"] == "FORMULA_LOCATED"
                                        for d in definitions),
            alias_count=len(aliases), claim_count=len(claims), lesson_count=len(lessons),
            locally_reproduced_claim_count=0, financial_falsification_count=0,
            source_factor_count=len(definitions),
            unresolved_definition_count=sum(d["definition_status"] == "UNRESOLVED"
                                            for d in definitions),
        )

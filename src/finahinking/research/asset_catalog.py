"""One bounded, hash-checked, read-only authority for installed research assets."""

from __future__ import annotations

import hashlib
import json
import re
from importlib.resources import files
from pathlib import Path, PurePosixPath

from .asset_contracts import AssetLoadError, ResourceRef, SkillDescriptor

_HASH = re.compile(r"[0-9a-f]{64}")
_SKILL_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_AVAILABILITY = {"PACKAGED", "UPSTREAM_ONLY", "MISSING", "UNLICENSED"}


class ResearchAssetCatalog:
    def __init__(self, root):
        self._root = root
        manifest = self._json(self._read_path("provenance/upstream-lock.json", 524288), "manifest")
        if manifest.get("schema_version") != "research-assets.v1":
            raise AssetLoadError("INVALID_MANIFEST", "manifest")
        self._resources = {}
        try:
            for item in manifest["resources"]:
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
        self._skills = self._load_skills()

    @classmethod
    def packaged(cls) -> ResearchAssetCatalog:
        return cls(files("finahinking").joinpath("_package_data", "research_assets"))

    @staticmethod
    def _json(text, resource_id):
        try:
            value = json.loads(text)
            if not isinstance(value, dict):
                raise TypeError("object required")
            return value
        except (ValueError, TypeError) as exc:
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
            if ref.license_id != "MIT" or not ref.license_ref or not ref.author:
                raise AssetLoadError("LICENSE_MISSING", resource_id)
            license_ref = self.get_resource(ref.license_ref)
            if license_ref.kind != "license":
                raise AssetLoadError("LICENSE_MISSING", resource_id)
            self._checked_bytes(license_ref, 65536)
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

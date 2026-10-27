"""Full, immutable method references, never executable model capabilities."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass

from .asset_contracts import AssetLoadError, MethodSelection
from .contracts import stable_digest

CONTEXT_MAX_BYTES = 32768
_HASH = re.compile(r"[0-9a-f]{64}")


class MethodContextError(ValueError):
    code = "BLOCKED_CONTEXT"

    def __init__(self):
        super().__init__("BLOCKED_CONTEXT: method context is invalid, incomplete or exceeds its total byte budget")


def _encode(payload):
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False)
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise MethodContextError() from exc


def _text(value):
    return isinstance(value, str) and bool(value.strip())


@dataclass(frozen=True, slots=True, repr=False)
class LoadedMethods:
    _json: str

    def __post_init__(self):
        try:
            payload = json.loads(self._json)
            if (not isinstance(payload, dict)
                    or set(payload) != {"schema_version", "status", "manifest", "bodies"}
                    or payload["schema_version"] != "loaded-methods.v1"
                    or payload["status"] not in {"READY", "BLOCKED_CONTEXT"}):
                raise MethodContextError()
            manifest, bodies = payload["manifest"], payload["bodies"]
            if (not isinstance(manifest, dict) or set(manifest) != {
                "schema_version", "selection_digest", "methods", "loaded", "missing",
                "skipped", "input_gaps",
            } or manifest["schema_version"] != "method-manifest.v1"
                    or not isinstance(manifest["selection_digest"], str)
                    or not _HASH.fullmatch(manifest["selection_digest"])
                    or not isinstance(bodies, dict)):
                raise MethodContextError()
            for name in ("methods", "loaded", "missing", "skipped", "input_gaps"):
                if not isinstance(manifest[name], list):
                    raise MethodContextError()
            if not all(_text(value) for value in manifest["input_gaps"]):
                raise MethodContextError()
            ids = set()
            kinds = {}
            for ref in manifest["loaded"]:
                if (not isinstance(ref, dict) or set(ref) != {
                    "resource_id", "sha256", "byte_size", "version", "kind", "load_reason",
                } or not all(_text(ref.get(name)) for name in (
                    "resource_id", "sha256", "version", "kind", "load_reason",
                )) or ref["resource_id"] in ids or not _HASH.fullmatch(ref["sha256"])
                        or type(ref["byte_size"]) is not int or ref["byte_size"] < 0):
                    raise MethodContextError()
                body = bodies.get(ref["resource_id"])
                if (not isinstance(body, str) or len(body.encode()) != ref["byte_size"]
                        or hashlib.sha256(body.encode()).hexdigest() != ref["sha256"]):
                    raise MethodContextError()
                ids.add(ref["resource_id"])
                kinds[ref["resource_id"]] = ref["kind"]
            if set(bodies) != ids:
                raise MethodContextError()
            methods = set()
            for method in manifest["methods"]:
                if (not isinstance(method, dict) or set(method) != {
                    "skill_id", "version", "main_resource", "policy_ref", "reason",
                } or not all(_text(value) for value in method.values())
                        or method["skill_id"] in methods):
                    raise MethodContextError()
                methods.add(method["skill_id"])
                if payload["status"] == "READY" and (
                    method["main_resource"] not in ids
                    or kinds.get(method["policy_ref"]) != "policy"
                ):
                    raise MethodContextError()
            for field in ("missing", "skipped"):
                for issue in manifest[field]:
                    if (not isinstance(issue, dict) or set(issue) != {"resource_id", "reason"}
                            or not all(_text(value) for value in issue.values())):
                        raise MethodContextError()
            encoded = _encode(payload)
            if payload["status"] == "READY":
                if not methods or manifest["missing"] or len(encoded.encode()) > CONTEXT_MAX_BYTES:
                    raise MethodContextError()
            elif bodies or manifest["loaded"] or not manifest["missing"]:
                raise MethodContextError()
            if len(encoded.encode()) > 131072:
                raise MethodContextError()
            object.__setattr__(self, "_json", encoded)
        except (KeyError, TypeError, ValueError, RecursionError) as exc:
            if isinstance(exc, MethodContextError):
                raise
            raise MethodContextError() from exc

    @classmethod
    def from_dict(cls, payload):
        return cls(_encode(payload))

    def to_dict(self):
        return json.loads(self._json)

    def to_json(self):
        return self._json

    @property
    def status(self):
        return self.to_dict()["status"]

    @property
    def actual_bytes(self):
        return len(self._json.encode("utf-8"))

    @property
    def fingerprint(self):
        return stable_digest(self.to_dict())

    @property
    def manifest_fingerprint(self):
        return stable_digest(self.to_dict()["manifest"])


def load_methods(selection, catalog, *, max_bytes=CONTEXT_MAX_BYTES) -> LoadedMethods:
    if not isinstance(selection, MethodSelection) or type(max_bytes) is not int or not 1 <= max_bytes <= CONTEXT_MAX_BYTES:
        raise MethodContextError()
    manifest = {"schema_version": "method-manifest.v1", "selection_digest": selection.fingerprint,
                "methods": [], "loaded": [], "missing": [], "skipped": [],
                "input_gaps": list(selection.missing_inputs)}
    payload = {"schema_version": "loaded-methods.v1", "status": "READY",
               "manifest": manifest, "bodies": {}}
    reasons = dict(selection.reasons)
    if selection.status != "READY":
        manifest["missing"].append({"resource_id": "selection", "reason": selection.status})
    for skill_id in selection.skill_ids:
        descriptor = catalog.get_skill(skill_id)
        if dict(selection.method_fingerprints).get(skill_id) != stable_digest(asdict(descriptor)):
            manifest["missing"].append({"resource_id": f"method:{skill_id}", "reason": "STALE_SELECTION"})
            continue
        manifest["methods"].append({"skill_id": skill_id, "version": descriptor.version,
                                    "main_resource": descriptor.main_resource,
                                    "policy_ref": descriptor.policy_ref,
                                    "reason": reasons[skill_id]})

    def read(ref, *, required):
        issues = manifest["missing"] if required else manifest["skipped"]
        try:
            current = catalog.get_resource(ref.resource_id)
            if (current.sha256, current.version, current.availability) != (
                ref.sha256, ref.version, ref.availability
            ):
                issues.append({"resource_id": ref.resource_id, "reason": "STALE_SELECTION"})
                return
            body = catalog.read_text(ref.resource_id)
        except AssetLoadError as exc:
            issues.append({"resource_id": ref.resource_id, "reason": exc.code})
            return
        item = {"resource_id": ref.resource_id, "sha256": ref.sha256,
                "version": ref.version, "kind": ref.kind, "byte_size": len(body.encode()),
                "load_reason": "required_by_selected_method" if required else "optional_related_resource"}
        manifest["loaded"].append(item)
        payload["bodies"][ref.resource_id] = body
        if not required and len(_encode(payload).encode()) > max_bytes:
            manifest["loaded"].pop()
            payload["bodies"].pop(ref.resource_id)
            issues.append({"resource_id": ref.resource_id, "reason": "OPTIONAL_BUDGET"})

    for ref in selection.required_resources:
        read(ref, required=True)
    for ref in selection.optional_resources:
        if ref.availability != "PACKAGED":
            manifest["skipped"].append({"resource_id": ref.resource_id, "reason": ref.availability})
        elif not manifest["missing"]:
            read(ref, required=False)
    if not manifest["missing"] and len(_encode(payload).encode()) > max_bytes:
        manifest["missing"].append({"resource_id": "required-method-context", "reason": "CONTEXT_BUDGET"})
    if manifest["missing"]:
        payload["status"] = "BLOCKED_CONTEXT"
        payload["bodies"] = {}
        manifest["loaded"] = []
    return LoadedMethods.from_dict(payload)

"""Explicit, content-bound external turns using the existing queue/artifact root."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from threading import RLock

from .codex_bridge import CodexBridge, CodexTaskEnvelope
from .context_bundle import ResearchContextBundle, merge_method_context
from .contracts import AgentOutcome, ResearchPlan, stable_digest, to_jsonable
from .method_context import LoadedMethods

_HASH = re.compile(r"[0-9a-f]{64}")
_PRIVATE = re.compile(
    r"file://|/(?:Users|home|private|tmp)/|(?:^|[\s\"'=:：])/(?!/)[^\s\"'<>]+|"
    r"(?:^|[\s\"'=])(?:[A-Za-z]:[\\/])|"
    r"sk-[A-Za-z0-9_-]{16,}|https?://[^/\s@]+@|"
    r"(?:api[_-]?key|authorization|password|access_token|token|secret)\s*[:=]\s*[\"']?[A-Za-z0-9_.-]{6,}",
    re.IGNORECASE,
)
_SECRET_KEYS = {"api_key", "authorization", "password", "credential", "secret", "access_token", "token",
                "client_secret", "refresh_token", "bearer_token", "passphrase", "credentials",
                "file_path", "path", "source_code", "pickle", "exec", "eval", "__import__"}
_SECRET_NORMALIZED_KEYS = {re.sub(r"[^a-z0-9]", "", key) for key in _SECRET_KEYS}
_OUTPUT_SCHEMAS = {"ResearchPlan": "research-plan.v1", "FactorProposal": "factor-proposal.v1"}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _private(value, depth=0):
    if depth > 32:
        return True
    if isinstance(value, Mapping):
        return any(not isinstance(key, str) or re.sub(r"[^a-z0-9]", "", key.casefold()) in _SECRET_NORMALIZED_KEYS
                   or _private(item, depth + 1) for key, item in value.items())
    if isinstance(value, (tuple, list)):
        return any(_private(item, depth + 1) for item in value)
    return isinstance(value, str) and bool(_PRIVATE.search(value))


@dataclass(frozen=True, slots=True, repr=False)
class MethodHandoffPackage:
    _json: str

    def __post_init__(self):
        try:
            payload = json.loads(self._json)
            if (not isinstance(payload, dict) or set(payload) != {
                "schema_version", "envelope", "context", "manifest", "package_digest",
            } or payload["schema_version"] != "method-handoff.v1" or _private(payload)):
                raise ValueError("method handoff boundary is invalid")
            raw = payload["envelope"]
            if (not isinstance(raw, dict) or raw.get("tool_names") != []
                    or raw.get("paper_only") is not True or raw.get("schema_version") != "codex-task.v1"):
                raise ValueError("method envelope authority is invalid")
            envelope = CodexTaskEnvelope(**raw)
            context = ResearchContextBundle(_json(payload["context"]))
            loaded = LoadedMethods.from_dict(context.to_dict()["method_context"])
            if (loaded.status != "READY" or len(context._json.encode()) > 32768
                    or envelope.context_digest != context.fingerprint
                    or context.to_dict().get("role") != envelope.role
                    or context.to_dict().get("schema_version") != "research-context.v1"
                    or not isinstance(context.to_dict().get("evidence_index"), dict)):
                raise ValueError("method context binding is invalid")
            if payload["manifest"] != {
                "schema_version": "method-handoff-manifest.v1",
                "methods_digest": loaded.fingerprint,
                "method_manifest_digest": loaded.manifest_fingerprint,
                "allowed_output_types": list(_OUTPUT_SCHEMAS),
            }:
                raise ValueError("method manifest binding is invalid")
            if payload["package_digest"] != stable_digest({k: v for k, v in payload.items()
                                                          if k != "package_digest"}):
                raise ValueError("method package integrity mismatch")
            object.__setattr__(self, "_json", _json(payload))
        except (KeyError, TypeError, RecursionError) as exc:
            raise ValueError("method package structure is invalid") from exc

    @classmethod
    def from_dict(cls, payload):
        return cls(_json(payload))

    def to_dict(self):
        return json.loads(self._json)

    @property
    def envelope(self):
        return CodexTaskEnvelope(**self.to_dict()["envelope"])

    @property
    def context(self):
        return ResearchContextBundle(_json(self.to_dict()["context"]))

    @property
    def package_digest(self):
        return self.to_dict()["package_digest"]


def build_method_handoff(task, context, loaded_methods) -> MethodHandoffPackage:
    combined = merge_method_context(context, loaded_methods)
    base = CodexBridge().create_handoff(task)
    envelope = replace(base, context_digest=combined.fingerprint,
                       prompt_digest=stable_digest({"base": base.prompt_digest,
                                                    "context": combined.fingerprint}))
    payload = {"schema_version": "method-handoff.v1", "envelope": envelope.to_dict(),
               "context": combined.to_dict(), "manifest": {
                   "schema_version": "method-handoff-manifest.v1",
                   "methods_digest": loaded_methods.fingerprint,
                   "method_manifest_digest": loaded_methods.manifest_fingerprint,
                   "allowed_output_types": list(_OUTPUT_SCHEMAS),
               }}
    payload["package_digest"] = stable_digest(payload)
    return MethodHandoffPackage.from_dict(payload)


def _rejected(envelope, failure):
    return CodexBridge._outcome(envelope, status="REJECTED", failure_kind=failure)


def _evidence_members(value, allowed):
    if isinstance(value, dict):
        for name, item in value.items():
            if name in {"evidence_refs", "supporting_evidence_refs"}:
                if (not isinstance(item, list) or not all(isinstance(ref, str) for ref in item)
                        or not set(item) <= allowed):
                    raise ValueError("artifact evidence is outside the authorized input index")
            elif name in {"evidence_bindings", "evidence_index"} and (
                not isinstance(item, dict) or not set(item) <= allowed
            ):
                raise ValueError("artifact evidence binding is outside the authorized input index")
            _evidence_members(item, allowed)
    elif isinstance(value, list):
        for item in value:
            _evidence_members(item, allowed)


def _outputs(package, response, *, allowed_evidence_refs=None):
    outputs = response.get("output_artifacts", [])
    if not isinstance(outputs, list) or len(outputs) > 2:
        raise ValueError("bounded typed outputs required")
    seen = set()
    validated = []
    for output in outputs:
        if (not isinstance(output, dict) or set(output) != {
            "artifact_type", "schema_version", "input_digest", "content_digest", "payload",
        } or len(_json(output).encode()) > 65536 or _private(output)):
            raise ValueError("typed output boundary is invalid")
        kind = output["artifact_type"]
        if (kind not in _OUTPUT_SCHEMAS or kind in seen
                or output["schema_version"] != _OUTPUT_SCHEMAS[kind]
                or output["input_digest"] != package.envelope.input_digest
                or output["content_digest"] != stable_digest(output["payload"])):
            raise ValueError("typed output binding is invalid")
        seen.add(kind)
        payload = output["payload"]
        if not isinstance(payload, dict):
            raise TypeError("typed output must be an object")
        allowed = set(package.context.evidence_index)
        if allowed_evidence_refs is not None:
            allowed &= set(allowed_evidence_refs)
        _evidence_members(payload, allowed)
        if kind == "ResearchPlan":
            if set(payload) != {"hypotheses", "required_datasets", "factor_ids",
                                "validation_spec", "risk_policy_version"}:
                raise ValueError("research plan schema is invalid")
            to_jsonable(ResearchPlan(**payload))
        else:
            from .factor_proposals import FactorProposal, validate_factor_proposal

            if set(payload) != {"proposal_id", "expression", "source_hypothesis", "required_fields",
                                "constraints", "paper_only"}:
                raise ValueError("factor proposal schema is invalid")
            proposal = FactorProposal(**{**payload, "required_fields": tuple(payload["required_fields"])})
            validate_factor_proposal(proposal)
        # Existing content-addressed artifact validation rejects executable keys,
        # non-finite numbers and unsupported values without running the payload.
        from finahinking.quant.artifacts import Artifact

        Artifact.create("method-output", kind, payload)
        validated.append(output)
    return tuple(sorted(validated, key=lambda item: item["artifact_type"]))


def _normalized_response(package, response):
    result = json.loads(_json(response["result"]))
    result["evidence_refs"] = sorted(result.get("evidence_refs", []))
    if "capabilities" in result:
        result["capabilities"] = sorted(result["capabilities"])
    return {"schema_version": "method-result.v1", "package_digest": package.package_digest,
            "result": result, "output_artifacts": list(_outputs(package, response))}


def accept_method_result(package, response, *, allowed_evidence_refs) -> AgentOutcome:
    if not isinstance(package, MethodHandoffPackage):
        raise TypeError("package must be a MethodHandoffPackage")
    envelope = package.envelope
    if response is None:
        return CodexBridge().accept_result(envelope, None)
    try:
        if (not isinstance(response, dict) or set(response) - {
            "schema_version", "package_digest", "result", "output_artifacts",
        } or response.get("schema_version") != "method-result.v1"
                or len(_json(response).encode()) > 262144 or _private(response)):
            return _rejected(envelope, "METHOD_RESULT_SCHEMA_ERROR")
        if response.get("package_digest") != package.package_digest:
            return _rejected(envelope, "PACKAGE_DIGEST_MISMATCH")
        result = response.get("result")
        if not isinstance(result, dict) or any(result.get(key) != getattr(envelope, key)
                                               for key in ("task_id", "role", "input_digest")):
            return _rejected(envelope, "TASK_BINDING_MISMATCH")
        refs = result.get("evidence_refs", [])
        if (not isinstance(refs, list) or not all(isinstance(ref, str) for ref in refs)
                or not set(refs) <= set(package.context.evidence_index)
                or not set(refs) <= set(allowed_evidence_refs)):
            return _rejected(envelope, "EVIDENCE_MEMBERSHIP_MISMATCH")
        outputs = _outputs(package, response, allowed_evidence_refs=allowed_evidence_refs)
        canonical = _normalized_response(package, response)
        outcome = CodexBridge().accept_result(envelope, canonical["result"])
        if outcome.status == "REJECTED":
            return outcome
        if outcome.status not in {"READY", "ANALYZED", "FAILED", "BLOCKED_DATA", "VALIDATION_FAILED"}:
            return _rejected(envelope, "METHOD_RESULT_STATUS_ERROR")
        if outputs and outcome.status not in {"READY", "ANALYZED"}:
            return _rejected(envelope, "METHOD_RESULT_STATUS_ERROR")
        return replace(outcome, output_digest=stable_digest({"v1_result": outcome.output_digest,
                        "package_digest": package.package_digest,
                        "outputs": [item["content_digest"] for item in outputs]}))
    except (TypeError, ValueError, KeyError, RecursionError, OverflowError):
        return _rejected(envelope, "METHOD_ARTIFACT_INVALID")


class MethodHandoffStore:
    """Private immutable files plus existing external_dispatches; no second queue."""

    def __init__(self, root, queue):
        self.root = Path(root)
        self.queue = queue
        self._lock = RLock()

    @staticmethod
    def _digest(reference):
        if not isinstance(reference, str) or not reference.startswith("method-handoff:"):
            raise ValueError("method handoff reference is invalid")
        digest = reference.removeprefix("method-handoff:")
        if not _HASH.fullmatch(digest):
            raise ValueError("method handoff reference is invalid")
        return digest

    def _path(self, folder, digest):
        if not isinstance(digest, str) or not _HASH.fullmatch(digest):
            raise ValueError("method artifact digest is invalid")
        path = self.root / folder
        if self.root.is_symlink() or path.is_symlink():
            raise ValueError("method artifact directory is invalid")
        return path / f"{digest}.json"

    @staticmethod
    def _read(path):
        from .application_service import _read_json

        try:
            return _read_json(path)
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            raise ValueError("method artifact is unavailable or unreadable") from exc

    def _save(self, path, value):
        from .application_service import _write_private_json

        try:
            _write_private_json(path, value)
        except FileExistsError:
            if self._read(path) != value:
                raise ValueError("immutable method artifact conflict") from None

    def export(self, package):
        if not isinstance(package, MethodHandoffPackage):
            raise TypeError("package must be a MethodHandoffPackage")
        digest = stable_digest(package.envelope.to_dict())
        self._save(self._path("method-handoffs", digest), package.to_dict())
        CodexBridge(queue=self.queue).enqueue(package.envelope)
        return f"method-handoff:{digest}"

    def load(self, reference):
        digest = self._digest(reference)
        package = MethodHandoffPackage.from_dict(self._read(self._path("method-handoffs", digest)))
        if stable_digest(package.envelope.to_dict()) != digest:
            raise ValueError("method handoff reference binding is invalid")
        return package

    def _receipt(self, reference):
        path = self._path("method-results", self._digest(reference))
        if not path.exists():
            record = self.queue.external_dispatch(self._digest(reference))
            if record is not None and record["status"] not in {"external_waiting", "queued"}:
                raise ValueError("accepted method receipt is unavailable")
            return None
        receipt = self._read(path)
        package = self.load(reference)
        if (set(receipt) != {"schema_version", "package_digest", "response_digest", "artifacts", "result", "outcome"}
                or receipt["schema_version"] != "method-receipt.v1"
                or receipt["package_digest"] != package.package_digest):
            raise ValueError("method receipt structure is invalid")
        outputs = self._artifact_outputs(package, receipt)
        response = {"schema_version": "method-result.v1", "package_digest": package.package_digest,
                    "result": receipt["result"], "output_artifacts": outputs}
        normalized = _normalized_response(package, response)
        outcome = accept_method_result(package, normalized,
                                       allowed_evidence_refs=tuple(package.context.evidence_index))
        if (outcome.status == "REJECTED" or to_jsonable(outcome) != receipt["outcome"]
                or stable_digest(normalized) != receipt["response_digest"]):
            raise ValueError("method receipt integrity mismatch")
        record = self.queue.external_dispatch(self._digest(reference))
        if record is not None and record["status"] not in {"external_waiting", "queued"} and (
            record["result_digest"] != receipt["response_digest"]
            or record["output_digest"] != outcome.output_digest or record["status"] != outcome.status
        ):
            raise ValueError("method queue receipt binding mismatch")
        return receipt

    def _artifact_outputs(self, package, receipt):
        if not isinstance(receipt["artifacts"], list) or len(receipt["artifacts"]) > 2:
            raise ValueError("method artifact index is invalid")
        outputs = []
        for item in receipt["artifacts"]:
            if not isinstance(item, dict) or set(item) != {
                "artifact_ref", "artifact_type", "artifact_digest", "content_digest",
            }:
                raise ValueError("method artifact index is invalid")
            digest = item["artifact_digest"]
            document = self._read(self._path("method-artifacts", digest))
            if (set(document) != {"schema_version", "package_digest", "task_id", "output"}
                    or document["schema_version"] != "method-output.v1"
                    or not isinstance(document["output"], dict)
                    or stable_digest(document) != digest or document.get("package_digest") != package.package_digest
                    or document.get("task_id") != package.envelope.task_id
                    or item["artifact_ref"] != f"artifact:method-{digest}"
                    or item["artifact_type"] != document["output"]["artifact_type"]
                    or item["content_digest"] != document["output"]["content_digest"]):
                raise ValueError("method output artifact integrity mismatch")
            outputs.append(document["output"])
        return outputs

    def status(self, reference):
        package = self.load(reference)
        record = self.queue.external_dispatch(self._digest(reference))
        if record is None:
            raise ValueError("unknown persisted method handoff")
        receipt = self._receipt(reference)
        if receipt is not None and record["status"] in {"external_waiting", "queued"}:
            outcome = AgentOutcome(**receipt["outcome"])
            self.queue.record_external_result(self._digest(reference), status=outcome.status,
                result_digest=receipt["response_digest"], failure_kind=outcome.failure_kind,
                output_digest=outcome.output_digest)
            record = self.queue.external_dispatch(self._digest(reference))
        status = record["status"]
        return {"status": "EXTERNAL_HANDOFF_REQUIRED" if status in {"external_waiting", "queued"} else status,
                "package_digest": package.package_digest, "output_digest": record["output_digest"]}

    def artifacts(self, reference):
        self.status(reference)
        package = self.load(reference)
        receipt = self._receipt(reference)
        if receipt is None:
            return ()
        if receipt["package_digest"] != package.package_digest:
            raise ValueError("method result package binding is invalid")
        outputs = self._artifact_outputs(package, receipt)
        return tuple({**item, "payload": output["payload"]}
                     for item, output in zip(receipt["artifacts"], outputs, strict=True))

    def accept(self, reference, response):
        with self._lock:
            package = self.load(reference)
            digest = self._digest(reference)
            if self.queue.external_dispatch(digest) is None:
                return _rejected(package.envelope, "UNKNOWN_HANDOFF")
            outcome = accept_method_result(package, response,
                                           allowed_evidence_refs=tuple(package.context.evidence_index))
            if response is None or outcome.status == "REJECTED":
                return outcome
            normalized = _normalized_response(package, response)
            response_digest = stable_digest(normalized)
            prior = self._receipt(reference)
            if prior is not None and prior["response_digest"] != response_digest:
                return _rejected(package.envelope, "METHOD_RESULT_CONFLICT")
            artifacts = []
            for output in _outputs(package, response):
                document = {"schema_version": "method-output.v1", "package_digest": package.package_digest,
                            "task_id": package.envelope.task_id, "output": output}
                artifact_digest = stable_digest(document)
                self._save(self._path("method-artifacts", artifact_digest), document)
                artifacts.append({"artifact_ref": f"artifact:method-{artifact_digest}",
                                  "artifact_type": output["artifact_type"], "artifact_digest": artifact_digest,
                                  "content_digest": output["content_digest"]})
            receipt = {"schema_version": "method-receipt.v1", "package_digest": package.package_digest,
                       "response_digest": response_digest, "artifacts": artifacts,
                       "result": normalized["result"], "outcome": to_jsonable(outcome)}
            try:
                self._save(self._path("method-results", digest), receipt)
            except ValueError:
                return _rejected(package.envelope, "METHOD_RESULT_CONFLICT")
            self.queue.record_external_result(digest, status=outcome.status,
                result_digest=response_digest, failure_kind=outcome.failure_kind,
                output_digest=outcome.output_digest)
            return outcome

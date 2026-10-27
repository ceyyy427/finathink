"""Explicit Codex handoff contracts for the offline research runtime.

This module creates an envelope for an external turn; it never calls the
current Codex session and never treats a missing callback as a successful
research outcome.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .contracts import AgentOutcome, stable_digest

_FORBIDDEN_CAPABILITY_PARTS = frozenset(
    {"account", "broker", "call", "cancel", "execute", "filesystem", "live", "network", "order", "shell", "tool", "write"}
)
_CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_ALLOWED_CAPABILITIES = frozenset(
    {
        "paper_only",
        "structured_output",
        "evidence_refs",
        "research_observation",
        "reasoning",
        "deterministic_gateway",
        "deterministic_risk_gateway",
        "deterministic_portfolio_gateway",
        "deterministic_paper_gateway",
        "risk_gateway",
        "portfolio_gateway",
        "paper_gateway",
    }
)
_ARTIFACT_REF_RE = re.compile(r"^(?:artifact:[A-Za-z0-9][A-Za-z0-9._:-]{0,127}|(?:digest|sha256|evidence):[0-9a-f]{64})$")
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "credential",
        "endpoint",
        "file_path",
        "path",
        "password",
        "prompt",
        "raw_provider_object",
        "raw_provider_response",
        "secret",
        "token",
    }
)
_SENSITIVE_TEXT = re.compile(
    r"(?:api[-_]?key|authorization|password|secret|token|https?://|/Users/|/private/|prompt)",
    re.IGNORECASE,
)


def _field(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _nonempty(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return value.strip()


def _capabilities(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError("capabilities must be a sequence of strings")
    normalized: list[str] = []
    for item in value:
        if type(item) is not str or not item.strip():
            raise TypeError("capabilities must contain only non-empty strings")
        name = item.strip()
        lowered = name.casefold()
        if not _CAPABILITY_RE.fullmatch(lowered) or name not in _ALLOWED_CAPABILITIES:
            raise ValueError("capability is outside the research boundary")
        if any(part in lowered.replace("_", " ").split() for part in _FORBIDDEN_CAPABILITY_PARTS):
            raise ValueError("capability is outside the research boundary")
        normalized.append(name)
    if len(set(normalized)) != len(normalized):
        raise ValueError("capabilities must not contain duplicates")
    return tuple(sorted(normalized))


@dataclass(frozen=True, slots=True)
class CodexTaskEnvelope:
    task_id: str
    role: str
    input_digest: str
    context_digest: str
    prompt_digest: str
    capabilities: tuple[str, ...] = ()
    workflow_version: str = "research.v1"
    schema_version: str = "codex-task.v1"
    paper_only: bool = True
    tool_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("task_id", "role", "input_digest", "context_digest", "prompt_digest", "workflow_version", "schema_version"):
            object.__setattr__(self, name, _nonempty(getattr(self, name), name))
        object.__setattr__(self, "role", self.role.casefold())
        object.__setattr__(self, "capabilities", _capabilities(self.capabilities))
        if self.paper_only is not True:
            raise ValueError("Codex handoff must be paper-only")
        object.__setattr__(self, "tool_names", ())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "role": self.role,
            "input_digest": self.input_digest,
            "context_digest": self.context_digest,
            "prompt_digest": self.prompt_digest,
            "capabilities": list(self.capabilities),
            "workflow_version": self.workflow_version,
            "paper_only": True,
            "tool_names": [],
        }


@dataclass(frozen=True, slots=True)
class CodexDispatchRecord:
    """Digest-only record of an external Codex turn."""

    task_id: str
    envelope_digest: str
    status: str
    external_ref: str
    result_digest: str = ""


def _contains_sensitive(value: object) -> bool:
    if value is None or isinstance(value, (bool, int)):
        return False
    if isinstance(value, float):
        return not math.isfinite(value)
    if not isinstance(value, (Mapping, list, str)):
        return True
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                return True
            if str(key).casefold() in _SENSITIVE_KEYS:
                return True
            if _contains_sensitive(item):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive(item) for item in value)
    elif isinstance(value, str):
        return _SENSITIVE_TEXT.search(value) is not None
    return False


class CodexBridge:
    def __init__(self, *, schema_version: str = "codex-task.v1", workflow_version: str = "research.v1", queue: Any | None = None) -> None:
        self.schema_version = _nonempty(schema_version, "schema_version")
        self.workflow_version = _nonempty(workflow_version, "workflow_version")
        self._accepted: set[str] = set()
        self._dispatches: dict[str, CodexDispatchRecord] = {}
        self._outcomes: dict[str, AgentOutcome] = {}
        self._queue = queue

    def create_handoff(self, task: object) -> CodexTaskEnvelope:
        task_id = _nonempty(_field(task, "task_id"), "task_id")
        role = _nonempty(_field(task, "role"), "role").casefold()
        input_digest = _nonempty(_field(task, "input_digest"), "input_digest")
        capabilities = _capabilities(_field(task, "capabilities", ()))
        context_value = _field(task, "context_digest")
        if context_value is None:
            context_value = stable_digest({"task_id": task_id, "role": role, "input_digest": input_digest})
        prompt_value = _field(task, "prompt_digest")
        if prompt_value is None:
            raw_prompt = _field(task, "prompt")
            prompt_value = stable_digest(raw_prompt if raw_prompt is not None else {"task_id": task_id, "role": role})
        return CodexTaskEnvelope(
            task_id=task_id,
            role=role,
            input_digest=input_digest,
            context_digest=_nonempty(context_value, "context_digest"),
            prompt_digest=_nonempty(prompt_value, "prompt_digest"),
            capabilities=capabilities,
            workflow_version=_nonempty(_field(task, "workflow_version", self.workflow_version), "workflow_version"),
            schema_version=self.schema_version,
        )

    def enqueue(self, envelope: CodexTaskEnvelope) -> CodexDispatchRecord:
        if not isinstance(envelope, CodexTaskEnvelope):
            raise TypeError("envelope must be a CodexTaskEnvelope")
        digest = stable_digest(envelope.to_dict())
        previous = self._dispatches.get(digest)
        if previous is not None:
            return previous
        external_ref = f"codex:{digest[:32]}"
        if self._queue is not None:
            self._queue.enqueue_external(envelope, idempotency_key=external_ref)
            persisted = self._queue.external_dispatch(digest)
            if persisted is not None:
                record = CodexDispatchRecord(envelope.task_id, digest, "EXTERNAL_HANDOFF_REQUIRED" if persisted["status"] == "external_waiting" else persisted["status"], persisted["external_ref"], persisted.get("result_digest", ""))
                self._dispatches[digest] = record
                return record
        record = CodexDispatchRecord(envelope.task_id, digest, "EXTERNAL_HANDOFF_REQUIRED", external_ref)
        self._dispatches[digest] = record
        return record

    def record_external_result(self, envelope: CodexTaskEnvelope, result: Mapping[str, Any] | None) -> AgentOutcome:
        if not isinstance(envelope, CodexTaskEnvelope):
            raise TypeError("envelope must be a CodexTaskEnvelope")
        digest = stable_digest(envelope.to_dict())
        prior = self._outcomes.get(digest)
        if prior is not None:
            return prior
        if self._queue is not None:
            persisted = self._queue.external_dispatch(digest)
            if persisted is not None and persisted.get("status") not in {"external_waiting", "queued"}:
                return self._outcome(envelope, status=persisted["status"], failure_kind=persisted.get("failure_kind"), output_digest=persisted.get("output_digest", ""))
        outcome = self.accept_result(envelope, result)
        if outcome.status not in {"EXTERNAL_HANDOFF_REQUIRED", "REJECTED"}:
            self._outcomes[digest] = outcome
            record = self._dispatches.get(digest)
            if record is not None:
                self._dispatches[digest] = CodexDispatchRecord(
                    record.task_id, record.envelope_digest, outcome.status, record.external_ref,
                    outcome.output_digest,
                )
            if self._queue is not None:
                self._queue.record_external_result(
                    digest,
                    status=outcome.status,
                    result_digest=stable_digest(result),
                    failure_kind=str(outcome.failure_kind) if outcome.failure_kind is not None else None,
                    output_digest=outcome.output_digest,
                )
        return outcome

    def accept_result(self, envelope: CodexTaskEnvelope, result: Mapping[str, Any] | None) -> AgentOutcome:
        if not isinstance(envelope, CodexTaskEnvelope):
            raise TypeError("envelope must be a CodexTaskEnvelope")
        envelope_key = stable_digest(envelope.to_dict())
        if envelope_key in self._accepted:
            return self._outcome(envelope, status="REJECTED", failure_kind="DUPLICATE_RESULT")
        if result is None:
            return self._outcome(envelope, status="EXTERNAL_HANDOFF_REQUIRED", failure_kind="EXTERNAL_HANDOFF_REQUIRED")
        if not isinstance(result, Mapping):
            return self._outcome(envelope, status="REJECTED", failure_kind="SCHEMA_ERROR")
        if _contains_sensitive(result):
            return self._outcome(envelope, status="REJECTED", failure_kind="ARTIFACT_BOUNDARY_VIOLATION")
        allowed_keys = frozenset(
            {"schema_version", "input_digest", "task_id", "role", "status", "failure_kind", "evidence_refs", "capabilities", "paper_only", "output_digest"}
        )
        if set(result) - allowed_keys:
            return self._outcome(envelope, status="REJECTED", failure_kind="SCHEMA_ERROR")
        if result.get("schema_version") != envelope.schema_version:
            return self._outcome(envelope, status="REJECTED", failure_kind="SCHEMA_ERROR")
        if result.get("input_digest") != envelope.input_digest:
            return self._outcome(envelope, status="REJECTED", failure_kind="INPUT_DIGEST_MISMATCH")
        if result.get("task_id", envelope.task_id) != envelope.task_id or result.get("role", envelope.role) != envelope.role:
            return self._outcome(envelope, status="REJECTED", failure_kind="SCHEMA_ERROR")
        if "paper_only" not in result or result["paper_only"] is not True:
            return self._outcome(envelope, status="REJECTED", failure_kind="PAPER_ONLY_VIOLATION")
        try:
            result_capabilities = _capabilities(result.get("capabilities", envelope.capabilities))
        except (TypeError, ValueError):
            return self._outcome(envelope, status="REJECTED", failure_kind="CAPABILITY_DENIED")
        if not set(result_capabilities).issubset(envelope.capabilities):
            return self._outcome(envelope, status="REJECTED", failure_kind="CAPABILITY_DENIED")
        if "status" not in result:
            return self._outcome(envelope, status="REJECTED", failure_kind="SCHEMA_ERROR")
        status = result["status"]
        if not isinstance(status, str) or not status.strip():
            return self._outcome(envelope, status="REJECTED", failure_kind="SCHEMA_ERROR")
        failure_kind = result.get("failure_kind")
        if failure_kind is not None and (not isinstance(failure_kind, str) or not failure_kind.strip()):
            return self._outcome(envelope, status="REJECTED", failure_kind="SCHEMA_ERROR")
        refs = result.get("evidence_refs", ())
        if isinstance(refs, (str, bytes)) or not isinstance(refs, Sequence) or any(not isinstance(item, str) or not item.strip() for item in refs):
            return self._outcome(envelope, status="REJECTED", failure_kind="SCHEMA_ERROR")
        if any(not _ARTIFACT_REF_RE.fullmatch(item.strip()) for item in refs):
            return self._outcome(envelope, status="REJECTED", failure_kind="ARTIFACT_BOUNDARY_VIOLATION")
        if "output_digest" in result and (not isinstance(result["output_digest"], str) or not re.fullmatch(r"[0-9a-f]{64}", result["output_digest"])):
            return self._outcome(envelope, status="REJECTED", failure_kind="SCHEMA_ERROR")
        outcome = self._outcome(
            envelope,
            status=status,
            failure_kind=failure_kind,
            evidence_refs=tuple(sorted({item.strip() for item in refs})),
            output_digest=stable_digest({"status": status.strip(), "failure_kind": failure_kind, "evidence_refs": tuple(refs)}),
        )
        self._accepted.add(envelope_key)
        return outcome

    @staticmethod
    def _outcome(
        envelope: CodexTaskEnvelope,
        *,
        status: str,
        failure_kind: str | None = None,
        evidence_refs: tuple[str, ...] = (),
        output_digest: str = "",
    ) -> AgentOutcome:
        return AgentOutcome(
            role=envelope.role,
            task_id=envelope.task_id,
            input_digest=envelope.input_digest,
            capabilities=envelope.capabilities,
            status=status,
            failure_kind=failure_kind,
            message_digest=stable_digest({"status": status, "failure_kind": failure_kind, "task_id": envelope.task_id}),
            evidence_refs=evidence_refs,
            output_digest=output_digest,
            paper_only=True,
        )


__all__ = ["CodexBridge", "CodexDispatchRecord", "CodexTaskEnvelope"]

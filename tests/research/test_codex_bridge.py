from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from finahinking.research.codex_bridge import CodexBridge, CodexTaskEnvelope


@dataclass(frozen=True)
class Task:
    task_id: str = "task-1"
    role: str = "technical"
    input_digest: str = "input-digest"
    context_digest: str = "context-digest"
    prompt_digest: str = "prompt-digest"
    capabilities: tuple[str, ...] = ("structured_output", "paper_only")
    workflow_version: str = "research.v1"


def test_create_handoff_contains_only_digests_capabilities_and_paper_boundary() -> None:
    bridge = CodexBridge()
    envelope = bridge.create_handoff(Task())

    assert isinstance(envelope, CodexTaskEnvelope)
    payload = envelope.to_dict()
    assert payload["task_id"] == "task-1"
    assert payload["input_digest"] == "input-digest"
    assert payload["context_digest"] == "context-digest"
    assert payload["prompt_digest"] == "prompt-digest"
    assert payload["paper_only"] is True
    assert payload["tool_names"] == []
    assert "full prompt" not in json.dumps(payload).casefold()


def test_accept_result_requires_external_handoff_when_result_is_missing() -> None:
    bridge = CodexBridge()
    envelope = bridge.create_handoff(Task())
    outcome = bridge.accept_result(envelope, None)

    assert outcome.status == "EXTERNAL_HANDOFF_REQUIRED"
    assert outcome.failure_kind == "EXTERNAL_HANDOFF_REQUIRED"
    assert outcome.input_digest == envelope.input_digest
    assert outcome.message_digest


@pytest.mark.parametrize("reference,expected", [
    ("evidence:" + "a" * 64, "READY"),
    ("evidence:short", "REJECTED"),
    ("evidence:" + "a" * 64 + "/private", "REJECTED"),
])
def test_context_evidence_digest_reference_is_narrowly_supported(reference, expected):
    bridge = CodexBridge()
    envelope = bridge.create_handoff(Task())
    result = {"schema_version": envelope.schema_version, "input_digest": envelope.input_digest,
              "status": "READY", "paper_only": True, "evidence_refs": [reference]}
    assert bridge.accept_result(envelope, result).status == expected


def test_accept_result_rejects_digest_mismatch_without_echoing_values() -> None:
    bridge = CodexBridge()
    envelope = bridge.create_handoff(Task())
    outcome = bridge.accept_result(
        envelope,
        {"schema_version": envelope.schema_version, "input_digest": "wrong-digest", "status": "READY"},
    )
    assert outcome.status == "REJECTED"
    assert outcome.failure_kind == "INPUT_DIGEST_MISMATCH"
    assert "wrong-digest" not in json.dumps(outcome, default=str)


def test_accept_result_rejects_capability_overreach_and_artifact_boundary() -> None:
    bridge = CodexBridge()
    envelope = bridge.create_handoff(Task())
    outcome = bridge.accept_result(
        envelope,
        {
            "schema_version": envelope.schema_version,
            "input_digest": envelope.input_digest,
            "capabilities": ["execute_order"],
            "endpoint": "https://secret.example",
            "status": "READY",
        },
    )
    assert outcome.status == "REJECTED"
    assert outcome.failure_kind in {"CAPABILITY_DENIED", "ARTIFACT_BOUNDARY_VIOLATION"}
    assert "https://secret.example" not in json.dumps(outcome, default=str)


def test_accept_result_rejects_sensitive_text_inside_evidence_refs() -> None:
    bridge = CodexBridge()
    envelope = bridge.create_handoff(Task())
    outcome = bridge.accept_result(
        envelope,
        {
            "schema_version": envelope.schema_version,
            "input_digest": envelope.input_digest,
            "evidence_refs": ["/Users/private/report.json"],
            "status": "READY",
        },
    )
    assert outcome.failure_kind == "ARTIFACT_BOUNDARY_VIOLATION"


def test_accept_result_rejects_schema_error_and_duplicate_result() -> None:
    bridge = CodexBridge()
    envelope = bridge.create_handoff(Task())
    valid = {"schema_version": envelope.schema_version, "input_digest": envelope.input_digest, "status": "READY", "paper_only": True, "evidence_refs": ["artifact:1"]}
    first = bridge.accept_result(envelope, valid)
    second = bridge.accept_result(envelope, valid)

    assert first.status == "READY"
    assert first.failure_kind is None
    assert second.status == "REJECTED"
    assert second.failure_kind == "DUPLICATE_RESULT"

    malformed = bridge.accept_result(
        CodexBridge().create_handoff(Task(task_id="task-2")),
        {"schema_version": "wrong", "input_digest": Task().input_digest, "status": "READY"},
    )
    assert malformed.failure_kind == "SCHEMA_ERROR"


def test_invalid_result_does_not_consume_envelope_and_valid_retry_is_accepted() -> None:
    bridge = CodexBridge()
    envelope = bridge.create_handoff(Task())
    invalid = bridge.accept_result(
        envelope,
        {"schema_version": envelope.schema_version, "input_digest": "wrong-digest", "status": "READY", "paper_only": True},
    )
    valid = bridge.accept_result(
        envelope,
        {"schema_version": envelope.schema_version, "input_digest": envelope.input_digest, "status": "READY", "paper_only": True},
    )
    assert invalid.failure_kind == "INPUT_DIGEST_MISMATCH"
    assert valid.status == "READY"
    assert valid.failure_kind is None


@pytest.mark.parametrize(
    "result",
    [
        {"schema_version": "codex-task.v1", "input_digest": "input-digest", "status": "READY"},
        {"schema_version": "codex-task.v1", "input_digest": "input-digest", "status": "READY", "paper_only": False},
        {"schema_version": "codex-task.v1", "input_digest": "input-digest", "status": "READY", "unknown": "field", "paper_only": True},
        {"schema_version": "codex-task.v1", "input_digest": "input-digest", "status": "READY", "paper_only": True, "extra": object()},
    ],
)
def test_result_schema_is_closed_and_requires_explicit_paper_only(result: dict[str, object]) -> None:
    envelope = CodexBridge().create_handoff(Task())
    outcome = CodexBridge().accept_result(envelope, result)
    assert outcome.status == "REJECTED"
    assert outcome.failure_kind in {"SCHEMA_ERROR", "PAPER_ONLY_VIOLATION", "ARTIFACT_BOUNDARY_VIOLATION"}


@pytest.mark.parametrize("ref", ["/tmp/report.json", "file:///tmp/report.json", "C:\\Users\\me\\report.json", "s3://bucket/report", "https://example/report"])
def test_evidence_refs_are_digest_or_artifact_only(ref: str) -> None:
    envelope = CodexBridge().create_handoff(Task())
    outcome = CodexBridge().accept_result(
        envelope,
        {"schema_version": envelope.schema_version, "input_digest": envelope.input_digest, "status": "READY", "paper_only": True, "evidence_refs": [ref]},
    )
    assert outcome.failure_kind == "ARTIFACT_BOUNDARY_VIOLATION"


@pytest.mark.parametrize("capability", ["execute_order", "execute-order", "tool-call", "network-call", "https://example", "broker", "filesystem", "network", "live"])
def test_create_handoff_rejects_non_research_capabilities(capability: str) -> None:
    with pytest.raises(ValueError, match="capability"):
        CodexBridge().create_handoff(Task(capabilities=(capability,)))

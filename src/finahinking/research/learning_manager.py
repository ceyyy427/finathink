"""Settlement-aware, proposal-only learning manager.

The manager can suggest bounded changes after paper settlement.  It has no
registry, risk-policy, or production writer: only an explicit typed admission
record turns a proposal into an ``AdmittedUpdate`` value object.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .contracts import _unsafe_public_text, stable_digest, to_jsonable
from .settlement import SettlementEvent

_PUBLIC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SENSITIVE_VALUE = re.compile(
    r"(?:api[-_]?key|secret|token|password|credential|authorization)\s*[=:]|\bprompt\b|[A-Za-z][A-Za-z0-9+.-]*://",
    re.IGNORECASE,
)


class LearningUpdateStatus(str, Enum):
    NO_LEARNING_UPDATE = "NO_LEARNING_UPDATE"
    PROPOSAL_READY = "PROPOSAL_READY"


class AdmissionDecision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


def _as_date(value: date | str, name: str) -> date:
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{name} must be an ISO date") from exc
    if not isinstance(value, date):
        raise TypeError(f"{name} must be a date")
    return value


def _public_id(value: str, name: str) -> str:
    if not isinstance(value, str) or not _PUBLIC_ID.fullmatch(value.strip()):
        raise ValueError(f"{name} must be a stable public identifier")
    return value.strip()


def _safe_text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be text")
    normalized = value.strip()
    if _SENSITIVE_VALUE.search(normalized) or _unsafe_public_text(normalized):
        raise ValueError(f"{name} contains unsafe persisted text")
    return normalized


def _text_sequence(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{name} must be a sequence of strings")
    if any(not isinstance(item, str) for item in value):
        raise TypeError(f"{name} must be a sequence of strings")
    return tuple(value)


def _clean_mapping(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    result: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip() or any(token in key.casefold() for token in ("prompt", "secret", "token", "provider", "credential", "endpoint", "path")):
            raise ValueError(f"{name} contains sensitive key")
        if isinstance(item, Mapping):
            result[key.strip()] = _clean_mapping(item, f"{name}.{key}")
        elif isinstance(item, str):
            result[key.strip()] = _safe_text(item, f"{name}.{key}")
        elif isinstance(item, bool) or item is None:
            result[key.strip()] = item
        elif isinstance(item, (int, float)):
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError(f"{name} contains a non-finite number")
            result[key.strip()] = item
        else:
            raise TypeError(f"{name} contains unsupported value")
    return result


def _clean_policy_mapping(value: Any, name: str) -> dict[str, Any]:
    """Copy a policy mapping while keeping the learning boundary secret-free."""

    return _clean_mapping(value, name)


def _freeze(value: Any) -> Any:
    """Deep-freeze JSON-safe mappings so fingerprints cannot drift in place."""

    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(child) for child in value)
    return value


def _strict_mapping(
    value: Any,
    *,
    allowed: set[str] | frozenset[str],
    required: set[str] | frozenset[str],
    name: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} payload is invalid")
    keys = set(value)
    if any(not isinstance(key, str) for key in keys) or not keys.issubset(allowed):
        raise ValueError(f"{name} schema contains unknown fields")
    missing = required - keys
    if missing:
        raise ValueError(f"{name} schema is missing required fields")
    return value


def _policy_version(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("policy version must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class LearningAdmissionRecord:
    admission_id: str
    proposal_digest: str
    admitted_by: str
    decision: AdmissionDecision | str
    as_of: date | str
    rationale: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "admission_id", _public_id(self.admission_id, "admission_id"))
        object.__setattr__(self, "proposal_digest", _public_id(self.proposal_digest, "proposal_digest"))
        object.__setattr__(self, "admitted_by", _public_id(self.admitted_by, "admitted_by"))
        if not isinstance(self.decision, AdmissionDecision):
            object.__setattr__(self, "decision", AdmissionDecision(self.decision))
        object.__setattr__(self, "as_of", _as_date(self.as_of, "admission as_of"))
        object.__setattr__(self, "rationale", _safe_text(self.rationale, "admission rationale"))


@dataclass(frozen=True, slots=True)
class LearningUpdateProposal:
    status: LearningUpdateStatus | str
    settlement_digest: str | None
    as_of: date | None
    factor_weight_updates: Mapping[str, Any] = field(default_factory=dict)
    risk_rule_updates: Mapping[str, Any] = field(default_factory=dict)
    registry_updates: Mapping[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    ledger_digest: str | None = None
    dataset_digest: str | None = None
    proposal_digest: str = ""
    admitted: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.status, LearningUpdateStatus):
            object.__setattr__(self, "status", LearningUpdateStatus(self.status))
        if self.settlement_digest is not None:
            object.__setattr__(self, "settlement_digest", _public_id(self.settlement_digest, "settlement_digest"))
        if self.ledger_digest is not None:
            object.__setattr__(self, "ledger_digest", _public_id(self.ledger_digest, "ledger_digest"))
        if self.dataset_digest is not None:
            object.__setattr__(self, "dataset_digest", _public_id(self.dataset_digest, "dataset_digest"))
        if self.as_of is not None and not isinstance(self.as_of, date):
            object.__setattr__(self, "as_of", _as_date(self.as_of, "proposal as_of"))
        object.__setattr__(self, "factor_weight_updates", _freeze(_clean_mapping(self.factor_weight_updates, "factor_weight_updates")))
        object.__setattr__(self, "risk_rule_updates", _freeze(_clean_mapping(self.risk_rule_updates, "risk_rule_updates")))
        object.__setattr__(self, "registry_updates", _freeze(_clean_mapping(self.registry_updates, "registry_updates")))
        refs = tuple(_safe_text(ref, "evidence_refs") for ref in _text_sequence(self.evidence_refs, "evidence_refs") if ref.strip())
        object.__setattr__(self, "evidence_refs", refs)
        if self.admitted is not False:
            raise ValueError("proposals cannot be marked admitted")
        expected = self._compute_digest()
        if self.proposal_digest:
            supplied = _public_id(self.proposal_digest, "proposal_digest")
            if supplied != expected:
                raise ValueError("proposal digest does not match payload")
            object.__setattr__(self, "proposal_digest", supplied)
        else:
            object.__setattr__(self, "proposal_digest", expected)

    @property
    def factor_weights(self) -> Mapping[str, Any]:
        return self.factor_weight_updates

    @property
    def risk_rules(self) -> Mapping[str, Any]:
        return self.risk_rule_updates

    @property
    def registry(self) -> Mapping[str, Any]:
        return self.registry_updates

    def _compute_digest(self) -> str:
        return stable_digest({
            "status": self.status,
            "settlement_digest": self.settlement_digest,
            "as_of": self.as_of,
            "factor_weight_updates": self.factor_weight_updates,
            "risk_rule_updates": self.risk_rule_updates,
            "registry_updates": self.registry_updates,
            "evidence_refs": self.evidence_refs,
            "ledger_digest": self.ledger_digest,
            "dataset_digest": self.dataset_digest,
        })


@dataclass(frozen=True, slots=True)
class LearningApplyPreview:
    """A deterministic, non-mutating preview of an admitted policy update."""

    proposal_digest: str
    baseline_fingerprint: str
    next_version: int
    factor_weights: Mapping[str, Any] = field(default_factory=dict)
    risk_rules: Mapping[str, Any] = field(default_factory=dict)
    registry: Mapping[str, Any] = field(default_factory=dict)
    settlement_as_of: date | None = None
    ledger_digest: str | None = None
    dataset_digest: str | None = None
    policy_fingerprint: str = ""
    limitations: tuple[str, ...] = ("preview only", "paper-only", "no production registry or risk-rule mutation")
    paper_only: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "proposal_digest", _public_id(self.proposal_digest, "proposal_digest"))
        object.__setattr__(self, "baseline_fingerprint", _public_id(self.baseline_fingerprint, "baseline_fingerprint"))
        object.__setattr__(self, "next_version", _policy_version(self.next_version))
        object.__setattr__(self, "factor_weights", _freeze(_clean_policy_mapping(self.factor_weights, "factor_weights")))
        object.__setattr__(self, "risk_rules", _freeze(_clean_policy_mapping(self.risk_rules, "risk_rules")))
        object.__setattr__(self, "registry", _freeze(_clean_policy_mapping(self.registry, "registry")))
        if self.settlement_as_of is not None and not isinstance(self.settlement_as_of, date):
            object.__setattr__(self, "settlement_as_of", _as_date(self.settlement_as_of, "settlement_as_of"))
        for name in ("ledger_digest", "dataset_digest"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _public_id(value, name))
        object.__setattr__(self, "limitations", tuple(_safe_text(item, "limitations") for item in _text_sequence(self.limitations, "limitations") if item.strip()))
        if self.paper_only is not True:
            raise ValueError("learning previews must be paper-only")
        expected = stable_digest(self._fingerprint_payload())
        if self.policy_fingerprint and self.policy_fingerprint != expected:
            raise ValueError("preview policy fingerprint does not match payload")
        object.__setattr__(self, "policy_fingerprint", expected)

    @property
    def fingerprint(self) -> str:
        return self.policy_fingerprint

    def _fingerprint_payload(self) -> dict[str, Any]:
        return {
            "proposal_digest": self.proposal_digest,
            "baseline_fingerprint": self.baseline_fingerprint,
            "next_version": self.next_version,
            "factor_weights": self.factor_weights,
            "risk_rules": self.risk_rules,
            "registry": self.registry,
            "settlement_as_of": self.settlement_as_of,
            "ledger_digest": self.ledger_digest,
            "dataset_digest": self.dataset_digest,
            "paper_only": self.paper_only,
        }


@dataclass(frozen=True, slots=True)
class VersionedResearchPolicy:
    """A next-run policy value; never a production registry or risk writer."""

    version: int
    factor_weights: Mapping[str, Any] = field(default_factory=dict)
    risk_rules: Mapping[str, Any] = field(default_factory=dict)
    registry: Mapping[str, Any] = field(default_factory=dict)
    baseline_fingerprint: str = ""
    source_proposal_digest: str = ""
    settlement_as_of: date | None = None
    ledger_digest: str | None = None
    dataset_digest: str | None = None
    policy_fingerprint: str = ""
    paper_only: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "version", _policy_version(self.version))
        object.__setattr__(self, "factor_weights", _freeze(_clean_policy_mapping(self.factor_weights, "factor_weights")))
        object.__setattr__(self, "risk_rules", _freeze(_clean_policy_mapping(self.risk_rules, "risk_rules")))
        object.__setattr__(self, "registry", _freeze(_clean_policy_mapping(self.registry, "registry")))
        if self.baseline_fingerprint:
            object.__setattr__(self, "baseline_fingerprint", _public_id(self.baseline_fingerprint, "baseline_fingerprint"))
        if self.source_proposal_digest:
            object.__setattr__(self, "source_proposal_digest", _public_id(self.source_proposal_digest, "source_proposal_digest"))
        if self.settlement_as_of is not None and not isinstance(self.settlement_as_of, date):
            object.__setattr__(self, "settlement_as_of", _as_date(self.settlement_as_of, "settlement_as_of"))
        for name in ("ledger_digest", "dataset_digest"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _public_id(value, name))
        if self.paper_only is not True:
            raise ValueError("research policies must be paper-only")
        expected = stable_digest(self._fingerprint_payload())
        if self.policy_fingerprint and self.policy_fingerprint != expected:
            raise ValueError("policy fingerprint does not match payload")
        object.__setattr__(self, "policy_fingerprint", expected)

    @property
    def fingerprint(self) -> str:
        return self.policy_fingerprint

    @property
    def factor_weight_updates(self) -> Mapping[str, Any]:
        return self.factor_weights

    @property
    def risk_rule_updates(self) -> Mapping[str, Any]:
        return self.risk_rules

    @property
    def registry_updates(self) -> Mapping[str, Any]:
        return self.registry

    def _fingerprint_payload(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "factor_weights": self.factor_weights,
            "risk_rules": self.risk_rules,
            "registry": self.registry,
            "baseline_fingerprint": self.baseline_fingerprint,
            "source_proposal_digest": self.source_proposal_digest,
            "settlement_as_of": self.settlement_as_of,
            "ledger_digest": self.ledger_digest,
            "dataset_digest": self.dataset_digest,
            "paper_only": self.paper_only,
        }


@dataclass(frozen=True, slots=True)
class AdmittedUpdate:
    proposal: LearningUpdateProposal
    admission: LearningAdmissionRecord

    def __post_init__(self) -> None:
        if not isinstance(self.proposal, LearningUpdateProposal) or not isinstance(self.admission, LearningAdmissionRecord):
            raise TypeError("admitted update requires typed proposal and admission")
        if self.proposal.status is not LearningUpdateStatus.PROPOSAL_READY:
            raise ValueError("only a ready proposal can be admitted")
        if self.admission.decision is not AdmissionDecision.APPROVED:
            raise ValueError("admission must be approved")
        if self.admission.proposal_digest != self.proposal.proposal_digest:
            raise ValueError("admission proposal digest does not match")
        if self.proposal.as_of is not None and self.admission.as_of < self.proposal.as_of:
            raise ValueError("admission as_of cannot precede proposal as_of")


@dataclass(frozen=True, slots=True)
class LearningProposalRecord:
    """Durable, secret-free learning evidence and its explicit review state."""

    proposal: LearningUpdateProposal
    admission: LearningAdmissionRecord | None = None
    preview: LearningApplyPreview | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.proposal, LearningUpdateProposal):
            raise TypeError("proposal must be LearningUpdateProposal")
        if self.admission is not None:
            if not isinstance(self.admission, LearningAdmissionRecord):
                raise TypeError("admission must be LearningAdmissionRecord")
            if self.admission.proposal_digest != self.proposal.proposal_digest:
                raise ValueError("admission proposal digest does not match")
        if self.preview is not None:
            if not isinstance(self.preview, LearningApplyPreview):
                raise TypeError("preview must be LearningApplyPreview")
            if self.admission is None or self.admission.decision is not AdmissionDecision.APPROVED:
                raise ValueError("preview requires an approved admission")
            if self.preview.proposal_digest != self.proposal.proposal_digest:
                raise ValueError("preview proposal digest does not match")

    @property
    def proposal_digest(self) -> str:
        return self.proposal.proposal_digest


def _proposal_payload(proposal: LearningUpdateProposal) -> dict[str, Any]:
    return {
        "status": proposal.status.value,
        "settlement_digest": proposal.settlement_digest,
        "as_of": proposal.as_of,
        "factor_weight_updates": dict(proposal.factor_weight_updates),
        "risk_rule_updates": dict(proposal.risk_rule_updates),
        "registry_updates": dict(proposal.registry_updates),
        "evidence_refs": proposal.evidence_refs,
        "ledger_digest": proposal.ledger_digest,
        "dataset_digest": proposal.dataset_digest,
        "proposal_digest": proposal.proposal_digest,
        "admitted": proposal.admitted,
    }


def _admission_payload(admission: LearningAdmissionRecord | None) -> dict[str, Any] | None:
    if admission is None:
        return None
    return {
        "admission_id": admission.admission_id,
        "proposal_digest": admission.proposal_digest,
        "admitted_by": admission.admitted_by,
        "decision": admission.decision.value,
        "as_of": admission.as_of,
        "rationale": admission.rationale,
    }


def _preview_payload(preview: LearningApplyPreview | None) -> dict[str, Any] | None:
    if preview is None:
        return None
    return {
        "proposal_digest": preview.proposal_digest,
        "baseline_fingerprint": preview.baseline_fingerprint,
        "next_version": preview.next_version,
        "factor_weights": dict(preview.factor_weights),
        "risk_rules": dict(preview.risk_rules),
        "registry": dict(preview.registry),
        "settlement_as_of": preview.settlement_as_of,
        "ledger_digest": preview.ledger_digest,
        "dataset_digest": preview.dataset_digest,
        "policy_fingerprint": preview.policy_fingerprint,
        "limitations": preview.limitations,
        "paper_only": preview.paper_only,
    }


def _record_payload(record: LearningProposalRecord) -> dict[str, Any]:
    body = {
        "proposal": _proposal_payload(record.proposal),
        "admission": _admission_payload(record.admission),
        "preview": _preview_payload(record.preview),
    }
    return {
        "schema_version": "learning-proposal.v2",
        "proposal_digest": record.proposal_digest,
        "record": body,
        "record_digest": stable_digest(body),
    }


def _decode_proposal(payload: Any) -> LearningUpdateProposal:
    payload = _strict_mapping(
        payload,
        allowed={
            "status",
            "settlement_digest",
            "as_of",
            "factor_weight_updates",
            "risk_rule_updates",
            "registry_updates",
            "evidence_refs",
            "ledger_digest",
            "dataset_digest",
            "proposal_digest",
            "admitted",
        },
        required={
            "status",
            "settlement_digest",
            "as_of",
            "factor_weight_updates",
            "risk_rule_updates",
            "registry_updates",
            "evidence_refs",
            "ledger_digest",
            "dataset_digest",
            "proposal_digest",
            "admitted",
        },
        name="learning proposal",
    )
    return LearningUpdateProposal(
        status=payload["status"],
        settlement_digest=payload["settlement_digest"],
        as_of=payload["as_of"],
        factor_weight_updates=payload["factor_weight_updates"],
        risk_rule_updates=payload["risk_rule_updates"],
        registry_updates=payload["registry_updates"],
        evidence_refs=_text_sequence(payload["evidence_refs"], "evidence_refs"),
        ledger_digest=payload["ledger_digest"],
        dataset_digest=payload["dataset_digest"],
        proposal_digest=payload["proposal_digest"],
        admitted=payload["admitted"],
    )


def _decode_admission(payload: Any) -> LearningAdmissionRecord | None:
    if payload is None:
        return None
    payload = _strict_mapping(
        payload,
        allowed={"admission_id", "proposal_digest", "admitted_by", "decision", "as_of", "rationale"},
        required={"admission_id", "proposal_digest", "admitted_by", "decision", "as_of", "rationale"},
        name="learning admission",
    )
    return LearningAdmissionRecord(
        admission_id=payload["admission_id"],
        proposal_digest=payload["proposal_digest"],
        admitted_by=payload["admitted_by"],
        decision=payload["decision"],
        as_of=payload["as_of"],
        rationale=payload["rationale"],
    )


def _decode_preview(payload: Any) -> LearningApplyPreview | None:
    if payload is None:
        return None
    payload = _strict_mapping(
        payload,
        allowed={
            "proposal_digest",
            "baseline_fingerprint",
            "next_version",
            "factor_weights",
            "risk_rules",
            "registry",
            "settlement_as_of",
            "ledger_digest",
            "dataset_digest",
            "policy_fingerprint",
            "limitations",
            "paper_only",
        },
        required={
            "proposal_digest",
            "baseline_fingerprint",
            "next_version",
            "factor_weights",
            "risk_rules",
            "registry",
            "settlement_as_of",
            "ledger_digest",
            "dataset_digest",
            "policy_fingerprint",
            "limitations",
            "paper_only",
        },
        name="learning preview",
    )
    return LearningApplyPreview(
        proposal_digest=payload["proposal_digest"],
        baseline_fingerprint=payload["baseline_fingerprint"],
        next_version=payload["next_version"],
        factor_weights=payload["factor_weights"],
        risk_rules=payload["risk_rules"],
        registry=payload["registry"],
        settlement_as_of=payload["settlement_as_of"],
        ledger_digest=payload["ledger_digest"],
        dataset_digest=payload["dataset_digest"],
        policy_fingerprint=payload["policy_fingerprint"],
        limitations=_text_sequence(payload["limitations"], "limitations"),
        paper_only=payload["paper_only"],
    )


class LearningProposalStore:
    """Atomic local persistence for proposal, admission and preview records."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, proposal_digest: str) -> Path:
        return self.root / f"proposal-{_public_id(proposal_digest, 'proposal_digest')}.json"

    def _read_path(self, path: Path) -> LearningProposalRecord:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("learning proposal record is corrupt") from exc
        payload = _strict_mapping(
            payload,
            allowed={"schema_version", "proposal_digest", "record", "record_digest"},
            required={"schema_version", "proposal_digest", "record", "record_digest"},
            name="learning proposal record",
        )
        if payload["schema_version"] != "learning-proposal.v2":
            raise ValueError("learning proposal record schema is invalid")
        body = _strict_mapping(
            payload["record"],
            allowed={"proposal", "admission", "preview"},
            required={"proposal", "admission", "preview"},
            name="learning proposal record body",
        )
        if payload["record_digest"] != stable_digest(body):
            raise ValueError("learning proposal record digest is invalid")
        try:
            record = LearningProposalRecord(
                proposal=_decode_proposal(body["proposal"]),
                admission=_decode_admission(body.get("admission")),
                preview=_decode_preview(body.get("preview")),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("learning proposal record payload is invalid") from exc
        if payload["proposal_digest"] != record.proposal_digest or path.stem != f"proposal-{record.proposal_digest}":
            raise ValueError("learning proposal record digest does not match path")
        return record

    def save(
        self,
        proposal: LearningUpdateProposal,
        *,
        admission: LearningAdmissionRecord | None = None,
        preview: LearningApplyPreview | None = None,
    ) -> LearningProposalRecord:
        if not isinstance(proposal, LearningUpdateProposal):
            raise TypeError("proposal must be LearningUpdateProposal")
        path = self._path(proposal.proposal_digest)
        current = self._read_path(path) if path.exists() else None
        if current is not None:
            # Permit an append-only enrichment from proposal -> admission -> preview,
            # while refusing conflicting review or proposal payloads.
            if current.proposal != proposal:
                raise ValueError("proposal digest conflicts with another payload")
            if admission is not None and current.admission is not None and current.admission != admission:
                raise ValueError("proposal already has a different admission")
            if preview is not None and current.preview is not None and current.preview != preview:
                raise ValueError("proposal already has a different preview")
            record = LearningProposalRecord(
                proposal,
                admission if admission is not None else current.admission,
                preview if preview is not None else current.preview,
            )
            if record == current:
                return current
        else:
            record = LearningProposalRecord(proposal, admission, preview)
        # A settlement can produce only one immutable proposal in this store.
        if proposal.settlement_digest is not None:
            for candidate in sorted(self.root.glob("proposal-*.json")):
                if candidate == path:
                    continue
                existing = self._read_path(candidate)
                if existing.proposal.settlement_digest == proposal.settlement_digest:
                    raise ValueError("duplicate settlement digest proposal")
        encoded = json.dumps(to_jsonable(_record_payload(record)), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return record

    def get(self, proposal_digest: str) -> LearningProposalRecord | None:
        path = self._path(proposal_digest)
        if not path.exists():
            return None
        return self._read_path(path)

    def list(self) -> tuple[LearningProposalRecord, ...]:
        records = [self._read_path(path) for path in sorted(self.root.glob("proposal-*.json"))]
        return tuple(sorted(records, key=lambda item: item.proposal_digest))


class LearningManager:
    def __init__(self) -> None:
        self._proposals: dict[str, LearningUpdateProposal] = {}
        self._admitted: dict[str, AdmittedUpdate] = {}
        self._applied: dict[tuple[str, str], VersionedResearchPolicy] = {}

    @property
    def admitted_updates(self) -> tuple[AdmittedUpdate, ...]:
        return tuple(self._admitted[key] for key in sorted(self._admitted))

    def propose_update(
        self,
        settlement: SettlementEvent | None,
        history: Sequence[SettlementEvent] = (),
    ) -> LearningUpdateProposal:
        if settlement is None:
            return LearningUpdateProposal(LearningUpdateStatus.NO_LEARNING_UPDATE, None, None)
        if not isinstance(settlement, SettlementEvent):
            raise TypeError("settlement must be a SettlementEvent")
        seen: set[str] = set()
        for previous in history:
            if not isinstance(previous, SettlementEvent):
                raise TypeError("learning history must contain SettlementEvent values")
            if previous.as_of > settlement.as_of:
                raise ValueError("learning history contains a future settlement")
            if previous.fingerprint in seen:
                raise ValueError("duplicate settlement in learning history")
            seen.add(previous.fingerprint)
        digest = settlement.fingerprint
        if digest in seen:
            raise ValueError("duplicate settlement in learning history")
        if digest in self._proposals:
            return self._proposals[digest]
        outcomes = settlement.realized_outcomes
        factor = outcomes.get("factor_weight_updates", outcomes.get("factor_weights", outcomes.get("factor_scores", {}))) if isinstance(outcomes, Mapping) else {}
        risk = outcomes.get("risk_rule_updates", outcomes.get("risk_rules", {})) if isinstance(outcomes, Mapping) else {}
        registry = outcomes.get("registry_updates", outcomes.get("registry", {})) if isinstance(outcomes, Mapping) else {}
        proposal = LearningUpdateProposal(
            LearningUpdateStatus.PROPOSAL_READY,
            digest,
            settlement.as_of,
            factor_weight_updates=_clean_mapping(factor, "factor_weight_updates"),
            risk_rule_updates=_clean_mapping(risk, "risk_rule_updates"),
            registry_updates=_clean_mapping(registry, "registry_updates"),
            evidence_refs=(f"settlement:{settlement.run_id}", f"ledger:{settlement.ledger_fingerprint}"),
            ledger_digest=settlement.ledger_fingerprint,
            dataset_digest=settlement.dataset_digest,
        )
        self._proposals[digest] = proposal
        return proposal

    def propose_factor_monitoring(self, monitoring, *, existing_notes=None):
        from .factor_monitoring import MonitoringProposal

        if not isinstance(monitoring, MonitoringProposal) or monitoring.to_dict()["status"] != "REVIEW_RECOMMENDED":
            raise ValueError("factor monitoring requires a review-worthy typed observation")
        value = monitoring.to_dict()
        notes = _clean_mapping(existing_notes or {}, "factor_monitoring")
        notes[value["scope_fingerprint"]] = {
            "factor_ref": value["factor_ref"], "observation_fingerprint": monitoring.fingerprint,
            "status": value["status"], "as_of": value["as_of"], "scope": "|".join(value["reproduction_scope"]),
            "action": "RECHECK_RESEARCH_EVIDENCE"}
        return LearningUpdateProposal(LearningUpdateStatus.PROPOSAL_READY, None, _as_date(value["as_of"], "monitoring as_of"),
            registry_updates={"factor_monitoring": notes},
            evidence_refs=tuple("factor-run:" + row["evaluation_fingerprint"] for row in value["evidence_refs"]))

    def admit_update(self, proposal: LearningUpdateProposal, admission_record: LearningAdmissionRecord) -> AdmittedUpdate:
        if not isinstance(proposal, LearningUpdateProposal) or not isinstance(admission_record, LearningAdmissionRecord):
            raise TypeError("explicit typed admission record is required")
        if admission_record.proposal_digest != proposal.proposal_digest:
            raise ValueError("admission proposal digest does not match")
        if proposal.proposal_digest in self._admitted:
            previous = self._admitted[proposal.proposal_digest]
            if previous.admission == admission_record:
                return previous
            raise ValueError("proposal already has an admission")
        admitted = AdmittedUpdate(proposal, admission_record)
        self._admitted[proposal.proposal_digest] = admitted
        return admitted

    @staticmethod
    def _baseline_payload(baseline: Mapping[str, Any] | VersionedResearchPolicy) -> dict[str, Any]:
        """Normalize a next-run baseline without accepting executable state.

        A baseline is deliberately a small mapping rather than a live registry
        object.  Only the policy fields are carried forward; optional evidence
        bindings are retained so a stale settlement cannot be applied silently.
        """

        if isinstance(baseline, VersionedResearchPolicy):
            return {
                "version": baseline.version,
                "factor_weights": dict(baseline.factor_weights),
                "risk_rules": dict(baseline.risk_rules),
                "registry": dict(baseline.registry),
                "as_of": baseline.settlement_as_of,
                "ledger_digest": baseline.ledger_digest,
                "dataset_digest": baseline.dataset_digest,
            }
        if not isinstance(baseline, Mapping):
            raise TypeError("baseline must be a mapping or VersionedResearchPolicy")
        allowed = {
            "version",
            "factor_weights",
            "risk_rules",
            "registry",
            "as_of",
            "settlement_as_of",
            "ledger_digest",
            "dataset_digest",
        }
        unknown = [key for key in baseline if not isinstance(key, str) or key not in allowed]
        if unknown:
            raise ValueError("baseline contains unsupported fields")
        version = baseline.get("version", 0)
        if isinstance(version, bool) or not isinstance(version, int) or version < 0:
            raise ValueError("baseline version must be a non-negative integer")
        result: dict[str, Any] = {"version": version}
        for name in ("factor_weights", "risk_rules", "registry"):
            result[name] = _clean_policy_mapping(baseline.get(name, {}), name)
        for name in ("as_of", "settlement_as_of"):
            if name in baseline and baseline[name] is not None:
                result["as_of"] = _as_date(baseline[name], name)
                break
        for name in ("ledger_digest", "dataset_digest"):
            if name in baseline and baseline[name] is not None:
                result[name] = _public_id(baseline[name], name)
        return result

    @staticmethod
    def _check_evidence_binding(proposal: LearningUpdateProposal, baseline: Mapping[str, Any]) -> None:
        """Fail closed when a baseline declares incompatible settlement evidence."""

        baseline_as_of = baseline.get("as_of")
        if proposal.as_of is not None and baseline_as_of is not None and baseline_as_of > proposal.as_of:
            raise ValueError("baseline as_of is newer than admitted settlement")
        for name in ("ledger_digest", "dataset_digest"):
            expected = getattr(proposal, name)
            actual = baseline.get(name)
            if expected is not None and actual is not None and expected != actual:
                raise ValueError(f"{name} does not match admitted settlement")

    def _resolve_admitted(self, value: LearningUpdateProposal | AdmittedUpdate) -> AdmittedUpdate:
        if isinstance(value, AdmittedUpdate):
            if value.admission.decision is not AdmissionDecision.APPROVED:
                raise ValueError("an approved admission is required")
            return value
        if isinstance(value, LearningUpdateProposal):
            admitted = self._admitted.get(value.proposal_digest)
            if admitted is None or admitted.proposal != value:
                raise ValueError("an approved admission is required")
            return admitted
        raise TypeError("an approved LearningUpdateProposal or AdmittedUpdate is required")

    def preview_proposal(
        self,
        proposal: LearningUpdateProposal,
        baseline: Mapping[str, Any] | VersionedResearchPolicy,
    ) -> LearningApplyPreview:
        """Inspect a draft without authorizing its application."""
        if not isinstance(proposal, LearningUpdateProposal) or proposal.status is not LearningUpdateStatus.PROPOSAL_READY:
            raise ValueError("a ready learning proposal is required")
        normalized = self._baseline_payload(baseline)
        self._check_evidence_binding(proposal, normalized)
        next_version = normalized["version"] + 1
        factor_weights = dict(normalized["factor_weights"])
        factor_weights.update(proposal.factor_weight_updates)
        risk_rules = dict(normalized["risk_rules"])
        risk_rules.update(proposal.risk_rule_updates)
        registry = dict(normalized["registry"])
        registry.update(proposal.registry_updates)
        return LearningApplyPreview(
            proposal_digest=proposal.proposal_digest,
            baseline_fingerprint=stable_digest(normalized),
            next_version=next_version,
            factor_weights=factor_weights,
            risk_rules=risk_rules,
            registry=registry,
            settlement_as_of=proposal.as_of,
            ledger_digest=proposal.ledger_digest,
            dataset_digest=proposal.dataset_digest,
        )

    def preview_apply(self, proposal, baseline) -> LearningApplyPreview:
        """Only an approved admission may become an applied preview."""
        return self.preview_proposal(self._resolve_admitted(proposal).proposal, baseline)

    def apply(
        self,
        admitted_update: AdmittedUpdate,
        baseline: Mapping[str, Any] | VersionedResearchPolicy,
    ) -> VersionedResearchPolicy:
        """Create a versioned paper-only next-run policy.

        This method intentionally returns a value object and never writes a
        registry, risk rule, broker, or other production subsystem.  Repeating
        the same admitted update against the same baseline is idempotent.
        """

        admitted = self._resolve_admitted(admitted_update)
        normalized = self._baseline_payload(baseline)
        self._check_evidence_binding(admitted.proposal, normalized)
        baseline_fingerprint = stable_digest(normalized)
        key = (admitted.proposal.proposal_digest, baseline_fingerprint)
        prior = self._applied.get(key)
        if prior is not None:
            return prior
        preview = self.preview_apply(admitted, normalized)
        policy = VersionedResearchPolicy(
            version=preview.next_version,
            factor_weights=preview.factor_weights,
            risk_rules=preview.risk_rules,
            registry=preview.registry,
            baseline_fingerprint=preview.baseline_fingerprint,
            source_proposal_digest=admitted.proposal.proposal_digest,
            settlement_as_of=preview.settlement_as_of,
            ledger_digest=preview.ledger_digest,
            dataset_digest=preview.dataset_digest,
        )
        self._applied[key] = policy
        return policy


__all__ = [
    "AdmissionDecision",
    "AdmittedUpdate",
    "LearningAdmissionRecord",
    "LearningApplyPreview",
    "LearningManager",
    "LearningProposalRecord",
    "LearningProposalStore",
    "LearningUpdateProposal",
    "LearningUpdateStatus",
    "VersionedResearchPolicy",
]

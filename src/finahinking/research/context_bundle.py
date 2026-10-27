"""Private, bounded projections of explicitly authorized research inputs.

These are model context, not a second market-data store. Public artifacts
carry fingerprints and validated observations, never this complete payload.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time
from itertools import pairwise
from types import MappingProxyType

from .contracts import ResearchPlan, _unsafe_public_text, stable_digest

ROLES = ("fundamentals", "technical", "sentiment", "news", "learning", "research")
_ROLE_TASKS = {
    "fundamentals": "Review the supplied financial/business summaries, separate stated facts from assumptions, and identify missing fundamentals; do not invent financial statements.",
    "technical": "Interpret the supplied price and volume observations; leave all financial calculations and trading policy to the server engine.",
    "sentiment": "Review the supplied sentiment material and its coverage/bias; do not infer crowd sentiment from price alone.",
    "news": "Summarize the supplied announcements and news known at the cutoff, separating reported events from possible implications.",
    "learning": "Review only supplied prior learning or settled-outcome summaries; propose checks without changing the active policy or importing future outcomes.",
    "research": "Synthesize the cited role observations, explicit missing inputs, disagreements and testable hypotheses; do not turn model views into numerical facts or alter the server policy.",
}
_MATERIAL_FIELDS = frozenset({"role", "instrument", "summary", "source_label", "observed_at", "available_at", "authorized"})


def _timestamp(value):
    if not isinstance(value, str):
        raise TypeError("material availability must be known")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("material timestamp is invalid") from None
    if parsed.tzinfo is None:
        raise ValueError("material timestamp must have a timezone")
    return parsed.astimezone(UTC)


def _text(value, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or _unsafe_public_text(value):
        raise ValueError("research material must contain bounded, non-sensitive text")
    return value.strip()


def normalize_materials(materials, request):
    """Validate all inputs, including ones not selected for a particular role."""
    if not isinstance(materials, (list, tuple)) or len(materials) > 16:
        raise ValueError("at most sixteen authorized research summaries are allowed")
    boundary = datetime.combine(request.as_of, time.max, UTC)
    result = []
    for value in materials:
        if not isinstance(value, Mapping) or set(value) != _MATERIAL_FIELDS:
            raise ValueError("research material fields are invalid")
        if value["role"] not in ROLES[:-1] or value["instrument"] != request.instrument or value["authorized"] is not True:
            raise ValueError("research material is not authorized for this instrument and role")
        observed, available = _timestamp(value["observed_at"]), _timestamp(value["available_at"])
        if not observed <= available <= boundary:
            raise ValueError("research material was not available at the research cutoff")
        result.append({
            "role": value["role"], "instrument": request.instrument,
            "summary": _text(value["summary"], 2000), "source_label": _text(value["source_label"], 120),
            "observed_at": observed.isoformat(), "available_at": available.isoformat(), "authorized": True,
        })
    return tuple(result)


@dataclass(frozen=True, slots=True, repr=False)
class ResearchContextBundle:
    _json: str

    @property
    def fingerprint(self):
        return stable_digest(self.to_dict())

    @property
    def evidence_index(self):
        return MappingProxyType(self.to_dict()["evidence_index"])

    def to_dict(self):
        return json.loads(self._json)

    def validate_references(self, refs):
        if not isinstance(refs, (tuple, list)) or not refs or any(type(ref) is not str or ref not in self.evidence_index for ref in refs):
            raise ValueError("model evidence is not bound to this run, role and context")


def build_context_bundle(request, snapshot, role, *, materials=(), reports: Sequence = (), max_bytes=32768, loaded_methods=None):
    if role not in ROLES or type(max_bytes) is not int or not 1 <= max_bytes <= 131072:
        raise ValueError("research context limits or role are invalid")
    boundary = datetime.combine(request.as_of, time.max, UTC)
    if request.snapshot_ref != snapshot.fingerprint or not snapshot.pit_available(boundary):
        raise ValueError("context snapshot binding or availability is invalid")
    rows = tuple(row for row in snapshot.observations if row.instrument == request.instrument)
    if len(rows) < 4 or len({row.timestamp.date() for row in rows}) != len(rows):
        raise ValueError("context requires low-frequency OHLCV for the selected instrument")
    if any(previous.available_at > following.timestamp for previous, following in pairwise(rows)):
        raise ValueError("context historical prices were not available at execution time")
    selected = [dict(item) for item in normalize_materials(materials, request) if role == "research" or item["role"] == role]
    if role in {"technical", "research"}:
        selected.insert(0, {
            "role": "technical", "instrument": request.instrument, "source_label": "Selected market snapshot",
            "summary": {"data_mode": snapshot.mode, "record_count": len(rows), "metric_basis": "OBSERVATION",
                        "observations": [{"timestamp": row.timestamp.isoformat(), "available_at": row.available_at.isoformat(),
                                          "close": row.close, "volume": row.volume} for row in rows[-16:]]},
            "observed_at": rows[-1].timestamp.isoformat(), "available_at": max(row.available_at for row in rows).isoformat(),
            "authorized": True,
        })
    from .snapshot_tools import local_research_policy

    base = request.research_plan if isinstance(request.research_plan, ResearchPlan) else None
    policy = base.validation_spec.get("policy_fingerprint", stable_digest(local_research_policy())) if base else stable_digest(local_research_policy())
    binding = {"run_id": request.run_id, "dataset_fingerprint": snapshot.fingerprint, "policy_fingerprint": policy, "as_of": request.as_of.isoformat()}
    index = {}
    for item in selected:
        # Material roles retain the same ids in the manager union; another run
        # or a different material gets a different reference.
        ref = "evidence:" + stable_digest({**binding, "material": item})
        item["evidence_ref"] = ref
        index[ref] = {"role": item["role"], "material_fingerprint": stable_digest({k: v for k, v in item.items() if k != "evidence_ref"})}
    observations = []
    if reports:
        if role != "research":
            raise ValueError("only the manager receives cross-role observations")
        for report in reports:
            if report.status == "READY" and (not report.evidence_refs or any(ref not in index for ref in report.evidence_refs)):
                raise ValueError("manager report contains an unbound evidence reference")
            observations.append({"role": report.role, "status": report.status, "claims": list(report.claims),
                                 "evidence_refs": list(report.evidence_refs), "limitations": list(report.limitations)})
    payload = {**binding, "schema_version": "research-context.v1", "role": role, "role_task": _ROLE_TASKS[role], "instrument": request.instrument,
               "question": base.hypotheses[0] if base and base.hypotheses else str(request.research_plan),
               "paper_only": True, "material_status": "AVAILABLE" if selected else "INSUFFICIENT_DATA",
               "materials": selected, "evidence_index": index, "observations": observations,
               "limitations": ["User summaries and model observations are not independently verified market facts.",
                               "No tool execution or numerical policy changes are authorized."]}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ValueError("research context exceeds its byte limit")
    bundle = ResearchContextBundle(encoded)
    return merge_method_context(bundle, loaded_methods, max_bytes=max_bytes) if loaded_methods is not None else bundle


def merge_method_context(context, loaded_methods, *, max_bytes=32768):
    """The merged context has one byte cap, not one allowance per component."""
    from .method_context import CONTEXT_MAX_BYTES, LoadedMethods, MethodContextError

    if (not isinstance(context, ResearchContextBundle) or not isinstance(loaded_methods, LoadedMethods)
            or loaded_methods.status != "READY" or type(max_bytes) is not int
            or not 1 <= max_bytes <= CONTEXT_MAX_BYTES):
        raise MethodContextError()
    payload = context.to_dict()
    payload["method_context"] = loaded_methods.to_dict()
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > max_bytes:
        raise MethodContextError()
    return ResearchContextBundle(encoded)

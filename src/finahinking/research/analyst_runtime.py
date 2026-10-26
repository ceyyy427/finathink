"""Bounded parallel analyst execution and evidence-only plan synthesis.

The runtime deliberately keeps the model boundary small: one proposal per
role, a paper-only context, and no tool gateway.  It does not execute model
output and it never turns analyst claims into research facts.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from contextvars import copy_context
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .contracts import AgentReport, FailureKind, ResearchPlan, ResearchRequest, stable_digest
from .drivers import DriverResult, ModelDriver
from .workflow import DEFAULT_ROLES, AnalystSpec


class AnalystFailureKind(str, Enum):
    """Failures that are local to the parallel analyst stage."""

    MISSING_REQUIRED = "MISSING_REQUIRED"
    OPTIONAL_FAILURE = "OPTIONAL_FAILURE"
    DUPLICATE_ROLE = "DUPLICATE_ROLE"
    TIMEOUT = "TIMEOUT"
    INVALID_REPORT = "INVALID_REPORT"


@dataclass(frozen=True, slots=True)
class AnalystOutcome:
    role: str
    status: str
    report: AgentReport | None = None
    failure_kind: AnalystFailureKind | FailureKind | str | None = None
    message_digest: str = ""
    duration_ms: int = 0

    def __post_init__(self) -> None:
        role = self.role.strip()
        if not role:
            raise ValueError("role must be non-empty")
        if not self.status.strip():
            raise ValueError("status must be non-empty")
        if not self.message_digest.strip():
            raise ValueError("message_digest must be non-empty")
        if self.duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "status", self.status.strip().upper())


def _paper_claim(claim: str) -> str:
    lowered = claim.casefold()
    if any(term in lowered for term in ("executed", "filled order", "placed order", "bought", "sold")):
        return "paper-only research observation; model execution claim ignored"
    return claim


def _sanitize_report(report: AgentReport) -> AgentReport:
    return AgentReport(
        role=report.role,
        status=report.status,
        claims=tuple(_paper_claim(claim) for claim in report.claims),
        evidence_refs=report.evidence_refs,
        limitations=report.limitations,
        model_ref=report.model_ref,
        finished_at=report.finished_at,
    )


def _safe_context(context: Mapping[str, Any], role: str) -> dict[str, Any]:
    """Build the only context a model proposal receives.

    Tool objects and execution hooks are intentionally removed even when a
    caller accidentally places one in a general context mapping.
    """

    blocked = {
        "allowed_tools",
        "callable",
        "code",
        "execute",
        "executor",
        "function",
        "network",
        "python",
        "shell",
        "tools",
        "tool",
        "tool_gateway",
        "tool_names",
        "url",
        "endpoint",
    }

    drop = object()

    def sanitize(value: Any) -> Any:
        if callable(value):
            return drop
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str) or key.casefold() in blocked:
                    continue
                cleaned = sanitize(item)
                if cleaned is not drop:
                    result[key] = cleaned
            return result
        if isinstance(value, (list, tuple)):
            cleaned_items = [item for item in (sanitize(item) for item in value) if item is not drop]
            return tuple(cleaned_items) if isinstance(value, tuple) else cleaned_items
        return drop

    cleaned_context = sanitize(context)
    safe = cleaned_context if isinstance(cleaned_context, dict) else {}
    safe.update({"analyst_role": role, "paper_only": True, "allowed_tools": (), "tool_names": ()})
    return safe


def _digest(role: str, status: str, *, report: AgentReport | None = None, message: str = "") -> str:
    payload: dict[str, Any] = {"role": role, "status": status, "message": message}
    if report is not None:
        payload["report"] = {
            "role": report.role,
            "status": report.status,
            "claims": report.claims,
            "evidence_refs": report.evidence_refs,
            "limitations": report.limitations,
            "model_ref": report.model_ref,
        }
    return stable_digest(payload)


class AnalystPool:
    """Run bounded, independent analyst proposals concurrently."""

    def run(
        self,
        specs: Sequence[AnalystSpec],
        request: ResearchRequest,
        driver: ModelDriver,
        context: Mapping[str, Any],
        max_workers: int = 5,
    ) -> tuple[AnalystOutcome, ...]:
        supplied_specs = tuple(
            AnalystSpec(role=spec.role.strip().casefold(), required=spec.required, max_rounds=spec.max_rounds, timeout_seconds=spec.timeout_seconds)
            for spec in specs
        )
        if not supplied_specs:
            roles = request.analyst_roles or DEFAULT_ROLES
            supplied_specs = tuple(AnalystSpec(role=role.strip().casefold(), required=role.strip().casefold() != "learning") for role in roles)
        if max_workers < 1:
            raise ValueError("max_workers must be positive")

        counts = Counter(spec.role for spec in supplied_specs)
        duplicate_roles = {role for role, count in counts.items() if count > 1}
        outcomes: list[AnalystOutcome] = []
        unique_specs = tuple(spec for spec in supplied_specs if counts[spec.role] == 1)

        for spec in supplied_specs:
            if spec.role in duplicate_roles:
                outcomes.append(
                    self._outcome(
                        spec.role,
                        "DUPLICATE_ROLE",
                        AnalystFailureKind.DUPLICATE_ROLE,
                        "role appears more than once",
                    )
                )

        if unique_specs:
            executor = ThreadPoolExecutor(max_workers=min(max_workers, len(unique_specs)), thread_name_prefix="finathink-analyst")
            submitted: dict[str, tuple[AnalystSpec, Future[DriverResult], float]] = {}
            try:
                for spec in unique_specs:
                    started = time.monotonic()
                    future = executor.submit(copy_context().run, driver.propose, request, _safe_context(context, spec.role))
                    submitted[spec.role] = (spec, future, started)

                pending = {item[1] for item in submitted.values()}
                role_for_future = {future: role for role, (_, future, _) in submitted.items()}
                while pending:
                    completed = {future for future in pending if future.done()}
                    for future in completed:
                        pending.remove(future)
                        role = role_for_future[future]
                        spec, _, started = submitted[role]
                        outcomes.append(self._resolve(role, spec, future, started))
                    if not pending:
                        break
                    now = time.monotonic()
                    expired = {
                        future
                        for role, (_, future, started) in submitted.items()
                        if future in pending and now - started >= submitted[role][0].timeout_seconds
                    }
                    for future in expired:
                        role = role_for_future[future]
                        spec = submitted[role][0]
                        pending.remove(future)
                        future.cancel()
                        outcomes.append(
                            self._outcome(
                                role,
                                "TIMEOUT",
                                AnalystFailureKind.TIMEOUT,
                                f"analyst exceeded {spec.timeout_seconds:.3f}s timeout",
                                started=submitted[role][2],
                            )
                        )
                    if not pending:
                        break
                    next_deadline = min(
                        submitted[role][2] + submitted[role][0].timeout_seconds
                        for role, (_, future, _) in submitted.items()
                        if future in pending
                    )
                    done, _ = wait(pending, timeout=max(0.0, min(0.05, next_deadline - time.monotonic())))
                    for future in done:
                        if future not in pending:
                            continue
                        pending.remove(future)
                        role = role_for_future[future]
                        spec, _, started = submitted[role]
                        outcomes.append(self._resolve(role, spec, future, started))
            finally:
                executor.shutdown(wait=False, cancel_futures=True)

        role_order = {role: index for index, role in enumerate(DEFAULT_ROLES)}
        return tuple(sorted(outcomes, key=lambda outcome: (role_order.get(outcome.role, len(role_order)), outcome.role)))

    def _resolve(self, role: str, spec: AnalystSpec, future: Future[DriverResult], started: float) -> AnalystOutcome:
        duration_ms = round((time.monotonic() - started) * 1000)
        try:
            result = future.result()
        except Exception:  # noqa: BLE001 - normalize every driver exception
            return self._outcome(role, "FAILED" if spec.required else "OPTIONAL_FAILED", FailureKind.INTERNAL_ERROR, "driver failed", duration_ms=duration_ms)
        if not isinstance(result, DriverResult):
            return self._outcome(role, "FAILED" if spec.required else "OPTIONAL_FAILED", AnalystFailureKind.INVALID_REPORT, "driver returned an invalid result", duration_ms=duration_ms)
        if result.failure_kind is not None or result.requires_external_turn:
            failure = result.failure_kind or FailureKind.PROVIDER_NOT_CONFIGURED
            if failure is FailureKind.PROVIDER_NOT_CONFIGURED:
                message = "provider is not configured"
            elif result.requires_external_turn:
                message = "driver requires an external turn"
            else:
                message = "driver failed"
            status = "FAILED" if spec.required else "OPTIONAL_FAILED"
            return self._outcome(role, status, failure, message, duration_ms=duration_ms)

        reports = tuple(report for report in result.reports if report.role == role)
        if len(reports) > 1:
            return self._outcome(role, "FAILED" if spec.required else "OPTIONAL_FAILED", AnalystFailureKind.INVALID_REPORT, "driver returned duplicate reports for role", duration_ms=duration_ms)
        if not reports:
            status = "MISSING_REQUIRED" if spec.required else "OPTIONAL_FAILED"
            failure = AnalystFailureKind.MISSING_REQUIRED if spec.required else AnalystFailureKind.OPTIONAL_FAILURE
            return self._outcome(role, status, failure, "driver did not return a report", duration_ms=duration_ms)

        report = _sanitize_report(reports[0])
        if report.status.casefold() == "failed":
            return self._outcome(role, "FAILED" if spec.required else "OPTIONAL_FAILED", FailureKind.INTERNAL_ERROR, "analyst report is failed", report=report, duration_ms=duration_ms)
        return self._outcome(role, report.status, None, "", report=report, duration_ms=duration_ms)

    @staticmethod
    def _outcome(
        role: str,
        status: str,
        failure_kind: AnalystFailureKind | FailureKind | str | None,
        message: str,
        *,
        report: AgentReport | None = None,
        started: float | None = None,
        duration_ms: int | None = None,
    ) -> AnalystOutcome:
        if duration_ms is None:
            duration_ms = round((time.monotonic() - started) * 1000) if started is not None else 0
        return AnalystOutcome(
            role=role,
            status=status,
            report=report,
            failure_kind=failure_kind,
            message_digest=_digest(role, status, report=report, message=message),
            duration_ms=duration_ms,
        )


class ResearchManager:
    """Create an evidence-indexed plan without asserting analyst claims."""

    def synthesize(self, reports: Sequence[AgentReport], request: ResearchRequest) -> ResearchPlan:
        usable = tuple(report for report in reports if report.status.casefold() != "failed")
        incomplete = tuple(report.role for report in usable if not report.evidence_refs)
        if incomplete:
            raise ValueError(f"usable reports missing evidence: {', '.join(sorted(incomplete))}")
        evidence_refs = tuple(sorted({ref for report in usable for ref in report.evidence_refs}))
        if not evidence_refs:
            raise ValueError("evidence refs are required for research synthesis")

        base = request.research_plan if isinstance(request.research_plan, ResearchPlan) else None
        return ResearchPlan(
            hypotheses=(),
            required_datasets=base.required_datasets if base is not None else (),
            factor_ids=base.factor_ids if base is not None else (),
            validation_spec={
                "analyst_roles": tuple(sorted(report.role for report in usable)),
                "evidence_refs": evidence_refs,
                "paper_only": True,
            },
            risk_policy_version=base.risk_policy_version if base is not None else "risk.v1",
        )


class GroundedResearchManager:
    """Model synthesis over already validated, run-bound observations only."""

    def __init__(self, driver):
        self.driver = driver

    def synthesize(self, reports, request):
        result = self.driver.propose(request, {"analyst_role": "research", "reports": reports})
        if result.failure_kind is not None or result.research_plan is None:
            raise ValueError("grounded manager could not validate its synthesis")
        return result.research_plan


__all__ = ["AnalystFailureKind", "AnalystOutcome", "AnalystPool", "ResearchManager"]

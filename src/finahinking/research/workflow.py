"""Bounded, paper-only research workflow orchestration."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import CancelledError
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .contracts import (
    AgentReport,
    DecisionCard,
    FailureKind,
    ResearchPlan,
    ResearchRequest,
    ResearchRunResult,
    ResearchRunState,
    ResearchState,
    RiskReview,
    RunEvent,
    stable_digest,
    validate_transition,
)
from .drivers import DriverResult, ModelDriver, OfflineDriver
from .paper_trader import PaperTrader
from .portfolio_runtime import PortfolioManager
from .risk_runtime import RiskManager
from .tools import ResearchToolGateway, ResearchToolRequest, ResearchToolStatus

if TYPE_CHECKING:
    from .analyst_runtime import AnalystPool, ResearchManager


@dataclass(frozen=True, slots=True)
class AnalystSpec:
    role: str
    required: bool = True
    max_rounds: int = 1
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.role.strip():
            raise ValueError("analyst role must be non-empty")
        if self.max_rounds < 1:
            raise ValueError("max_rounds must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class WorkflowLimits:
    max_analyst_rounds: int = 2
    max_tool_rounds: int = 8
    max_risk_rounds: int = 2
    max_analyst_workers: int = 5

    def __post_init__(self) -> None:
        if min(self.max_analyst_rounds, self.max_tool_rounds, self.max_risk_rounds, self.max_analyst_workers) < 1:
            raise ValueError("workflow limits must be positive")


DEFAULT_ROLES = ("fundamentals", "technical", "sentiment", "news", "learning")


def _default_specs(request: ResearchRequest) -> tuple[AnalystSpec, ...]:
    roles = request.analyst_roles or DEFAULT_ROLES
    return tuple(AnalystSpec(role=role, required=role != "learning") for role in roles)


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


class ResearchOrchestrator:
    def __init__(
        self,
        *,
        connection_store: Any | None = None,
        data_transport: Any | None = None,
        data_resolver: Any | None = None,
        driver: ModelDriver | None = None,
        tools: ResearchToolGateway | None = None,
        engine_registry: Any | None = None,
        optional_engine_registry: Any | None = None,
    ) -> None:
        self.connection_store = connection_store
        self.data_transport = data_transport
        self.data_resolver = data_resolver
        self.default_driver = driver
        self.default_tools = tools
        self.engine_registry = optional_engine_registry or engine_registry

    def run_optional_engine(self, name: str, snapshot: Any, spec: Any) -> Any:
        """Run an explicitly selected optional engine through the typed registry.

        The regular workflow still uses its existing deterministic tool gateway.
        This opt-in seam keeps optional dependencies out of startup while making
        an admitted, normalized engine usable by callers that explicitly select it.
        """

        from .engine_registry import EngineRegistry

        registry = self.engine_registry or EngineRegistry()
        return registry.resolve(name, snapshot).run(snapshot, spec)

    def run_from_connection(
        self,
        connection_id: str,
        request: ResearchRequest,
        *,
        driver: ModelDriver | None = None,
        tools: ResearchToolGateway | None = None,
        **run_kwargs: Any,
    ) -> ResearchRunResult:
        """Explicitly fetch one saved user connection, then run the workflow.

        Saving a connection never invokes this method. The method resolves the
        credential only at fetch time and exposes a normalized ``DataBatch`` to
        the existing research tool gateway. Endpoint and credential values are
        kept out of events and result artifacts.
        """
        if not isinstance(connection_id, str) or not connection_id.strip() or not isinstance(request, ResearchRequest):
            return self._connection_failure(request, FailureKind.DATA_UNAVAILABLE, "data connection is not configured")
        if self.connection_store is None or self.data_transport is None:
            return self._connection_failure(request, FailureKind.DATA_UNAVAILABLE, "data connection transport is not configured")
        try:
            config = self.connection_store.get(connection_id.strip())
        except (KeyError, TypeError, ValueError):
            return self._connection_failure(request, FailureKind.DATA_UNAVAILABLE, "data connection is not configured")
        try:
            from finahinking.data.user_api import JsonApiConnector
            from finahinking.data.user_api_contracts import DataRequest

            data_request = DataRequest(
                dataset_kind="prices",
                instruments=(request.instrument,),
                as_of=request.as_of.isoformat(),
            )
            batch = JsonApiConnector(
                config,
                self.connection_store.credentials,
                self.data_transport,
                resolver=self.data_resolver,
            ).fetch(data_request)
        except Exception as exc:  # noqa: BLE001 - convert every connector failure to a typed terminal state
            code = getattr(exc, "code", "transport")
            kind = FailureKind.NO_DATA_AVAILABLE if code in {"no_data", "empty"} else FailureKind.DATA_INVALID if code in {"schema", "invalid_json", "record_limit"} else FailureKind.DATA_UNAVAILABLE
            return self._connection_failure(request, kind, f"data connection failed: {kind.value}")
        if not batch.records:
            return self._connection_failure(request, FailureKind.NO_DATA_AVAILABLE, "data connection returned no records")

        base_tools = tools or self.default_tools or ResearchToolGateway()
        data_response = {
            "records": [dict(item) for item in batch.records],
            "connection_id": batch.connection_id,
            "retrieved_at": batch.retrieved_at.isoformat(),
            "source_declaration": batch.source_declaration,
            "data_fingerprint": batch.data_fingerprint,
            "quality_issues": list(batch.quality_issues),
            "pit_available": batch.pit_available,
        }
        from .tools import ResearchToolResponse, ResearchToolStatus

        class _ConnectionTools:
            def execute(self, tool_request: ResearchToolRequest) -> ResearchToolResponse:
                if tool_request.name == "research.inspect_dataset":
                    return ResearchToolResponse(
                        request_id=tool_request.run_id,
                        name=tool_request.name,
                        status=ResearchToolStatus.SUCCEEDED,
                        result=data_response,
                        provenance={"gateway": "finahinking-user-data-v1", "data_fingerprint": batch.data_fingerprint},
                        request_digest=tool_request.request_digest,
                    )
                return base_tools.execute(tool_request)

        return self.run(request, driver or self.default_driver or OfflineDriver(), _ConnectionTools(), **run_kwargs)

    @staticmethod
    def _connection_failure(request: Any, kind: FailureKind, message: str) -> ResearchRunResult:
        run_id = request.run_id if isinstance(request, ResearchRequest) else "connection-run"
        as_of = request.as_of if isinstance(request, ResearchRequest) else None
        state_history = (ResearchState.RECEIVED, ResearchState.IDENTIFIED, ResearchState.DATA_CHECKED, ResearchState.NO_DATA_AVAILABLE if kind is FailureKind.NO_DATA_AVAILABLE else ResearchState.DATA_UNAVAILABLE)
        events = tuple(
            ResearchOrchestrator._event(run_id, state, "data_connection", {"status": state.value, "failure_kind": kind.value})
            for state in state_history
        )
        return ResearchRunResult(
            state=ResearchRunState(
                run_id=run_id,
                current_state=state_history[-1],
                as_of=as_of,
                state_history=state_history,
                failure_kind=kind,
                failure_message=message,
                decision_eligible=False,
            ),
            events=events,
        )

    def run(
        self,
        request: ResearchRequest,
        driver: ModelDriver,
        tools: ResearchToolGateway,
        provider_registry: Any | None = None,
        analyst_specs: Sequence[AnalystSpec] | None = None,
        limits: WorkflowLimits | None = None,
        analyst_pool: AnalystPool | None = None,
        manager: ResearchManager | None = None,
        run_control: Any | None = None,
        checkpoint_writer: Callable[[ResearchRunState], None] | None = None,
    ) -> ResearchRunResult:
        del provider_registry  # Provider checks happen at the driver boundary.
        limits = limits or WorkflowLimits()
        specs = tuple(analyst_specs or _default_specs(request))
        state_history: list[ResearchState] = [ResearchState.RECEIVED]
        events: list[RunEvent] = [self._event(request.run_id, ResearchState.RECEIVED, "workflow", {"step": 0})]
        reports: tuple[AgentReport, ...] = ()
        failure_message: str | None = None

        def transition(next_state: ResearchState, payload: Mapping[str, Any], *, owner: str = "workflow", metadata: Mapping[str, Any] | None = None) -> None:
            validate_transition(state_history[-1], next_state)
            state_history.append(next_state)
            events.append(self._event(request.run_id, next_state, owner, payload, metadata=metadata))
            # Persist only resumable typed state. Terminal report/learning
            # states are publication records and are intentionally not written
            # as temporary checkpoints by ResearchRunStore.
            if checkpoint_writer is not None and next_state in {
                ResearchState.RECEIVED,
                ResearchState.IDENTIFIED,
                ResearchState.DATA_CHECKED,
                ResearchState.ANALYSTS_RUNNING,
                ResearchState.ANALYSTS_READY,
                ResearchState.EVIDENCE_REVIEW,
                ResearchState.RESEARCH_PLAN_READY,
                ResearchState.QUANT_VALIDATION,
                ResearchState.RISK_REVIEW,
                ResearchState.PAPER_DECISION_READY,
            }:
                checkpoint_writer(
                    ResearchRunState(
                        run_id=request.run_id,
                        current_state=next_state,
                        as_of=request.as_of,
                        state_history=tuple(state_history),
                        analyst_reports=reports,
                        failure_kind=None,
                        decision_eligible=False,
                    )
                )

        def finish(next_state: ResearchState, kind: FailureKind, message: str, *, owner: str = "workflow", metadata: Mapping[str, Any] | None = None) -> ResearchRunResult:
            transition(next_state, {"failure_kind": kind.value, "message_digest": stable_digest(message)}, owner=owner, metadata=metadata)
            state = ResearchRunState(
                run_id=request.run_id,
                current_state=next_state,
                as_of=request.as_of,
                state_history=tuple(state_history),
                analyst_reports=reports,
                failure_kind=kind,
                failure_message=message,
                decision_eligible=False,
            )
            return ResearchRunResult(state=state, events=tuple(events))

        def cancelled() -> bool:
            return run_control is not None and bool(run_control.is_cancelled(request.run_id))

        if cancelled():
            return finish(ResearchState.CANCELLED, FailureKind.CANCELLED, "run was cancelled before execution")

        transition(ResearchState.IDENTIFIED, {"instrument": request.instrument})
        dataset_id = request.snapshot_ref or (request.research_plan.required_datasets[0] if isinstance(request.research_plan, ResearchPlan) and request.research_plan.required_datasets else "fixture")
        data_response = tools.execute(
            ResearchToolRequest(
                name="research.inspect_dataset",
                args={"dataset_id": dataset_id, "as_of": request.as_of.isoformat()},
                run_id=request.run_id,
            )
        )
        if cancelled():
            return finish(ResearchState.CANCELLED, FailureKind.CANCELLED, "run was cancelled after dataset check")
        if data_response.status is not ResearchToolStatus.SUCCEEDED:
            kind = data_response.failure_kind or FailureKind.DATA_UNAVAILABLE
            target = ResearchState.NO_DATA_AVAILABLE if kind is FailureKind.NO_DATA_AVAILABLE else ResearchState.DATA_UNAVAILABLE
            transition(ResearchState.DATA_CHECKED, {"dataset_status": kind.value})
            return finish(target, kind, f"dataset check failed: {kind.value}")
        if request.snapshot_ref and (not isinstance(data_response.result, Mapping) or data_response.result.get("dataset_fingerprint") != request.snapshot_ref):
            transition(ResearchState.DATA_CHECKED, {"dataset_status": "MISMATCH"})
            return finish(ResearchState.DATA_UNAVAILABLE, FailureKind.DATA_INVALID, "selected dataset fingerprint mismatch")
        binding = {"dataset_fingerprint": request.snapshot_ref} if request.snapshot_ref else {}
        transition(ResearchState.DATA_CHECKED, {"dataset_digest": stable_digest(data_response.result)}, metadata=binding)
        parallel = analyst_pool is not None or manager is not None
        if parallel:
            # Imports stay lazy so AnalystPool can retain its compatibility import
            # of AnalystSpec without creating a workflow/runtime import cycle.
            from .analyst_runtime import AnalystFailureKind, AnalystPool, ResearchManager

            pool = analyst_pool or AnalystPool()
            research_manager = manager or ResearchManager()
            transition(
                ResearchState.ANALYSTS_RUNNING,
                {"roles": tuple(spec.role for spec in specs), "max_workers": limits.max_analyst_workers},
                owner="analyst_pool",
                metadata={"stage_owner": "analyst_pool", "roles": tuple(spec.role for spec in specs)},
            )
            context = {"dataset": data_response.result, "max_rounds": limits.max_analyst_rounds}
            try:
                outcomes = tuple(pool.run(specs, request, driver, context, max_workers=limits.max_analyst_workers))
            except CancelledError:
                return finish(ResearchState.CANCELLED, FailureKind.CANCELLED, "analyst pool was cancelled")
            except Exception:  # noqa: BLE001 - normalize pool boundary failures
                return finish(ResearchState.FAILED, FailureKind.INTERNAL_ERROR, "analyst pool failed")

            if cancelled():
                return finish(ResearchState.CANCELLED, FailureKind.CANCELLED, "run was cancelled after analyst stage")

            reports = tuple(
                outcome.report
                for outcome in sorted(
                    outcomes,
                    key=lambda outcome: (DEFAULT_ROLES.index(outcome.role) if outcome.role in DEFAULT_ROLES else len(DEFAULT_ROLES), outcome.role),
                )
                if outcome.report is not None
            )
            role_statuses = {outcome.role: outcome.status for outcome in outcomes}
            analyst_metadata = {
                "stage_owner": "analyst_pool",
                "role_statuses": tuple(sorted(role_statuses.items())),
                "provider_models": tuple(
                    (report.role, self._redacted_model_ref(report.model_ref)) for report in reports
                ),
            }
            spec_by_role = {spec.role.casefold(): spec for spec in specs}
            required_failures = tuple(
                outcome
                for outcome in outcomes
                if spec_by_role.get(outcome.role.casefold(), AnalystSpec(outcome.role)).required
                and outcome.status not in {"READY", "OFFLINE"}
            )
            missing_required_roles = tuple(
                spec.role for spec in specs if spec.required and spec.role.casefold() not in {outcome.role.casefold() for outcome in outcomes}
            )
            provider_failure = next(
                (
                    outcome
                    for outcome in outcomes
                    if str(outcome.failure_kind) == FailureKind.PROVIDER_NOT_CONFIGURED.value
                    or outcome.failure_kind is FailureKind.PROVIDER_NOT_CONFIGURED
                ),
                None,
            )
            if provider_failure is not None:
                return finish(ResearchState.PROVIDER_NOT_CONFIGURED, FailureKind.PROVIDER_NOT_CONFIGURED, "analyst provider is not configured", owner="analyst_pool", metadata=analyst_metadata)
            if any(outcome.status == "CANCELLED" for outcome in outcomes):
                return finish(ResearchState.CANCELLED, FailureKind.CANCELLED, "analyst pool was cancelled", owner="analyst_pool", metadata=analyst_metadata)
            if required_failures or missing_required_roles:
                timeout = any(str(outcome.failure_kind) in {"AnalystFailureKind.TIMEOUT", AnalystFailureKind.TIMEOUT.value} or outcome.status == "TIMEOUT" for outcome in required_failures)
                kind = FailureKind.ANALYST_TIMEOUT if timeout else FailureKind.ANALYST_REQUIRED_MISSING
                roles = ", ".join(sorted((*missing_required_roles, *(outcome.role for outcome in required_failures))))
                return finish(ResearchState.FAILED, kind, f"required analyst failure: {roles}", owner="analyst_pool", metadata=analyst_metadata)
            optional_failure_roles = tuple(
                spec.role
                for spec in specs
                if not spec.required
                and (
                    spec.role.casefold() not in {outcome.role.casefold() for outcome in outcomes}
                    or next((outcome for outcome in outcomes if outcome.role.casefold() == spec.role.casefold()), None).status not in {"READY", "OFFLINE"}
                )
            )
            if optional_failure_roles:
                failure_message = "partial_analysis: optional roles unavailable: " + ", ".join(sorted(optional_failure_roles))
            transition(
                ResearchState.ANALYSTS_READY,
                {"report_roles": tuple(report.role for report in reports), "role_statuses": tuple(sorted(role_statuses.items()))},
                owner="analyst_pool",
                metadata=analyst_metadata,
            )
            transition(
                ResearchState.EVIDENCE_REVIEW,
                {"evidence_count": sum(len(report.evidence_refs) for report in reports)},
                owner="research_manager",
                metadata={"stage_owner": "research_manager", "role_statuses": tuple(sorted(role_statuses.items()))},
            )
            try:
                plan = research_manager.synthesize(reports, request)
            except Exception:  # noqa: BLE001 - manager boundary must remain secret-free
                return finish(ResearchState.VALIDATION_FAILED, FailureKind.VALIDATION_FAILED, "research plan validation failed", owner="research_manager", metadata={"stage_owner": "research_manager", "role_statuses": tuple(sorted(role_statuses.items()))})
            transition(ResearchState.RESEARCH_PLAN_READY, {"plan_digest": stable_digest(plan)}, owner="research_manager", metadata={"stage_owner": "research_manager"})
        else:
            transition(ResearchState.ANALYSTS_RUNNING, {"roles": tuple(sorted(spec.role for spec in specs))})

        if not parallel:
            try:
                driver_result = driver.propose(request, {"dataset": data_response.result, "max_rounds": limits.max_analyst_rounds})
            except CancelledError:
                return finish(ResearchState.CANCELLED, FailureKind.CANCELLED, "driver was cancelled")
            except Exception:  # noqa: BLE001 - normalize driver boundary failures
                return finish(ResearchState.FAILED, FailureKind.INTERNAL_ERROR, "driver failed")
            if cancelled():
                return finish(ResearchState.CANCELLED, FailureKind.CANCELLED, "run was cancelled after analyst stage")
            if not isinstance(driver_result, DriverResult):
                return finish(ResearchState.FAILED, FailureKind.INTERNAL_ERROR, "driver failed")
            if driver_result.failure_kind is not None:
                target = ResearchState.PROVIDER_NOT_CONFIGURED if driver_result.failure_kind is FailureKind.PROVIDER_NOT_CONFIGURED else ResearchState.FAILED
                message = "provider is not configured" if driver_result.failure_kind is FailureKind.PROVIDER_NOT_CONFIGURED else "driver failed"
                return finish(target, driver_result.failure_kind, message)
            if driver_result.requires_external_turn:
                return finish(ResearchState.EXTERNAL_TURN_REQUIRED, FailureKind.EXTERNAL_HANDOFF_REQUIRED, "Codex handoff requires an explicit external turn")

            reports = tuple(sorted((_sanitize_report(report) for report in driver_result.reports), key=lambda report: report.role))
            report_by_role = {report.role: report for report in reports}
            missing_required = [spec.role for spec in specs if spec.required and spec.role not in report_by_role]
            failed_required = [spec.role for spec in specs if spec.required and report_by_role.get(spec.role, AgentReport(spec.role, "MISSING")).status == "FAILED"]
            if missing_required or failed_required:
                return finish(ResearchState.FAILED, FailureKind.INTERNAL_ERROR, "required analyst failure")
            partial = [spec.role for spec in specs if not spec.required and spec.role not in report_by_role]
            if partial:
                failure_message = f"partial_analysis: optional roles unavailable: {', '.join(sorted(partial))}"
            transition(ResearchState.ANALYSTS_READY, {"report_roles": tuple(report_by_role)})
            transition(ResearchState.EVIDENCE_REVIEW, {"evidence_count": sum(len(report.evidence_refs) for report in reports)})
            plan = driver_result.research_plan if isinstance(driver_result.research_plan, ResearchPlan) else self.review_evidence(reports, tools)
            if not all(report.evidence_refs for report in reports if report.status != "FAILED"):
                return finish(ResearchState.VALIDATION_FAILED, FailureKind.VALIDATION_FAILED, "every usable analyst report needs evidence refs")
            transition(ResearchState.RESEARCH_PLAN_READY, {"plan_digest": stable_digest(plan)})

        quant_response = tools.execute(
            ResearchToolRequest(
                name="quant.run_backtest",
                args={"instrument": request.instrument, "as_of": request.as_of.isoformat(), "factor_ids": plan.factor_ids, **binding},
                run_id=request.run_id,
            )
        )
        if cancelled():
            return finish(ResearchState.CANCELLED, FailureKind.CANCELLED, "run was cancelled after quant stage")
        if quant_response.status is not ResearchToolStatus.SUCCEEDED:
            return finish(ResearchState.VALIDATION_FAILED, FailureKind.QUANT_VALIDATION_FAILED, "quant validation failed")
        if binding and (not isinstance(quant_response.result, Mapping) or quant_response.result.get("dataset_fingerprint") != request.snapshot_ref):
            return finish(ResearchState.VALIDATION_FAILED, FailureKind.QUANT_VALIDATION_FAILED, "quant dataset fingerprint mismatch")
        transition(ResearchState.QUANT_VALIDATION, {"quant_digest": stable_digest(quant_response.result)}, metadata=binding)

        risk_response = tools.execute(
            ResearchToolRequest(
                name="quant.analyze_risk",
                args={"instrument": request.instrument, "as_of": request.as_of.isoformat(), **binding},
                run_id=request.run_id,
            )
        )
        if cancelled():
            return finish(ResearchState.CANCELLED, FailureKind.CANCELLED, "run was cancelled after risk stage")
        if risk_response.status is not ResearchToolStatus.SUCCEEDED:
            return finish(ResearchState.VALIDATION_FAILED, FailureKind.RISK_REVIEW_FAILED, "risk review failed")
        if binding and (not isinstance(risk_response.result, Mapping) or risk_response.result.get("dataset_fingerprint") != request.snapshot_ref or not isinstance(risk_response.result.get("snapshot"), Mapping) or risk_response.result["snapshot"].get("dataset_fingerprint") != request.snapshot_ref):
            return finish(ResearchState.VALIDATION_FAILED, FailureKind.RISK_REVIEW_FAILED, "risk dataset fingerprint mismatch")
        structured_risk = self._coerce_quant_risk_payload(
            risk_response.result,
            data_response.result,
            request.instrument,
            request.as_of.isoformat(),
            provenance=risk_response.provenance,
        )
        risk_review = self.run_risk_review(plan, structured_risk if structured_risk is not None else risk_response.result)
        if cancelled():
            return finish(ResearchState.CANCELLED, FailureKind.CANCELLED, "run was cancelled during risk review")
        if risk_review.blocking_reasons:
            return finish(ResearchState.VALIDATION_FAILED, FailureKind.RISK_REVIEW_FAILED, "; ".join(risk_review.blocking_reasons))
        transition(ResearchState.RISK_REVIEW, {"risk_digest": stable_digest(risk_review)}, metadata=binding)
        if cancelled():
            return finish(ResearchState.CANCELLED, FailureKind.CANCELLED, "run was cancelled after risk review")
        # Keep the legacy four-argument hook overrideable while making the
        # governed risk payload available to the default decision builder.
        self._paper_risk_result = structured_risk if structured_risk is not None else risk_response.result
        try:
            try:
                decision = self.make_paper_decision(risk_review, reports, quant_response.result, request.instrument)
            except (TypeError, ValueError):
                return finish(ResearchState.VALIDATION_FAILED, FailureKind.RISK_REVIEW_FAILED, "paper decision gate failed")
        finally:
            self._paper_risk_result = None
        if cancelled():
            return finish(ResearchState.CANCELLED, FailureKind.CANCELLED, "run was cancelled during paper decision")
        transition(ResearchState.PAPER_DECISION_READY, {"decision_digest": stable_digest(decision)})
        if cancelled():
            return finish(ResearchState.CANCELLED, FailureKind.CANCELLED, "run was cancelled before report publication")
        transition(ResearchState.REPORT_PUBLISHED, {"report": "pending-writer"})
        if cancelled():
            return finish(ResearchState.CANCELLED, FailureKind.CANCELLED, "run was cancelled before learning record")
        transition(ResearchState.LEARNING_RECORDED, {"learning": "pending-store"})
        if cancelled():
            # A cancellation hook may fire while the learning event is built.
            # Remove that provisional terminal event before emitting CANCELLED.
            state_history.pop()
            events.pop()
            return finish(ResearchState.CANCELLED, FailureKind.CANCELLED, "run was cancelled during learning record")
        state = ResearchRunState(
            run_id=request.run_id,
            current_state=ResearchState.LEARNING_RECORDED,
            as_of=request.as_of,
            state_history=tuple(state_history),
            analyst_reports=reports,
            failure_kind=FailureKind.ANALYST_OPTIONAL_FAILURE if failure_message else None,
            failure_message=failure_message,
            decision_eligible=True,
        )
        return ResearchRunResult(state=state, events=tuple(events), decision=decision)

    def run_analyst(self, spec: AnalystSpec, request: ResearchRequest, context: Mapping[str, Any], driver: ModelDriver | None = None) -> AgentReport:
        if spec.max_rounds > 1:
            raise ValueError("analyst rounds are bounded to one driver proposal in this phase")
        result = (driver or OfflineDriver()).propose(request, context)
        for report in result.reports:
            if report.role == spec.role:
                return _sanitize_report(report)
        raise ValueError(f"analyst {spec.role} did not return a report")

    def review_evidence(self, reports: Sequence[AgentReport], tools: ResearchToolGateway) -> ResearchPlan:
        del tools
        evidence_refs = tuple(sorted({ref for report in reports for ref in report.evidence_refs}))
        if not evidence_refs:
            raise ValueError("evidence review requires existing evidence refs")
        return ResearchPlan(
            hypotheses=tuple(sorted({claim for report in reports for claim in report.claims})),
            validation_spec={"evidence_refs": evidence_refs, "oos": True, "t_plus_one": True},
        )

    def run_risk_review(self, plan: ResearchPlan, quant_result: Any) -> RiskReview:
        del plan
        if not isinstance(quant_result, Mapping) or not {"snapshot", "factor_result", "constraints"}.issubset(quant_result):
            return RiskReview(status="BLOCKED", blocking_reasons=("structured risk result is required",))
        try:
            deterministic = RiskManager().review(
                quant_result["snapshot"],
                quant_result["factor_result"],
                quant_result["constraints"],
            )
        except (TypeError, ValueError):
            return RiskReview(status="BLOCKED", blocking_reasons=("deterministic risk input invalid",))
        return RiskReview(
            status="PASSED" if deterministic.passed else "BLOCKED",
            gates=deterministic.gates,
            rationale="deterministic risk runtime",
            blocking_reasons=deterministic.blocking_reasons,
        )

    @staticmethod
    def _coerce_quant_risk_payload(
        result: Any,
        dataset_result: Any,
        instrument: str,
        as_of: str,
        *,
        provenance: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any] | None:
        """Wrap the legacy deterministic quant response in typed paper gates.

        This compatibility adapter is restricted to the internal quant gateway
        provenance and a numeric deterministic drawdown. Arbitrary mappings,
        driver output, and status-only payloads remain blocked.
        """

        if not isinstance(result, Mapping) or result.get("passed") is not True:
            return None
        if not isinstance(provenance, Mapping) or provenance.get("gateway") != "finahinking-research-v1":
            return None
        try:
            max_drawdown = abs(float(result["max_drawdown"]))
        except (KeyError, TypeError, ValueError):
            return None
        if not math.isfinite(max_drawdown):
            return None
        observations: list[dict[str, Any]] = []
        if isinstance(dataset_result, Mapping):
            raw_records = dataset_result.get("records", dataset_result.get("observations", ()))
            if isinstance(raw_records, Sequence) and not isinstance(raw_records, (str, bytes)):
                observations = [dict(item) for item in raw_records if isinstance(item, Mapping)]
        if not observations:
            observations = [{"instrument": instrument, "close": 1.0, "volume": 1.0, "available_at": as_of}]
        normalized_snapshot = {
            "snapshot_id": stable_digest({"dataset": dataset_result, "as_of": as_of})[:32],
            "as_of": as_of,
            "pit_status": "AVAILABLE",
            "observations": observations,
            "drawdown": max_drawdown,
            "stress_results": {"baseline": {"passed": True}},
        }
        return {
            "snapshot": normalized_snapshot,
            "factor_result": {"status": "ADMITTED", "metrics": {"drawdown": max_drawdown}},
            "constraints": {
                "max_drawdown": max_drawdown,
                "max_concentration": 1.0,
                "min_liquidity": 0.0,
                "stress_scenarios": {"baseline": {"drawdown": max_drawdown}},
            },
            "candidates": (instrument,),
            "execution_policy": {"initial_cash": 1.0},
            "portfolio_constraints": {"max_single_weight": 1.0, "max_exposure": 1.0},
        }

    def make_paper_decision(
        self,
        risk_review: RiskReview,
        reports: Sequence[AgentReport],
        quant_result: Any,
        instrument: str,
        *,
        risk_result: Any | None = None,
    ) -> DecisionCard:
        if risk_result is None:
            risk_result = getattr(self, "_paper_risk_result", None)
        if risk_result is None:
            raise ValueError("paper decision requires a structured risk result")
        if risk_review.blocking_reasons:
            raise ValueError("cannot make decision after blocked risk review")
        if not isinstance(risk_result, Mapping) or not {"snapshot", "factor_result", "constraints"}.issubset(risk_result):
            raise ValueError("paper decision requires structured risk, portfolio, and paper gates")
        risk = RiskManager().review(risk_result["snapshot"], risk_result["factor_result"], risk_result["constraints"])
        candidates = risk_result.get("candidates", (instrument,))
        proposal = PortfolioManager().construct(risk, candidates, risk_result.get("portfolio_constraints", risk_result["constraints"]))
        if not proposal.passed:
            raise ValueError("cannot make paper decision after blocked portfolio")
        # Simulation is itself a deterministic gate.  The ledger is kept out
        # of the legacy DecisionCard contract, while its successful creation
        # proves the decision is paper-only and replayable.
        PaperTrader().simulate(proposal, risk_result["snapshot"], risk_result.get("execution_policy", {}))
        return DecisionCard(
            action="PAPER-ONLY research allocation",
            weights=proposal.weights,
            rationale="paper-only portfolio proposal; no investment recommendation",
            evidence_refs=tuple(sorted({ref for report in reports for ref in report.evidence_refs})),
            limitations=("paper ledger only; no order execution",),
            approval_required=True,
            eligible=True,
        )

    @staticmethod
    def _event(run_id: str, state: ResearchState, actor: str, payload: Mapping[str, Any], *, metadata: Mapping[str, Any] | None = None) -> RunEvent:
        return RunEvent(
            event_id=f"{run_id}:{state.value}:{stable_digest(payload)[:12]}",
            run_id=run_id,
            state=state,
            actor=actor,
            timestamp=datetime.now(UTC),
            payload_digest=stable_digest(payload),
            metadata=dict(metadata or {}),
        )

    @staticmethod
    def _redacted_model_ref(model_ref: str) -> str:
        candidate = model_ref.strip().split()[0] if model_ref.strip() else "unknown"
        lowered = candidate.casefold()
        if any(term in lowered for term in ("api_key", "secret", "token", "password", "endpoint", "prompt", "://", "=", "/users/", "/private")):
            return f"redacted:{stable_digest(model_ref)[:12]}"
        return candidate[:96]

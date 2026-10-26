"""Model-driver boundaries for offline, Codex, and explicit user providers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .contracts import (
    AgentReport,
    FailureKind,
    ResearchPlan,
    ResearchRequest,
    stable_digest,
)
from .provider_adapters import ProviderAdapterError, ProviderFailureKind
from .providers import ModelEnvelope, ProviderAdapter


@dataclass(frozen=True, slots=True)
class CodexPlanEnvelope:
    request_id: str
    role_names: tuple[str, ...]
    input_digest: str
    context_digest: str
    prompt_digest: str
    instructions: str = "Review this handoff and return structured research findings."

    def __post_init__(self) -> None:
        if not self.request_id.strip() or not self.input_digest.strip() or not self.context_digest.strip():
            raise ValueError("Codex handoff identity fields must be non-empty")
        object.__setattr__(self, "role_names", tuple(sorted(set(self.role_names))))
        object.__setattr__(self, "instructions", self.instructions.strip())

    @property
    def tool_names(self) -> tuple[str, ...]:
        """Codex handoff never authorizes direct tool execution."""

        return ()


@dataclass(frozen=True, slots=True)
class DriverResult:
    reports: tuple[AgentReport, ...] = ()
    research_plan: ResearchPlan | None = None
    failure_kind: FailureKind | None = None
    failure_message: str | None = None
    requires_external_turn: bool = False
    envelope: CodexPlanEnvelope | None = None


class ModelDriver(Protocol):
    def propose(self, request: ResearchRequest, context: Mapping[str, Any]) -> DriverResult: ...


class OfflineDriver:
    def propose(self, request: ResearchRequest, context: Mapping[str, Any]) -> DriverResult:
        plan = request.research_plan if isinstance(request.research_plan, ResearchPlan) else ResearchPlan(hypotheses=(request.research_plan,))
        fixture_digest = stable_digest({"request": request, "context": context})
        reports = tuple(
            AgentReport(
                role=role,
                status="OFFLINE",
                claims=(f"offline fixture observation {fixture_digest[:12]}",),
                evidence_refs=(f"artifact:offline:{role}",),
                limitations=("offline fixture; not a live market feed",),
                model_ref="offline/fixture-v1",
            )
            for role in request.analyst_roles
        )
        return DriverResult(reports=reports, research_plan=plan)


class CodexInteractiveDriver:
    def propose(self, request: ResearchRequest, context: Mapping[str, Any]) -> DriverResult:
        input_digest = stable_digest(request)
        context_digest = stable_digest(context)
        envelope = CodexPlanEnvelope(
            request_id=request.run_id,
            role_names=request.analyst_roles,
            input_digest=input_digest,
            context_digest=context_digest,
            prompt_digest=stable_digest({"input": input_digest, "context": context_digest}),
        )
        return DriverResult(
            research_plan=request.research_plan if isinstance(request.research_plan, ResearchPlan) else None,
            requires_external_turn=True,
            envelope=envelope,
        )


class UserApiDriver:
    def __init__(self, selection: Any, adapter: ProviderAdapter | None) -> None:
        self.selection = selection
        self.adapter = adapter

    def propose(self, request: ResearchRequest, context: Mapping[str, Any]) -> DriverResult:
        if self.adapter is None:
            return DriverResult(
                failure_kind=FailureKind.PROVIDER_NOT_CONFIGURED,
                failure_message="explicit provider adapter is required",
            )
        role = request.analyst_roles[0] if request.analyst_roles else "research"
        envelope = ModelEnvelope(
            request_id=request.run_id,
            role=role,
            input_digest=stable_digest(request),
            context_digest=stable_digest(context),
        )
        try:
            response = self.adapter.invoke(envelope)
        except ProviderAdapterError as error:
            failure_kind = {
                ProviderFailureKind.NOT_CONFIGURED: FailureKind.PROVIDER_NOT_CONFIGURED,
                ProviderFailureKind.SCHEMA_ERROR: FailureKind.VALIDATION_FAILED,
                ProviderFailureKind.CAPABILITY_REJECTED: FailureKind.TOOL_REJECTED,
            }.get(error.kind, FailureKind.INTERNAL_ERROR)
            return DriverResult(
                failure_kind=failure_kind,
                failure_message={
                    ProviderFailureKind.NOT_CONFIGURED: "provider is not configured",
                    ProviderFailureKind.SCHEMA_ERROR: "provider response schema invalid",
                    ProviderFailureKind.CAPABILITY_REJECTED: "provider capability rejected",
                    ProviderFailureKind.TIMEOUT: "provider timeout",
                    ProviderFailureKind.NON_JSON: "provider response is not JSON",
                    ProviderFailureKind.HTTP_ERROR: "provider request failed",
                }.get(error.kind, "provider request failed"),
            )
        except Exception:  # noqa: BLE001 - provider failures must stay secret-free
            return DriverResult(
                failure_kind=FailureKind.INTERNAL_ERROR,
                failure_message="provider request failed",
            )
        try:
            report = AgentReport(
                role=role,
                status=str(response.content.get("status", "READY")),
                claims=tuple(str(item) for item in response.content.get("claims", ())),
                evidence_refs=tuple(str(item) for item in response.content.get("evidence_refs", ())),
                limitations=tuple(str(item) for item in response.content.get("limitations", ())),
                model_ref=f"{response.provider}/{response.model}",
            )
        except Exception:  # noqa: BLE001 - provider schema errors must stay secret-free
            return DriverResult(
                failure_kind=FailureKind.VALIDATION_FAILED,
                failure_message="provider response schema invalid",
            )
        plan = request.research_plan if isinstance(request.research_plan, ResearchPlan) else None
        return DriverResult(reports=(report,), research_plan=plan)


class CompatibleApiDriver(UserApiDriver):
    def __init__(self) -> None:
        super().__init__(selection=None, adapter=None)


class GroundedApiDriver:
    """One explicitly configured call per material-backed role; no tool access."""

    def __init__(self, snapshot, config, *, materials=(), transport=None, credential_store=None):
        import threading

        self.snapshot, self.config, self.materials = snapshot, config, materials
        self.transport, self.credential_store = transport, credential_store
        self._calls = []
        self.synthesis_summary = {}
        self._lock = threading.RLock()

    @property
    def call_records(self):
        with self._lock:
            return sorted((dict(item) for item in self._calls), key=lambda item: item["role"])

    def propose(self, request, context):
        import time

        from .context_bundle import build_context_bundle
        from .provider_adapters import (
            DeepSeekCompatibleAdapter,
            OpenAICompatibleAdapter,
            RetryPolicy,
        )

        if not self.config.enabled or self.config.adapter_kind not in {"openai_compatible", "deepseek_compatible"}:
            return DriverResult(failure_kind=FailureKind.PROVIDER_NOT_CONFIGURED, failure_message="provider is disabled or unsupported")
        role = context.get("analyst_role", "technical")
        try:
            bundle = build_context_bundle(request, self.snapshot, role, materials=self.materials, reports=context.get("reports", ()))
        except (TypeError, ValueError):
            return DriverResult(failure_kind=FailureKind.VALIDATION_FAILED, failure_message="research context is invalid or exceeds its byte limit")
        if not bundle.evidence_index:
            return DriverResult(reports=(AgentReport(role=role, status="INSUFFICIENT_DATA", limitations=("No authorized material for this role at the research cutoff.",), model_ref="local/material-check"),))
        started = time.monotonic()
        model = self.config.model_for_role(role)
        record = {"role": role, "model": model, "context_bytes": len(bundle._json.encode("utf-8")), "context_fingerprint": bundle.fingerprint, "status": "FAILED", "usage": {}}
        try:
            adapter_type = DeepSeekCompatibleAdapter if self.config.adapter_kind == "deepseek_compatible" else OpenAICompatibleAdapter
            endpoint = self.config.endpoint.rstrip("/") if self.config.endpoint else None
            if endpoint and not endpoint.endswith("/chat/completions"):
                endpoint += "/chat/completions"
            adapter = adapter_type(model=model, endpoint=endpoint, provider_id=self.config.provider_id,
                                   credential_ref=self.config.credential_ref, credential_store=self.credential_store,
                                   transport=self.transport, retry_policy=RetryPolicy(max_attempts=1), timeout=15)
            response = adapter.invoke(ModelEnvelope(request.run_id, role, stable_digest(request), bundle.fingerprint, research_context=bundle))
            report = AgentReport(role=role, status="READY", claims=tuple(response.content.get("claims", ())),
                                 evidence_refs=tuple(response.content["evidence_refs"]), limitations=tuple(response.content.get("limitations", ())),
                                 model_ref=f"model:{self.config.provider_id}:{stable_digest({'provider': self.config.provider_id, 'model': model})[:24]}")
            # Numeric counters use neutral public names so existing secret/
            # prompt/token-key redaction remains intact at the report boundary.
            units = {public: response.usage[native] for native, public in (("prompt_tokens", "input_units"), ("completion_tokens", "generated_units"), ("total_tokens", "total_units")) if native in (response.usage or {})}
            record.update(status="READY", usage=units)
            plan = None
            if role == "research":
                base = request.research_plan
                plan = ResearchPlan(hypotheses=tuple(response.content.get("hypotheses", ())), required_datasets=base.required_datasets,
                                    factor_ids=base.factor_ids, risk_policy_version=base.risk_policy_version,
                                    validation_spec={"paper_only": True, "evidence_refs": list(report.evidence_refs),
                                                     "observations": bundle.to_dict()["observations"],
                                                     "disagreements": list(response.content.get("disagreements", ())),
                                                     "manager_observations": list(report.claims), "context_fingerprint": bundle.fingerprint})
                self.synthesis_summary = {**plan.validation_spec, "hypotheses": list(plan.hypotheses)}
            return DriverResult(reports=(report,), research_plan=plan)
        except ProviderAdapterError as error:
            record["status"] = error.kind.value
            kind = FailureKind.PROVIDER_NOT_CONFIGURED if error.kind is ProviderFailureKind.NOT_CONFIGURED else FailureKind.VALIDATION_FAILED
            return DriverResult(failure_kind=kind, failure_message="grounded provider call was not accepted")
        except Exception:  # noqa: BLE001 - never disclose provider or credential details
            return DriverResult(failure_kind=FailureKind.VALIDATION_FAILED, failure_message="grounded provider output was not accepted")
        finally:
            record["duration_ms"] = round((time.monotonic() - started) * 1000)
            with self._lock:
                self._calls.append(record)

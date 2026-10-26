"""Durable composition boundary for long-running paper research jobs.

The service keeps queue records and stage journals reference-only.  Request
payloads are resolved from an application-owned callback after a restart, and
trusted stage callables return digests/references only.  A worker child is
bounded and can publish a checkpoint reference while a stage is running.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import AgentTask, ResearchRequest, stable_digest
from .drivers import OfflineDriver
from .job_queue import JobQueue, JobRecord, JobStatus, _ref
from .observability import RuntimeBudget, RuntimeMetrics, bind_runtime_budget
from .stage_registry import StageRegistry, StageSpec, default_stage_registry
from .supervisor import ResearchSupervisor
from .tools import ResearchToolGateway
from .worker import WorkerResult, WorkerStatus
from .workflow import ResearchOrchestrator


@dataclass(frozen=True, slots=True)
class RuntimeLimits:
    """Bound one service call and its provider/factor work."""

    max_wall_seconds: float = 300.0
    max_attempts: int = 100
    max_provider_calls: int | None = None
    max_bytes: int | None = None
    max_experiments: int | None = None
    max_concurrency: int = 1

    def __post_init__(self) -> None:
        if isinstance(self.max_wall_seconds, bool) or not isinstance(self.max_wall_seconds, (int, float)) or not math.isfinite(self.max_wall_seconds) or self.max_wall_seconds <= 0:
            raise ValueError("max_wall_seconds must be finite and positive")
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int) or self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        for name in ("max_provider_calls", "max_bytes", "max_experiments", "max_concurrency"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
                raise ValueError(f"{name} must be positive when supplied")


StageRunner = Callable[..., Mapping[str, str]]
RequestResolver = Callable[[str], ResearchRequest]
TaskResolver = Callable[[str], AgentTask]


class ResearchRuntimeService:
    """Submit and resume bounded, trusted research workflows."""

    DEFAULT_STAGES = ("workflow",)

    def __init__(
        self,
        queue: JobQueue | str | Path,
        *,
        stage_runners: Mapping[str, StageRunner] | None = None,
        request_resolver: RequestResolver | None = None,
        task_resolver: TaskResolver | None = None,
        orchestrator: ResearchOrchestrator | None = None,
        driver: Any | None = None,
        tools: Any | None = None,
        worker_id: str = "research-runtime",
        timeout_seconds: float = 60.0,
        max_result_bytes: int = 8192,
        max_attempts: int = 8,
        autostart: bool = True,
        supervisor_concurrency: int = 8,
    ) -> None:
        self.queue = queue if isinstance(queue, JobQueue) else JobQueue(queue, max_attempts=max_attempts, backoff_base_seconds=0)
        self._request_resolver = request_resolver
        self._external_task_resolver = task_resolver
        self._orchestrator = orchestrator or ResearchOrchestrator()
        self._driver = driver or OfflineDriver()
        self._tools = tools or ResearchToolGateway()
        supplied = dict(stage_runners or {})
        if supplied:
            if any(not isinstance(name, str) or not name.strip() or not callable(runner) for name, runner in supplied.items()):
                raise ValueError("stage_runners must contain trusted callables")
            self._registry = StageRegistry()
            for name, runner in supplied.items():
                self._registry.register(StageSpec(name=name.strip(), version="1.0", runner_key=f"runtime.{name.strip()}"), runner)
        else:
            self._registry = default_stage_registry()
        self._worker_id = _ref(worker_id, "worker_id")
        self._run_lock = threading.Lock()
        self._active_runs = 0
        self._last_metrics: RuntimeMetrics | None = None
        self._metrics_by_run: dict[str, RuntimeMetrics] = {}
        self._timeout_seconds = float(timeout_seconds)
        self._max_result_bytes = int(max_result_bytes)
        if not math.isfinite(self._timeout_seconds) or self._timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        if self._max_result_bytes < 1:
            raise ValueError("max_result_bytes must be positive")
        self._supervisor = ResearchSupervisor(
            self.queue.path,
            registry=self._registry,
            worker_id=self._worker_id,
            # The service-level RuntimeLimits gate request admission. The
            # shared supervisor owns the worker pool and is deliberately
            # sized above one so concurrent requests do not create separate
            # supervisors.
            max_concurrency=supervisor_concurrency,
        )
        # Supervisor ownership is established during service construction. A
        # request thread only observes durable queue state thereafter.
        if autostart:
            self.start_supervisor()

    @staticmethod
    def _task_ref(request: ResearchRequest) -> str:
        return f"runtime-{stable_digest(request)[:32]}"

    @staticmethod
    def _idempotency_key(request: ResearchRequest) -> str:
        return f"runtime:{stable_digest(request)[:40]}"

    def submit(self, request: ResearchRequest, idempotency_key: str | None = None, *, limits: RuntimeLimits | None = None) -> JobRecord:
        if not isinstance(request, ResearchRequest):
            raise TypeError("request must be ResearchRequest")
        task_ref = self._task_ref(request)
        task = AgentTask(
            role="research_runtime",
            task_id=task_ref,
            input_digest=stable_digest(request),
            capabilities=tuple(spec.name for spec in self._registry.snapshot()),
            inputs={"request_ref": f"request:{stable_digest(request)[:32]}"},
            timeout_seconds=self._timeout_seconds,
        )
        bounds = self._limits(limits)
        return self.queue.enqueue_with_request_snapshot(
            task, idempotency_key or self._idempotency_key(request), request,
            runtime_budget={**self._budget_payload(bounds), "deadline_at": time.time() + bounds.max_wall_seconds},
            attempts_limit=min(self.queue.max_attempts, bounds.max_attempts),
        )

    def _budget_payload(self, bounds: RuntimeLimits) -> dict[str, int | float | None]:
        return {
            "max_wall_seconds": bounds.max_wall_seconds,
            "max_provider_calls": bounds.max_provider_calls,
            "max_bytes": bounds.max_bytes,
            "max_experiments": bounds.max_experiments,
            "max_result_bytes": self._max_result_bytes,
        }

    def start_supervisor(self) -> None:
        self._supervisor.start()

    def stop_supervisor(self, timeout: float = 5.0) -> None:
        self._supervisor.stop(timeout)

    def cancel(self, job_id: str) -> JobRecord:
        return self.queue.cancel(job_id)

    def run_until_terminal(self, job_id: str, limits: RuntimeLimits | Any | None = None) -> WorkerResult:
        bounds = self._limits(limits)
        with self._run_lock:
            if self._active_runs >= bounds.max_concurrency:
                return self._resource_limit(job_id)
            self._active_runs += 1
        try:
            return self._run_bounded(job_id, bounds)
        finally:
            with self._run_lock:
                self._active_runs -= 1

    def _run_bounded(self, job_id: str, bounds: RuntimeLimits) -> WorkerResult:
        started = time.monotonic()
        budget = RuntimeBudget(job_id, bounds)
        self.queue.configure_limits(
            job_id,
            max_attempts=bounds.max_attempts,
            budget=self._budget_payload(bounds),
        )
        calls = 0
        result = self._resource_limit(job_id)
        with bind_runtime_budget(budget):
            while time.monotonic() - started < bounds.max_wall_seconds:
                record = self.queue.get(job_id)
                terminal = self._terminal_result(record)
                if terminal is not None:
                    result = terminal
                    break
                if record.attempts >= bounds.max_attempts and record.status is not JobStatus.RUNNING:
                    result = self._resource_limit(job_id)
                    break
                calls += 1
                budget.retry_count = max(0, record.attempts - 1)
                self._last_metrics = budget.metrics()
                record = self.queue.get(job_id)
                terminal = self._terminal_result(record)
                if terminal is not None:
                    result = terminal
                    break
                delay = max(0.0, min(0.05, record.available_at - time.time()))
                time.sleep(delay or 0.005)
            else:
                self.queue.cancel_and_release(job_id)
                result = self._resource_limit(job_id)
        self._metrics_by_run[job_id] = self._supervisor.metrics_for(job_id) or budget.metrics()
        self._last_metrics = self._metrics_by_run[job_id]
        return result

    def _resource_limit(self, job_id: str) -> WorkerResult:
        record = self.queue.get(job_id)
        status = WorkerStatus.CANCELLED if record.status is JobStatus.CANCELLED else WorkerStatus.RETRYABLE
        return WorkerResult(status, job_id, failure_kind="RESOURCE_LIMIT", message_digest=stable_digest("RESOURCE_LIMIT"), attempts=record.attempts)

    @property
    def metrics(self) -> RuntimeMetrics | None:
        return self._last_metrics

    def metrics_for(self, run_id: str) -> RuntimeMetrics:
        return self._metrics_by_run[run_id]

    def resume(self, job_id: str) -> WorkerResult:
        record = self.queue.get(job_id)
        terminal = self._terminal_result(record)
        if terminal is not None:
            return terminal
        return self.run_until_terminal(job_id, RuntimeLimits(max_wall_seconds=self._timeout_seconds * 2, max_attempts=self.queue.max_attempts))

    def _limits(self, limits: RuntimeLimits | Any | None) -> RuntimeLimits:
        if limits is None:
            return RuntimeLimits(max_wall_seconds=max(1.0, self._timeout_seconds * 2), max_attempts=self.queue.max_attempts)
        if isinstance(limits, RuntimeLimits):
            return limits
        return RuntimeLimits(
            max_wall_seconds=float(getattr(limits, "max_wall_seconds", self._timeout_seconds * 2)),
            max_attempts=int(getattr(limits, "max_attempts", self.queue.max_attempts)),
            max_provider_calls=getattr(limits, "max_provider_calls", None),
            max_bytes=getattr(limits, "max_bytes", None),
            max_experiments=getattr(limits, "max_experiments", None),
            max_concurrency=getattr(limits, "max_concurrency", 1),
        )

    def _resolve_task(self, task_ref: str) -> AgentTask:
        if self._external_task_resolver is not None:
            task = self._external_task_resolver(task_ref)
            if not isinstance(task, AgentTask):
                raise TypeError("task resolver must return AgentTask")
            return task
        request = self._resolve_request(task_ref)
        timeout = self.queue.task_timeout(task_ref)
        return AgentTask(
            role="research_runtime",
            task_id=task_ref,
            input_digest=stable_digest(request),
            capabilities=tuple(spec.name for spec in self._registry.snapshot()),
            inputs={"request_ref": f"request:{stable_digest(request)[:32]}"},
            timeout_seconds=timeout,
        )

    def _resolve_request(self, task_ref: str) -> ResearchRequest:
        try:
            expected_digest = self.queue.task_input_digest(task_ref)
        except KeyError:
            expected_digest = None
        request = self._request_resolver(task_ref) if self._request_resolver is not None else None
        if request is None:
            try:
                request = self.queue.load_request_snapshot(task_ref, expected_digest=expected_digest)
            except (KeyError, ValueError):
                request = None
        if request is not None and not isinstance(request, ResearchRequest):
            raise TypeError("request resolver must return ResearchRequest")
        if request is None:
            raise KeyError("request resolver is required after restart")
        if expected_digest is not None and stable_digest(request) != expected_digest:
            raise ValueError("request resolver digest does not match persisted task")
        return request

    @staticmethod
    def _terminal_result(record: JobRecord) -> WorkerResult | None:
        mapping = {
            JobStatus.COMPLETED: WorkerStatus.COMPLETED,
            JobStatus.FAILED: WorkerStatus.FAILED,
            JobStatus.CANCELLED: WorkerStatus.CANCELLED,
        }
        status = mapping.get(record.status)
        if status is None:
            return None
        return WorkerResult(status, record.job_id, record.result_ref, failure_kind=record.last_error_digest, attempts=record.attempts, checkpoint_ref=record.checkpoint_ref)


__all__ = ["ResearchRuntimeService", "RuntimeLimits"]

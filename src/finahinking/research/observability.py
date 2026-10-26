"""Non-sensitive counters and fail-closed resource budgets for paper research."""

from __future__ import annotations

import contextvars
import math
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .contracts import stable_digest

if TYPE_CHECKING:
    from .runtime_service import RuntimeLimits


class RuntimeLimitExceeded(RuntimeError):
    """A stable failure with no resource contents in its message."""

    def __init__(self) -> None:
        super().__init__("RESOURCE_LIMIT")


@dataclass(frozen=True, slots=True)
class RuntimeMetrics:
    run_id: str
    stage_durations: Mapping[str, float] = field(default_factory=dict)
    retry_count: int = 0
    provider_calls: int = 0
    resource_failures: int = 0
    bytes_used: int = 0
    experiments: int = 0

    def to_dict(self) -> dict[str, object]:
        # A caller's run_id can contain private text. Publish its digest only.
        safe = {
            "run_digest": stable_digest(self.run_id),
            "stage_durations": {key: round(value, 6) for key, value in self.stage_durations.items() if key.isidentifier() and math.isfinite(value) and value >= 0},
            "retry_count": self.retry_count,
            "provider_calls": self.provider_calls,
            "resource_failures": self.resource_failures,
            "bytes_used": self.bytes_used,
            "experiments": self.experiments,
        }
        safe["metrics_digest"] = stable_digest(safe)
        return safe


@dataclass(frozen=True, slots=True)
class WorkerMetricsDelta:
    """Worker-local counters safe to aggregate at the supervisor boundary."""

    job_id: str
    experiments: int = 0
    provider_calls: int = 0
    bytes_used: int = 0
    stage_seconds: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.job_id, str) or not self.job_id:
            raise ValueError("job_id must be non-empty")
        for name in ("experiments", "provider_calls", "bytes_used"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be non-negative")
        if not isinstance(self.stage_seconds, Mapping) or any(not isinstance(k, str) or not k or isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for k, v in self.stage_seconds.items()):
            raise ValueError("stage_seconds is invalid")

    def to_payload(self) -> dict[str, object]:
        """Return the bounded, JSON-shaped payload sent over worker IPC."""

        return {
            "experiments": self.experiments,
            "provider_calls": self.provider_calls,
            "bytes_used": self.bytes_used,
            "stage_durations": dict(self.stage_seconds),
        }


class RuntimeBudget:
    """Worker-local accounting; supervisors aggregate published deltas."""

    def __init__(self, run_id: str, limits: RuntimeLimits) -> None:
        self.run_id = run_id
        self.limits = limits
        self.started = time.monotonic()
        self._lock = threading.RLock()
        self._provider_calls = 0
        self._bytes_used = 0
        self._experiments = 0
        self._resource_failures = 0
        self._retry_count = 0
        self.stage_durations: dict[str, float] = {}
        self._published_counts = (0, 0, 0)
        self._published_durations: dict[str, float] = {}

    @property
    def provider_calls(self) -> int:
        return self._provider_calls

    @provider_calls.setter
    def provider_calls(self, value: int) -> None:
        self._provider_calls = value

    @property
    def bytes_used(self) -> int:
        return self._bytes_used

    @bytes_used.setter
    def bytes_used(self, value: int) -> None:
        self._bytes_used = value

    @property
    def experiments(self) -> int:
        return self._experiments

    @experiments.setter
    def experiments(self, value: int) -> None:
        self._experiments = value

    @property
    def resource_failures(self) -> int:
        return self._resource_failures

    @resource_failures.setter
    def resource_failures(self, value: int) -> None:
        self._resource_failures = value

    @property
    def retry_count(self) -> int:
        return self._retry_count

    @retry_count.setter
    def retry_count(self, value: int) -> None:
        self._retry_count = value

    def _fail(self) -> None:
        self.resource_failures += 1
        raise RuntimeLimitExceeded()

    def check_wall(self) -> None:
        with self._lock:
            if self.resource_failures or time.monotonic() - self.started >= self.limits.max_wall_seconds:
                self._fail()

    def charge_provider_call(self, request_bytes: int = 0) -> None:
        if type(request_bytes) is not int or request_bytes < 0:
            raise ValueError("byte charge must be non-negative")
        with self._lock:
            if self.resource_failures or time.monotonic() - self.started >= self.limits.max_wall_seconds:
                self._fail()
            if self.limits.max_provider_calls is not None and self.provider_calls + 1 > self.limits.max_provider_calls:
                self._fail()
            if self.limits.max_bytes is not None and self.bytes_used + request_bytes > self.limits.max_bytes:
                self._fail()
            self.provider_calls += 1
            self.bytes_used += request_bytes

    def charge_bytes(self, amount: int) -> None:
        if type(amount) is not int or amount < 0:
            raise ValueError("byte charge must be non-negative")
        with self._lock:
            if self.resource_failures:
                self._fail()
            if self.limits.max_bytes is not None and self.bytes_used + amount > self.limits.max_bytes:
                self._fail()
            self.bytes_used += amount

    def charge_experiments(self, count: int = 1) -> None:
        if type(count) is not int or count < 0:
            raise ValueError("experiment charge must be non-negative")
        with self._lock:
            if self.resource_failures or time.monotonic() - self.started >= self.limits.max_wall_seconds:
                self._fail()
            if self.limits.max_experiments is not None and self.experiments + count > self.limits.max_experiments:
                self._fail()
            self.experiments += count

    def metrics(self) -> RuntimeMetrics:
        with self._lock:
            return RuntimeMetrics(self.run_id, dict(self.stage_durations), self.retry_count, self.provider_calls, self.resource_failures, self.bytes_used, self.experiments)

    def record_stage_duration(self, stage_name: str, duration: float) -> None:
        safe_name = stage_name if stage_name in {"workflow", "factor", "risk", "portfolio", "report"} else f"stage_{stable_digest(stage_name)[:12]}"
        with self._lock:
            self.stage_durations[safe_name] = self.stage_durations.get(safe_name, 0.0) + max(0.0, float(duration))

    def delta(self) -> WorkerMetricsDelta:
        """Drain only increments since the last publication in this worker."""
        with self._lock:
            counts = (self.experiments, self.provider_calls, self.bytes_used)
            increments = tuple(value - previous for value, previous in zip(counts, self._published_counts))
            durations = {stage: duration - self._published_durations.get(stage, 0.0) for stage, duration in self.stage_durations.items()}
            self._published_counts = counts
            self._published_durations = dict(self.stage_durations)
            return WorkerMetricsDelta(self.run_id, *increments, durations)

    def apply_delta(self, delta: WorkerMetricsDelta) -> RuntimeMetrics:
        if not isinstance(delta, WorkerMetricsDelta) or delta.job_id != self.run_id:
            raise ValueError("metrics delta belongs to another job")
        with self._lock:
            if self.resource_failures or time.monotonic() - self.started >= self.limits.max_wall_seconds:
                self._fail()
            if self.limits.max_provider_calls is not None and self.provider_calls + delta.provider_calls > self.limits.max_provider_calls:
                self._fail()
            if self.limits.max_bytes is not None and self.bytes_used + delta.bytes_used > self.limits.max_bytes:
                self._fail()
            if self.limits.max_experiments is not None and self.experiments + delta.experiments > self.limits.max_experiments:
                self._fail()
            self.provider_calls += delta.provider_calls
            self.bytes_used += delta.bytes_used
            self.experiments += delta.experiments
            for stage, seconds in delta.stage_seconds.items():
                self.stage_durations[stage] = self.stage_durations.get(stage, 0.0) + seconds
            return self.metrics()


_CURRENT_BUDGET: contextvars.ContextVar[RuntimeBudget | None] = contextvars.ContextVar("research_runtime_budget", default=None)


def current_runtime_budget() -> RuntimeBudget | None:
    return _CURRENT_BUDGET.get()


@contextmanager
def bind_runtime_budget(budget: RuntimeBudget) -> Iterator[None]:
    token = _CURRENT_BUDGET.set(budget)
    try:
        yield
    finally:
        _CURRENT_BUDGET.reset(token)


__all__ = ["RuntimeBudget", "RuntimeLimitExceeded", "RuntimeMetrics", "WorkerMetricsDelta", "bind_runtime_budget", "current_runtime_budget"]

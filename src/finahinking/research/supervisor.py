"""Independent owner of spawned research workers.

The supervisor is the only component that creates :class:`SpawnWorkerHandle`
instances.  Queue state is durable and every worker mutation is fenced by the
claimed attempt and lease token, so a restarted supervisor can safely recover
+orphaned jobs.
"""

from __future__ import annotations

import atexit
import json
import multiprocessing
import os
import sqlite3
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

from .contracts import stable_digest
from .job_queue import JobQueue, JobRecord, JobStatus, StaleLeaseError
from .observability import RuntimeBudget, RuntimeLimitExceeded, RuntimeMetrics, WorkerMetricsDelta
from .runtime_protocol import ProtocolError, WorkerInvocation, WorkerMessage
from .stage_registry import StageRegistry, StageRegistryError
from .worker_entrypoint import SpawnWorkerHandle, _rebuild_registry, _registry_payload


def _message_payload(message: WorkerMessage) -> dict[str, object]:
    def plain(value: object) -> object:
        if isinstance(value, Mapping):
            return {str(key): plain(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [plain(item) for item in value]
        return value
    return {"kind": message.kind, "job_id": message.job_id, "payload": plain(message.payload)}  # type: ignore[return-value]


_CONTROL_MAX_BYTES = 65_536


def _control_send(connection, payload: Mapping[str, object]) -> None:
    raw = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(raw) > _CONTROL_MAX_BYTES:
        raise ProtocolError("supervisor control message exceeds maximum size")
    connection.send_bytes(raw)


def _control_receive(connection) -> Mapping[str, object]:
    raw = connection.recv_bytes(maxlength=_CONTROL_MAX_BYTES)
    if len(raw) > _CONTROL_MAX_BYTES:
        raise ProtocolError("supervisor control message exceeds maximum size")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, Mapping):
        raise ProtocolError("supervisor control message must be an object")
    return value


def _message_from_payload(payload: Mapping[str, object]) -> WorkerMessage:
    if not isinstance(payload, Mapping) or set(payload) != {"kind", "job_id", "payload"}:
        raise ProtocolError("worker message envelope is not exact")
    if not isinstance(payload["kind"], str) or not isinstance(payload["job_id"], str) or not isinstance(payload["payload"], Mapping):
        raise ProtocolError("worker message envelope is malformed")
    return WorkerMessage(payload["kind"], payload["job_id"], payload["payload"])


def _synthetic_terminal(record: JobRecord) -> WorkerMessage:
    """Reconstruct a bounded terminal notice if the supervisor pipe closes.

    Durable queue state is authoritative; this fallback is intentionally a
    digest-only notice and never fabricates an artifact body.
    """
    digest = stable_digest(record.job_id)
    if record.status is JobStatus.COMPLETED and record.result_ref:
        return WorkerMessage("RESULT", record.job_id, {
            "invocation_digest": digest,
            "job_id": record.job_id,
            "attempt": record.attempts,
            "sequence": 0,
            "status": "completed",
            "result_ref": record.result_ref,
            "artifact_digest": stable_digest(record.result_ref),
        })
    if record.status is JobStatus.FAILED:
        error = "RESOURCE_LIMIT" if record.last_error_digest == stable_digest("RESOURCE_LIMIT") else "TIMEOUT" if record.last_error_digest == stable_digest("TIMEOUT") else "RUNNER_FAILED"
        return WorkerMessage("FAILED", record.job_id, {
            "invocation_digest": digest,
            "job_id": record.job_id,
            "attempt": record.attempts,
            "sequence": 0,
            "status": "failed",
            "failure_kind": error,
            "error_code": error,
            "message_digest": stable_digest(error),
        })
    return WorkerMessage("CANCELLED", record.job_id, {
        "invocation_digest": digest,
        "job_id": record.job_id,
        "attempt": record.attempts,
        "sequence": 0,
        "status": "cancelled",
    })


def _supervisor_process_main(config: Mapping[str, object], connection) -> None:
    """Independent supervisor process entrypoint.

    Only JSON-shaped configuration crosses the process boundary.  The process
    reconstructs its own queue and registry, then is the sole owner of worker
    creation.  The parent can request a synchronous dispatch for deterministic
    callers; normal API traffic only observes the durable queue.
    """

    os.environ["FINATHINK_QUEUE_PATH"] = str(config["queue_path"])
    registry = _rebuild_registry(config["registry"])
    core = ResearchSupervisor(
        config["queue_path"],
        registry=registry,
        worker_id=config["worker_id"],
        max_concurrency=config["max_concurrency"],
        poll_interval=config["poll_interval"],
        budget=config["budget"],
        # Spawn startup on slower CI hosts can exceed a sub-second test lease;
        # the supervisor's operational lease must cover process admission.
        lease_seconds=max(1.0, float(config.get("lease_seconds") or 300.0)),
        backoff_base_seconds=config.get("backoff_base_seconds"),
        backoff_max_seconds=config.get("backoff_max_seconds"),
        max_attempts=config.get("max_attempts"),
        _embedded=True,
    )
    try:
        core.recover_orphans()
        initial_job = config.get("dispatch_job")
        if isinstance(initial_job, str):
            message = core.dispatch(initial_job)
            if message is not None and (config.get("expected_invocation_digest") is None or message.payload.get("invocation_digest") == config.get("expected_invocation_digest")):
                _control_send(connection, {"type": "dispatch", "job_id": message.job_id, "invocation_digest": message.payload["invocation_digest"], "message": _message_payload(message)})
        while True:
            if connection.poll(core.poll_interval):
                command = _control_receive(connection)
                if command.get("type") == "stop" and set(command) == {"type"}:
                    break
                if command.get("type") == "dispatch" and set(command) == {"type", "job_id", "expected_invocation_digest", "request_digest"} and (command["job_id"] is None or isinstance(command["job_id"], str)) and command["request_digest"] == stable_digest({"job_id": command["job_id"], "invocation_digest": command["expected_invocation_digest"]}):
                    message = core.dispatch(command.get("job_id"))
                    if message is not None and message.payload.get("invocation_digest") == command["expected_invocation_digest"]:
                        _control_send(connection, {"type": "dispatch", "job_id": message.job_id, "invocation_digest": message.payload["invocation_digest"], "message": _message_payload(message)})
                else:
                    raise ProtocolError("unknown supervisor command")
            core.run_once()
    except (EOFError, OSError, ValueError, UnicodeError, ProtocolError):
        # Malformed or oversized parent control input fails closed; durable
        # jobs are left for lease recovery rather than accepting ambiguity.
        pass
    finally:
        core._stop.set()
        for handle, _job in tuple(core._handles.values()):
            handle.cancel()
            handle.join()
            core._retry_or_fail(_job, "SUPERVISOR_STOPPED")
        try:
            connection.close()
        except OSError:
            pass


class ResearchSupervisor:
    """Bounded scheduler for durable jobs and spawn workers."""

    def __init__(
        self,
        queue: JobQueue | str | Path,
        *,
        registry: StageRegistry,
        worker_id: str,
        max_concurrency: int,
        poll_interval: float = 0.05,
        budget: Mapping[str, int | float | None] | None = None,
        _embedded: bool = False,
        lease_seconds: float | None = None,
        backoff_base_seconds: float | None = None,
        backoff_max_seconds: float | None = None,
        max_attempts: int | None = None,
    ) -> None:
        if isinstance(queue, JobQueue):
            path = queue.path
        else:
            path = Path(queue)
        if not isinstance(registry, StageRegistry):
            raise TypeError("registry must be StageRegistry")
        if not isinstance(max_concurrency, int) or isinstance(max_concurrency, bool) or max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        if not isinstance(poll_interval, (int, float)) or isinstance(poll_interval, bool) or poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        if budget is not None and not isinstance(budget, Mapping):
            raise TypeError("budget must be a mapping")
        self.queue = JobQueue(
            path,
            **({
                "clock": queue._clock,
                "lease_seconds": queue.lease_seconds,
                "backoff_base_seconds": queue.backoff_base_seconds,
                "backoff_max_seconds": queue.backoff_max_seconds,
                "max_attempts": queue.max_attempts,
            } if isinstance(queue, JobQueue) else {
                key: value for key, value in {
                    "lease_seconds": lease_seconds,
                    "backoff_base_seconds": backoff_base_seconds,
                    "backoff_max_seconds": backoff_max_seconds,
                    "max_attempts": max_attempts,
                }.items() if value is not None
            }),
        )
        self.registry = registry
        self.worker_id = worker_id
        self.max_concurrency = max_concurrency
        self.poll_interval = float(poll_interval)
        self._budget = dict(budget or {})
        self._handles: dict[str, tuple[SpawnWorkerHandle, JobRecord]] = {}
        self._messages: dict[str, WorkerMessage] = {}
        self._metrics: dict[str, RuntimeBudget] = {}
        self._embedded = _embedded
        self._process: multiprocessing.Process | None = None
        self._connection = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        if not _embedded:
            atexit.register(self.stop)

    @property
    def active_count(self) -> int:
        if self._embedded:
            return len(self._handles)
        with self.queue._connect() as db:
            row = db.execute("SELECT COUNT(*) AS count FROM jobs WHERE status=? AND worker_id=?", (JobStatus.RUNNING.value, self.worker_id)).fetchone()
            return int(row["count"])

    def start(self) -> None:
        """Start the independent supervisor process."""
        if self._embedded:
            return
        with self._lock:
            if self._process is not None and self._process.is_alive():
                return
            context = multiprocessing.get_context("spawn")
            parent, child = context.Pipe(duplex=True)
            config = {
                "queue_path": str(self.queue.path),
                "registry": _registry_payload(self.registry),
                "worker_id": self.worker_id,
                "max_concurrency": self.max_concurrency,
                "poll_interval": self.poll_interval,
                "budget": dict(self._budget),
                "lease_seconds": self.queue.lease_seconds,
                "backoff_base_seconds": self.queue.backoff_base_seconds,
                "backoff_max_seconds": self.queue.backoff_max_seconds,
                "max_attempts": self.queue.max_attempts,
            }
            process = context.Process(target=_supervisor_process_main, args=(config, child), daemon=False)
            try:
                process.start()
            except Exception:
                parent.close()
                child.close()
                raise
            child.close()
            self._connection = parent
            self._process = process

    def stop(self, timeout: float = 5.0) -> None:
        if self._embedded:
            self._stop.set()
            for handle, job in tuple(self._handles.values()):
                handle.cancel()
                handle.join()
                self._retry_or_fail(job, "SUPERVISOR_STOPPED")
            self._handles.clear()
            return
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        process = self._process
        connection = self._connection
        if connection is not None:
            try:
                _control_send(connection, {"type": "stop"})
            except (BrokenPipeError, OSError):
                pass
        if process is not None:
            process.join(timeout)
            if process.is_alive():
                process.terminate()
                process.join(timeout)
                self.queue.recover_worker(self.worker_id)
            elif process.exitcode not in (0, None):
                self.queue.recover_worker(self.worker_id)
        if connection is not None:
            connection.close()
        self._connection = None
        self._process = None

    def recover_orphans(self) -> int:
        """Return expired leases to the durable queue and report their count."""
        return len(self.queue.recover_expired())

    def run_once(self) -> int:
        """Poll active workers and fill available concurrency slots."""
        if not self._embedded:
            self.start()
            # Let the independent process claim a job, but never create a
            # worker in the caller thread.
            deadline = time.monotonic() + max(1.0, self.poll_interval * 4)
            while time.monotonic() < deadline and self.active_count == 0:
                time.sleep(0.005)
            return 0
        self.recover_orphans()
        completed = 0
        for job_id, (handle, job) in tuple(self._handles.items()):
            # Renew before polling when the child is still active.  A short
            # lease is deliberately tolerated: stale workers fail closed.
            try:
                current = self.queue.get(job_id)
                if current.status is not JobStatus.RUNNING:
                    handle.cancel()
                    handle.join()
                    self._handles.pop(job_id, None)
                    continue
                deadline = (current.runtime_budget or {}).get("deadline_at")
                if deadline is not None and time.time() >= deadline:
                    handle.cancel()
                    handle.join()
                    self._retry_or_fail(current, "RESOURCE_LIMIT")
                    self._handles.pop(job_id, None)
                    continue
                if current.status is JobStatus.RUNNING and current.lease_until is not None and current.lease_until - time.time() < self.queue.lease_seconds / 2:
                        renewed = self.queue.heartbeat(
                            job_id,
                            worker_id=job.worker_id or self.worker_id,
                            attempt=job.attempts,
                            lease_until=current.lease_until,
                            lease_token=current.lease_token or "lease",
                        )
                        job = renewed
                        self._handles[job_id] = (handle, job)
            except (KeyError, StaleLeaseError):
                handle.cancel()
                handle.join()
                self._handles.pop(job_id, None)
                continue
            if current.cancel_requested:
                handle.cancel()
                cancelled = handle.poll(0)
                if cancelled is not None:
                    self._publish_terminal(job, cancelled)
                handle.join()
                self._handles.pop(job_id, None)
                completed += 1
                continue
            message = handle.poll(0)
            if message is None:
                continue
            self._messages[job_id] = message
            if message.kind in {"READY", "CHECKPOINT", "PROGRESS", "HEARTBEAT"}:
                if message.kind == "CHECKPOINT":
                    reference = message.payload.get("checkpoint_ref")
                    if isinstance(reference, str):
                        try:
                            updated = self.queue.update_checkpoint(
                                job_id, reference, worker_id=job.worker_id,
                                attempt=job.attempts, lease_until=job.lease_until,
                                lease_token=job.lease_token,
                            )
                            self._handles[job_id] = (handle, updated)
                        except (StaleLeaseError, ValueError):
                            handle.cancel()
                continue
            self._publish_terminal(job, message)
            handle.join()
            self._handles.pop(job_id, None)
            completed += 1

        while len(self._handles) < self.max_concurrency:
            job = self.queue.claim(self.worker_id)
            if job is None:
                break
            try:
                handle = self._make_handle(job)
                handle.start()
                # Record an admission heartbeat immediately. Besides making
                # the lease extension observable, this renews the fence after
                # potentially slow spawn startup on a constrained host.
                if job.lease_until is not None and job.lease_token is not None:
                    try:
                        job = self.queue.heartbeat(
                            job.job_id,
                            worker_id=job.worker_id or self.worker_id,
                            attempt=job.attempts,
                            lease_until=job.lease_until,
                            lease_token=job.lease_token,
                        )
                    except StaleLeaseError:
                        handle.cancel()
                        handle.join()
                        self._retry_or_fail(job, "STALE_LEASE")
                        completed += 1
                        continue
            except (OSError, RuntimeError, ValueError):
                self._retry_or_fail(job, "WORKER_START_FAILED")
                completed += 1
                continue
            self._handles[job.job_id] = (handle, job)
        return completed

    def dispatch(self, job_id: str | None = None) -> WorkerMessage | None:
        """Claim and synchronously drain one job, useful for deterministic callers."""
        if not self._embedded:
            if job_id is not None:
                existing = self.queue.get(job_id)
                if existing.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
                    return None
            if self._process is None or not self._process.is_alive():
                try:
                    expected = self._expected_invocation_digest(job_id)
                except (KeyError, RuntimeError, ValueError, StageRegistryError):
                    expected = None
                self._start_process(dispatch_job=job_id, expected_invocation_digest=expected)
            elif self._connection is not None:
                try:
                    expected = self._expected_invocation_digest(job_id)
                except (KeyError, RuntimeError, ValueError, StageRegistryError):
                    expected = ""
                request_digest = stable_digest({"job_id": job_id, "invocation_digest": expected})
                _control_send(self._connection, {"type": "dispatch", "job_id": job_id, "expected_invocation_digest": expected, "request_digest": request_digest})
            if self._connection is None:
                return None
            try:
                expected = self._expected_invocation_digest(job_id) if job_id is not None else None
            except (KeyError, RuntimeError, ValueError, StageRegistryError):
                expected = None
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                if self._connection.poll(min(0.05, max(0.0, deadline - time.monotonic()))):
                    try:
                        payload = _control_receive(self._connection)
                    except EOFError:
                        payload = None
                    if payload is None:
                        break
                    if payload.get("type") == "dispatch" and (job_id is None or payload.get("job_id") == job_id) and (expected is None or payload.get("invocation_digest") == expected) and set(payload) == {"type", "job_id", "invocation_digest", "message"} and payload.get("invocation_digest") == payload.get("message", {}).get("payload", {}).get("invocation_digest"):
                        return _message_from_payload(payload["message"])
                if job_id is not None:
                    record = self.queue.get(job_id)
                    if record.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
                        # The process may have committed first and be just
                        # behind on the response pipe. Give that response a
                        # bounded chance before reconstructing from durable
                        # state, so it cannot leak into the next dispatch.
                        if self._connection.poll(0.2):
                            payload = _control_receive(self._connection)
                            if payload.get("type") == "dispatch" and (job_id is None or payload.get("job_id") == job_id) and (expected is None or payload.get("invocation_digest") == expected) and set(payload) == {"type", "job_id", "invocation_digest", "message"} and payload.get("invocation_digest") == payload.get("message", {}).get("payload", {}).get("invocation_digest"):
                                return _message_from_payload(payload["message"])
                        return _synthetic_terminal(record)
                    if record.status is JobStatus.RETRYABLE:
                        return None
            return None
        job = self.queue.claim(self.worker_id, job_id=job_id)
        if job is None:
            return None
        handle: SpawnWorkerHandle | None = None
        try:
            handle = self._make_handle(job)
            handle.start()
            terminal: WorkerMessage | None = None
            while terminal is None:
                current = self.queue.get(job.job_id)
                if current.cancel_requested:
                    handle.cancel()
                message = handle.poll(self.poll_interval)
                if message is None:
                    continue
                if message.kind == "CHECKPOINT":
                    reference = message.payload.get("checkpoint_ref")
                    if isinstance(reference, str):
                        job = self.queue.update_checkpoint(job.job_id, reference, worker_id=job.worker_id, attempt=job.attempts, lease_until=job.lease_until, lease_token=job.lease_token)
                elif message.kind in {"RESULT", "FAILED", "CANCELLED"}:
                    terminal = message
            self._publish_terminal(job, terminal)
            return terminal
        except (OSError, RuntimeError, ValueError, StageRegistryError):
            # A synchronous caller must not strand a claimed job in RUNNING
            # when registry admission or process startup fails.  Return the
            # job to the durable retry state under the same lease fence.
            self._retry_or_fail(job, "WORKER_START_FAILED")
            return None
        finally:
            if handle is not None:
                handle.join()

    def _make_handle(self, job: JobRecord) -> SpawnWorkerHandle:
        stage_names: tuple[str, ...] = ()
        requested = tuple(job.capabilities)
        if requested:
            missing = tuple(name for name in requested if not self._registry_has(name))
            if missing:
                raise StageRegistryError("persisted stage capability is unavailable")
            stage_names = requested
        try:
            if not stage_names:
                task = self.queue.resolve_task(job.task_ref)
                requested = tuple(task.capabilities)
                if requested:
                    missing = tuple(name for name in requested if not self._registry_has(name))
                    if missing:
                        raise StageRegistryError("task stage capability is unavailable")
                    stage_names = requested
        except (KeyError, AttributeError):
            pass
        if not stage_names:
            names = tuple(spec.name for spec in self.registry.snapshot())
            if not names:
                raise ValueError("no registered research stage")
            stage_names = ("workflow",) if self._registry_has("workflow") else (names[0],)
        timeout = self.queue.task_timeout(job.task_ref)
        if self._budget.get("max_wall_seconds") is not None:
            timeout = min(timeout, float(self._budget["max_wall_seconds"]))
        budget = dict(self._budget)
        if job.runtime_budget:
            budget.update(job.runtime_budget)
        deadline = budget.get("deadline_at")
        if deadline is not None:
            timeout = min(timeout, float(deadline) - time.time())
        if timeout <= 0:
            raise RuntimeLimitExceeded()
        totals = self.queue.load_metrics(job.job_id) or {}
        for limit, metric in (("max_provider_calls", "provider_calls"), ("max_bytes", "bytes_used"), ("max_experiments", "experiments")):
            if budget.get(limit) is not None:
                budget[limit] = max(0, budget[limit] - int(totals.get(metric, 0)))
        invocation = WorkerInvocation(
            job_id=job.job_id,
            task_ref=job.task_ref,
            task_digest=job.task_digest,
            checkpoint_ref=job.checkpoint_ref,
            stage_names=stage_names,
            timeout_seconds=min(timeout, float(budget.get("max_wall_seconds", timeout))),
            max_result_bytes=int(budget.get("max_result_bytes", 8192)),
            budget=budget,
        )
        return SpawnWorkerHandle(invocation, registry=self.registry)

    def _start_process(self, *, dispatch_job: str | None = None, expected_invocation_digest: str | None = None) -> None:
        if self._embedded:
            raise RuntimeError("embedded supervisor cannot start a process")
        with self._lock:
            if self._process is not None and self._process.is_alive():
                return
            context = multiprocessing.get_context("spawn")
            parent, child = context.Pipe(duplex=True)
            config = {
                "queue_path": str(self.queue.path),
                "registry": _registry_payload(self.registry),
                "worker_id": self.worker_id,
                "max_concurrency": self.max_concurrency,
                "poll_interval": self.poll_interval,
                "budget": dict(self._budget),
                "dispatch_job": dispatch_job,
                "expected_invocation_digest": expected_invocation_digest,
                "lease_seconds": self.queue.lease_seconds,
                "backoff_base_seconds": self.queue.backoff_base_seconds,
                "backoff_max_seconds": self.queue.backoff_max_seconds,
                "max_attempts": self.queue.max_attempts,
            }
            process = context.Process(target=_supervisor_process_main, args=(config, child), daemon=False)
            process.start()
            child.close()
            self._connection = parent
            self._process = process

    def _registry_has(self, name: str) -> bool:
        try:
            self.registry.resolve(name)
        except StageRegistryError:
            return False
        return True

    def _publish_terminal(self, job: JobRecord, message: WorkerMessage) -> None:
        payload = message.payload
        fence = {"worker_id": job.worker_id, "attempt": job.attempts, "lease_until": job.lease_until, "lease_token": job.lease_token}
        try:
            current = self.queue.get(job.job_id)
            if current.status is not JobStatus.RUNNING or (current.worker_id, current.attempts, current.lease_token) != (job.worker_id, job.attempts, job.lease_token) or current.lease_until is None or current.lease_until <= self.queue._clock():
                return
            metrics_payload = payload.get("metrics")
            if isinstance(metrics_payload, Mapping):
                self._persist_metrics(job.job_id, metrics_payload, resource_failure=message.kind == "FAILED" and payload.get("failure_kind") == "RESOURCE_LIMIT")
            elif message.kind in {"FAILED", "CANCELLED"}:
                self._persist_metrics(job.job_id, {}, resource_failure=message.kind == "FAILED" and payload.get("failure_kind") == "RESOURCE_LIMIT")
            if message.kind == "RESULT":
                result_ref = payload.get("result_ref")
                if isinstance(result_ref, str):
                    publication = None
                    if "local_research" in job.capabilities or result_ref.startswith("research-result:"):
                        from .application_service import verify_research_receipt

                        try:
                            publication = verify_research_receipt(self.queue.path.parent, self.queue, current, result_ref, artifact_digest=payload.get("artifact_digest"), verify_input=True)
                        except (OSError, ValueError, KeyError, TypeError):
                            self._retry_or_fail(job, "PUBLICATION_INVALID")
                            return
                    completed = self.queue.complete(job.job_id, result_ref=result_ref, checkpoint_ref=current.checkpoint_ref, **fence)
                    if publication is not None and completed.status is JobStatus.COMPLETED:
                        from .application_service import index_verified_publication

                        try:
                            index_verified_publication(self.queue.path.parent, publication)
                        except (OSError, ValueError, KeyError, sqlite3.Error):
                            # Queue completion is authoritative. Startup or
                            # a subsequent GET repairs an interrupted index.
                            pass
                else:
                    self._retry_or_fail(job, "RESULT_INVALID")
            elif message.kind == "CANCELLED":
                self.queue.mark_cancelled(job.job_id, **fence)
            else:
                failure = str(payload.get("error_code", payload.get("failure_kind", "RUNNER_FAILED")))
                self._retry_or_fail(job, failure)
        except RuntimeLimitExceeded:
            self._retry_or_fail(job, "RESOURCE_LIMIT")
        except StaleLeaseError:
            return
        except ValueError:
            self._retry_or_fail(job, "RESULT_INVALID")
        except KeyError:
            # Another supervisor already owns or published the job.  Terminal
            # publication is intentionally idempotent and fail closed.
            return

    def _retry_or_fail(self, job: JobRecord, reason: str) -> None:
        try:
            if reason in {"RESOURCE_LIMIT", "PUBLICATION_INVALID", "DATA_UNAVAILABLE", "DATA_INVALID", "NO_DATA_AVAILABLE", "SNAPSHOT_INTEGRITY_FAILED", "REQUEST_UNAVAILABLE", "QUANT_VALIDATION_FAILED", "RISK_REVIEW_FAILED", "POLICY_CHANGED"}:
                self.queue.fail(job.job_id, reason, worker_id=job.worker_id, attempt=job.attempts, lease_until=job.lease_until, lease_token=job.lease_token)
            else:
                self.queue.retry(job.job_id, reason, worker_id=job.worker_id, attempt=job.attempts, lease_until=job.lease_until, lease_token=job.lease_token)
            budget = self._metrics_budget(job.job_id)
            budget.retry_count = max(0, job.attempts - 1)
            self.queue.save_metrics(budget.metrics())
        except (StaleLeaseError, ValueError, KeyError):
            return

    def _expected_invocation_digest(self, job_id: str) -> str:
        job = self.queue.get(job_id)
        return self._make_handle(job).invocation.digest()

    @staticmethod
    def _unbounded_limits() -> SimpleNamespace:
        return SimpleNamespace(max_wall_seconds=float("inf"), max_provider_calls=None, max_bytes=None, max_experiments=None)

    def _persist_metrics(self, job_id: str, payload: Mapping[str, object], *, resource_failure: bool = False) -> None:
        delta = WorkerMetricsDelta(
            job_id,
            experiments=int(payload.get("experiments", 0)),
            provider_calls=int(payload.get("provider_calls", 0)),
            bytes_used=int(payload.get("bytes_used", 0)),
            stage_seconds=dict(payload.get("stage_durations", {})),
        )
        budget = self._metrics_budget(job_id)
        aggregate = budget.apply_delta(delta)
        if resource_failure:
            budget.resource_failures += 1
            aggregate = budget.metrics()
        self.queue.save_metrics(aggregate)

    def _metrics_budget(self, job_id: str) -> RuntimeBudget:
        if job_id not in self._metrics:
            budget = RuntimeBudget(job_id, self._unbounded_limits())
            durable = self.queue.load_metrics(job_id)
            if durable:
                for key in ("provider_calls", "bytes_used", "experiments", "resource_failures", "retry_count"):
                    setattr(budget, key, durable[key])
                budget.stage_durations = dict(durable["stage_durations"])
            self._metrics[job_id] = budget
        return self._metrics[job_id]

    def metrics_for(self, job_id: str):
        """Return the supervisor's aggregated worker metrics snapshot."""
        budget = self._metrics.get(job_id)
        if budget is not None:
            return budget.metrics()
        durable = self.queue.load_metrics(job_id)
        if durable is None:
            return None
        return RuntimeMetrics(
            durable["run_id"],
            durable["stage_durations"],
            durable["retry_count"],
            durable["provider_calls"],
            durable["resource_failures"],
            durable["bytes_used"],
            durable["experiments"],
        )

    def _budget_limits(self) -> SimpleNamespace:
        values = self._budget
        wall = values.get("max_wall_seconds")
        return SimpleNamespace(
            max_wall_seconds=float("inf") if wall is None else float(wall),
            max_provider_calls=values.get("max_provider_calls"),
            max_bytes=values.get("max_bytes"),
            max_experiments=values.get("max_experiments"),
        )

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(self.poll_interval)


__all__ = ["ResearchSupervisor"]

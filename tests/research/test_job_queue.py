from __future__ import annotations

import sqlite3

import pytest

from finahinking.research.contracts import AgentTask, stable_digest
from finahinking.research.job_queue import JobQueue, JobStatus, StaleLeaseError


def task(task_id: str = "task-1") -> AgentTask:
    return AgentTask(
        role="technical",
        task_id=task_id,
        input_digest=f"input-{task_id}",
        inputs={"dataset_ref": "dataset-1"},
    )


@pytest.fixture
def tracked_connections(monkeypatch):
    original = sqlite3.connect
    opened = []
    statements = []

    class TrackedConnection(sqlite3.Connection):
        closed = False

        def close(self):
            self.closed = True
            super().close()

    def connect(*args, **kwargs):
        connection = original(*args, factory=TrackedConnection, **kwargs)
        connection.set_trace_callback(statements.append)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    return opened, statements


def test_queue_transactions_close_connections_on_success_and_rollback(tmp_path, tracked_connections):
    connections, _ = tracked_connections
    queue = JobQueue(tmp_path / "closed.sqlite")
    record = queue.enqueue(task(), "closed")
    queue.get(record.job_id)
    with queue._connect() as db:
        db.execute("CREATE TABLE should_rollback (value TEXT)")
    with pytest.raises(RuntimeError), queue._connect() as db:
        db.execute("INSERT INTO should_rollback VALUES ('uncommitted')")
        raise RuntimeError("abort the operation")
    with queue._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM should_rollback").fetchone()[0] == 0
    assert connections and all(connection.closed for connection in connections)


def test_status_reads_do_not_change_the_persistent_journal_mode(tmp_path, tracked_connections):
    _, statements = tracked_connections
    queue = JobQueue(tmp_path / "read-only-status.sqlite")
    record = queue.enqueue(task(), "status-read")
    statements.clear()
    queue.get(record.job_id)
    assert not any("JOURNAL_MODE=WAL" in statement.upper().replace(" ", "") for statement in statements)


def test_enqueue_is_idempotent_and_does_not_persist_task_inputs(tmp_path) -> None:
    queue = JobQueue(tmp_path / "jobs.sqlite")
    first = queue.enqueue(task(), "idem-1")
    again = queue.enqueue(task(), "idem-1")

    assert first == again
    assert first.status is JobStatus.QUEUED
    raw = (tmp_path / "jobs.sqlite").read_bytes()
    assert b"dataset_ref" not in raw
    assert b"prompt" not in raw


def test_claim_transitions_and_crash_recovery(tmp_path) -> None:
    now = [100.0]
    queue = JobQueue(tmp_path / "jobs.sqlite", clock=lambda: now[0], lease_seconds=5)
    queued = queue.enqueue(task(), "idem-1")
    running = queue.claim("worker-a")
    assert running is not None and running.status is JobStatus.RUNNING
    assert queue.claim("worker-b") is None

    now[0] = 106.0
    recovered = queue.claim("worker-b")
    assert recovered is not None
    assert recovered.job_id == queued.job_id
    assert recovered.attempts == 2


def test_retry_backoff_cancel_and_terminal_transitions(tmp_path) -> None:
    now = [100.0]
    queue = JobQueue(tmp_path / "jobs.sqlite", clock=lambda: now[0], backoff_base_seconds=10)
    record = queue.enqueue(task(), "idem-1")
    queue.claim("worker-a")
    running = queue.get(record.job_id)
    retried = queue.retry(record.job_id, "temporary failure", worker_id=running.worker_id, attempt=running.attempts, lease_until=running.lease_until, lease_token=running.lease_token)
    assert retried.status is JobStatus.RETRYABLE
    assert retried.available_at == 110.0
    assert retried.last_error_digest
    assert queue.cancel(record.job_id).status is JobStatus.CANCELLED
    with pytest.raises(ValueError):
        queue.retry(record.job_id, "must not revive")


def test_queue_reopens_and_preserves_only_references(tmp_path) -> None:
    path = tmp_path / "jobs.sqlite"
    first = JobQueue(path)
    record = first.enqueue(task(), "idem-1")
    claimed = first.claim("worker-a")
    assert claimed is not None
    first.update_checkpoint(record.job_id, "checkpoint:abc123", worker_id=claimed.worker_id, attempt=claimed.attempts, lease_until=claimed.lease_until, lease_token=claimed.lease_token)
    reopened = JobQueue(path)
    loaded = reopened.get(record.job_id)
    assert loaded.checkpoint_ref == "checkpoint:abc123"
    assert loaded.task_ref == "task-1"


def test_persisted_task_id_collision_is_rejected_after_restart(tmp_path) -> None:
    path = tmp_path / "jobs.sqlite"
    original = task()
    JobQueue(path).enqueue(original, "idem-1")
    reopened = JobQueue(path)
    with pytest.raises(ValueError, match="persisted task digest"):
        reopened.enqueue(AgentTask(role="technical", task_id="task-1", input_digest="different-input"), "idem-2")
    accepted = reopened.enqueue(original, "idem-1")
    assert accepted.task_digest == reopened._task_digest(reopened.resolve_task("task-1"))


def test_stale_worker_cannot_complete_or_update_checkpoint(tmp_path) -> None:
    now = [100.0]
    queue = JobQueue(tmp_path / "jobs.sqlite", clock=lambda: now[0], lease_seconds=5, backoff_base_seconds=0)
    record = queue.enqueue(task(), "idem-1")
    first = queue.claim("worker-a")
    assert first is not None
    now[0] = 106.0
    second = queue.claim("worker-b")
    assert second is not None
    with pytest.raises(StaleLeaseError):
        queue.complete(record.job_id, result_ref="artifact:stale", worker_id=first.worker_id, attempt=first.attempts, lease_until=first.lease_until, lease_token=first.lease_token)
    with pytest.raises(StaleLeaseError):
        queue.update_checkpoint(record.job_id, "checkpoint:stale", worker_id=first.worker_id, attempt=first.attempts, lease_until=first.lease_until, lease_token=first.lease_token)


def test_checkpoint_event_contains_owner_attempt_and_lease_digest(tmp_path) -> None:
    queue = JobQueue(tmp_path / "jobs.sqlite")
    record = queue.enqueue(task(), "idem-1")
    claimed = queue.claim("worker-a")
    assert claimed is not None
    queue.update_checkpoint(record.job_id, "checkpoint:owned", worker_id=claimed.worker_id, attempt=claimed.attempts, lease_until=claimed.lease_until, lease_token=claimed.lease_token)
    import sqlite3

    with sqlite3.connect(tmp_path / "jobs.sqlite") as db:
        row = db.execute("SELECT worker_id, attempt, lease_token_digest FROM job_events WHERE status='checkpoint_updated'").fetchone()
    assert row == ("worker-a", claimed.attempts, stable_digest(claimed.lease_token)[:32])


def test_cancelled_running_job_cannot_be_revived_by_retry(tmp_path) -> None:
    queue = JobQueue(tmp_path / "jobs.sqlite")
    record = queue.enqueue(task(), "idem-1")
    claimed = queue.claim("worker-a")
    assert claimed is not None
    queue.cancel(record.job_id)
    result = queue.retry(record.job_id, "late failure", worker_id=claimed.worker_id, attempt=claimed.attempts, lease_until=claimed.lease_until, lease_token=claimed.lease_token)
    assert result.status is JobStatus.CANCELLED

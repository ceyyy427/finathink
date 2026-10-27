from dataclasses import dataclass

from finahinking.research.codex_bridge import CodexBridge
from finahinking.research.job_queue import JobQueue
from finahinking.research.worker import ResearchWorker, WorkerStatus


def no_op_runner(task, checkpoint):
    del task, checkpoint
    return {"status": "completed", "result_ref": "artifact:no"}


@dataclass(frozen=True)
class Task:
    task_id: str = "dispatch-1"
    role: str = "technical"
    input_digest: str = "input-1"
    context_digest: str = "context-1"
    prompt_digest: str = "prompt-1"
    capabilities: tuple[str, ...] = ("structured_output", "paper_only")


def test_dispatch_is_digest_only_and_external_until_callback(tmp_path):
    queue = JobQueue(tmp_path / "jobs.sqlite")
    bridge = CodexBridge(queue=queue)
    envelope = bridge.create_handoff(Task())
    record = bridge.enqueue(envelope)
    assert record.status == "EXTERNAL_HANDOFF_REQUIRED"
    assert queue.claim("worker") is None
    assert bridge.record_external_result(envelope, None).status == "EXTERNAL_HANDOFF_REQUIRED"


def test_persisted_callback_accepts_context_evidence_digest_without_body(tmp_path):
    queue = JobQueue(tmp_path / "jobs.sqlite")
    bridge = CodexBridge(queue=queue)
    envelope = bridge.create_handoff(Task())
    dispatch = bridge.enqueue(envelope)
    valid = {"schema_version": envelope.schema_version, "input_digest": envelope.input_digest,
             "paper_only": True, "status": "READY", "evidence_refs": ["evidence:" + "a" * 64]}
    assert bridge.record_external_result(envelope, valid).status == "READY"
    stored = queue.external_dispatch(dispatch.envelope_digest)
    assert stored["status"] == "READY"
    assert set(stored) == {"envelope_digest", "task_id", "external_ref", "status",
                           "result_digest", "failure_kind", "output_digest"}


def test_dispatch_rejects_input_fingerprint_and_duplicate_callback_is_idempotent():
    bridge = CodexBridge()
    envelope = bridge.create_handoff(Task())
    bridge.enqueue(envelope)
    bad = bridge.record_external_result(envelope, {"schema_version": envelope.schema_version, "input_digest": "wrong", "status": "READY"})
    assert bad.failure_kind == "INPUT_DIGEST_MISMATCH"
    valid = {"schema_version": envelope.schema_version, "input_digest": envelope.input_digest, "status": "READY", "paper_only": True}
    first = bridge.record_external_result(envelope, valid)
    second = bridge.record_external_result(envelope, valid)
    assert first.status == second.status == "READY"
    assert first == second


def test_external_handoff_is_not_claimed_or_failed_by_normal_worker(tmp_path):
    queue = JobQueue(tmp_path / "jobs.sqlite")
    bridge = CodexBridge(queue=queue)
    envelope = bridge.create_handoff(Task())
    record = bridge.enqueue(envelope)
    result = ResearchWorker(queue, runner=no_op_runner, worker_id="worker").run_once()
    assert result.status is WorkerStatus.IDLE
    assert queue.external_dispatch(record.envelope_digest)["status"] == "external_waiting"


def test_callback_status_and_idempotency_survive_bridge_restart(tmp_path):
    path = tmp_path / "jobs.sqlite"
    bridge = CodexBridge(queue=JobQueue(path))
    envelope = bridge.create_handoff(Task())
    bridge.enqueue(envelope)
    valid = {"schema_version": envelope.schema_version, "input_digest": envelope.input_digest, "status": "READY", "paper_only": True}
    first = bridge.record_external_result(envelope, valid)
    restarted = CodexBridge(queue=JobQueue(path))
    replay = restarted.record_external_result(envelope, valid)
    assert replay.status == first.status == "READY"
    with __import__("sqlite3").connect(path) as db:
        raw = repr(db.execute("SELECT * FROM external_dispatches").fetchall())
    assert "prompt-1" not in raw and "provider" not in raw and "secret" not in raw


def test_pre_migration_external_row_is_backfilled_on_restart(tmp_path):
    import sqlite3

    path = tmp_path / "jobs.sqlite"
    first_queue = JobQueue(path)
    envelope = CodexBridge().create_handoff(Task())
    old = first_queue.enqueue_external(envelope, idempotency_key="codex:legacy")
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM external_dispatches")
        db.execute("UPDATE jobs SET status='queued' WHERE job_id=?", (old.job_id,))
    bridge = CodexBridge(queue=JobQueue(path))
    valid = {"schema_version": envelope.schema_version, "input_digest": envelope.input_digest, "status": "READY", "paper_only": True}
    assert bridge.record_external_result(envelope, valid).status == "READY"
    assert bridge.record_external_result(envelope, valid).status == "READY"

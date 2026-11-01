import multiprocessing
import sqlite3

import pytest


def journal(path, *, scope="local", job="job-1"):
    from finahinking.research.model_call_journal import ModelCallJournal

    return ModelCallJournal(path, job_id=job, run_id="run-1", request_digest="a" * 64, attempt=1, scope_id=scope)


def record():
    return {"role": "technical", "model": "custom-v1", "context_fingerprint": "b" * 64, "context_bytes": 512}


def test_pre_dispatch_and_unknown_interrupted_outcomes_survive_restart(tmp_path):
    path = tmp_path / "calls.sqlite3"
    store = journal(path)
    first = store.begin(record())
    store.dispatched(first, 900)
    second = store.begin(record())
    restored = journal(path).records(terminal=True)
    assert [item["status"] for item in restored] == ["UNKNOWN", "NOT_SENT"]
    assert restored[0]["dispatched"] and restored[0]["usage"] is None and restored[0]["duration_ms"] is None
    assert not restored[1]["dispatched"]
    assert journal(path, scope="another").records() == []
    assert journal(path, job="another-job").records() == []
    store.finish(first, {"status": "SCHEMA_ERROR", "usage": {"total_units": 160}, "duration_ms": 10})
    assert store.records(terminal=True)[0]["usage"] == {"total_units": 160}
    assert store.records()[1]["call_id"] == second


def test_private_journal_rejects_raw_or_unbound_metadata(tmp_path):
    store = journal(tmp_path / "calls.sqlite3")
    with pytest.raises(ValueError):
        store.begin({**record(), "endpoint": "https://models.acme.com/v1"})
    with pytest.raises(ValueError):
        store.begin({**record(), "context_fingerprint": "not-bound"})
    assert store.records() == []


def test_initialization_retries_a_busy_mode_switch_on_a_fresh_connection(tmp_path, monkeypatch):
    connect = sqlite3.connect
    connections = []

    class BusyOnce(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql == "PRAGMA journal_mode=WAL" and len(connections) == 1:
                error = sqlite3.OperationalError("database is locked")
                error.sqlite_errorcode = sqlite3.SQLITE_BUSY
                raise error
            return super().execute(sql, *args, **kwargs)

    def open_connection(*args, **kwargs):
        db = connect(*args, **kwargs, factory=BusyOnce)
        connections.append(db)
        return db

    monkeypatch.setattr(sqlite3, "connect", open_connection)
    path = tmp_path / "calls.sqlite3"
    store = journal(path)
    call_id = store.begin(record())
    assert store.records()[0]["call_id"] == call_id
    with connect(path) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")


def test_initialization_busy_retries_are_bounded(tmp_path, monkeypatch):
    from finahinking.research import model_call_journal

    connect = sqlite3.connect
    attempts = []

    class AlwaysBusy(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql == "PRAGMA journal_mode=WAL":
                attempts.append(sql)
                error = sqlite3.OperationalError("database is locked")
                error.sqlite_errorcode = sqlite3.SQLITE_BUSY
                raise error
            return super().execute(sql, *args, **kwargs)

    monkeypatch.setattr(model_call_journal, "_INITIALIZE_TIMEOUT", 0.1, raising=False)
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: connect(*args, **kwargs, factory=AlwaysBusy))
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        journal(tmp_path / "calls.sqlite3")
    assert 1 < len(attempts) < 10


def test_corrupt_journal_is_not_retried_or_replaced(tmp_path, monkeypatch):
    connect = sqlite3.connect
    attempts = []

    def open_connection(*args, **kwargs):
        attempts.append(args)
        return connect(*args, **kwargs)

    path = tmp_path / "calls.sqlite3"
    original = b"not a SQLite database" * 100
    path.write_bytes(original)
    monkeypatch.setattr(sqlite3, "connect", open_connection)
    with pytest.raises(sqlite3.DatabaseError):
        journal(path)
    assert len(attempts) == 1 and path.read_bytes() == original


def _spawn_journal_writer(path, barrier, number):
    barrier.wait(timeout=15)
    store = journal(path, job=f"job-{number}")
    call_id = store.begin(record())
    store.dispatched(call_id, 900)
    store.finish(call_id, {"status": "READY", "usage": {"total_units": 10}, "duration_ms": 1})


def test_spawn_workers_initialize_and_write_one_journal(tmp_path):
    path = tmp_path / "calls.sqlite3"
    # A prior interrupted creator can leave an empty file before any schema/WAL.
    path.touch()
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(4)
    workers = [context.Process(target=_spawn_journal_writer, args=(path, barrier, number)) for number in range(4)]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=20)
        assert [worker.exitcode for worker in workers] == [0] * 4
        for number in range(4):
            records = journal(path, job=f"job-{number}").records()
            assert len(records) == 1 and records[0]["status"] == "READY"
        with sqlite3.connect(path) as db:
            assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)

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

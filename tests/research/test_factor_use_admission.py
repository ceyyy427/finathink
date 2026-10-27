"""The existing queue DB owns immutable reviews and survives offline backup."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from finahinking.research.job_queue import JobQueue
from tests.research.test_factor_lifecycle import api, decision, evidence


@pytest.mark.parametrize("changes", [{"market": "US"}, {"factor_ref": "d" * 64},
    {"universe_ref": "d" * 64}, {"policy_fingerprint": "d" * 64}, {"evaluation_fingerprint": "d" * 64},
    {"run_ref": "other-run"}, {"purpose": "PAPER_SCREENING"}])
def test_mismatched_scope_or_fixture_production_use_never_admitted(tmp_path, changes):
    store = api().FactorUseStore(JobQueue(tmp_path / "queue.sqlite3"), scope_id="one")
    with pytest.raises(ValueError):
        store.record(decision(**changes), evidence=evidence())
    assert store.list() == []


@pytest.mark.parametrize("status", ["BLOCKED_DATA", "EXECUTION_ERROR", "COMPLETE_WITH_BLOCKS"])
def test_execution_and_data_failures_cannot_be_approved_or_financially_rejected(tmp_path, status):
    store = api().FactorUseStore(JobQueue(tmp_path / "queue.sqlite3"), scope_id="one")
    for choice in ("APPROVE_FOR_RESEARCH", "REJECT"):
        with pytest.raises(ValueError):
            store.record(decision(decision=choice), evidence=evidence(status=status))
    store.record(decision(decision="OBSERVE"), evidence=evidence(status=status))
    assert len(store.list()) == 1


def test_concurrent_idempotence_semantic_dedup_and_conflicts(tmp_path):
    queue = JobQueue(tmp_path / "queue.sqlite3")
    store = api().FactorUseStore(queue, scope_id="one")
    with ThreadPoolExecutor(max_workers=8) as workers:
        records = list(workers.map(lambda _: store.record(decision(), evidence=evidence()), range(16)))
    assert all(row == records[0] for row in records)
    assert store.record(replace(decision(), idempotency_key="another-key"), evidence=evidence()) == records[0]
    with pytest.raises(ValueError):
        store.record(replace(decision(), decision="REJECT"), evidence=evidence())
    with pytest.raises(ValueError):
        store.record(replace(decision(), decision="REJECT", idempotency_key="conflict"), evidence=evidence())
    assert len(store.list()) == 1
    assert api().FactorUseStore(queue, scope_id="other").list() == []


def test_new_store_migration_restart_backup_and_corrupt_record_fail_closed(tmp_path):
    from finahinking.maintenance import backup_state, restore_state

    path = tmp_path / "state.sqlite3"
    queue = JobQueue(path)
    store = api().FactorUseStore(queue, scope_id="one")
    expected = store.record(decision(), evidence=evidence())
    assert api().FactorUseStore(queue, scope_id="one").list() == [expected]
    archive = tmp_path / "backup.zip"
    backup_state(path, archive)
    restored = restore_state(archive, tmp_path / "restored")
    assert api().FactorUseStore(JobQueue(restored), scope_id="one").list() == [expected]
    with queue._connect() as db:
        db.execute("UPDATE factor_use_events SET payload='{}' WHERE scope_id='one'")
    with pytest.raises(ValueError):
        store.list()


def test_application_exposes_scoped_store_without_starting_workers(tmp_path):
    from finahinking.research.application_service import ResearchApplicationService

    app = ResearchApplicationService(tmp_path, allow_model_calls=False)
    try:
        assert hasattr(app, "factor_uses"), "application does not own scoped use admission"
        assert app.factor_uses.list() == []
        result = app.factor_evidence("dshq-f-score")
        assert result["lifecycle"]["state"] == "SOURCE_ONLY"
        assert app.list_jobs()["total"] == 0
    finally:
        app.close()


def test_existing_explicit_pilot_review_writes_one_scoped_event_atomically(tmp_path, monkeypatch):
    from finahinking.research.application_service import ResearchApplicationService
    from tests.research.test_factor_study_application import wait_study
    from tests.research.test_method_factor_journey import prepared

    app = ResearchApplicationService(tmp_path, allow_model_calls=False)
    try:
        parent, handoff, artifact, _, fields = prepared(app)
        app.attach_research_fields(parent["job_id"], fields)
        app.start()
        created = app.create_method_factor_study(parent["job_id"], handoff, artifact["artifact_ref"],
            attachment_ref=fields.fingerprint, idempotency_key="scoped-review")
        study = wait_study(app, created["study_id"], "validation")
        app.freeze_factor_study(study["study_id"], study["validation"]["experiments"][0]["experiment_id"])
        app.test_factor_study(study["study_id"], "scoped-final")
        wait_study(app, study["study_id"], "test")
        original = app.factor_uses.record

        def crash(*args, **kwargs):
            raise RuntimeError("simulated atomic admission failure")

        monkeypatch.setattr(app.factor_uses, "record", crash)
        with pytest.raises(RuntimeError):
            app.review_factor_study(study["study_id"], "REJECT", "explicit-reviewer")
        assert app.factor_studies.get(study["study_id"])["state"] == "TEST_EVALUATED"
        monkeypatch.setattr(app.factor_uses, "record", original)
        app.review_factor_study(study["study_id"], "REJECT", "explicit-reviewer")
        events = app.factor_uses.list()
        assert len(events) == 1
        assert events[0]["decision"]["reviewer"] == "explicit-reviewer"
        assert events[0]["decision"]["purpose"] == "FORMULA_RESEARCH"
        app.review_factor_study(study["study_id"], "REJECT", "explicit-reviewer")
        assert app.factor_uses.list() == events
        projection = app.factor_evidence(study["spec"]["factor_spec"]["factor_id"])
        assert projection["lifecycle"]["state"] == "REJECTED"
        assert projection["lifecycle"]["financial_falsification"] is False
    finally:
        app.close()

import hashlib
from dataclasses import replace

import pandas as pd
import pytest

from finahinking.p8_2.data_sources import FixtureMarketDataSource
from finahinking.research.application_service import ResearchApplicationService
from tests.research.test_application_service import wait_for


def inputs(service):
    template = FixtureMarketDataSource(points=12).snapshot("AAA")
    rows = []
    for index in range(12):
        timestamp = pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(days=index)
        price = 100 + index
        rows.append(replace(template.observations[0], timestamp=timestamp, available_at=timestamp, open=price, close=price, high=price + 1, low=price - 1, volume=100000, industry="technology"))
    source = replace(template, observations=tuple(rows), as_of=rows[-1].timestamp)
    future = []
    for index, price in enumerate((100, 98, 96, 94, 92, 90), 1):
        timestamp = rows[-1].timestamp + pd.Timedelta(days=index)
        future.append(replace(rows[-1], timestamp=timestamp, available_at=timestamp, open=price, close=price, high=price + 1, low=price - 1))
    evidence = replace(source, dataset_id="sample-followup", observations=(*rows, *future), as_of=future[-1].timestamp)
    source_ref, evidence_ref = service.snapshots.save(source), service.snapshots.save(evidence)
    job = service.submit({"idempotency_key": "original", "snapshot_ref": source_ref, "question": "Observe positive two-observation momentum with declared risk limits", "instrument": "AAA", "as_of": "2026-01-12"})
    service.start()
    result = wait_for(service, job["job_id"])
    assert result["status"] == "completed"
    return result, source_ref, evidence_ref


def settle(service, source, evidence_ref):
    return service.learning.settle({"run_id": source["run_id"], "evidence_snapshot_ref": evidence_ref, "as_of": "2026-01-18"})


def decide(service, review, decision="APPROVED"):
    return service.learning.decide(review["review_id"], {"decision": decision, "expected_preview_fingerprint": review["preview"]["policy_fingerprint"], "rationale": "Inspect fixed-horizon paper outcome"})


def test_settlement_preview_approval_drives_new_policy_and_preserves_original_report(tmp_path):
    service = ResearchApplicationService(tmp_path)
    try:
        source, source_ref, evidence_ref = inputs(service)
        entry = service.runs.get_run(source["run_id"])
        report_path = entry["bundle"] / "complete_report.html"
        original_hash = hashlib.sha256(report_path.read_bytes()).hexdigest()
        review = settle(service, source, evidence_ref)
        assert review["status"] == "PENDING_REVIEW"
        assert review["data_modes"] == {"source": "SAMPLE", "evidence": "SAMPLE", "observed": "SAMPLE"}
        assert review["observation"]["mark_timestamp"].startswith("2026-01-18")
        assert review["observation"]["equity"] == pytest.approx(9697.302023650842)
        assert review["preview"]["factor_weights"]["momentum_2obs"] == pytest.approx(0.9)
        assert review["preview"]["risk_rules"]["max_cvar"] == pytest.approx(0.045)
        assert "factor_decay" in review["observation"]
        approved = decide(service, review)
        assert approved["status"] == "APPROVED" and approved["policy_ref"]
        assert len(service.learning.policies()) == 2
        replay = service.learning.rerun({"idempotency_key": "v1-replay", "run_id": source["run_id"], "policy_ref": approved["policy_ref"], "snapshot_ref": source_ref, "as_of": approved["available_as_of"]})
        completed = wait_for(service, replay["job_id"])
        assert completed["status"] == "completed"
        assert completed["policy_ref"] == approved["policy_ref"]
        assert completed["risk_portfolio"]["allocation"]["weights"]["AAA"] == pytest.approx(0.27)
        assert source["risk_portfolio"]["allocation"]["weights"]["AAA"] == pytest.approx(0.3)
        assert completed["run_id"] != source["run_id"]
        assert hashlib.sha256(report_path.read_bytes()).hexdigest() == original_hash
        assert completed["policy_lineage"]["retrospective"] is True
        assert completed["policy_lineage"]["rerun_of"] == source["run_id"]
        new_report = service.runs.get_run(completed["run_id"])["bundle"] / "complete_report.html"
        assert "回溯比较" in new_report.read_text() and approved["policy_ref"] in new_report.read_text()
        with pytest.raises(ValueError):
            service.learning.settle({"run_id": completed["run_id"], "evidence_snapshot_ref": evidence_ref, "as_of": approved["available_as_of"]})
    finally:
        service.close()
    reopened = ResearchApplicationService(tmp_path)
    try:
        assert reopened.learning.get(review["review_id"])["policy_ref"] == approved["policy_ref"]
        assert reopened.get_job(replay["job_id"])["report_status"] == "AVAILABLE"
    finally:
        reopened.close()


def test_approved_admission_recovers_one_policy_after_index_commit_failure(tmp_path):
    import sqlite3

    service = ResearchApplicationService(tmp_path)
    try:
        source, _, evidence_ref = inputs(service)
        review = settle(service, source, evidence_ref)
        with service.queue._connect() as db:
            db.execute("CREATE TRIGGER fail_policy_insert BEFORE INSERT ON learning_policies BEGIN SELECT RAISE(ABORT, 'simulated power loss'); END")
        with pytest.raises(sqlite3.IntegrityError):
            decide(service, review)
        assert service.learning.store.proposals.get(review["proposal_digest"]).admission.decision.value == "APPROVED"
        with service.queue._connect() as db:
            db.execute("DROP TRIGGER fail_policy_insert")
    finally:
        service.close()
    reopened = ResearchApplicationService(tmp_path)
    try:
        recovered = reopened.learning.get(review["review_id"])
        assert recovered["status"] == "APPROVED" and recovered["policy_ref"]
        assert reopened.learning.get(review["review_id"])["policy_ref"] == recovered["policy_ref"]
        assert len(reopened.learning.policies()) == 2
    finally:
        reopened.close()


def test_concurrent_conflicting_decisions_have_one_winner_and_scopes_are_isolated(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    service = ResearchApplicationService(tmp_path)
    try:
        source, _, evidence_ref = inputs(service)
        review = settle(service, source, evidence_ref)

        def attempt(decision):
            try:
                return decide(service, review, decision)["status"]
            except ValueError:
                return "CONFLICT"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ("APPROVED", "REJECTED")))
        assert results.count("CONFLICT") == 1
        winner = service.learning.get(review["review_id"])
        assert len(service.learning.policies()) == (2 if winner["status"] == "APPROVED" else 1)
        foreign = ResearchApplicationService(tmp_path, scope_id="another-session")
        try:
            assert foreign.learning.list_reviews() == []
            assert len(foreign.learning.policies()) == 1
            with pytest.raises(KeyError):
                foreign.learning.get(review["review_id"])
            with pytest.raises(ValueError):
                foreign.learning.rerun({"idempotency_key": "foreign", "run_id": source["run_id"], "as_of": "2026-01-18"})
        finally:
            foreign.close()
    finally:
        service.close()


def test_fixed_mark_does_not_follow_later_prices_and_pending_intents_do_not_settle(tmp_path):
    service = ResearchApplicationService(tmp_path)
    try:
        source, _, evidence_ref = inputs(service)
        evidence = service.snapshots.load(evidence_ref)
        rows = list(evidence.observations)
        rows[12] = replace(rows[12], suspended=True)
        pending = service.snapshots.save(replace(evidence, observations=tuple(rows)))
        with pytest.raises(ValueError, match="未成交"):
            settle(service, source, pending)
        assert service.learning.list_reviews() == []
        later = replace(rows[-1], timestamp="2026-01-19T00:00:00+00:00", available_at="2026-01-19T00:00:00+00:00", open=200, close=200, high=201, low=199)
        extended = replace(evidence, observations=(*evidence.observations, later), as_of=later.timestamp)
        review = service.learning.settle({"run_id": source["run_id"], "evidence_snapshot_ref": service.snapshots.save(extended), "as_of": "2026-01-19"})
        assert review["observation"]["mark_price"] == 90
        assert review["observation"]["mark_timestamp"].startswith("2026-01-18")
        clone = service.submit({"idempotency_key": "clone", "snapshot_ref": source["snapshot_ref"], "question": "Same paper signal must not count twice", "instrument": "AAA", "as_of": "2026-01-12"})
        cloned = wait_for(service, clone["job_id"])
        with pytest.raises(ValueError, match="重复"):
            settle(service, cloned, evidence_ref)
    finally:
        service.close()


def test_duplicate_settlement_and_rejected_review_never_create_a_new_policy(tmp_path):
    service = ResearchApplicationService(tmp_path)
    try:
        source, _, evidence_ref = inputs(service)
        one = settle(service, source, evidence_ref)
        two = settle(service, source, evidence_ref)
        assert two["review_id"] == one["review_id"] and len(service.learning.list_reviews()) == 1
        rejected = decide(service, one, "REJECTED")
        assert rejected["status"] == "REJECTED" and rejected["policy_ref"] is None
        assert len(service.learning.policies()) == 1
        with pytest.raises(ValueError):
            decide(service, one)
        with pytest.raises(ValueError):
            service.learning.rerun({"idempotency_key": "bad-replay", "run_id": source["run_id"], "policy_ref": one["proposal_digest"], "as_of": "2026-01-18"})
    finally:
        service.close()


def test_positive_outcome_keeps_policy_and_bounded_rules_do_not_create_empty_updates(tmp_path):
    from finahinking.research.settlement import observe_paper_outcome

    service = ResearchApplicationService(tmp_path)
    try:
        source, source_ref, evidence_ref = inputs(service)
        original = service.snapshots.load(source_ref)
        evidence = service.snapshots.load(evidence_ref)
        risk = source["risk_portfolio"]
        event, observation, _ = observe_paper_outcome(run_id=source["run_id"], source=original, evidence=evidence, source_as_of="2026-01-12", as_of="2026-01-18", allocation=risk["allocation"], execution_policy=risk["paper_ledger"]["execution_policy"], factor_id="momentum_2obs", factor_weights={"momentum_2obs": 0.1}, risk_rules={"max_cvar": 0.01})
        assert not observation["has_updates"]
        assert not event.realized_outcomes["factor_weight_updates"] and not event.realized_outcomes["risk_rule_updates"]
        rows = list(evidence.observations[:12])
        for index, row in enumerate(evidence.observations[12:]):
            price = 100 + index * 2
            rows.append(replace(row, open=price, close=price, high=price + 1, low=price - 1))
        positive = replace(evidence, observations=tuple(rows))
        review = settle(service, source, service.snapshots.save(positive))
        assert review["status"] == "KEEP_POLICY" and review["observation"]["net_return"] > 0
        with pytest.raises(ValueError, match="没有待审核"):
            decide(service, review)
        assert len(service.learning.policies()) == 1
    finally:
        service.close()


@pytest.mark.parametrize("kind", ["future", "immature", "revised", "arbitrary_update", "future_clock"])
def test_settlement_rejects_future_immature_revised_or_client_generated_outcomes(tmp_path, kind):
    service = ResearchApplicationService(tmp_path)
    try:
        source, _, evidence_ref = inputs(service)
        evidence = service.snapshots.load(evidence_ref)
        if kind == "future":
            evidence = replace(evidence, observations=(*evidence.observations[:-1], replace(evidence.observations[-1], available_at="2026-01-19T00:00:00+00:00")))
        elif kind == "immature":
            evidence = replace(evidence, observations=evidence.observations[:-1])
        elif kind == "revised":
            evidence = replace(evidence, observations=(replace(evidence.observations[0], close=99, low=98), *evidence.observations[1:]))
        elif kind == "future_clock":
            following = [replace(row, timestamp=row.timestamp.replace(year=2100), available_at=row.available_at.replace(year=2100)) for row in evidence.observations[12:]]
            evidence = replace(evidence, observations=(*evidence.observations[:12], *following), as_of=following[-1].timestamp)
        body = {"run_id": source["run_id"], "evidence_snapshot_ref": service.snapshots.save(evidence), "as_of": "2026-01-18"}
        if kind == "future_clock":
            body["as_of"] = "2100-01-18"
        if kind == "arbitrary_update":
            body["realized_outcomes"] = {"factor_weights": {"momentum_2obs": 100}}
        with pytest.raises(ValueError):
            service.learning.settle(body)
        assert service.learning.list_reviews() == []
    finally:
        service.close()


def test_same_approval_with_whitespace_retries_idempotently_and_keeps_sample_labels(tmp_path):
    service = ResearchApplicationService(tmp_path)
    try:
        source, _, evidence_ref = inputs(service)
        review = settle(service, source, evidence_ref)
        body = {"decision": "APPROVED", "expected_preview_fingerprint": review["preview_fingerprint"], "rationale": "  same decision  "}
        one = service.learning.decide(review["review_id"], body)
        two = service.learning.decide(review["review_id"], body)
        assert one["policy_ref"] == two["policy_ref"] and len(service.learning.policies()) == 2
    finally:
        service.close()


def test_legacy_source_is_not_reinterpreted_using_a_different_current_baseline(tmp_path, monkeypatch):
    from finahinking.research import learning_review

    service = ResearchApplicationService(tmp_path)
    try:
        source, _, evidence_ref = inputs(service)
        original = service._request(service.queue.get(source["job_id"]))
        legacy = replace(original, run_id="legacy-source", previous_policy_fingerprint=None)
        record = service.runtime.submit(legacy, idempotency_key="legacy")
        completed = wait_for(service, record.job_id)
        assert completed["status"] == "completed"
        old = learning_review.local_research_policy()
        monkeypatch.setattr(learning_review, "local_research_policy", lambda: {**old, "max_cvar": 0.04})
        with pytest.raises(ValueError, match="原配置"):
            settle(service, completed, evidence_ref)
        assert service.learning.list_reviews() == []
    finally:
        service.close()


def test_genuinely_new_observations_complete_second_learning_cycle_and_preserve_synthetic_mode(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    from finahinking.research import learning_review

    clock = [datetime(2026, 1, 18, tzinfo=UTC)]

    class ReviewClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)

    monkeypatch.setattr(learning_review, "datetime", ReviewClock)
    service = ResearchApplicationService(tmp_path)
    try:
        source, source_ref, evidence_ref = inputs(service)
        first = decide(service, settle(service, source, evidence_ref))
        snapshot = service.snapshots.load(source_ref)
        rows = list(snapshot.observations)
        for index in range(7):
            timestamp = pd.Timestamp("2026-01-13", tz="UTC") + pd.Timedelta(days=index)
            price = 112 + index
            rows.append(replace(rows[-1], timestamp=timestamp, available_at=timestamp, open=price, close=price, high=price + 1, low=price - 1))
        forward = replace(snapshot, observations=tuple(rows), as_of=rows[-1].timestamp, mode="SYNTHETIC")
        forward_ref = service.snapshots.save(forward)
        clock[0] = datetime(2026, 1, 19, tzinfo=UTC)
        job = service.learning.rerun({"idempotency_key": "genuine-forward", "run_id": source["run_id"], "policy_ref": first["policy_ref"], "snapshot_ref": forward_ref, "as_of": "2026-01-19"})
        next_run = wait_for(service, job["job_id"])
        assert next_run["status"] == "completed" and next_run["policy_lineage"]["retrospective"] is False
        assert next_run["risk_portfolio"]["allocation"]["weights"]["AAA"] == pytest.approx(0.27)
        for index, price in enumerate((100, 98, 96, 94, 92, 90)):
            timestamp = pd.Timestamp("2026-01-20", tz="UTC") + pd.Timedelta(days=index)
            rows.append(replace(rows[-1], timestamp=timestamp, available_at=timestamp, open=price, close=price, high=price + 1, low=price - 1))
        followup = replace(forward, observations=tuple(rows), as_of=rows[-1].timestamp)
        clock[0] = datetime(2026, 1, 26, tzinfo=UTC)
        second = service.learning.settle({"run_id": next_run["run_id"], "evidence_snapshot_ref": service.snapshots.save(followup), "as_of": "2026-01-26"})
        assert second["data_modes"]["observed"] == "SYNTHETIC"
        assert second["preview"]["factor_weights"]["momentum_2obs"] == pytest.approx(0.81)
        assert second["preview"]["risk_rules"]["max_cvar"] == pytest.approx(0.0405)
        second_approved = decide(service, second)
        replay = service.learning.rerun({"idempotency_key": "v2", "run_id": next_run["run_id"], "policy_ref": second_approved["policy_ref"], "snapshot_ref": forward_ref, "as_of": "2026-01-26"})
        replayed = wait_for(service, replay["job_id"])
        assert replayed["status"] == "completed"
        assert replayed["risk_portfolio"]["allocation"]["weights"]["AAA"] == pytest.approx(0.243)
        assert len(service.learning.policies()) == 3
        assert service.learning.store.policy(first["policy_ref"])["factor_weights"]["momentum_2obs"] == 0.9
    finally:
        service.close()


@pytest.mark.parametrize("kind", ["future_timestamp", "future_availability", "available_today", "available_now"])
def test_realized_learning_caps_today_by_actual_current_instant(tmp_path, monkeypatch, kind):
    from datetime import UTC, datetime

    from finahinking.research import learning_review

    now = datetime(2026, 1, 18, 1, tzinfo=UTC)

    class ReviewClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)

    monkeypatch.setattr(learning_review, "datetime", ReviewClock)
    service = ResearchApplicationService(tmp_path)
    try:
        source, _, evidence_ref = inputs(service)
        evidence = service.snapshots.load(evidence_ref)
        last = evidence.observations[-1]
        if kind == "future_timestamp":
            last = replace(last, timestamp="2026-01-18T18:00:00+00:00")
        elif kind == "future_availability":
            last = replace(last, available_at="2026-01-18T18:00:00+00:00")
        elif kind == "available_now":
            last = replace(last, available_at=now)
        evidence = replace(evidence, observations=(*evidence.observations[:-1], last), as_of=max(last.timestamp, last.available_at))
        reference = service.snapshots.save(evidence)
        if kind.startswith("future"):
            with pytest.raises(ValueError):
                settle(service, source, reference)
            assert service.learning.list_reviews() == []
            assert len(service.learning.policies()) == 1
        else:
            review = settle(service, source, reference)
            assert review["status"] == "PENDING_REVIEW"
            assert pd.Timestamp(review["observation"]["available_at"]) <= pd.Timestamp(now)
            assert review["observed_at"] == now.isoformat()
    finally:
        service.close()


def test_future_policy_and_stale_preview_are_blocked_and_old_policy_can_be_selected(tmp_path):
    service = ResearchApplicationService(tmp_path)
    try:
        source, source_ref, evidence_ref = inputs(service)
        review = settle(service, source, evidence_ref)
        with pytest.raises(ValueError):
            service.learning.decide(review["review_id"], {"decision": "APPROVED", "expected_preview_fingerprint": "0" * 64, "rationale": "changed"})
        assert service.learning.get(review["review_id"])["status"] == "PENDING_REVIEW"
        approved = decide(service, review)
        with pytest.raises(ValueError):
            service.submit({"idempotency_key": "future-policy", "snapshot_ref": source_ref, "question": "Do not use a future policy", "instrument": "AAA", "as_of": "2026-01-12", "policy_ref": approved["policy_ref"]})
        baseline = service.learning.policies()[0]
        job = service.learning.rerun({"idempotency_key": "old-version", "run_id": source["run_id"], "policy_ref": baseline["policy_ref"], "snapshot_ref": source_ref, "as_of": approved["available_as_of"]})
        done = wait_for(service, job["job_id"])
        assert done["status"] == "completed"
        assert done["risk_portfolio"]["allocation"]["weights"]["AAA"] == pytest.approx(0.3)
        assert len(service.learning.policies()) == 2
    finally:
        service.close()

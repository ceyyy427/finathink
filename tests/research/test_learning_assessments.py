from dataclasses import replace
from datetime import UTC, datetime

import pandas as pd
import pytest

from finahinking.research.application_service import ResearchApplicationService
from finahinking.research.learning_assessments import LearningAssessmentService
from tests.research.test_application_service import wait_for
from tests.research.test_phase8_learning_flow import inputs, settle


def today():
    return datetime.now(UTC).date().isoformat()


def test_single_outcome_is_explicit_exploration_and_insufficient_cohort(tmp_path):
    app = ResearchApplicationService(tmp_path)
    try:
        source, _, evidence = inputs(app)
        review = settle(app, source, evidence)
        service = LearningAssessmentService(app)
        assessment = service.assess({"anchor_review_id": review["review_id"], "start": "2026-01-01", "as_of": today()})
        assert assessment["sample_count"] == 1 and assessment["status"] == "OBSERVE"
        historical = service.assess({"anchor_review_id": review["review_id"], "start": "2026-01-01", "as_of": "2026-01-18"})
        assert historical["sample_count"] == 0 and historical["exclusion_counts"] == {"LATE_EVIDENCE": 1}
        with pytest.raises(ValueError, match="样本"):
            service.propose(assessment["assessment_id"])
        values = {"decision": "APPROVED", "expected_preview_fingerprint": review["preview_fingerprint"], "rationale": "受控探索"}
        with pytest.raises(ValueError, match="探索"):
            app.learning.decide(review["review_id"], values)
        assert len(app.learning.policies()) == 1
        approved = app.learning.decide(review["review_id"], {**values, "allow_exploratory": True})
        assert approved["admission"]["admitted_by"] == "local-user-exploratory"
        assert app.learning.decide(review["review_id"], values)["policy_ref"] == approved["policy_ref"]
        # The persisted evidence is validated, not treated as a source of fresh facts.
        stored = app.runs.get_run(source["run_id"])
        (stored["bundle"] / "complete_report.html").write_text("broken")
        with pytest.raises(ValueError):
            service.assess({"anchor_review_id": review["review_id"], "start": "2026-01-01", "as_of": today()})
    finally:
        app.close()


def cohort_reviews(app):
    original, source_ref, evidence_ref = inputs(app)
    reviews = [settle(app, original, evidence_ref)]
    source, evidence = app.snapshots.load(source_ref), app.snapshots.load(evidence_ref)
    jobs = []
    for i in range(1, 20):
        shift = pd.Timedelta(days=14*i)
        def shifted(snapshot, shift=shift):
            return replace(snapshot, observations=tuple(replace(row, timestamp=row.timestamp+shift, available_at=row.available_at+shift) for row in snapshot.observations), as_of=snapshot.as_of+shift)
        shifted_source, shifted_evidence = shifted(source), shifted(evidence)
        ref, evidence_id = app.snapshots.save(shifted_source), app.snapshots.save(shifted_evidence)
        job = app.submit({"idempotency_key": f"cohort-{i}", "snapshot_ref": ref, "question": "Observe momentum with fixed risk limits", "instrument": "AAA", "as_of": shifted_source.as_of.date().isoformat()})
        jobs.append((job, evidence_id, shifted_evidence.as_of.date().isoformat()))
    for job, evidence_id, as_of in jobs:
        complete = wait_for(app, job["job_id"])
        assert complete["status"] == "completed"
        reviews.append(app.learning.settle({"run_id": complete["run_id"], "evidence_snapshot_ref": evidence_id, "as_of": as_of}))
    return reviews, source_ref


def test_twenty_actual_settlements_immutable_assessment_and_governed_proposal(tmp_path):
    app = ResearchApplicationService(tmp_path)
    try:
        reviews, _source_ref = cohort_reviews(app)
        service = LearningAssessmentService(app)
        values = {"anchor_review_id": reviews[0]["review_id"], "start": "2026-01-01", "as_of": today()}
        assessment = service.assess(values)
        assert assessment["status"] == "RECOMMENDED", assessment["exclusions"]
        assert assessment["sample_count"] == 20
        assert service.assess(values) == assessment
        assert len(app.learning.policies()) == 1
        proposal = service.propose(assessment["assessment_id"])
        assert proposal["source_kind"] == "LEARNING_COHORT"
        assert service.propose(assessment["assessment_id"])["review_id"] == proposal["review_id"]
        approved = app.learning.decide(proposal["review_id"], {"decision": "APPROVED", "rationale": "核对二十个分离纸面窗口及区间假设", "expected_preview_fingerprint": proposal["preview_fingerprint"]})
        assert approved["policy_ref"] and len(app.learning.policies()) == 2
        foreign = ResearchApplicationService(tmp_path, scope_id="foreign")
        try:
            with pytest.raises(KeyError):
                LearningAssessmentService(foreign).get(assessment["assessment_id"])
        finally:
            foreign.close()
        # Frozen assessment is retained, but damaged proofs cannot create/recreate proposals.
        entry = app.runs.get_run(reviews[-1]["run_id"])
        (entry["bundle"] / "complete_report.html").write_text("tampered")
        with pytest.raises(ValueError, match="证据"):
            service.propose(assessment["assessment_id"])
    finally:
        app.close()
    reopened = ResearchApplicationService(tmp_path)
    try:
        assert LearningAssessmentService(reopened).get(assessment["assessment_id"]) == assessment
        assert reopened.learning.get(proposal["review_id"])["policy_ref"] == approved["policy_ref"]
    finally:
        reopened.close()


def test_repeated_frozen_signal_cannot_manufacture_twenty_windows(tmp_path):
    app = ResearchApplicationService(tmp_path)
    try:
        original, source_ref, evidence_ref = inputs(app)
        first = settle(app, original, evidence_ref)
        evidence = app.snapshots.load(evidence_ref)
        jobs = []
        for i in range(1, 20):
            shift = pd.Timedelta(days=14*i)
            future = tuple(replace(row, timestamp=row.timestamp+shift, available_at=row.available_at+shift) for row in evidence.observations[12:])
            later = replace(evidence, observations=(*evidence.observations[:12], *future), as_of=future[-1].timestamp)
            job = app.submit({"idempotency_key": f"same-signal-{i}", "snapshot_ref": source_ref, "question": "Observe the same frozen signal again", "instrument": "AAA", "as_of": (pd.Timestamp("2026-01-12")+shift).date().isoformat()})
            jobs.append((job, app.snapshots.save(later), later.as_of.date().isoformat()))
        for job, ref, as_of in jobs:
            done = wait_for(app, job["job_id"])
            assert done["status"] == "completed"
            review = app.learning.settle({"run_id": done["run_id"], "evidence_snapshot_ref": ref, "as_of": as_of})
            assert review["settlement"]["ledger"]["execution_policy"]["signal_at"] == first["settlement"]["ledger"]["execution_policy"]["signal_at"]
        service = LearningAssessmentService(app)
        result = service.assess({"anchor_review_id": first["review_id"], "start": "2026-01-01", "as_of": today()})
        assert result["sample_count"] == 1
        assert result["exclusion_counts"] == {"DUPLICATE": 19}
        assert result["status"] == "OBSERVE"
        with pytest.raises(ValueError):
            service.propose(result["assessment_id"])
    finally:
        app.close()


def test_damaged_profitable_counterevidence_blocks_new_and_frozen_proposals(tmp_path):
    app = ResearchApplicationService(tmp_path)
    try:
        reviews, source_ref = cohort_reviews(app)
        shift = pd.Timedelta(days=280)
        def shifted(snapshot):
            return replace(snapshot, observations=tuple(replace(row, timestamp=row.timestamp+shift, available_at=row.available_at+shift) for row in snapshot.observations), as_of=snapshot.as_of+shift)
        source = shifted(app.snapshots.load(source_ref))
        evidence = shifted(app.snapshots.load(reviews[0]["evidence_snapshot_ref"]))
        rows = list(evidence.observations)
        for i in range(12, 18):
            price = 100 if i == 12 else 1000
            rows[i] = replace(rows[i], open=price, close=price, high=price+1, low=price-1)
        evidence = replace(evidence, observations=tuple(rows))
        job = app.submit({"idempotency_key": "profitable-counterexample", "snapshot_ref": app.snapshots.save(source), "question": "Observe momentum with fixed risk limits", "instrument": "AAA", "as_of": source.as_of.date().isoformat()})
        done = wait_for(app, job["job_id"])
        assert done["status"] == "completed"
        winner = app.learning.settle({"run_id": done["run_id"], "evidence_snapshot_ref": app.snapshots.save(evidence), "as_of": evidence.as_of.date().isoformat()})
        service = LearningAssessmentService(app)
        values = {"anchor_review_id": reviews[0]["review_id"], "start": "2026-01-01", "as_of": today()}
        before = service.assess(values)
        assert before["status"] == "KEEP_POLICY" and before["sample_count"] == 21
        assert before["statistics"]["mean"] > 0
        entry = app.runs.get_run(winner["run_id"])
        (entry["bundle"] / "complete_report.html").write_text("damaged")
        damaged = service.assess(values)
        assert damaged["status"] == "OBSERVE"
        assert damaged["sample_count"] == 20 and damaged["statistics"]["mean"] < 0
        assert damaged["exclusion_counts"] == {"INVALID_PROOF": 1}
        assert not damaged["factor_weight_updates"] and not damaged["risk_rule_updates"]
        for assessment in (before, damaged):
            with pytest.raises(ValueError):
                service.propose(assessment["assessment_id"])
        assert len(app.learning.policies()) == 1
    finally:
        app.close()

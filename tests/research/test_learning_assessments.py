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


def test_twenty_actual_settlements_immutable_assessment_and_governed_proposal(tmp_path):
    app = ResearchApplicationService(tmp_path)
    try:
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

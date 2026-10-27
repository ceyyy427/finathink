import json
import math
from dataclasses import replace

import pandas as pd
import pytest

from finahinking.p8_2.data_sources import FixtureMarketDataSource
from finahinking.research.application_service import ResearchApplicationService
from finahinking.research.contracts import ResearchPlan, ResearchRequest, stable_digest
from finahinking.research.drivers import OfflineDriver
from finahinking.research.factor_workbench import compute_study, prepare_study
from finahinking.research.reports import compare_experiments
from finahinking.research.snapshot_tools import SnapshotResearchTools, local_research_policy
from finahinking.research.workflow import ResearchOrchestrator
from tests.research.test_application_service import wait_for
from tests.research.test_factor_study_application import values, wait_study


def test_validation_publication_never_exposes_reserved_prices_or_auxiliary_performance(tmp_path):
    source = FixtureMarketDataSource(points=240).snapshot("AAA")
    changed = replace(source, observations=tuple(replace(row, close=row.close * 7, high=row.high * 7) if index >= 192 else row for index, row in enumerate(source.observations)))
    service = ResearchApplicationService(tmp_path)
    service.start()
    try:
        results = []
        for index, snapshot in enumerate((source, changed)):
            created = service.create_factor_study(values(service.snapshots.save(snapshot), idempotency_key=f"unseen-{index}", recipe_id="close_momentum", windows=[2], n_splits=2))
            study = wait_study(service, created["study_id"], "validation")
            entry = service.runs.get_run(study["validation_job"]["run_id"])
            analytics = json.loads((entry["bundle"] / "4_quant" / "analytics.json").read_text())
            assert pd.Timestamp(analytics["backtest"]["equity_curve"][-1][0]) < pd.Timestamp(study["spec"]["test_period"][0])
            assert "2042.25" not in (entry["bundle"] / "4_quant" / "index.html").read_text()
            assert analytics["factor_study"]["test_evaluation"] is None
            results.append((study, analytics))
        assert results[0][1]["metrics"] == results[1][1]["metrics"]
        assert results[0][1]["risk_portfolio"]["attribution"]["strategy"] == results[1][1]["risk_portfolio"]["attribution"]["strategy"]
        study = results[1][0]
        service.freeze_factor_study(study["study_id"], study["validation"]["ranking"][0])
        service.test_factor_study(study["study_id"], "reveal-after-claim")
        tested = wait_study(service, study["study_id"], "test")
        entry = service.runs.get_run(tested["test_job"]["run_id"])
        final = json.loads((entry["bundle"] / "4_quant" / "analytics.json").read_text())
        assert pd.Timestamp(final["backtest"]["equity_curve"][-1][0]) >= pd.Timestamp(study["spec"]["test_period"][0])
    finally:
        service.close()


def test_one_snapshot_with_two_instruments_never_groups_their_baselines_or_factors():
    source = FixtureMarketDataSource(points=240).snapshot("AAA")
    other = tuple(replace(row, instrument="BBB", close=row.close * (1 + 0.001 * index), high=row.high * (1 + 0.001 * index)) for index, row in enumerate(source.observations))
    snapshot = replace(source, observations=(*source.observations, *other))
    baselines, factors = [], []
    for instrument in ("AAA", "BBB"):
        tools = SnapshotResearchTools(snapshot)
        request = ResearchRequest(run_id=instrument, instrument=instrument, as_of="2026-10-01", asset_class="EQUITY", workflow_version="local-research.v1", config_digest="review", snapshot_ref=snapshot.fingerprint, research_plan=ResearchPlan(factor_ids=(local_research_policy()["factor_id"],)), analyst_roles=("technical",))
        result = ResearchOrchestrator().run(request, OfflineDriver(), tools)
        assert result.decision is not None
        baselines.append({"run_id": instrument, "metrics": tools.analysis["metrics"], "conditions": tools.analysis["comparison_conditions"]})
        spec = prepare_study({key: value for key, value in values(snapshot.fingerprint, instrument=instrument, windows=[3]).items() if key != "idempotency_key"}, snapshot, policy_fingerprint=stable_digest(local_research_policy()))
        study = compute_study(snapshot, {"study_id": instrument, "spec": spec, "spec_ref": stable_digest(spec)}, phase="validation")
        summary = study["summaries"][0]
        factors.append({"run_id": instrument, "metrics": summary["metrics"], "conditions": summary["conditions"]})
    for runs in (baselines, factors):
        compared = compare_experiments(runs)
        assert len(compared["groups"]) == 2
        assert all(not group["differences"] for group in compared["groups"])


@pytest.mark.parametrize("points", [2000, 5000])
def test_supported_multi_year_eight_candidate_study_publishes_and_restores(tmp_path, points):
    source = FixtureMarketDataSource(points=20).snapshot("AAA")
    observations = []
    for index in range(points):
        timestamp = pd.Timestamp("2015-01-01", tz="UTC") + pd.Timedelta(days=index)
        close = 100 + index * 0.01 + math.sin(index / 4) * 2
        observations.append(replace(source.observations[0], timestamp=timestamp, available_at=timestamp, retrieved_at=timestamp, open=close, close=close, high=close + 1, low=close - 1))
    snapshot = replace(source, observations=tuple(observations), as_of=observations[-1].timestamp)
    service = ResearchApplicationService(tmp_path)
    try:
        created = service.create_factor_study(values(service.snapshots.save(snapshot), recipe_id="close_momentum", windows=list(range(2, 10)), n_splits=4, as_of=observations[-1].timestamp.date().isoformat()))
        job_id = created["validation_job_id"]
        service.start()
        completed = wait_for(service, job_id, timeout=120)
        assert completed["status"] == "completed", completed
        entry = service.runs.get_run(completed["run_id"])
        assert (entry["bundle"] / "manifest.json").stat().st_size > 1_000_000
        assert entry["report_status"] == "AVAILABLE"
    finally:
        service.close()
    restarted = ResearchApplicationService(tmp_path)
    try:
        assert restarted.get_job(job_id)["report_status"] == "AVAILABLE"
        assert restarted.factor_study(created["study_id"])["state"] == "VALIDATED"
    finally:
        restarted.close()


@pytest.mark.parametrize("phase", ["validation", "test"])
@pytest.mark.parametrize("after_enqueue", [False, True])
def test_interrupted_study_enqueue_recovers_from_read_without_releasing_claim(tmp_path, monkeypatch, phase, after_enqueue):
    service = ResearchApplicationService(tmp_path)
    reference = service.snapshots.save(FixtureMarketDataSource(points=240).snapshot("AAA"))
    study_id = None
    original_job_id = None
    try:
        if phase == "test":
            service.start()
            created = service.create_factor_study(values(reference))
            validated = wait_study(service, created["study_id"], "validation")
            service.freeze_factor_study(created["study_id"], validated["validation"]["ranking"][0])
            study_id = created["study_id"]
        original = service._submit_factor_job

        def crash(study, submitted_phase, key):
            nonlocal original_job_id
            if after_enqueue:
                original_job_id = original(study, submitted_phase, key)["job_id"]
            raise RuntimeError("simulated process loss between durable transitions")

        with monkeypatch.context() as patch:
            patch.setattr(service, "_submit_factor_job", crash)
            with pytest.raises(RuntimeError):
                if phase == "validation":
                    service.create_factor_study(values(reference))
                else:
                    service.test_factor_study(study_id, "same-final-claim")
        study_id = study_id or service.factor_studies.list()[0]["study_id"]
    finally:
        service.close()
    restarted = ResearchApplicationService(tmp_path)
    try:
        restored = restarted.factor_study(study_id)  # ordinary browser GET, not a second test POST
        assert restored[f"{phase}_job_id"] is not None
        if original_job_id:
            assert restored[f"{phase}_job_id"] == original_job_id
        if phase == "test":
            assert restored["test_key"] == "same-final-claim"
            with pytest.raises(ValueError):
                restarted.factor_studies.reserve_test(study_id, "replace-claim")
        assert restarted.list_jobs()["total"] == (1 if phase == "validation" else 2)
        restarted.start()
        terminal = wait_study(restarted, study_id, phase)
        assert terminal["state"] == ("VALIDATED" if phase == "validation" else "TEST_EVALUATED")
    finally:
        restarted.close()

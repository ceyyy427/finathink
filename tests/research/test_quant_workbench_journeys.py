"""All published paths reach a bound report on real HTTP and spawned workers."""
import json
from pathlib import Path

import pytest

from tests.p7_5.test_phase4_browser import http_app
from tests.research.test_discovery_journeys import call
from tests.research.test_strategy_worker import completed

__all__ = ["http_app"]

CASES = json.loads((Path(__file__).parents[1] / "fixtures/quant_workbench/journeys.json").read_text())


@pytest.mark.parametrize(("journey_id", "target", "tool"), CASES, ids=[row[0] for row in CASES])
@pytest.mark.parametrize("http_app", [False], indirect=True)
def test_twelve_journeys_reach_explainable_report(http_app, journey_id, target, tool):
    app, base = http_app
    directory = json.loads(call(app, base, "/api/research/quant-workspace"))
    assert len(directory["journeys"]) == len(CASES) == 12
    path = next(row for row in directory["journeys"] if row["id"] == journey_id)
    assert path["href"] == tool and call(app, base, tool)
    html = call(app, base, "/research/workbench?journey=" + journey_id).decode()
    assert 'id="qw-start"' in html and path["question"] in html
    assert app.research_service.list_jobs()["total"] == 0
    question = {"question": path["question"], "profile": "balanced"}
    if journey_id == "missing-data":
        missing = call(app, base, "/api/research/discovery/preview", question)
        assert missing["status"] == "NEEDS_DATA"
        assert {"high", "low"} <= set(missing["missing_fields"])
    factor = target.startswith("lib-")
    sample = call(app, base, "/api/research/discovery/sample", {"question": question["question"]})
    values = {**question, **{key: sample[key] for key in ("contract", "attachment_refs")}}
    plan = call(app, base, "/api/research/discovery/preview", values)
    assert plan["status"] == "READY" and plan["target_id"] == target
    submitted = call(app, base, "/api/research/discovery", {
        **values, "authorized": True, "blueprint_fingerprint": plan["fingerprint"], "idempotency_key": journey_id})
    job = completed(app.research_service, submitted["job"]["job_id"])
    initial = job["run_id"]
    if factor:
        study_path = "/api/research/experiments/" + submitted["study_id"]
        ranked = job["factor_study"]["ranking"]
        assert ranked
        call(app, base, study_path + "/freeze", {"candidate_id": ranked[0]})
        tested = call(app, base, study_path + "/test", {"idempotency_key": "test-" + journey_id})
        job = completed(app.research_service, tested["test_job"]["job_id"])
        assert job["factor_study"]["library_validation"]["phase"] == "test"
        reviewed = call(app, base, study_path + "/review", {
            "decision": "APPROVE_FOR_RESEARCH", "reviewer_id": "path-review"})
        assert reviewed["state"] == "REVIEWED"
    else:
        result = job["strategy_study"]["result"]
        if journey_id == "event":
            assert result["status"] == "RESEARCH_ONLY"
        else:
            assert result["ledger"]["fills"]
    assert "研究计划与分析师证据".encode() in call(app, base, job["report_url"])
    assert job["discovery"]["blueprint"]["fingerprint"] == plan["fingerprint"]
    if journey_id == "pool":
        decision = next(row for row in reversed(result["decisions"]) if row["status"] == "SCHEDULED")
        entry = call(app, base, "/api/research/pools", {"job_id": job["job_id"], "pool_ref": "workbench-pool",
            "instrument": next(iter(decision["target_weights"])), "stage": "DECISION", "decision_origin": "USER"})
        assert entry["instrument"].encode() in call(app, base, "/research/pools?pool_ref=workbench-pool")
        cohort = call(app, base, "/api/research/pools/freeze", {
            "pool_ref": entry["pool_ref"], "decision_at": entry["decision_at"]})
        observed = call(app, base, "/api/research/pools/observe", {
            "cohort_ref": cohort["cohort_ref"], "snapshot_ref": sample["snapshot_ref"]})
        assert len(observed["horizons"]) == 4
    if journey_id == "learning":
        assert job["discovery"]["review_status"] == "REVIEW_REQUIRED" and job["discovery"]["knowledge_suggestion"]
    if journey_id == "replay":
        prefix = "/api/research/reproduction"
        comparison = json.loads(call(app, base, f"{prefix}/compare?left={initial}&right={job['run_id']}"))
        assert comparison["left"]["run_id"] == initial and comparison["right"]["run_id"] == job["run_id"]
        package = call(app, base, prefix + "/export", {"run_id": job["run_id"], "include_data": True})
        saved = call(app, base, prefix + "/restore", {"package": package})
        replay = call(app, base, f"{prefix}/{saved['package_id']}/recompute", {})
        assert replay["status"] == "PARTIAL", replay
        assert replay["products"]["discovery"] == "REQUIRES_ORIGINAL_WORKFLOW"

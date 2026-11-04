"""Real queued strategy reports and source associations."""

import time

import pytest

from finahinking.research.application_service import ResearchApplicationService
from finahinking.research.pool_jobs import pool_store, published_signal, record_published_decision
from finahinking.research.reproduction import ReproductionService
from finahinking.research.strategy_catalog import StrategyCatalog


def completed(service, job_id):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        job = service.get_job(job_id)
        if job["status"] in {"completed", "failed", "cancelled"}:
            assert job["status"] == "completed" and job["report_status"] == "AVAILABLE", job
            return job
        time.sleep(.1)
    raise AssertionError(f"strategy job did not finish: {job_id}")


def test_each_template_executes_with_benchmark_and_costs(tmp_path):
    service = ResearchApplicationService(tmp_path, allow_model_calls=False)
    try:
        sample = service.create_strategy_sample()
        inputs = {key: sample[key] for key in ("contract", "attachment_refs")}
        service.start()
        templates = StrategyCatalog().templates()
        assert len(templates) == 12
        jobs = {}
        for template in templates:
            template_id = template["template_id"]
            job = service.run_strategy_template(
                {**inputs, "template_id": template_id, "idempotency_key": template_id}
            )
            jobs[template_id] = completed(service, job["job_id"])
            output = jobs[template_id]["strategy_study"]
            result = output["result"]
            assert output["binding"]["template_id"] == template_id
            assert output["snapshot_ref"] == sample["snapshot_ref"]
            assert result["benchmark"]["status"] == "COMPLETE"
            assert result["ledger"]["marks"]
            if template["execution_mode"] == "PAPER_LONG_ONLY":
                assert result["status"] == "COMPLETE", (template_id, result["decisions"][-1])
                assert result["ledger"]["fills"]
                assert any(fill["fees"] > 0 for fill in result["ledger"]["fills"])
            else:
                assert result["status"] == "RESEARCH_ONLY"
                assert not result["ledger"]["fills"]
        equal = jobs["equal-weight"]
        with pytest.raises(ValueError, match="普通纸面研究报告"):
            service.learning._source(equal["run_id"])
        package = ReproductionService(service).export(equal["run_id"])
        assert package["product_coverage"]["strategy_study"] == "REQUIRES_ORIGINAL_WORKFLOW"
        assert "STRATEGY_WORKFLOW_DEPENDENCIES" in package["missing_dependencies"]

        scheduled = next(row for row in reversed(equal["strategy_study"]["result"]["decisions"])
                         if row["status"] == "SCHEDULED")
        instrument = next(iter(scheduled["target_weights"]))
        values = {"job_id": equal["job_id"], "pool_ref": "strategy-sample",
                  "instrument": instrument, "stage": "DECISION"}
        entries = [record_published_decision(service, {**values, "decision_origin": origin})
                   for origin in ("USER", "QUANT_BASELINE")]
        assert len({row["sample_ref"] for row in entries}) == 1
        scan = record_published_decision(service, {**values, "decision_origin": "AUTO_SCAN", "stage": "WATCH"})
        assert scan["sample_ref"] != entries[0]["sample_ref"]
        store = pool_store(service)
        cohort = store.freeze_cohort("strategy-sample", decision_at=entries[0]["decision_at"])
        assert len(cohort["samples"]) == 1
        assert len(store.list_entries("strategy-sample")) == 3
    finally:
        service.close()


def test_model_and_public_method_require_their_own_published_products():
    job = {"instrument": "EQA", "execution_mode": "provider-grounded", "snapshot_ref": "a" * 64}
    model = {"synthesis": {"hypotheses": ["比较已知前缀"], "context_fingerprint": "a" * 64},
             "calls": [{"dispatched": True, "role": "research", "status": "READY",
                        "context_fingerprint": "a" * 64}]}
    method = {"snapshot_ref": job["snapshot_ref"], "methods_digest": "b" * 64,
              "method_manifest_digest": "c" * 64, "factor_spec_fingerprint": "d" * 64}
    factor = {"phase": "TEST", "test_summary": {"failure_reason": None}, "selected_candidate": "quality"}
    report = {"model_research": model, "method_factor_snapshot": method, "factor_study": factor}
    for origin, kind in (("MODEL", "model-hypothesis"), ("PUBLIC_METHOD", "method-candidate")):
        signal, _ = published_signal(job, report, "EQA", origin)
        assert signal["kind"] == kind and "weight" not in signal
        with pytest.raises(ValueError, match="原始候选报告"):
            published_signal(job, {**report, "strategy_study": {"result": {"status": "COMPLETE"}}},
                             "EQA", origin)


def test_rule_scan_distinguishes_values_even_when_binary_status_matches():
    decision = {"status": "SCHEDULED", "target_weights": {"EQA": .2},
                "observed_at": "2025-01-02T16:00:00+00:00",
                "risk": [{"rule_id": "volatility", "status": "PASS", "value": .05,
                          "limit": .2, "fingerprint": "e" * 64}]}
    source = {"strategy_study": {"result": {"status": "COMPLETE", "decisions": [decision]}}}
    original, _ = published_signal({}, source, "EQA", "AUTO_SCAN")
    duplicate, _ = published_signal({}, source, "EQA", "AUTO_SCAN")
    assert original == duplicate
    decision["risk"][0]["value"] = .15
    changed, _ = published_signal({}, source, "EQA", "AUTO_SCAN")
    assert changed != original

"""Accepted external JSON enters the existing spawn/study/report path only."""

import copy
import hashlib
import math
from dataclasses import replace

import pytest

from finahinking.data.research_fields import ResearchFieldAttachment
from finahinking.factors.dsl import parse_factor_expression
from finahinking.p8_2.data_sources import FixtureMarketDataSource
from finahinking.research import factor_proposals
from finahinking.research.application_service import ResearchApplicationService
from finahinking.research.asset_catalog import ResearchAssetCatalog
from finahinking.research.asset_contracts import MethodRouteRequest
from finahinking.research.contracts import stable_digest
from finahinking.research.method_handoff import MethodHandoffPackage
from tests.research.test_factor_study_application import wait_study


def prepared(service):
    original = FixtureMarketDataSource(points=240).snapshot("AAA")
    snapshot = replace(original, observations=tuple(replace(row, suspended=False, amount=row.close * row.volume,
        industry="fixture-industry") for row in original.observations))
    reference = service.snapshots.save(snapshot)
    job = service.submit({"idempotency_key": "method-parent", "snapshot_ref": reference, "instrument": "AAA",
        "as_of": "2026-10-01", "question": "研究低换手相对低波动的增量信息", "model_config_ref": "offline"})
    exported = service.export_method_handoff(job["job_id"], MethodRouteRequest(
        purposes=("factor-mining",), market="CN", available_fields=("close", "volume", "amount", "turnover_rate")))
    package = MethodHandoffPackage.from_dict(exported["package"])
    expression, text = "rolling_mean(turnover_rate,5)", "研究低换手相对低波动的增量信息"
    parsed = parse_factor_expression(expression, ("turnover_rate",))
    identifier = hashlib.sha256(f"{text}|{parsed.expression}|{','.join(parsed.fields)}".encode()).hexdigest()[:12]
    payload = {"proposal_id": "factor-proposal-" + identifier, "expression": expression,
        "source_hypothesis": text, "required_fields": ["turnover_rate"],
        "constraints": {"max_window": 252, "point_in_time": True, "paper_only": True}, "paper_only": True}
    output = {"artifact_type": "FactorProposal", "schema_version": "factor-proposal.v1",
        "input_digest": package.envelope.input_digest, "content_digest": stable_digest(payload), "payload": payload}
    response = {"schema_version": "method-result.v1", "package_digest": package.package_digest,
        "result": {"schema_version": "codex-task.v1", "input_digest": package.envelope.input_digest,
            "task_id": package.envelope.task_id, "role": package.envelope.role, "paper_only": True,
            "status": "READY", "evidence_refs": list(package.context.evidence_index)}, "output_artifacts": [output]}
    accepted = service.accept_method_handoff(job["job_id"], exported["handoff_ref"], response)
    assert accepted["outcome"]["status"] == "READY"
    attachment = ResearchFieldAttachment(snapshot.fingerprint, "CN",
        {"turnover_rate": "ratio_free_float", "amount": "CNY", "volume": "shares"},
        [{"instrument": row.instrument, "timestamp": row.timestamp.isoformat(), "available_at": row.available_at.isoformat(),
          "turnover_rate": 0.02 + math.sin(number / 3) / 100} for number, row in enumerate(snapshot.observations)])
    return job, exported["handoff_ref"], accepted["artifacts"][0], package, attachment


def test_question_methods_persisted_proposal_existing_worker_and_nine_step_report(tmp_path):
    service = ResearchApplicationService(tmp_path, allow_model_calls=False)
    try:
        job, handoff, artifact, package, fields = prepared(service)
        stored = service.attach_research_fields(job["job_id"], fields)
        service.start()
        created = service.create_method_factor_study(job["job_id"], handoff, artifact["artifact_ref"],
            attachment_ref=stored["attachment_ref"], idempotency_key="method-pilot")
        result = wait_study(service, created["study_id"], "validation")
        validation = result["validation"]["validation_run"]
        assert validation["status"] in {"COMPLETE", "COMPLETE_WITH_BLOCKS"}
        assert len(validation["steps"]) == 9
        assert validation["reproduction_scope"] == "FORMULA_FIXTURE"
        assert validation["bindings"]["snapshot_ref"] == job["snapshot_ref"]
        assert validation["bindings"]["attachment_ref"] == fields.fingerprint
        assert result["spec"]["method_binding"]["methods_digest"] == package.to_dict()["manifest"]["methods_digest"]
        assert result["spec"]["hypothesis"]["family"] == "liquidity"
        assert result["production_write"] is False
        assert validation["final_test_metrics"] is None
        repeated = service.create_method_factor_study(job["job_id"], handoff, artifact["artifact_ref"],
            attachment_ref=fields.fingerprint, idempotency_key="method-pilot")
        assert repeated["study_id"] == result["study_id"]
        assert service.list_jobs()["total"] == 2
        candidate = result["validation"]["experiments"][0]["experiment_id"]
        original_fingerprint = validation["fingerprint"]
        service.freeze_factor_study(result["study_id"], candidate)
        service.test_factor_study(result["study_id"], "one-pilot-final-test")
        tested = wait_study(service, result["study_id"], "test")
        assert tested["test_result"]["validation_run"]["final_test_metrics"] is not None
        assert tested["validation"]["validation_run"]["fingerprint"] == original_fingerprint
        with pytest.raises(ValueError):
            service.test_factor_study(result["study_id"], "second-final-test")
        service.review_factor_study(result["study_id"], "REJECT", "fixture-reviewer")
    finally:
        service.close()


def test_missing_true_turnover_still_publishes_explicit_block_without_factor_rejection(tmp_path):
    service = ResearchApplicationService(tmp_path, allow_model_calls=False)
    try:
        job, handoff, artifact, _, _ = prepared(service)
        service.start()
        created = service.create_method_factor_study(job["job_id"], handoff, artifact["artifact_ref"],
                                                     idempotency_key="missing-turnover")
        result = wait_study(service, created["study_id"], "validation")
        validation = result["validation"]["validation_run"]
        assert validation["status"] == "BLOCKED_DATA"
        assert validation["financial_falsification"] is False
        assert result["state"] != "VALIDATED"
        with pytest.raises(ValueError):
            service.freeze_factor_study(result["study_id"], result["validation"]["experiments"][0]["experiment_id"])
    finally:
        service.close()


def test_forged_artifact_or_zero_candidate_budget_never_enters_a_study(tmp_path):
    assert hasattr(factor_proposals, "proposal_from_handoff"), "controlled proposal handoff is missing"
    service = ResearchApplicationService(tmp_path, allow_model_calls=False)
    try:
        _, _, artifact, package, _ = prepared(service)
        catalog = ResearchAssetCatalog.packaged()
        valid = factor_proposals.proposal_from_handoff(package, artifact, catalog=catalog, policy={"max_candidates": 1})
        assert valid.expression == "rolling_mean(turnover_rate,5)"
        forged = copy.deepcopy(artifact)
        forged["payload"]["expression"] = "unknown(turnover_rate,5)"
        forged["content_digest"] = stable_digest(forged["payload"])
        with pytest.raises(ValueError):
            factor_proposals.proposal_from_handoff(package, forged, catalog=catalog, policy={"max_candidates": 1})
        with pytest.raises(ValueError):
            factor_proposals.proposal_from_handoff(package, artifact, catalog=catalog, policy={"max_candidates": 0})
        assert service.factor_studies.list() == []
    finally:
        service.close()

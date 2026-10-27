"""Use approvals are scoped events, never a factor-wide production flag."""

import importlib
import importlib.util
import json
from dataclasses import replace

import pytest

from finahinking.research.asset_contracts import FactorEvidenceView
from finahinking.research.contracts import stable_digest


def api():
    name = "finahinking.research.factor_lifecycle"
    assert importlib.util.find_spec(name) is not None, "scope-bound lifecycle is missing"
    return importlib.import_module(name)


def evidence(*, status="COMPLETE", mode="FORMULA_FIXTURE", archive="PENDING_HUMAN_RESEARCH_REVIEW"):
    universe = {"market": "CN", "instruments": ["CN:A"], "membership_policy": "STATIC_USER_DECLARED"}
    local = {"run_ref": "run-0", "factor_ref": "a" * 64, "factor_id": "fixture-factor", "version": "1.0.0",
        "evaluation_fingerprint": "b" * 64, "policy_fingerprint": "c" * 64, "definition_matches": True,
        "status": status, "phase": "test", "settled": status == "COMPLETE", "scope": {"universe": universe},
        "reproduction_scope": mode, "local_metric": {"archive_decision": {"decision": archive}},
        "stages": [{"stage_id": str(n), "status": "COMPLETE" if status == "COMPLETE" else "EXECUTION_ERROR"} for n in range(9)]}
    return FactorEvidenceView(json.dumps({"factor_id": "fixture-factor", "factor_ref": "a" * 64,
        "definition": {"definition_status": "DEFINED", "recipe_id": "fixture"}, "local_runs": [local],
        "source_claims": [], "missing_dependencies": [], "reproduction_scope": [mode]}))


def decision(**changes):
    values = {"factor_ref": "a" * 64, "market": "CN", "universe_ref": stable_digest(evidence().to_dict()["local_runs"][0]["scope"]["universe"]),
        "purpose": "FORMULA_RESEARCH", "policy_fingerprint": "c" * 64, "evaluation_fingerprint": "b" * 64,
        "reviewer": "local-reviewer", "decision": "APPROVE_FOR_RESEARCH", "idempotency_key": "approve-one", "run_ref": "run-0"}
    return api().FactorUseDecision(**{**values, **changes})


def test_without_explicit_review_no_research_approval():
    projected = api().project_factor_lifecycle(evidence(), ()).to_dict()
    assert projected["state"] != "RESEARCH_APPROVED"
    assert projected["use_scopes"] == []
    assert projected["production_write"] is False


@pytest.mark.parametrize("changes", [{"reviewer": ""}, {"reviewer": "/private/me"}, {"evaluation_fingerprint": "missing"},
                                    {"purpose": "LIVE_TRADING"}, {"market": ""}, {"idempotency_key": ""}])
def test_decision_metadata_is_bounded_and_explicit(changes):
    with pytest.raises(ValueError):
        decision(**changes)


@pytest.mark.parametrize("status", ["BLOCKED_DATA", "EXECUTION_ERROR", "COMPLETE_WITH_BLOCKS"])
def test_missing_or_failed_runs_do_not_become_financial_rejection(status):
    projected = api().project_factor_lifecycle(evidence(status=status), ()).to_dict()
    assert projected["state"] not in {"RESEARCH_APPROVED", "REJECTED"}
    assert projected["financial_falsification"] is False


def test_approval_applies_only_to_exact_scope_and_retirement_keeps_history(tmp_path):
    from finahinking.research.job_queue import JobQueue

    store = api().FactorUseStore(JobQueue(tmp_path / "queue.sqlite3"), scope_id="one")
    approved = store.record(decision(), evidence=evidence())
    projected = api().project_factor_lifecycle(evidence(), store.list()).to_dict()
    assert projected["state"] == "RESEARCH_APPROVED"
    scope = {key: decision().to_dict()[key] for key in ("factor_ref", "market", "universe_ref", "purpose", "policy_fingerprint")}
    assert api().use_is_approved(projected, **scope)
    for key, value in (("market", "US"), ("purpose", "PAPER_SCREENING"), ("factor_ref", "d" * 64),
                       ("universe_ref", "d" * 64), ("policy_fingerprint", "d" * 64)):
        assert not api().use_is_approved(projected, **{**scope, key: value})
    store.record(replace(decision(), decision="MONITOR", idempotency_key="monitor"), evidence=evidence())
    store.record(replace(decision(), decision="RETIRE", idempotency_key="retire"), evidence=evidence())
    retired = api().project_factor_lifecycle(evidence(), store.list()).to_dict()
    assert retired["state"] == "RETIRED"
    assert not api().use_is_approved(retired, **scope)
    assert store.list()[0] == approved
    assert len(store.list()) == 3


def test_one_factors_retirement_never_changes_another_factor(tmp_path):
    from finahinking.research.job_queue import JobQueue

    store = api().FactorUseStore(JobQueue(tmp_path / "queue.sqlite3"), scope_id="one")
    store.record(decision(), evidence=evidence())
    store.record(replace(decision(), decision="RETIRE", idempotency_key="retire"), evidence=evidence())
    other = evidence().to_dict()
    other.update(factor_id="other-factor", factor_ref="other-factor", local_runs=[], definition={"definition_status": "UNRESOLVED"})
    projected = api().project_factor_lifecycle(FactorEvidenceView(json.dumps(other)), store.list()).to_dict()
    assert projected["state"] == "SOURCE_ONLY"
    assert projected["history"] == []

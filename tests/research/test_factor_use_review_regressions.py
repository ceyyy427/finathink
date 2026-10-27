"""Concrete whole-review regressions, not assertions of a blanket safe state."""

import json
from dataclasses import replace

import pytest

from finahinking.research.asset_contracts import FactorEvidenceView
from tests.research.test_factor_lifecycle import api, decision, evidence


def test_unrelated_event_volume_never_erases_a_durable_scope_approval(tmp_path, monkeypatch):
    from finahinking.research import factor_provenance
    from finahinking.research.application_service import ResearchApplicationService

    app = ResearchApplicationService(tmp_path, allow_model_calls=False)
    left = evidence()
    requested = []

    def load(ref, **kwargs):
        requested.extend(kwargs.get("run_refs", ()))
        return left

    monkeypatch.setattr(factor_provenance, "load_factor_evidence", load)
    try:
        app.factor_uses.record(decision(), evidence=left)
        assert app.factor_evidence("fixture-factor")["lifecycle"]["state"] == "RESEARCH_APPROVED"
        other = left.to_dict()
        other.update(factor_id="other-factor", factor_ref="d" * 64)
        other["local_runs"][0].update(factor_ref="d" * 64, factor_id="other-factor")
        for number in range(201):
            changes = {"factor_ref": "d" * 64, "decision": "OBSERVE", "idempotency_key": f"unrelated-{number}"}
            changes["factor_id"] = "other-factor"
            app.factor_uses.record(replace(decision(), **changes), evidence=FactorEvidenceView(json.dumps(other)))
        projected = app.factor_evidence("fixture-factor")["lifecycle"]
        assert projected["state"] == "RESEARCH_APPROVED"
        assert len(projected["history"]) == 1
        assert "run-0" in requested, "durable approval must explicitly hydrate its original report beyond the inventory cap"
    finally:
        app.close()


def test_an_approval_query_without_a_time_and_evaluation_domain_is_not_authorized(tmp_path):
    from finahinking.research.job_queue import JobQueue

    store = api().FactorUseStore(JobQueue(tmp_path / "queue.sqlite3"), scope_id="one")
    store.record(decision(), evidence=evidence())
    view = api().project_factor_lifecycle(evidence(), store.list()).to_dict()
    old_broad_scope = {key: decision().to_dict()[key] for key in ("factor_ref", "market", "universe_ref", "purpose", "policy_fingerprint")}
    assert not api().use_is_approved(view, **old_broad_scope)


@pytest.mark.parametrize("changes", [{"run_ref": "no-such-run"}, {"evaluation_fingerprint": "f" * 64}])
def test_retirement_cannot_invent_its_supporting_run_or_evaluation(tmp_path, changes):
    from finahinking.research.job_queue import JobQueue

    store = api().FactorUseStore(JobQueue(tmp_path / "queue.sqlite3"), scope_id="one")
    store.record(decision(), evidence=evidence())
    forged = replace(decision(), decision="RETIRE", idempotency_key="forged-retire", **changes)
    with pytest.raises(ValueError):
        store.record(forged, evidence=evidence())
    assert len(store.list()) == 1


def test_public_retirement_rejects_a_different_route_factor(tmp_path, monkeypatch):
    from finahinking.research import factor_provenance
    from finahinking.research.application_service import ResearchApplicationService

    app = ResearchApplicationService(tmp_path, allow_model_calls=False)
    try:
        app.factor_uses.record(decision(), evidence=evidence())
        unrelated = evidence().to_dict()
        unrelated.update(factor_id="other-factor", factor_ref="other-factor", local_runs=[])
        monkeypatch.setattr(factor_provenance, "load_factor_evidence", lambda *args, **kwargs: FactorEvidenceView(json.dumps(unrelated)))
        with pytest.raises(ValueError):
            app.review_factor_use("other-factor", replace(decision(), decision="RETIRE", idempotency_key="wrong-route").to_dict())
    finally:
        app.close()


def test_authorization_is_exact_to_the_evaluated_temporal_sample(tmp_path):
    from finahinking.research.job_queue import JobQueue

    store = api().FactorUseStore(JobQueue(tmp_path / "queue.sqlite3"), scope_id="one")
    store.record(decision(), evidence=evidence())
    view = api().project_factor_lifecycle(evidence(), store.list()).to_dict()
    keys = ("factor_ref", "market", "universe_ref", "time_domain", "purpose", "policy_fingerprint", "evaluation_fingerprint")
    scope = {key: decision().to_dict()[key] for key in keys}
    assert api().use_is_approved(view, **scope)
    assert not api().use_is_approved(view, **{**scope, "time_domain": ["2025-01-01", "2025-01-21"]})
    assert not api().use_is_approved(view, **{**scope, "evaluation_fingerprint": "f" * 64})
    assert view["history"][0]["decision"]["time_domain"] == scope["time_domain"]


def test_retirement_of_old_definition_uses_original_approval_not_current_resolver(tmp_path):
    from finahinking.research.job_queue import JobQueue

    store = api().FactorUseStore(JobQueue(tmp_path / "queue.sqlite3"), scope_id="one")
    store.record(decision(), evidence=evidence())
    historical = evidence().to_dict()
    historical["local_runs"][0]["definition_matches"] = False
    store.record(replace(decision(), decision="RETIRE", idempotency_key="retire-historical"),
                 evidence=FactorEvidenceView(json.dumps(historical)))
    assert api().project_factor_lifecycle(FactorEvidenceView(json.dumps(historical)), store.list()).to_dict()["state"] == "RETIRED"


def test_exact_scope_lookup_stays_available_beyond_the_display_page(tmp_path):
    from finahinking.research.job_queue import JobQueue

    store = api().FactorUseStore(JobQueue(tmp_path / "queue.sqlite3"), scope_id="one")
    original = store.record(decision(), evidence=evidence())
    for number in range(201):
        values = evidence().to_dict()
        fingerprint, reference = f"{number + 2:064x}", f"revision-{number}"
        values["local_runs"][0].update(evaluation_fingerprint=fingerprint, run_ref=reference)
        store.record(replace(decision(), evaluation_fingerprint=fingerprint, run_ref=reference,
            decision="OBSERVE", idempotency_key=reference), evidence=FactorEvidenceView(json.dumps(values)))
    assert store.current("fixture-factor")["scope_truncated"] is True
    keys = ("factor_ref", "market", "universe_ref", "time_domain", "purpose", "policy_fingerprint", "evaluation_fingerprint")
    scope = {key: decision().to_dict()[key] for key in keys}
    assert store.current("fixture-factor", use_scope=scope)["events"] == [original]

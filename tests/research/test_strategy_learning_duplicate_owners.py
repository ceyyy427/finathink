"""Observing the same economic windows through two published runs cannot spend them."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from finahinking.research.application_service import ResearchApplicationService
from finahinking.research.strategy_learning import StrategyLearningService
from tests.research.test_strategy_worker import completed


def test_first_observing_run_does_not_change_eligibility_or_allow_a_second_proposal(tmp_path):
    economic_sets = []
    for first in ("partial", "wide"):
        app = ResearchApplicationService(tmp_path / first, allow_model_calls=False)
        try:
            sample = app.create_strategy_sample()
            inputs = {key: sample[key] for key in ("contract", "attachment_refs")}
            app.start()
            jobs = [app.run_strategy_template({**inputs, "template_id": "equal-weight", "idempotency_key": key})
                    for key in ("receipt-A", "receipt-B")]
            a, b = [completed(app, job["job_id"])["run_id"] for job in jobs]
            learning = StrategyLearningService(app)
            observe = learning.settlements.observe
            if first == "partial":
                partial = observe(a, "2025-02-13")
                wider = observe(b, "2025-02-27")
                duplicate = observe(a, "2025-02-27")
                assert wider["new_sample_count"] == 1 and wider["duplicate_count"] == 2
            else:
                wider = observe(a, "2025-02-27")
                partial = observe(b, "2025-02-13")
                duplicate = observe(b, "2025-02-27")
                assert wider["new_sample_count"] == 3
            assert partial["sample_count"] == 2 and duplicate["status"] == "DUPLICATE_SOURCE"
            assert {r["sample_ref"] for r in duplicate["samples"]} == {r["sample_ref"] for r in wider["samples"]}
            witness = learning.audit(wider["observation_ref"])
            assert witness["status"] == "READY" and witness["sample_count"] == 3
            economic_sets.append(witness["economic_set_fingerprint"])
            copy = learning.audit(duplicate["observation_ref"])
            assert copy["status"] == "DUPLICATE_SOURCE"
            with pytest.raises(ValueError, match="至少三个"):
                learning.propose(copy["audit_id"])
            with ThreadPoolExecutor(max_workers=2) as threads:
                proposals = list(threads.map(learning.propose, [witness["audit_id"]] * 2))
            assert proposals[0] == proposals[1] and len(learning.store.list("proposal")) == 1
            assert learning.store.claimed([r["sample_ref"] for r in wider["samples"]]) == {
                r["sample_ref"] for r in witness["selected_samples"]}
        finally:
            app.close()
    assert economic_sets[0] == economic_sets[1]


@pytest.mark.parametrize(("earlier_mode", "wider_mode"), [("SAMPLE", "SYNTHETIC"), ("CAPTURED", "LIVE")])
def test_transport_aliases_share_unclaimed_economics_but_not_approved_policy_scope(tmp_path, earlier_mode, wider_mode):
    from dataclasses import replace
    from datetime import UTC, datetime

    from finahinking.data.panel_contracts import PanelContract, PanelFieldAttachment
    from finahinking.data.research_fields import ResearchFieldStore
    from finahinking.research.strategy_sample import strategy_sample

    app = ResearchApplicationService(tmp_path, allow_model_calls=False)
    try:
        original, (fields,), contract, _ = strategy_sample()
        app.start()
        def publish(mode, end, key):
            snapshot = replace(original, mode=mode)
            app.snapshots.save(snapshot)
            att = PanelFieldAttachment(snapshot.fingerprint, fields.units, fields.records, fields.source_declaration)
            ref = ResearchFieldStore(app.snapshots).save(att)
            times = [t for t in contract.observed_at if t[:10] <= end]
            selection = PanelContract(snapshot.fingerprint, contract.instrument_ids, times, contract.field_units,
                                      contract.scope_id, contract.currencies, contract.timezones, ref, ref, ref, ref)
            return app.run_strategy_template({"template_id": "equal-weight", "idempotency_key": key,
                                              "attachment_refs": [ref], "contract": selection.to_dict()})["job_id"]

        ids = [publish(earlier_mode, "2025-02-13", "two"), publish(wider_mode, "2025-02-27", "three"),
               publish(earlier_mode, "2025-02-27", "three-copy")]
        partial, extended, copy = [completed(app, key)["run_id"] for key in ids]
        learning = StrategyLearningService(app)
        at = datetime.now(UTC).date().isoformat()
        early = learning.settlements.observe(partial, at)
        wide = learning.settlements.observe(extended, at)
        duplicate = learning.settlements.observe(copy, at)
        assert (early["sample_count"], wide["new_sample_count"], wide["duplicate_count"]) == (2, 1, 2)
        assert {row["sample_ref"] for row in wide["samples"]} == {row["sample_ref"] for row in duplicate["samples"]}
        audit = learning.audit(wide["observation_ref"])
        assert audit["status"] == "READY" and audit["sample_count"] == 3
        proposal = learning.propose(audit["audit_id"])
        decision = learning.decide(proposal["proposal_id"], {"decision": "APPROVED", "rationale": "复核同一经济样本的原始决策。",
            "expected_preview_fingerprint": proposal["preview_fingerprint"]})
        assert decision["policy"]["scope"]["data_mode"] == wider_mode
        with pytest.raises(ValueError, match="不在批准范围"):
            learning.apply_to_run(proposal["proposal_id"], copy)
        copied = learning.audit(duplicate["observation_ref"])
        assert copied["status"] == "DUPLICATE_SOURCE"
        with pytest.raises(ValueError, match="至少三个"):
            learning.propose(copied["audit_id"])
        assert len(learning.store.list("proposal")) == len(learning.store.list("policy")) == 1
    finally:
        app.close()

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

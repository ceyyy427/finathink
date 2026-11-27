"""Approved policy pairs replay in an empty workspace without importing any policy."""
from copy import deepcopy
from hashlib import sha256

import pytest

from finahinking.research.application_service import ResearchApplicationService
from finahinking.research.contracts import stable_digest
from finahinking.research.learning_replays import LearningReplayService
from finahinking.research.portable_learning import (
    freeze_pair,
    list_pairs,
    replay_pair,
    validate_pair,
)
from finahinking.research.reproduction import _digest
from finahinking.research.strategy_learning import StrategyLearningService
from tests.research.test_learning_replays import prepared
from tests.research.test_strategy_worker import completed


def test_research_pair_replays_two_offline_children_and_preserves_approval(tmp_path):
    origin = ResearchApplicationService(tmp_path / "origin")
    target = ResearchApplicationService(tmp_path / "destination", scope_id="fresh")
    try:
        setup = prepared(origin)
        pair = LearningReplayService(origin).create(setup)
        for side in pair["children"].values():
            completed(origin, side["job_id"])
        review = origin.learning.get(setup["review_id"])
        package = freeze_pair(origin, pair["pair_id"])
        assert replay_pair(target, package["inputs"]) == package["expected"]
        assert package["expected"]["learning_baseline"]["policy_ref"] == review["parent_policy_ref"]
        assert package["expected"]["learning_candidate"]["policy_ref"] == review["policy_ref"]
        assert list_pairs(origin) == [{"pair_id": pair["pair_id"], "kind": "research_pair", "status": "AVAILABLE"}]
        assert list_pairs(target) == []
        assert len(target.learning.policies()) == 1 and target.list_jobs()["total"] == 0
        assert origin.learning.get(setup["review_id"]) == review
        # Imported data must obey the earliest applicable publication, even
        # when a later-approved policy gave the pair a much later research date.
        for clock in ("timestamp", "available_at"):
            after = deepcopy(package["inputs"])
            last = after["snapshot"]["observations"][-1]
            after["children"]["right"]["publication_before"] = "2026-01-12T18:00:00Z"
            last[clock] = "2026-01-12T18:00:00Z"
            if clock == "timestamp":
                last["available_at"] = last[clock]
            after["snapshot"]["fingerprint"] = _digest({k: v for k, v in after["snapshot"].items() if k != "fingerprint"})
            with pytest.raises(ValueError):
                validate_pair(after)
        after = deepcopy(package["inputs"])
        after["snapshot"]["observations"][-1]["available_at"] = "2026-01-14T00:00:00Z"
        after["snapshot"]["fingerprint"] = _digest({k: v for k, v in after["snapshot"].items() if k != "fingerprint"})
        with pytest.raises(ValueError, match="发表范围"):
            validate_pair(after)
        damaged = deepcopy(package["inputs"])
        damaged["review"]["settlement"]["realized_outcomes"]["observation"]["mark_price"] *= 2
        with pytest.raises(ValueError):
            validate_pair(damaged)
        damaged = deepcopy(package["inputs"])
        damaged["review"]["admission"]["decision"] = "REJECTED"
        with pytest.raises(ValueError):
            validate_pair(damaged)
    finally:
        origin.close()
        target.close()


def test_strategy_pair_rebuilds_observed_windows_and_both_real_ledgers(tmp_path):
    origin = ResearchApplicationService(tmp_path / "origin")
    target = ResearchApplicationService(tmp_path / "destination", scope_id="fresh")
    try:
        sample = origin.create_strategy_sample()
        origin.start()
        job = origin.run_strategy_template({k: sample[k] for k in ("contract", "attachment_refs")} |
                                           {"template_id": "equal-weight", "idempotency_key": "frozen-pair"})
        source = completed(origin, job["job_id"])
        report = origin.runs.get_run(source["run_id"])["bundle"] / "complete_report.html"
        before = sha256(report.read_bytes()).hexdigest()
        learning = StrategyLearningService(origin)
        receipt = learning.settlements.observe(source["run_id"], source["as_of"][:10])
        audit = learning.audit(receipt["observation_ref"])
        proposal = learning.propose(audit["audit_id"])
        approved = learning.decide(proposal["proposal_id"], {"decision": "APPROVED", "rationale": "允许对照复算",
                                   "expected_preview_fingerprint": proposal["preview_fingerprint"]})
        pair = learning.pair(approved["proposal_id"])
        package = freeze_pair(origin, pair["pair_id"])
        actual = replay_pair(target, package["inputs"])
        assert actual == package["expected"]
        assert actual["learning_baseline"]["ledger"] == source["strategy_study"]["result"]["ledger"]
        assert actual["learning_candidate"]["ledger"]["fills"] != actual["learning_baseline"]["ledger"]["fills"]
        assert actual["learning_comparison"]["metrics"] == pair["metrics"]
        assert actual["learning_approval"]["observation_digest"] == stable_digest(receipt)
        assert list_pairs(origin) == [{"pair_id": pair["pair_id"], "kind": "strategy_pair", "status": "AVAILABLE"}]
        assert list_pairs(target) == [] and StrategyLearningService(target).store.list("policy") == []
        assert sha256(report.read_bytes()).hexdigest() == before
        assert learning.proposal(proposal["proposal_id"])["admission"] == approved["admission"]
        for changes in ({"decision": {**package["inputs"]["decision"], "requested_effective_on": "1900-01-01"}},
                        {"observation": {**receipt, "as_of": "2020-01-01"}}):
            with pytest.raises(ValueError):
                validate_pair({**package["inputs"], **changes})
    finally:
        origin.close()
        target.close()

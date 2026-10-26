"""Regression proofs for the one independent Phase6 review."""

import json as json_module

import pytest

from finahinking.research.analyst_runtime import AnalystPool, GroundedResearchManager
from finahinking.research.contracts import ResearchState
from finahinking.research.observability import (
    RuntimeBudget,
    RuntimeLimitExceeded,
    bind_runtime_budget,
)
from finahinking.research.runtime_service import RuntimeLimits
from finahinking.research.snapshot_tools import SnapshotResearchTools
from finahinking.research.workflow import ResearchOrchestrator, WorkflowLimits
from tests.research.test_context_bundle import material
from tests.research.test_grounded_driver import ModelPeer, setup_driver


def test_byte_exhaustion_latches_and_blocks_all_further_resource_admission():
    budget = RuntimeBudget("latched", RuntimeLimits(max_bytes=8, max_provider_calls=6))
    with pytest.raises(RuntimeLimitExceeded):
        budget.charge_bytes(9)
    for charge in (budget.check_wall, budget.charge_provider_call, budget.charge_experiments):
        with pytest.raises(RuntimeLimitExceeded):
            charge()
    assert budget.provider_calls == 0


def test_optional_learning_byte_failure_cannot_start_manager_or_finish_research():
    class OversizedLearning(ModelPeer):
        def request(self, method, url, *, headers, json: object, timeout):
            payload = json
            result = super().request(method, url, headers=headers, json=payload, timeout=timeout)
            if '"role": "learning"' in payload["messages"][1]["content"]:
                result["json"]["padding"] = "x" * 300000
            return result

    req, driver, peer = setup_driver(OversizedLearning(), materials=(material(role="learning"),))
    budget = RuntimeBudget("shared", RuntimeLimits(max_bytes=200000, max_provider_calls=6))
    with bind_runtime_budget(budget):
        result = ResearchOrchestrator().run(req, driver, SnapshotResearchTools(driver.snapshot), analyst_pool=AnalystPool(), manager=GroundedResearchManager(driver), limits=WorkflowLimits(max_analyst_workers=1))
    assert budget.resource_failures > 0
    assert result.state.current_state is not ResearchState.LEARNING_RECORDED
    assert all('"role": "research"' not in call["payload"]["messages"][1]["content"] for call in peer.calls)


@pytest.mark.parametrize("role,mutation", [
    ("technical", "missing_claims"), ("technical", "empty_claims"), ("technical", "missing_limitations"),
    ("research", "missing_hypotheses"), ("research", "empty_hypotheses"), ("research", "missing_disagreements"),
])
def test_grounded_success_requires_substantive_role_schema(role, mutation):
    class SparsePeer(ModelPeer):
        def request(self, method, url, *, headers, json: object, timeout):
            response = super().request(method, url, headers=headers, json=json, timeout=timeout)
            content = json_module.loads(response["json"]["choices"][0]["message"]["content"])
            action, field = mutation.split("_", 1)
            if action == "missing":
                content.pop(field)
            else:
                content[field] = []
            response["json"]["choices"][0]["message"]["content"] = json_module.dumps(content)
            return response

    req, driver, _ = setup_driver(SparsePeer())
    result = driver.propose(req, {"analyst_role": role})
    assert result.failure_kind is not None and not result.reports

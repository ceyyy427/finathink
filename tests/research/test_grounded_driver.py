import json as json_module
from dataclasses import replace

import pytest

from finahinking.p8_2.data_sources import FixtureMarketDataSource
from finahinking.research.analyst_runtime import AnalystPool
from finahinking.research.credentials import InMemoryCredentialStore
from finahinking.research.observability import RuntimeBudget, bind_runtime_budget
from finahinking.research.provider_config import ProviderConfig
from finahinking.research.provider_status import ProviderCredentialRef
from finahinking.research.runtime_service import RuntimeLimits
from finahinking.research.workflow import AnalystSpec
from tests.research.test_context_bundle import material
from tests.research.test_snapshot_workflow import request


class ModelPeer:
    def __init__(self, *, ref_override=None, finish="stop"):
        self.calls = []
        self.ref_override = ref_override
        self.finish = finish

    def request(self, method, url, *, headers, json, timeout):
        payload = json
        self.calls.append({"method": method, "url": url, "payload": payload, "timeout": timeout})
        context = json_module.loads(payload["messages"][1]["content"])
        refs = list(context["evidence_index"]) if self.ref_override is None else [self.ref_override]
        content = {"status": "READY", "claims": ["Review the supplied observations."], "evidence_refs": refs, "limitations": ["Interpretation only."]}
        if context["role"] == "research":
            content.update(hypotheses=["Test the interpretation out of sample."], disagreements=["News interpretation needs confirmation."])
        return {"status_code": 200, "json": {"choices": [{"message": {"content": json_module.dumps(content)}, "finish_reason": self.finish}], "usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160}}}


def setup_driver(peer=None, *, materials=()):
    from finahinking.research.drivers import GroundedApiDriver

    snapshot = FixtureMarketDataSource(points=8).snapshot("AAA")
    req = replace(request(snapshot.fingerprint), analyst_roles=("fundamentals", "technical", "sentiment", "news", "learning"))
    ref = ProviderCredentialRef("desk", env_var="FINAHINK_TEST_KEY")
    config = ProviderConfig("desk", "openai_compatible", "gpt-4o", endpoint="https://models.acme.com/v1", credential_ref=ref, role_models={"technical": "gpt-4.1"})
    transport = peer or ModelPeer()
    return req, GroundedApiDriver(snapshot, config, materials=materials, transport=transport, credential_store=InMemoryCredentialStore({ref: "fake-local-value"})), transport


@pytest.mark.parametrize("kind,limit_field,model", [("openai_compatible", "max_completion_tokens", "gpt-4.1"), ("deepseek_compatible", "max_tokens", "deepseek-chat")])
def test_wire_contains_readable_context_only_protocol_fields_and_role_model(kind, limit_field, model):
    req, driver, peer = setup_driver()
    driver.config = replace(driver.config, adapter_kind=kind, role_models={"technical": model})
    result = driver.propose(req, {"analyst_role": "technical"})
    assert result.failure_kind is None and result.reports[0].role == "technical"
    payload = peer.calls[0]["payload"]
    assert set(payload) == {"model", "messages", "response_format", limit_field}
    assert payload["model"] == model and payload[limit_field] == 1024
    assert "JSON" in payload["messages"][0]["content"]
    context = json_module.loads(payload["messages"][1]["content"])
    assert context["question"] == "bounded local study" and context["materials"][0]["summary"]["record_count"] == 8
    assert "fake-local-value" not in str(payload)
    assert driver.call_records[0]["usage"]["total_units"] == 160


def test_missing_role_returns_insufficient_without_a_model_call():
    req, driver, peer = setup_driver()
    result = driver.propose(req, {"analyst_role": "fundamentals"})
    assert result.reports[0].status == "INSUFFICIENT_DATA"
    assert result.reports[0].evidence_refs == () and peer.calls == []


def test_disabled_model_config_cannot_be_invoked_directly():
    req, driver, peer = setup_driver()
    driver.config = replace(driver.config, enabled=False)
    assert driver.propose(req, {"analyst_role": "technical"}).failure_kind is not None
    assert peer.calls == []


def test_provider_cannot_echo_an_unpatterned_credential_into_public_observations():
    class EchoPeer(ModelPeer):
        def request(self, method, url, *, headers, json, timeout):
            response = super().request(method, url, headers=headers, json=json, timeout=timeout)
            content = json_module.loads(response["json"]["choices"][0]["message"]["content"])
            content["claims"] = ["The returned value is fake-local-value."]
            response["json"]["choices"][0]["message"]["content"] = json_module.dumps(content)
            return response

    req, driver, _ = setup_driver(EchoPeer())
    result = driver.propose(req, {"analyst_role": "technical"})
    assert result.failure_kind is not None and not result.reports


def test_accidentally_pasted_actual_credential_is_blocked_before_transmission():
    req, driver, peer = setup_driver()
    req = replace(req, research_plan=replace(req.research_plan, hypotheses=("Review fake-local-value",)))
    result = driver.propose(req, {"analyst_role": "technical"})
    assert result.failure_kind is not None and peer.calls == []


@pytest.mark.parametrize("peer", [ModelPeer(ref_override="evidence:another-run"), ModelPeer(finish="length"), ModelPeer(finish="content_filter")])
def test_forged_evidence_or_incomplete_output_does_not_become_a_report(peer):
    req, driver, _ = setup_driver(peer)
    result = driver.propose(req, {"analyst_role": "technical"})
    assert result.failure_kind is not None and not result.reports


def test_parallel_role_calls_share_the_worker_call_budget():
    req, driver, peer = setup_driver(materials=(material(),))
    budget = RuntimeBudget("bounded", RuntimeLimits(max_provider_calls=1))
    with bind_runtime_budget(budget):
        outcomes = AnalystPool().run((AnalystSpec("technical"), AnalystSpec("news")), req, driver, {})
    assert len(peer.calls) == 1 and budget.provider_calls == 1
    assert sum(outcome.status == "READY" for outcome in outcomes) == 1


def test_manager_summarizes_observations_disagreements_and_hypotheses_without_replacing_policy():
    from finahinking.research.analyst_runtime import GroundedResearchManager

    req, driver, peer = setup_driver(materials=(material(),))
    reports = tuple(driver.propose(req, {"analyst_role": role}).reports[0] for role in ("technical", "news", "learning"))
    plan = GroundedResearchManager(driver).synthesize(reports, req)
    assert plan.factor_ids == req.research_plan.factor_ids
    assert plan.hypotheses == ("Test the interpretation out of sample.",)
    assert plan.validation_spec["disagreements"] == ["News interpretation needs confirmation."]
    assert len(plan.validation_spec["observations"]) == 3
    assert json_module.loads(peer.calls[-1]["payload"]["messages"][1]["content"])["role"] == "research"

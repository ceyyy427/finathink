"""Replay a published ledger and an independently published studio plan without their origin."""
import copy
import json
from types import SimpleNamespace

import pytest

from finahinking.research import portable_strategy
from finahinking.research.application_service import ResearchApplicationService
from finahinking.research.contracts import stable_digest
from finahinking.research.portable_inputs import PortableUnavailable
from finahinking.research.reproduction import ReproductionService
from finahinking.research.strategy_validation_service import StrategyValidationService
from tests.research.test_strategy_worker import completed


@pytest.fixture(scope="module")
def publications(tmp_path_factory):
    origin = ResearchApplicationService(tmp_path_factory.mktemp("strategy-origin"), allow_model_calls=False)
    try:
        sample = origin.create_strategy_sample()
        origin.start()
        request = {key: sample[key] for key in ("contract", "attachment_refs")}
        request["question"] = "等权组合的集中风险如何？"
        preview = origin.preview_discovery(request)
        job = origin.run_discovery({**request, "authorized": True, "blueprint_fingerprint": preview["fingerprint"],
                                    "idempotency_key": "portable"})["job"]
        original = completed(origin, job["job_id"])["run_id"]
        service = StrategyValidationService(origin)
        plan = service.preview(original, {"train_size": 12, "test_size": 5, "parameter_values": [4, 5]},
                               {"max_single_weight": .35})
        studio = completed(origin, service.submit(original, plan["plan_fingerprint"])["job_id"])["run_id"]
        result = {}
        for kind, run_id in (("strategy_study", original), ("strategy_validation", studio)):
            package = ReproductionService(origin).export(run_id, include_data=True, data_scope="workflow")
            body = json.loads(package["archive"])
            result[kind] = {"inputs": body["inputs"], "expected": body["expected"], "package": package}
        return json.loads(json.dumps(result))
    finally:
        origin.close()


def test_rebuilds_paper_account_and_frozen_validation_in_empty_directory(publications, tmp_path):
    clean = SimpleNamespace(root=tmp_path)
    for kind, archive in publications.items():
        inputs = archive["inputs"]
        portable_strategy.validate_run(inputs, kind)
        actual = portable_strategy.replay_run(clean, inputs, kind)
        assert stable_digest(actual) == stable_digest(archive["expected"])
        if kind == "strategy_study":
            assert actual["strategy_ledger"]["fills"]
            assert actual["strategy_benchmark"]["marks"] and actual["strategy_decisions"]
        else:
            assert actual["strategy_portfolio"]["account"]["holdings"]
            assert len(actual["strategy_walkforward"]["windows"]) == 3
            assert actual["strategy_validation_plan"]["options"]["parameter_values"] == [4, 5]
    assert list(tmp_path.iterdir()) == []


def test_imported_studio_returns_per_product_results_without_creating_jobs(publications, tmp_path):
    target = ResearchApplicationService(tmp_path / "target", allow_model_calls=False)
    try:
        importer = ReproductionService(target)
        for archive in publications.values():
            package = archive["package"]
            importer.restore(package)
            receipt = importer.recompute(package["package_id"])
            assert receipt["status"] == "REPRODUCED", receipt
            for name in archive["expected"]:
                product = receipt["products"][name]
                assert product["status"] == "REPRODUCED" and product["fields_checked"] > 0
                assert product["difference_count"] == 0
        assert target.list_jobs()["total"] == 0
        assert not target.snapshots.list_snapshots()
    finally:
        target.close()


def test_panel_binding_source_withdrawal_and_runtime_version_fail_closed(publications, tmp_path, monkeypatch):
    frozen = publications["strategy_study"]["inputs"]["source"]
    clean = SimpleNamespace(root=tmp_path)
    missing = copy.deepcopy(frozen)
    missing["data"]["attachments"] = []
    with pytest.raises(ValueError, match="附件"):
        portable_strategy.validate_source(missing)
    incompatible = {**frozen, "runner_version": "strategy-runner.experimental"}
    with pytest.raises(PortableUnavailable, match="STRATEGY_VERSION_MISMATCH"):
        portable_strategy.replay_source(clean, incompatible)
    assert frozen["knowledge_items"]
    background = {ref["repository"] for ref in portable_strategy._knowledge_refs(frozen["knowledge_items"])}
    strategy = {ref["repository"] for ref in frozen["source_refs"]}
    withdrawn = next(iter(background - strategy))
    monkeypatch.setattr(portable_strategy, "source_policy",
                        lambda root, refs: {"blocked": [r["path"] for r in refs if r["repository"] == withdrawn]})
    with pytest.raises(PortableUnavailable, match="SOURCE_WITHDRAWN"):
        portable_strategy.replay_source(clean, frozen)


def test_studio_plan_is_bound_to_original_scope_and_ledger(publications, tmp_path):
    material = publications["strategy_validation"]["inputs"]
    clean = SimpleNamespace(root=tmp_path)
    for altered in ({**material, "original_scope": "unrelated"},
                    {**material, "plan": {**material["plan"], "original_ledger": "0" * 64}}):
        with pytest.raises(ValueError, match="原策略"):
            portable_strategy.replay_run(clean, altered, "strategy_validation")

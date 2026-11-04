from finahinking.research.application_service import ResearchApplicationService


def test_discovery_requires_explicit_authorization_and_preview(tmp_path):
    app = ResearchApplicationService(tmp_path)
    values = {"question": "价格动量能解释收益吗？", "profile": "balanced"}
    preview = app.preview_discovery(values)
    assert preview["status"] == "NEEDS_DATA"
    assert not app.list_jobs()["jobs"]


def test_preview_changes_and_duplicate_submissions_are_fenced(tmp_path, monkeypatch):
    import pytest

    from finahinking.research.asset_contracts import MethodRouteRequest
    from tests.research.test_strategy_worker import completed

    app = ResearchApplicationService(tmp_path, allow_model_calls=False)
    try:
        sample = app.create_strategy_sample()
        values = {key: sample[key] for key in ("contract", "attachment_refs")}
        values["question"] = "等权组合策略能降低单一标的集中吗？"
        preview = app.preview_discovery(values)
        assert preview["status"] == "READY", preview
        submit = {**values, "authorized": True, "blueprint_fingerprint": preview["fingerprint"],
                  "idempotency_key": "discovery-1"}
        with pytest.raises(ValueError, match="授权"):
            app.run_discovery({**submit, "authorized": False})
        with pytest.raises(ValueError, match="重新预览"):
            app.run_discovery({**submit, "profile": "defensive"})
        assert app.list_jobs()["total"] == 0
        first = app.run_discovery(submit)["job"]
        assert app.run_discovery(submit)["job"]["job_id"] == first["job_id"]
        app.close()
        app = ResearchApplicationService(tmp_path, allow_model_calls=False)
        app.start()
        job = completed(app, first["job_id"])
        proof = job["discovery"]
        assert proof["blueprint"]["fingerprint"] == preview["fingerprint"]
        assert {r["role"] for r in proof["roles"]} == {"fundamentals", "technical", "news", "learning", "sentiment"}
        assert "sentiment" in proof["unresolved_views"]
        assert "fundamentals" not in proof["unresolved_views"]
        assert all(role["evidence_refs"] == [role["knowledge"]["ref"]] for role in proof["roles"])
        assert job["strategy_study"]["result"]["ledger"]["fills"]
        exported = app.export_method_handoff(job["job_id"], MethodRouteRequest(purposes=("backtest",)))
        assert exported["package"]["context"]["knowledge_background"]["blueprint_ref"] == preview["fingerprint"]
        assert exported["package"]["envelope"]["tool_names"] == []
        monkeypatch.setattr(app, "preview_discovery", lambda _values: {**preview, "fingerprint": "b" * 64})
        assert app.get_method_handoff(job["job_id"], exported["handoff_ref"])["handoff_ref"] == exported["handoff_ref"]
        with pytest.raises(ValueError, match="重新预览"):
            app.accept_method_handoff(job["job_id"], exported["handoff_ref"], None)
    finally:
        app.close()


def test_optional_provider_is_explicit_and_preserves_frozen_inputs(tmp_path):
    import pytest

    from finahinking.research.provider_config import ProviderConfig, ProviderConfigStore
    from finahinking.research.provider_status import ProviderCredentialRef

    configs = ProviderConfigStore()
    config = ProviderConfig("desk", "openai_compatible", "model-1", endpoint="https://models.example.com/v1",
                            credential_ref=ProviderCredentialRef("desk", env_var="FINATHINK_DISCOVERY_TEST_KEY"))
    configs.save(config)
    app = ResearchApplicationService(tmp_path, provider_configs=configs)
    try:
        sample = app.create_strategy_sample()
        values = {key: sample[key] for key in ("contract", "attachment_refs")}
        values.update(question="等权组合能降低集中吗？", model_config_ref="desk")
        preview = app.preview_discovery(values)
        submit = {**values, "authorized": True, "blueprint_fingerprint": preview["fingerprint"],
                  "idempotency_key": "provider-discovery"}
        with pytest.raises(ValueError, match="授权模型"):
            app.run_discovery(submit)
        configs.remove("desk")
        configs.save(ProviderConfig("desk", "openai_compatible", "model-2", endpoint=config.endpoint,
                                    credential_ref=config.credential_ref))
        with pytest.raises(ValueError, match="重新预览"):
            app.run_discovery({**submit, "allow_model_calls": True})
        assert app.list_jobs()["total"] == 0
        current = app.preview_discovery(values)
        job = app.run_discovery({**submit, "allow_model_calls": True,
                                 "blueprint_fingerprint": current["fingerprint"]})["job"]
        assert job["model_config_ref"] == "desk" and job["budget"]["max_attempts"] == 1
        request = app._request(app.queue.get(job["job_id"]))
        assert "models.example.com" not in str(request)
        assert request.research_plan.validation_spec["discovery"]["blueprint"]["fingerprint"] == current["fingerprint"]
    finally:
        app.close()


def test_grounded_analyst_only_receives_its_role_knowledge():
    from finahinking.research.asset_catalog import ResearchAssetCatalog
    from finahinking.research.discovery_planner import open_knowledge, prepare_discovery
    from finahinking.research.discovery_runtime import DiscoveryDriver
    from tests.research.test_grounded_driver import setup_driver

    request, driver, peer = setup_driver()
    plan = prepare_discovery("等权组合风险如何？", "balanced", request.snapshot_ref, panel_ref="a" * 64,
                             available_fields=["close", "in_universe", "is_session"]).to_dict()
    with open_knowledge(ResearchAssetCatalog.packaged()) as index:
        driver.discovery = DiscoveryDriver(request, plan, index)
    result = driver.propose(request, {"analyst_role": "technical"})
    assert result.reports and result.reports[0].status == "READY"
    messages = peer.calls[0]["payload"]["messages"]
    from json import loads

    payload = loads(messages[-1]["content"])
    assert payload["knowledge_background"]["role"] == "technical"
    assert payload["knowledge_background"]["method"]["ids"] == plan["method_refs"]
    assert all(ref.startswith("evidence:") for ref in result.reports[0].evidence_refs)


def test_cancelled_discovery_cannot_publish_or_export_a_handoff(tmp_path):
    import pytest

    from finahinking.research.asset_contracts import MethodRouteRequest

    app = ResearchApplicationService(tmp_path, allow_model_calls=False)
    try:
        sample = app.create_strategy_sample()
        values = {key: sample[key] for key in ("contract", "attachment_refs")}
        values["question"] = "等权组合风险如何？"
        preview = app.preview_discovery(values)
        job = app.run_discovery({**values, "authorized": True, "blueprint_fingerprint": preview["fingerprint"],
                                 "idempotency_key": "cancel-discovery"})["job"]
        app.cancel(job["job_id"])
        cancelled = app.get_job(job["job_id"])
        assert cancelled["status"] == "cancelled" and cancelled["report_status"] == "UNAVAILABLE"
        with pytest.raises(ValueError, match="需要已完成报告"):
            app.export_method_handoff(job["job_id"], MethodRouteRequest(purposes=("backtest",)))
    finally:
        app.close()

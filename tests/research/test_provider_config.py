from __future__ import annotations

import json

import pytest

from finahinking.research.provider_config import ProviderConfig, ProviderConfigStore
from finahinking.research.provider_status import ProviderCredentialRef
from finahinking.research.providers import (
    ProviderCapabilities,
    ProviderRegistry,
    load_provider_config,
    load_provider_config_from_mapping,
    resolve_provider_selection,
)


def test_provider_precedence_and_secret_reference_redaction() -> None:
    selection = resolve_provider_selection(defaults={"provider": "offline", "model": "fixture", "role_models": {"technical": "fixture"}}, local_config={"provider": "local", "model": "local-v1"}, environment={"FINAHINKING_PROVIDER": "env", "FINAHINKING_MODEL": "env-v1", "FINAHINKING_API_KEY_REF": "keychain://finathink"}, cli_ui={"model": "cli-v1"}, run_override={"role_models": {"technical": "run-v1"}})
    assert selection.provider == "env"
    assert selection.model == "cli-v1"
    assert selection.role_models["technical"] == "run-v1"
    assert "keychain://finathink" not in json.dumps(selection.redacted())


def test_example_provider_config_is_local_and_schema_checked() -> None:
    payload = load_provider_config("docs/research-providers.example.json")
    assert payload["defaults"]["provider"] == "offline"
    assert all("api_key" not in json.dumps(item) for item in payload["providers"])
    with pytest.raises((TypeError, ValueError), match="providers"):
        load_provider_config_from_mapping({"defaults": {}})


def test_provider_registry_does_not_autofallback_after_capability_error() -> None:
    registry = ProviderRegistry()
    registry.register("offline", type("Adapter", (), {"capabilities": lambda self: ProviderCapabilities(True, False, False, 100, True), "invoke": lambda self, envelope: None})())
    selection = resolve_provider_selection(defaults={"provider": "offline", "model": "fixture"})
    with pytest.raises(ValueError, match="tool_calling"):
        registry.check(selection, ("tool_calling",))


def _config(provider_id: str = "user-compatible") -> ProviderConfig:
    return ProviderConfig(
        provider_id=provider_id,
        adapter_kind="openai_compatible",
        model="user-model",
        endpoint="https://provider.example.net/v1",
        credential_ref=ProviderCredentialRef(provider_id, env_var="FINAHINK_USER_API_KEY"),
        enabled=True,
        role_models={"technical": "user-model"},
    )


def test_provider_config_store_persists_restart_and_redacts_secret_material(tmp_path) -> None:
    path = tmp_path / "providers.json"
    store = ProviderConfigStore(path)
    store.save(_config())
    listed = store.list()
    assert listed[0]["provider_id"] == "user-compatible"
    assert listed[0]["credential_ref"] == {"env_var": "FINAHINK_USER_API_KEY"}
    assert listed[0]["endpoint_configured"] is True
    assert "https://provider.example.net" not in json.dumps(listed)
    assert '"api_key"' not in path.read_text(encoding="utf-8").lower()

    reopened = ProviderConfigStore(path)
    loaded = reopened.get("user-compatible")
    assert loaded is not None
    assert loaded.role_models["technical"] == "user-model"
    assert reopened.get_model_for_role("user-compatible", "technical") == "user-model"


def test_provider_config_store_rejects_duplicate_and_unsafe_endpoint(tmp_path) -> None:
    store = ProviderConfigStore(tmp_path / "providers.json")
    store.save(_config())
    with pytest.raises(ValueError, match="already exists"):
        store.save(_config())
    with pytest.raises(ValueError, match="endpoint"):
        ProviderConfig(
            provider_id="bad",
            adapter_kind="openai_compatible",
            model="user-model",
            endpoint="http://user:password@example.test/v1?api_key=secret",
            credential_ref=ProviderCredentialRef("bad", env_var="SAFE_KEY"),
            enabled=True,
        )


def test_provider_config_allow_lists_adapter_model_and_fields() -> None:
    with pytest.raises(ValueError, match="adapter"):
        ProviderConfig("x", "shell", "user-model")
    with pytest.raises(ValueError, match="model"):
        ProviderConfig("x", "offline", "unapproved-model")
    with pytest.raises(ValueError, match="unsupported"):
        ProviderConfig.from_mapping({"provider_id": "x", "adapter_kind": "offline", "model": "fixture-v1", "prompt": "secret"})


def test_user_compatible_model_identifiers_are_not_a_stale_vendor_catalog():
    from finahinking.research.provider_config import ProviderConfig

    config = ProviderConfig("user-desk", "openai_compatible", "my-org/research-model:2026-10", role_models={"news": "custom-news-v2"})
    assert config.model_for_role("news") == "custom-news-v2"
    for model in ("", "x" * 129, "https://acme.com/model", "model\nexecute", "api_key=value", "/private/model"):
        with pytest.raises(ValueError):
            ProviderConfig("user-desk", "openai_compatible", model)


@pytest.mark.parametrize("host", ["foo.internal", "foo.intranet", "foo.lan", "foo.home", "foo.test", "foo.invalid", "foo.example", "singlelabel"])
def test_provider_config_rejects_special_use_dns_hosts(host: str) -> None:
    with pytest.raises(ValueError, match="endpoint"):
        ProviderConfig("bad-host", "openai_compatible", "user-model", endpoint=f"https://{host}/v1")


def test_provider_config_remove_persists_across_restart(tmp_path) -> None:
    path = tmp_path / "providers.json"
    store = ProviderConfigStore(path)
    store.save(_config())
    store.remove("user-compatible")
    assert store.list() == ()
    assert ProviderConfigStore(path).list() == ()

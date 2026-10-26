"""Provider capability contracts without provider SDKs or secret handling."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from .contracts import ProviderSelection, stable_digest, to_jsonable

if TYPE_CHECKING:
    from .context_bundle import ResearchContextBundle


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    structured_output: bool
    tool_calling: bool
    parallel_roles: bool
    max_context: int
    offline: bool

    def __post_init__(self) -> None:
        if self.max_context <= 0:
            raise ValueError("max_context must be positive")


@dataclass(frozen=True, slots=True)
class ModelEnvelope:
    """A secret-free request envelope.

    Legacy probes use digests. Grounded runs additionally carry a private,
    authorized context projection, never credentials or execution hooks.
    """

    request_id: str
    role: str
    input_digest: str
    context_digest: str
    tool_names: tuple[str, ...] = ()
    instructions: str = "Return a structured research observation for review."
    research_context: ResearchContextBundle | None = None

    def __post_init__(self) -> None:
        for name, value in (("request_id", self.request_id), ("role", self.role), ("input_digest", self.input_digest), ("context_digest", self.context_digest)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        object.__setattr__(self, "tool_names", tuple(sorted(set(self.tool_names))))
        object.__setattr__(self, "instructions", self.instructions.strip())
        if self.research_context is not None:
            from .context_bundle import ResearchContextBundle

            if not isinstance(self.research_context, ResearchContextBundle):
                raise TypeError("research context must be a validated bundle")
            payload = self.research_context.to_dict()
            if payload["run_id"] != self.request_id or payload["role"] != self.role or self.context_digest != self.research_context.fingerprint:
                raise ValueError("research context envelope binding mismatch")


@dataclass(frozen=True, slots=True)
class ModelResponse:
    provider: str
    model: str
    content: Mapping[str, Any]
    finish_reason: str = "stop"
    usage: Mapping[str, int] | None = None

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("provider and model must be non-empty")
        object.__setattr__(self, "content", dict(self.content))


class ProviderAdapter(Protocol):
    def capabilities(self) -> ProviderCapabilities: ...

    def invoke(self, envelope: ModelEnvelope) -> ModelResponse: ...


class ProviderRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, ProviderAdapter] = {}

    def register(self, name: str, adapter: ProviderAdapter) -> None:
        normalized = name.strip()
        if not normalized:
            raise ValueError("provider name must be non-empty")
        if not hasattr(adapter, "capabilities") or not hasattr(adapter, "invoke"):
            raise TypeError("provider adapter must expose capabilities and invoke")
        self._adapters[normalized] = adapter

    def resolve(self, selection: ProviderSelection) -> ProviderAdapter:
        try:
            return self._adapters[selection.provider]
        except KeyError as exc:
            raise LookupError(f"provider {selection.provider!r} is not registered") from exc

    def check(self, selection: ProviderSelection, required_capabilities: tuple[str, ...]) -> ProviderCapabilities:
        if not selection.model.strip():
            raise ValueError("model must be configured")
        adapter = self.resolve(selection)
        capabilities = adapter.capabilities()
        for capability_name in required_capabilities:
            if not hasattr(capabilities, capability_name):
                raise ValueError(f"unknown provider capability: {capability_name}")
            if not getattr(capabilities, capability_name):
                raise ValueError(f"provider lacks capability: {capability_name}")
        return capabilities

    def redacted_selection(self, selection: ProviderSelection) -> dict[str, Any]:
        return to_jsonable(selection.redacted())


def envelope_digest(envelope: ModelEnvelope) -> str:
    return stable_digest(envelope)


def load_provider_config_from_mapping(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("defaults"), Mapping):
        raise TypeError("provider config requires a defaults mapping")
    providers = payload.get("providers")
    if not isinstance(providers, list):
        raise TypeError("provider config requires a providers list")
    allowed_provider_keys = {
        "name",
        "model",
        "capabilities",
        "enabled",
        "offline",
        "credential_ref",
    }
    normalized: list[dict[str, Any]] = []
    for item in providers:
        if not isinstance(item, Mapping) or not str(item.get("name", "")).strip():
            raise ValueError("each provider requires a name")
        if set(item) - allowed_provider_keys:
            raise ValueError("provider config contains unsupported or secret fields")
        if item.get("credential_ref") is not None:
            credential_ref = item["credential_ref"]
            if not isinstance(credential_ref, Mapping):
                raise ValueError("credential_ref must be a mapping")
            if set(credential_ref) - {"env_var", "keychain_label"}:
                raise ValueError("credential_ref contains unsupported or secret fields")
        normalized.append({key: item[key] for key in sorted(item)})
    defaults = dict(payload["defaults"])
    if set(defaults) - {"provider", "model", "role_models", "capabilities"}:
        raise ValueError("defaults contains unsupported or secret fields")
    return {"defaults": defaults, "providers": normalized}


def load_provider_config(path: str | Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"provider config cannot be read: {exc}") from exc
    return load_provider_config_from_mapping(payload)


def resolve_provider_selection(
    *,
    defaults: Mapping[str, Any],
    local_config: Mapping[str, Any] | None = None,
    environment: Mapping[str, Any] | None = None,
    cli_ui: Mapping[str, Any] | None = None,
    run_override: Mapping[str, Any] | None = None,
) -> ProviderSelection:
    """Merge non-secret provider settings in documented precedence order."""

    merged: dict[str, Any] = {"provider": "offline", "model": "fixture-v1", "role_models": {}, "capabilities": ()}
    role_models: dict[str, str] = {}
    for layer in (defaults, local_config or {}):
        if not isinstance(layer, Mapping):
            raise TypeError("provider config layers must be mappings")
        merged.update({key: layer[key] for key in ("provider", "model", "capabilities") if key in layer})
        role_models.update({str(key): str(value) for key, value in dict(layer.get("role_models", {})).items()})
    env = environment or {}
    if "FINAHINKING_PROVIDER" in env:
        merged["provider"] = str(env["FINAHINKING_PROVIDER"])
    if "FINAHINKING_MODEL" in env:
        merged["model"] = str(env["FINAHINKING_MODEL"])
    for layer in (cli_ui or {}, run_override or {}):
        if not isinstance(layer, Mapping):
            raise TypeError("provider config layers must be mappings")
        merged.update({key: layer[key] for key in ("provider", "model", "capabilities") if key in layer})
        role_models.update({str(key): str(value) for key, value in dict(layer.get("role_models", {})).items()})
    return ProviderSelection(
        provider=str(merged["provider"]),
        model=str(merged["model"]),
        role_models=role_models,
        capabilities=tuple(str(item) for item in merged.get("capabilities", ())),
        metadata={"config_precedence": "defaults>local>environment>cli_ui>run_override"},
    )

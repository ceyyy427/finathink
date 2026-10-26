"""User-owned model settings; persistence contains credential references only.

Saving these settings never resolves credentials or invokes a provider.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

from finahinking.data.connection_settings import KeychainDataCredentialStore
from finahinking.data.user_api_contracts import DataSourceCredentialRef

from .provider_status import ProviderCredentialRef

# Adapter admission and offline fixtures; remote model identifiers are validated
# syntactically, not treated as a vendor availability catalog.
ALLOWED_ADAPTER_MODELS = MappingProxyType({
    "offline": frozenset({"fixture-v1"}),
    "openai_compatible": frozenset({"user-model", "gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-5", "gpt-5-mini"}),
    "deepseek_compatible": frozenset({"deepseek-chat", "deepseek-reasoner"}),
})
ALLOWED_ROLES = frozenset({"technical", "fundamentals", "news", "sentiment", "learning", "factor", "risk", "portfolio", "research", "quant", "paper_decision"})
_FIELDS = frozenset({"provider_id", "adapter_kind", "model", "endpoint", "credential_ref", "enabled", "role_models"})
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SENSITIVE = re.compile(r"api[-_]?key|secret|token|password|credential|prompt|raw[-_]?provider", re.IGNORECASE)
_SPECIAL_USE_SUFFIXES = frozenset({"internal", "intranet", "lan", "home", "test", "invalid", "example", "localhost", "local", "onion", "alt"})


def _model_allowed(adapter_kind, model):
    if type(model) is not str:
        return False
    if adapter_kind == "offline":
        return model in ALLOWED_ADAPTER_MODELS["offline"]
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", model) and "://" not in model and ".." not in model and not _SENSITIVE.search(model))


def _endpoint(value: str | None) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not value or len(value) > 2048 or any(char.isspace() for char in value) or "\\" in value:
        raise ValueError("provider endpoint is invalid")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError("provider endpoint is invalid") from None
    if parsed.scheme != "https" or not host or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment or port not in {None, 443}:
        raise ValueError("provider endpoint must be a credential-free HTTPS address")
    normalized_host = host.rstrip(".").lower()
    if normalized_host in _SPECIAL_USE_SUFFIXES or any(normalized_host.endswith("." + suffix) for suffix in _SPECIAL_USE_SUFFIXES) or _SENSITIVE.search(parsed.path):
        raise ValueError("provider endpoint is invalid")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", host) or "." not in host or ".." in host:
            raise ValueError("provider endpoint is invalid") from None
    else:
        if not address.is_global:
            raise ValueError("provider endpoint must target a public host")
    return value


@dataclass(frozen=True, slots=True, repr=False)
class ProviderConfig:
    provider_id: str
    adapter_kind: str
    model: str
    endpoint: str | None = None
    credential_ref: ProviderCredentialRef | None = None
    enabled: bool = True
    role_models: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.provider_id) is not str or not _IDENTIFIER.fullmatch(self.provider_id) or _SENSITIVE.search(self.provider_id):
            raise ValueError("provider identifier is invalid")
        if type(self.adapter_kind) is not str or self.adapter_kind not in ALLOWED_ADAPTER_MODELS:
            raise ValueError("provider adapter is not allowed")
        if not _model_allowed(self.adapter_kind, self.model):
            raise ValueError("provider model is not allowed")
        if type(self.enabled) is not bool:
            raise TypeError("provider enabled must be boolean")
        endpoint = _endpoint(self.endpoint)
        if self.adapter_kind == "offline" and (endpoint is not None or self.credential_ref is not None):
            raise ValueError("offline provider cannot accept endpoint or credential references")
        if self.credential_ref is not None and (not isinstance(self.credential_ref, ProviderCredentialRef) or self.credential_ref.provider != self.provider_id):
            raise ValueError("credential reference provider does not match configuration")
        if not isinstance(self.role_models, Mapping):
            raise TypeError("role_models must be a mapping")
        roles = dict(self.role_models)
        if any(type(role) is not str or role not in ALLOWED_ROLES or not _model_allowed(self.adapter_kind, model) for role, model in roles.items()):
            raise ValueError("role model route is not allowed")
        object.__setattr__(self, "endpoint", endpoint)
        object.__setattr__(self, "role_models", MappingProxyType(dict(sorted(roles.items()))))

    def __repr__(self) -> str:
        return f"ProviderConfig({self.provider_id!r}, endpoint=<redacted>, credential=<reference only>)"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ProviderConfig:
        if not isinstance(value, Mapping):
            raise TypeError("provider configuration must be an object")
        if set(value) - _FIELDS:
            raise ValueError("provider configuration contains unsupported or secret fields")
        data = dict(value)
        ref = data.get("credential_ref")
        if ref is not None:
            data["credential_ref"] = ProviderCredentialRef.from_mapping(data.get("provider_id", ""), ref)
        try:
            return cls(**data)
        except TypeError:
            raise ValueError("provider configuration fields are invalid") from None

    def model_for_role(self, role: str) -> str:
        if role not in ALLOWED_ROLES:
            raise ValueError("research role is not allowed")
        return self.role_models.get(role, self.model)

    def redacted(self) -> dict[str, Any]:
        return {"provider_id": self.provider_id, "adapter_kind": self.adapter_kind, "model": self.model, "endpoint_configured": self.endpoint is not None, "credential_ref": self.credential_ref.to_dict() if self.credential_ref else None, "enabled": self.enabled, "role_models": dict(self.role_models)}

    def _stored(self) -> dict[str, Any]:
        return {**{key: value for key, value in self.redacted().items() if key != "endpoint_configured"}, "endpoint": self.endpoint}


class ProviderConfigStore:
    """Atomic local JSON settings with fail-closed restart validation."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._lock = threading.RLock()
        self._configs: dict[str, ProviderConfig] = {}
        if self._path is not None and self._path.exists():
            try:
                payload = json.loads(self._path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict) or set(payload) != {"schema_version", "providers"} or type(payload["schema_version"]) is not int or payload["schema_version"] != 1 or not isinstance(payload["providers"], list):
                    raise ValueError()
                for item in payload["providers"]:
                    config = ProviderConfig.from_mapping(item)
                    if config.provider_id in self._configs:
                        raise ValueError()
                    self._configs[config.provider_id] = config
            except (OSError, ValueError, TypeError):
                raise ValueError("provider configuration cannot be read") from None

    def _persist(self, configs: Mapping[str, ProviderConfig]) -> None:
        if self._path is None:
            return
        temporary: str | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self._path.parent, prefix=".providers-", delete=False) as stream:
                temporary = stream.name
                json.dump({"schema_version": 1, "providers": [configs[key]._stored() for key in sorted(configs)]}, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        except OSError:
            raise ValueError("provider configuration cannot be saved") from None
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)

    def save(self, config: ProviderConfig) -> None:
        if not isinstance(config, ProviderConfig):
            raise TypeError("provider configuration must be a ProviderConfig")
        with self._lock:
            if config.provider_id in self._configs:
                raise ValueError("provider already exists")
            configs = {**self._configs, config.provider_id: config}
            self._persist(configs)
            self._configs = configs

    def get(self, provider_id: str) -> ProviderConfig | None:
        with self._lock:
            return self._configs.get(provider_id)

    def list(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(self._configs[key].redacted() for key in sorted(self._configs))

    def remove(self, provider_id: str) -> None:
        with self._lock:
            if provider_id not in self._configs:
                raise KeyError("provider configuration not found")
            configs = {key: config for key, config in self._configs.items() if key != provider_id}
            self._persist(configs)
            self._configs = configs

    def get_model_for_role(self, provider_id: str, role: str) -> str:
        config = self.get(provider_id)
        if config is None:
            raise KeyError("provider configuration not found")
        if not config.enabled:
            raise ValueError("provider is disabled")
        return config.model_for_role(role)


def keychain_provider_ready(ref: ProviderCredentialRef) -> bool:
    """Reuse the OS store's sanitized presence check without resolving values."""
    if ref.keychain_label is None:
        return False
    return KeychainDataCredentialStore().has(DataSourceCredentialRef(keychain_label=ref.keychain_label))

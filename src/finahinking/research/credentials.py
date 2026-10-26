"""Secret-free credential references and provider runtime configuration.

Credential values are deliberately kept behind a tiny store interface.  The
research contracts receive only a :class:`ProviderCredentialRef`; adapters
may resolve that reference immediately before an outbound request, but the
resolved value is never part of a runtime config, selection, report, or log.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .provider_status import ProviderCredentialRef

_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_ALLOWED_CONFIG_FIELDS = frozenset({"provider", "model", "credential_ref", "capabilities"})
_ALLOWED_CAPABILITIES = frozenset(
    {
        "structured_output",
        "tool_calling",
        "parallel_roles",
        "streaming",
        "reasoning",
        "offline",
        "max_context",
    }
)


class CredentialStore(Protocol):
    """A secret store used by an adapter at the point of invocation."""

    def has(self, ref: ProviderCredentialRef) -> bool:
        """Return whether a referenced credential is configured."""

    def resolve(self, ref: ProviderCredentialRef) -> str:
        """Resolve a secret for adapter-internal use only."""


def _validate_ref(ref: ProviderCredentialRef) -> ProviderCredentialRef:
    if not isinstance(ref, ProviderCredentialRef):
        raise TypeError("credential reference must be a ProviderCredentialRef")
    if ref.env_var is None:
        raise ValueError("environment credential store requires an environment reference")
    if not _ENV_NAME.fullmatch(ref.env_var):
        raise ValueError("credential environment variable name is invalid")
    if ref.keychain_label is not None:
        raise ValueError("environment credential store does not support keychain references")
    return ref


class EnvironmentCredentialStore:
    """Read credentials only from explicitly named environment variables."""

    __slots__ = ("_environment",)

    def __init__(self, environment: Mapping[str, Any] | None = None) -> None:
        source = os.environ if environment is None else environment
        if not isinstance(source, Mapping):
            raise TypeError("environment must be a mapping")
        # Keep the mapping as a handle and perform one explicit lookup per
        # reference.  Copying os.environ would retain every unrelated secret.
        self._environment = source

    def __repr__(self) -> str:
        return "EnvironmentCredentialStore(<environment values redacted>)"

    __str__ = __repr__

    def _lookup(self, ref: ProviderCredentialRef) -> Any:
        try:
            return self._environment[ref.env_var]
        except KeyError:
            return None

    def has(self, ref: ProviderCredentialRef) -> bool:
        ref = _validate_ref(ref)
        value = self._lookup(ref)
        return isinstance(value, str) and bool(value)

    def resolve(self, ref: ProviderCredentialRef) -> str:
        ref = _validate_ref(ref)
        value = self._lookup(ref)
        if not isinstance(value, str) or not value:
            raise ValueError("credential is not configured")
        return value


class InMemoryCredentialStore:
    """Small secret store intended for tests; never a serializable contract."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[ProviderCredentialRef, str] | None = None) -> None:
        if values is None:
            values = {}
        if not isinstance(values, Mapping):
            raise TypeError("credential values must be a mapping")
        normalized: dict[ProviderCredentialRef, str] = {}
        for ref, value in values.items():
            if not isinstance(ref, ProviderCredentialRef):
                raise TypeError("in-memory credential keys must be references")
            if not isinstance(value, str) or not value:
                raise ValueError("credential values must be non-empty strings")
            normalized[ref] = value
        self._values = normalized

    def __repr__(self) -> str:
        return "InMemoryCredentialStore(<values redacted>)"

    __str__ = __repr__

    def __reduce__(self) -> tuple[object, ...]:
        raise TypeError("InMemoryCredentialStore cannot be serialized")

    def __reduce_ex__(self, protocol: int) -> tuple[object, ...]:
        raise TypeError("InMemoryCredentialStore cannot be serialized")

    def has(self, ref: ProviderCredentialRef) -> bool:
        if not isinstance(ref, ProviderCredentialRef):
            raise TypeError("credential reference must be a ProviderCredentialRef")
        return ref in self._values

    def resolve(self, ref: ProviderCredentialRef) -> str:
        if not isinstance(ref, ProviderCredentialRef):
            raise TypeError("credential reference must be a ProviderCredentialRef")
        try:
            return self._values[ref]
        except KeyError as exc:
            raise ValueError("credential is not configured") from exc


class LocalProviderCredentialStore:
    """Resolve an explicitly named environment or existing OS keychain entry."""

    def resolve(self, ref):
        if ref.env_var is not None:
            return EnvironmentCredentialStore().resolve(ref)
        from finahinking.data.connection_settings import KeychainDataCredentialStore
        from finahinking.data.user_api_contracts import DataSourceCredentialRef

        return KeychainDataCredentialStore().resolve(DataSourceCredentialRef(keychain_label=ref.keychain_label))


@dataclass(frozen=True, slots=True)
class ProviderRuntimeConfig:
    provider: str
    model: str
    credential_ref: ProviderCredentialRef
    capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise ValueError("provider must be non-empty")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("model must be non-empty")
        if not isinstance(self.credential_ref, ProviderCredentialRef):
            raise TypeError("credential_ref must be a ProviderCredentialRef")
        if not isinstance(self.capabilities, (tuple, list)):
            raise TypeError("capabilities must be a sequence of strings")
        capabilities = tuple(self.capabilities)
        if any(type(value) is not str for value in capabilities):
            raise TypeError("capabilities must contain only strings")
        capabilities = tuple(value.strip() for value in capabilities)
        if any(not value for value in capabilities):
            raise ValueError("capabilities must contain non-empty strings")
        if any(value not in _ALLOWED_CAPABILITIES for value in capabilities):
            raise ValueError("capabilities contain an unsupported name")
        if len(set(capabilities)) != len(capabilities):
            raise ValueError("capabilities must not contain duplicates")
        object.__setattr__(self, "provider", self.provider.strip())
        object.__setattr__(self, "model", self.model.strip())
        object.__setattr__(self, "capabilities", capabilities)

    def redacted(self) -> dict[str, Any]:
        """Return the only representation allowed outside an adapter."""

        return {
            "provider": self.provider,
            "model": self.model,
            "credential_ref": self.credential_ref.to_dict(),
            "capabilities": list(self.capabilities),
            "configured": True,
        }


def _credential_ref(provider: str, value: Any) -> ProviderCredentialRef:
    if value is None:
        raise ValueError("credential reference is required")
    if isinstance(value, ProviderCredentialRef):
        if value.provider != provider:
            raise ValueError("credential reference provider does not match configuration")
        return value
    if isinstance(value, Mapping):
        return ProviderCredentialRef.from_mapping(provider, value)
    raise TypeError("credential_ref must be a mapping or ProviderCredentialRef")


def normalize_provider_error(error: BaseException) -> str:
    """Map adapter/store failures to a stable message without echoing details."""

    if isinstance(error, TimeoutError):
        return "provider timeout"
    if isinstance(error, (TypeError, ValueError, KeyError)):
        return "provider malformed response"
    return "provider request failed"


def build_provider_runtime(
    config: Mapping[str, Any], store: CredentialStore
) -> ProviderRuntimeConfig:
    """Validate a provider config and check readiness without network access."""

    if not isinstance(config, Mapping):
        raise TypeError("provider config must be a mapping")
    unsupported = set(config) - _ALLOWED_CONFIG_FIELDS
    if unsupported:
        names = ", ".join(sorted(str(key) for key in unsupported))
        raise ValueError(f"unsupported or secret configuration fields: {names}")
    if not hasattr(store, "has") or not hasattr(store, "resolve"):
        raise TypeError("store must implement has and resolve")
    provider = config.get("provider")
    model = config.get("model")
    if not isinstance(provider, str) or not provider.strip():
        raise ValueError("provider must be non-empty")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model must be non-empty")
    provider = provider.strip()
    ref = _credential_ref(provider, config.get("credential_ref"))
    try:
        configured = store.has(ref)
    except Exception as exc:  # noqa: BLE001 - normalize every backend detail
        raise ValueError(normalize_provider_error(exc)) from None
    if not isinstance(configured, bool):
        raise TypeError("credential store returned an invalid readiness state")
    if not configured:
        raise ValueError("credential is not configured")
    capabilities = config.get("capabilities", ())
    return ProviderRuntimeConfig(provider, model, ref, capabilities)

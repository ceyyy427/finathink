"""Local-only data connection settings and credential boundaries."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from .user_api_contracts import DataConnectionConfig, DataSourceCredentialRef


class DataCredentialStore(Protocol):
    def has(self, ref: DataSourceCredentialRef) -> bool: ...
    def resolve(self, ref: DataSourceCredentialRef) -> str: ...
    def put(self, ref: DataSourceCredentialRef, value: str) -> None: ...
    def remove(self, ref: DataSourceCredentialRef) -> None: ...


class KeychainBackend(Protocol):
    """Minimal backend used by the credential store and its tests."""

    def get(self, label: str) -> str | None: ...
    def put(self, label: str, value: str) -> None: ...


class KeychainError(RuntimeError):
    """A generic, secret-free keychain operation failure."""


class DataConnectionPersistenceError(ValueError):
    """A sanitized, fail-closed connection persistence failure."""


class MacOSKeychainBackend:
    """Use the macOS keychain through the argument-array ``security`` CLI.

    The command never uses a shell and command output is intentionally not
    included in exceptions.  Tests inject a ``KeychainBackend`` instead of
    touching a user's keychain.
    """

    __slots__ = ("_account", "_executable", "_timeout")

    def __init__(self, *, executable: str = "/usr/bin/security", account: str = "finathink-data", timeout: float = 5.0) -> None:
        self._executable = executable
        self._account = account
        self._timeout = timeout

    def _run(self, args: list[str], *, input_data: str | None = None) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [self._executable, *args],
                check=False,
                capture_output=True,
                text=True,
                shell=False,
                timeout=self._timeout,
                input=input_data,
            )
        except (OSError, subprocess.SubprocessError):
            raise KeychainError("system credential storage is unavailable") from None

    def has(self, label: str) -> bool:
        return self._run(["find-generic-password", "-a", self._account, "-s", label]).returncode == 0

    def get(self, label: str) -> str | None:
        result = self._run(["find-generic-password", "-a", self._account, "-s", label, "-w"])
        if result.returncode != 0:
            return None
        value = result.stdout.strip()
        return value or None

    def put(self, label: str, value: str) -> None:
        result = self._run([
            "add-generic-password",
            "-a",
            self._account,
            "-s",
            label,
            "-U",
            "-w",
        ], input_data=value)
        if result.returncode != 0:
            raise KeychainError("system credential storage rejected the value")

    def remove(self, label: str) -> None:
        result = self._run(["delete-generic-password", "-a", self._account, "-s", label])
        if result.returncode not in {0, 44}:
            raise KeychainError("system credential storage rejected the removal")


class KeychainDataCredentialStore:
    """Data credential store backed by the OS keychain.

    Only credential references cross the settings boundary.  ``backend`` is
    injectable so tests can prove the boundary without writing a real key.
    """

    __slots__ = ("_backend",)

    def __init__(self, backend: KeychainBackend | None = None) -> None:
        self._backend = backend or MacOSKeychainBackend()

    def has(self, ref: DataSourceCredentialRef) -> bool:
        if not isinstance(ref, DataSourceCredentialRef) or ref.keychain_label is None:
            return False
        try:
            presence_check = getattr(self._backend, "has", None)
            if presence_check is not None:
                return presence_check(ref.keychain_label)
            return self._backend.get(ref.keychain_label) is not None
        except KeychainError:
            return False

    def resolve(self, ref: DataSourceCredentialRef) -> str:
        if not isinstance(ref, DataSourceCredentialRef) or ref.keychain_label is None:
            raise ValueError("data credential is not configured")
        try:
            value = self._backend.get(ref.keychain_label)
        except KeychainError:
            raise ValueError("data credential is not configured") from None
        if not value:
            raise ValueError("data credential is not configured")
        return value

    def put(self, ref: DataSourceCredentialRef, value: str) -> None:
        if not isinstance(ref, DataSourceCredentialRef) or ref.keychain_label is None:
            raise ValueError("keychain credential reference is required")
        if not isinstance(value, str) or not value:
            raise ValueError("data credential must be non-empty")
        try:
            self._backend.put(ref.keychain_label, value)
        except KeychainError:
            raise ValueError("system credential storage is unavailable") from None

    def remove(self, ref: DataSourceCredentialRef) -> None:
        if not isinstance(ref, DataSourceCredentialRef) or ref.keychain_label is None:
            return
        try:
            self._backend.remove(ref.keychain_label)
        except KeychainError:
            raise ValueError("system credential storage is unavailable") from None


class InMemoryDataCredentialStore:
    """Process-local test/UI store; values cannot be serialized by design."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[object, str] | None = None) -> None:
        self._values: dict[object, str] = {}
        for key, value in (values or {}).items():
            if not isinstance(value, str) or not value:
                raise ValueError("data credential values must be non-empty strings")
            self._values[key] = value

    def __repr__(self) -> str:
        return "InMemoryDataCredentialStore(<values redacted>)"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("data credentials cannot be serialized")

    def put(self, ref: DataSourceCredentialRef, value: str) -> None:
        if not isinstance(ref, DataSourceCredentialRef) or not isinstance(value, str) or not value:
            raise ValueError("invalid data credential")
        self._values[ref] = value

    def has(self, ref: DataSourceCredentialRef) -> bool:
        if not isinstance(ref, DataSourceCredentialRef):
            return False
        aliases = [ref]
        if ref.env_var is not None:
            aliases.append(ref.env_var)
        if ref.keychain_label is not None:
            aliases.append(ref.keychain_label)
        return any(alias in self._values for alias in aliases)

    def resolve(self, ref: DataSourceCredentialRef) -> str:
        if not self.has(ref):
            raise ValueError("data credential is not configured")
        if ref in self._values:
            return self._values[ref]
        alias = ref.env_var or ref.keychain_label
        if alias is not None and alias in self._values:
            return self._values[alias]
        raise ValueError("data credential is not configured")

    def remove(self, ref: DataSourceCredentialRef) -> None:
        if not isinstance(ref, DataSourceCredentialRef):
            return
        self._values.pop(ref, None)
        if ref.env_var is not None:
            self._values.pop(ref.env_var, None)
        if ref.keychain_label is not None:
            self._values.pop(ref.keychain_label, None)


class EnvironmentDataCredentialStore:
    """Read only the explicit data environment variable named by a reference."""

    def __init__(self, environment: Mapping[str, str] | None = None) -> None:
        self._environment = os.environ if environment is None else environment

    def has(self, ref: DataSourceCredentialRef) -> bool:
        if ref.env_var is None:
            return False
        return bool(self._environment.get(ref.env_var, ""))

    def resolve(self, ref: DataSourceCredentialRef) -> str:
        if not self.has(ref):
            raise ValueError("data credential is not configured")
        assert ref.env_var is not None
        return str(self._environment[ref.env_var])

    def put(self, ref: DataSourceCredentialRef, value: str) -> None:
        del ref, value
        raise ValueError("environment credentials are read-only")

    def remove(self, ref: DataSourceCredentialRef) -> None:
        del ref
        raise ValueError("environment credentials are read-only")


class DataConnectionSettingsStore:
    """Keep redacted connection config in process memory; secrets use keychain."""

    def __init__(self, credential_store: DataCredentialStore | None = None) -> None:
        self.credentials = credential_store or KeychainDataCredentialStore()
        self._configs: dict[str, DataConnectionConfig] = {}

    def save(self, config: DataConnectionConfig, *, credential_value: str | None = None) -> None:
        if credential_value is not None:
            if config.credential_ref is None:
                raise ValueError("credential value supplied for an unauthenticated connection")
            self.credentials.put(config.credential_ref, credential_value)
        self._configs[config.connection_id] = config

    def get(self, connection_id: str) -> DataConnectionConfig:
        try:
            return self._configs[connection_id]
        except KeyError as exc:
            raise KeyError("data connection not found") from exc

    def list(self) -> tuple[dict[str, object], ...]:
        return tuple(
            {**config.redacted(), "credential_configured": bool(config.credential_ref and self.credentials.has(config.credential_ref))}
            for config in sorted(self._configs.values(), key=lambda item: item.connection_id)
        )

    def remove(self, connection_id: str) -> None:
        self._configs.pop(connection_id, None)


class PersistentDataConnectionStore(DataConnectionSettingsStore):
    """A local connection registry that persists only non-secret settings.

    The JSON file contains the connection contract and an opaque credential
    reference. Credential values are always delegated to ``credential_store``
    and never enter the file, repr, logs, or serialized status payloads.
    Loading this store is side-effect free: it does not resolve credentials or
    contact a remote endpoint.
    """

    def __init__(self, path: str | os.PathLike[str], *, credential_store: DataCredentialStore | None = None) -> None:
        super().__init__(credential_store=credential_store)
        self.path = Path(path).expanduser()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise DataConnectionPersistenceError("data connection persistence is unavailable") from None
        self._load()

    @staticmethod
    def _serialize(config: DataConnectionConfig) -> dict[str, object]:
        ref = config.credential_ref.to_dict() if config.credential_ref is not None else None
        # The endpoint is part of the user-owned connection contract, but the
        # redacted view used by reports/UI intentionally omits it.
        return {
            "connection_id": config.connection_id,
            "display_name": config.display_name,
            "base_url": config.base_url,
            "credential_ref": ref,
            "auth_mode": config.auth_mode,
            "field_mapping": dict(config.field_mapping),
            "records_path": config.records_path,
            "auth_header": config.auth_header,
            "source_declaration": config.source_declaration,
            "allow_local": config.allow_local,
        }

    @staticmethod
    def _deserialize(payload: Mapping[str, object]) -> DataConnectionConfig:
        expected = {
            "connection_id", "display_name", "base_url", "credential_ref", "auth_mode",
            "field_mapping", "records_path", "auth_header", "source_declaration", "allow_local",
        }
        if set(payload) != expected:
            raise ValueError("persisted connection fields are invalid")
        required_text = ("connection_id", "display_name", "base_url", "auth_mode", "source_declaration")
        if any(not isinstance(payload.get(key), str) for key in required_text):
            raise TypeError("persisted connection text fields are invalid")
        if not isinstance(payload.get("allow_local"), bool):
            raise TypeError("persisted allow_local must be a bool")
        raw_ref = payload.get("credential_ref")
        ref: DataSourceCredentialRef | None = None
        if raw_ref is not None:
            if not isinstance(raw_ref, Mapping):
                raise ValueError("persisted credential reference is invalid")
            if set(raw_ref) not in ({"env_var"}, {"keychain_label"}):
                raise ValueError("persisted credential reference is invalid")
            if any(not isinstance(value, str) for value in raw_ref.values()):
                raise TypeError("persisted credential reference is invalid")
            ref = DataSourceCredentialRef(
                env_var=raw_ref.get("env_var") if "env_var" in raw_ref else None,
                keychain_label=raw_ref.get("keychain_label") if "keychain_label" in raw_ref else None,
            )
        mapping = payload.get("field_mapping", {})
        if not isinstance(mapping, Mapping):
            raise TypeError("persisted field mapping is invalid")
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in mapping.items()):
            raise TypeError("persisted field mapping is invalid")
        records_path = payload.get("records_path")
        auth_header = payload.get("auth_header")
        if records_path is not None and not isinstance(records_path, str):
            raise TypeError("persisted records_path is invalid")
        if auth_header is not None and not isinstance(auth_header, str):
            raise TypeError("persisted auth_header is invalid")
        return DataConnectionConfig(
            connection_id=payload["connection_id"],  # type: ignore[arg-type]
            display_name=payload["display_name"],  # type: ignore[arg-type]
            base_url=payload["base_url"],  # type: ignore[arg-type]
            credential_ref=ref,
            auth_mode=payload["auth_mode"],  # type: ignore[arg-type]
            field_mapping=dict(mapping),
            records_path=records_path,
            auth_header=auth_header,
            source_declaration=payload["source_declaration"],  # type: ignore[arg-type]
            allow_local=payload["allow_local"],  # type: ignore[arg-type]
        )

    def _load(self) -> None:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError:
            raise DataConnectionPersistenceError("data connection persistence is unavailable") from None
        if not raw.strip():
            return
        try:
            payload = json.loads(raw)
            if not isinstance(payload, Mapping) or type(payload.get("schema_version")) is not int or payload.get("schema_version") != 1:
                raise TypeError("persisted connections are invalid")
            rows = payload.get("connections")
            if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
                raise TypeError("persisted connections are invalid")
            for row in rows:
                config = self._deserialize(row)
                if config.connection_id in self._configs:
                    raise ValueError("duplicate persisted connection")
                self._configs[config.connection_id] = config
        except (TypeError, ValueError, json.JSONDecodeError):
            raise DataConnectionPersistenceError("persisted data connections are invalid") from None

    def _flush(self, configs: Mapping[str, DataConnectionConfig] | None = None) -> None:
        configs = self._configs if configs is None else configs
        payload = {
            "schema_version": 1,
            "connections": [self._serialize(config) for config in sorted(configs.values(), key=lambda item: item.connection_id)],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        temporary = self.path.with_name(f".{self.path.name}.{secrets.token_hex(8)}.tmp")
        try:
            temporary.write_text(encoded, encoding="utf-8")
            os.replace(temporary, self.path)
        except (OSError, TypeError, ValueError):
            raise DataConnectionPersistenceError("data connection persistence failed") from None
        finally:
            try:
                temporary.unlink()
            except OSError:
                pass

    def save(self, config: DataConnectionConfig, *, credential_value: str | None = None) -> None:
        if not isinstance(config, DataConnectionConfig):
            raise TypeError("config must be a DataConnectionConfig")
        if credential_value is not None and (config.credential_ref is None or not isinstance(credential_value, str) or not credential_value):
            raise ValueError("invalid data credential")
        previous = dict(self._configs)
        candidate = dict(previous)
        candidate[config.connection_id] = config
        old_config = previous.get(config.connection_id)
        old_ref = old_config.credential_ref if old_config is not None else None
        old_secret: str | None = None
        if old_ref is not None:
            try:
                if self.credentials.has(old_ref):
                    old_secret = self.credentials.resolve(old_ref)
            except Exception:  # noqa: BLE001 - secret store failures are sanitized below
                old_secret = None
        try:
            self._flush(candidate)
            if credential_value is not None:
                self.credentials.put(config.credential_ref, credential_value)  # type: ignore[arg-type]
        except Exception as exc:
            try:
                self._flush(previous)
                if old_ref is not None and old_secret is not None:
                    self.credentials.put(old_ref, old_secret)
                elif config.credential_ref is not None and hasattr(self.credentials, "remove"):
                    self.credentials.remove(config.credential_ref)
            except Exception:  # noqa: BLE001 - preserve a sanitized failure
                self._configs = previous
                raise DataConnectionPersistenceError("data connection persistence failed") from None
            self._configs = previous
            if isinstance(exc, DataConnectionPersistenceError):
                raise
            raise DataConnectionPersistenceError("data connection persistence failed") from None
        self._configs = candidate

    def remove(self, connection_id: str) -> None:
        previous = dict(self._configs)
        candidate = dict(previous)
        candidate.pop(connection_id, None)
        self._flush(candidate)
        self._configs = candidate


def new_local_credential_ref(connection_id: str) -> DataSourceCredentialRef:
    """Create an opaque keychain reference without embedding user identifiers."""

    del connection_id
    return DataSourceCredentialRef(keychain_label=f"finathink-data-{secrets.token_hex(24)}")


__all__ = [
    "DataConnectionPersistenceError",
    "DataConnectionSettingsStore",
    "DataCredentialStore",
    "EnvironmentDataCredentialStore",
    "InMemoryDataCredentialStore",
    "KeychainBackend",
    "KeychainDataCredentialStore",
    "KeychainError",
    "MacOSKeychainBackend",
    "PersistentDataConnectionStore",
    "new_local_credential_ref",
]

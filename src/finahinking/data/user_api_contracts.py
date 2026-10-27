"""Provider-neutral contracts for user-owned data connections.

The contracts in this module deliberately contain references to credentials,
never credential values.  They are safe to pass to reports and status views
after the endpoint and reference fields have been redacted.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import ip_address
from itertools import pairwise
from urllib.parse import parse_qsl, urlsplit

_CONNECTION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_FIELD_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_INSTRUMENT = re.compile(r"^[A-Za-z0-9._:/-]{1,64}$")
_AUTH_MODES = frozenset({"no_auth", "bearer", "api_key_header"})
_PIT_STATES = frozenset({"UNKNOWN", "AVAILABLE", "UNAVAILABLE"})
_SENSITIVE_QUERY_KEYS = frozenset({
    "api_key", "apikey", "api_token", "access_token", "auth_token", "client_secret",
    "credential", "credential_ref", "authorization", "password", "secret", "token", "key",
})
_SECRET_LIKE_LABEL = re.compile(r"(?:api[-_]?key|secret|token|password|credential)", re.IGNORECASE)
MAX_INSTRUMENTS = 100
MAX_FIELD_MAPPING = 32


@dataclass(frozen=True, slots=True)
class DataSourceCredentialRef:
    """A data credential pointer; this namespace is separate from model refs."""

    env_var: str | None = None
    keychain_label: str | None = None

    def __post_init__(self) -> None:
        if (self.env_var is None) == (self.keychain_label is None):
            raise ValueError("data credential reference needs exactly one source")
        if self.env_var is not None and not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", self.env_var):
            raise ValueError("data credential environment name is invalid")
        if self.keychain_label is not None and (
            not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", self.keychain_label)
            or _SECRET_LIKE_LABEL.search(self.keychain_label) is not None
        ):
            raise ValueError("data credential keychain label is invalid")

    def to_dict(self) -> dict[str, str]:
        if self.env_var is not None:
            return {"env_var": self.env_var}
        return {"keychain_label": self.keychain_label or ""}


def _validate_url(value: str, *, allow_local: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("base_url must be a bounded URL")
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base_url must use http or https")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("base_url cannot contain credentials or fragments")
    if any(_is_sensitive_query_key(key) for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
        raise ValueError("base_url cannot contain credential query parameters")
    host = parsed.hostname.rstrip(".").lower()
    if not allow_local:
        blocked_names = {"localhost", "localhost.localdomain", "metadata.google.internal"}
        if host in blocked_names:
            raise ValueError("base_url target is local or metadata service")
        try:
            address = ip_address(host)
        except ValueError:
            address = None
        if address is not None and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved):
            raise ValueError("base_url target is local or private")
    return value.strip().rstrip("/")


def _is_sensitive_query_key(key: str) -> bool:
    """Reject credential-like query names on token boundaries only.

    Delimiter normalization catches spelling variants such as ``api-key`` and
    ``X-Api-Key`` while leaving ordinary words such as ``monkey`` untouched.
    """

    normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
    if not normalized:
        return False
    if normalized in _SENSITIVE_QUERY_KEYS:
        return True
    tokens = normalized.split("_")
    if any(token in {"authorization", "credential", "password", "secret", "token", "key"} for token in tokens):
        return True
    return any(first == "api" and second == "key" for first, second in pairwise(tokens))


@dataclass(frozen=True, slots=True)
class DataConnectionConfig:
    connection_id: str
    display_name: str
    base_url: str
    credential_ref: DataSourceCredentialRef | None
    auth_mode: str
    field_mapping: Mapping[str, str]
    records_path: str | None = None
    auth_header: str | None = None
    source_declaration: str = "User-declared data source; Finathink has not independently verified it."
    allow_local: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.connection_id, str) or not _CONNECTION_ID.fullmatch(self.connection_id):
            raise ValueError("connection_id is invalid")
        if not isinstance(self.display_name, str) or not self.display_name.strip() or len(self.display_name) > 160:
            raise ValueError("display_name must be non-empty and bounded")
        object.__setattr__(self, "base_url", _validate_url(self.base_url, allow_local=self.allow_local))
        if self.auth_mode not in _AUTH_MODES:
            raise ValueError("unsupported auth mode")
        if self.auth_mode == "no_auth" and self.credential_ref is not None:
            raise ValueError("no_auth cannot have a credential reference")
        if self.auth_mode != "no_auth" and not isinstance(self.credential_ref, DataSourceCredentialRef):
            raise ValueError("authenticated connections require a data credential reference")
        if self.auth_mode == "api_key_header":
            if not self.auth_header or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,63}", self.auth_header):
                raise ValueError("api_key_header requires a valid header name")
        elif self.auth_header is not None:
            raise ValueError("auth_header is only valid for api_key_header")
        mapping = dict(self.field_mapping)
        if len(mapping) > MAX_FIELD_MAPPING or any(not _FIELD_NAME.fullmatch(str(k)) or not _FIELD_NAME.fullmatch(str(v)) for k, v in mapping.items()):
            raise ValueError("field_mapping contains invalid or too many fields")
        object.__setattr__(self, "field_mapping", mapping)
        if self.records_path is not None and (not isinstance(self.records_path, str) or self.records_path.startswith("/") or any(not _FIELD_NAME.fullmatch(part) for part in self.records_path.split("."))):
            raise ValueError("records_path must be a simple dotted field path")
        if not isinstance(self.source_declaration, str) or len(self.source_declaration) > 500:
            raise ValueError("source_declaration is invalid")

    def redacted(self) -> dict[str, object]:
        return {
            "connection_id": self.connection_id,
            "display_name": self.display_name,
            "auth_mode": self.auth_mode,
            "field_mapping": dict(self.field_mapping),
            "records_path": self.records_path,
            "credential_configured": self.credential_ref is not None,
            "source_declaration": self.source_declaration,
        }


def _date_or_datetime(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError(f"{name} must be a bounded ISO date/time")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid ISO date/time") from exc
    if parsed.tzinfo is None and "T" in value:
        raise ValueError(f"{name} datetime must include a timezone")
    return value


@dataclass(frozen=True, slots=True)
class DataRequest:
    dataset_kind: str
    instruments: tuple[str, ...] = ()
    start: str | None = None
    end: str | None = None
    as_of: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.dataset_kind, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", self.dataset_kind):
            raise ValueError("dataset_kind is invalid")
        instruments = tuple(self.instruments)
        if len(instruments) > MAX_INSTRUMENTS:
            raise ValueError("instruments exceed the maximum limit")
        if any(not isinstance(item, str) or not _INSTRUMENT.fullmatch(item) for item in instruments):
            raise ValueError("instrument contains an invalid value")
        if len(set(instruments)) != len(instruments):
            raise ValueError("instruments must not contain duplicates")
        start = _date_or_datetime(self.start, "start")
        end = _date_or_datetime(self.end, "end")
        as_of = _date_or_datetime(self.as_of, "as_of")
        if start and end and start > end:
            raise ValueError("start must not be after end")
        object.__setattr__(self, "instruments", instruments)
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        object.__setattr__(self, "as_of", as_of)


@dataclass(frozen=True, slots=True)
class DataBatch:
    records: tuple[Mapping[str, object], ...]
    connection_id: str
    retrieved_at: datetime
    source_declaration: str
    data_fingerprint: str
    quality_issues: tuple[str, ...] = ()
    pit_available: str = "UNKNOWN"

    def __post_init__(self) -> None:
        if not _CONNECTION_ID.fullmatch(self.connection_id):
            raise ValueError("connection_id is invalid")
        if self.retrieved_at.tzinfo is None:
            raise ValueError("retrieved_at must be timezone-aware")
        if self.pit_available not in _PIT_STATES:
            raise ValueError("pit_available is invalid")
        if not re.fullmatch(r"[a-f0-9]{64}", self.data_fingerprint):
            raise ValueError("data_fingerprint must be a SHA-256 digest")
        records = tuple(dict(item) for item in self.records)
        object.__setattr__(self, "records", records)
        object.__setattr__(self, "quality_issues", tuple(str(item) for item in self.quality_issues))

    def to_dict(self) -> dict[str, object]:
        return {
            "records": [dict(item) for item in self.records],
            "connection_id": self.connection_id,
            "retrieved_at": self.retrieved_at.astimezone(UTC).isoformat(),
            "source_declaration": self.source_declaration,
            "data_fingerprint": self.data_fingerprint,
            "quality_issues": list(self.quality_issues),
            "pit_available": self.pit_available,
        }

    def to_research_attachment(self, snapshot_ref, *, market, units):
        from .research_fields import (
            DATE_RESEARCH_FIELDS,
            IDENTITY_FIELDS,
            RESEARCH_FIELDS,
            ResearchFieldAttachment,
        )

        records = [{name: value for name, value in record.items()
                    if name in IDENTITY_FIELDS | RESEARCH_FIELDS | DATE_RESEARCH_FIELDS}
                   for record in self.records if set(record) & RESEARCH_FIELDS]
        return ResearchFieldAttachment(snapshot_ref, market, units, records, self.source_declaration)

    def __repr__(self) -> str:
        return f"DataBatch(records={len(self.records)}, connection_id={self.connection_id!r}, pit_available={self.pit_available!r})"


def fingerprint_records(records: Sequence[Mapping[str, object]]) -> str:
    payload = json.dumps([dict(item) for item in records], ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "MAX_INSTRUMENTS",
    "DataBatch",
    "DataConnectionConfig",
    "DataRequest",
    "DataSourceCredentialRef",
    "fingerprint_records",
]

"""Bounded JSON connector for user-configured data APIs."""

from __future__ import annotations

import inspect
import json
import socket
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import ip_address
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from .connection_settings import DataCredentialStore
from .field_mapping import normalize_records
from .user_api_contracts import DataBatch, DataConnectionConfig, DataRequest

MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_RECORDS = 10_000
MAX_PAGES = 20
REQUEST_TIMEOUT_SECONDS = 15.0
MAX_RETRIES = 2


class DataConnectorError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class TransportResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes
    url: str | None = None


class DataTransport(Protocol):
    def request(self, url: str, *, headers: Mapping[str, str], timeout: float, allow_redirects: bool = False) -> TransportResponse: ...


class UserDataConnector(Protocol):
    def fetch(self, request: DataRequest) -> DataBatch: ...


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError:
        return "", "", -1
    return parsed.scheme.lower(), (parsed.hostname or "").lower().rstrip("."), port


def _default_resolver(host: str, port: int | None) -> tuple[str, ...]:
    try:
        rows = socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
    except OSError:
        return ()
    values: list[str] = []
    for row in rows:
        sockaddr = row[4]
        if isinstance(sockaddr, tuple) and sockaddr:
            values.append(str(sockaddr[0]))
    return tuple(values)


def _forbidden_address(value: str) -> bool:
    try:
        address = ip_address(value)
    except ValueError:
        return False
    return bool(not address.is_global or address.is_loopback or address.is_private or address.is_link_local or address.is_reserved or address.is_unspecified or address.is_multicast)


def _append_query(url: str, request: DataRequest, page: int) -> str:
    parsed = urlsplit(url)
    values = list(parse_qsl(parsed.query, keep_blank_values=False))
    values.extend([("dataset_kind", request.dataset_kind), ("page", str(page))])
    if request.instruments:
        values.append(("instruments", ",".join(request.instruments)))
    if request.start:
        values.append(("start", request.start))
    if request.end:
        values.append(("end", request.end))
    if request.as_of:
        values.append(("as_of", request.as_of))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(values), ""))


def _path_value(payload: object, path: str | None) -> object:
    if path is None:
        return payload
    value = payload
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            raise DataConnectorError("schema", "data response records path is missing")
        value = value[part]
    return value


class JsonApiConnector:
    def __init__(self, config: DataConnectionConfig, credential_store: DataCredentialStore, transport: DataTransport, *, resolver: Any | None = None) -> None:
        self.config = config
        self.credential_store = credential_store
        self.transport = transport
        self._resolver = resolver or _default_resolver
        self._validate_target_url(config.base_url)

    def _validate_target_url(self, url: str) -> None:
        allowed = False
        try:
            parsed = urlsplit(url)
            allowed = bool(
                parsed.scheme in {"http", "https"}
                and parsed.hostname
                and not parsed.username
                and not parsed.password
                and not parsed.fragment
                and _origin(url) == _origin(self.config.base_url)
            )
            if allowed:
                try:
                    values = self._resolver(parsed.hostname, parsed.port)
                except TypeError:
                    values = self._resolver(parsed.hostname)
                if isinstance(values, str):
                    values = (values,)
                addresses = tuple(values or ())
                allowed = bool(addresses) and not any(
                    _forbidden_address(str(value[-1] if isinstance(value, tuple) else value))
                    for value in addresses
                )
        except Exception:  # noqa: BLE001 - target validation must sanitize resolver failures
            allowed = False
        if not allowed:
            raise DataConnectorError("target", "data source target is not allowed")

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        secret: str | None = None
        try:
            if self.config.auth_mode == "bearer" or self.config.auth_mode == "api_key_header":
                secret = self.credential_store.resolve(self.config.credential_ref)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 - credential errors must never escape with secret context
            secret = None
        if self.config.auth_mode == "bearer":
            if secret is None:
                raise DataConnectorError("credential", "data credential is not configured")
            headers["Authorization"] = f"Bearer {secret}"
        elif self.config.auth_mode == "api_key_header":
            if secret is None:
                raise DataConnectorError("credential", "data credential is not configured")
            headers[self.config.auth_header or "X-API-Key"] = secret
        return headers

    def _transport_supports_no_redirect(self) -> bool:
        try:
            parameters = inspect.signature(self.transport.request).parameters
        except (TypeError, ValueError):
            return False
        return "allow_redirects" in parameters or any(item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values())

    def _transport_request(self, url: str, headers: Mapping[str, str]) -> TransportResponse:
        request = self.transport.request
        if self._transport_supports_no_redirect():
            return request(url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS, allow_redirects=False)
        return request(url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)

    def _request(self, url: str, headers: Mapping[str, str]) -> TransportResponse:
        self._validate_target_url(url)
        for attempt in range(MAX_RETRIES + 1):
            timeout_error = False
            transport_error = False
            try:
                response = self._transport_request(url, headers)
            except TimeoutError:
                timeout_error = True
            except Exception:  # noqa: BLE001 - transport failures are always normalized
                transport_error = True
            if timeout_error:
                if attempt < MAX_RETRIES:
                    continue
                raise DataConnectorError("timeout", "data request timed out")
            if transport_error:
                if attempt < MAX_RETRIES:
                    continue
                raise DataConnectorError("transport", "data request failed")
            if not isinstance(response, TransportResponse):
                raise DataConnectorError("transport", "data transport returned an invalid response")
            if not isinstance(response.body, bytes):
                raise DataConnectorError("transport", "data transport returned an invalid body")
            if (response.status_code in {408, 425, 429} or 500 <= response.status_code <= 599) and attempt < MAX_RETRIES:
                continue
            if response.status_code in {401, 403}:
                raise DataConnectorError("unauthorized", "data source rejected authentication")
            if response.status_code == 429:
                raise DataConnectorError("rate_limited", "data source rate limit reached")
            if response.status_code < 200 or response.status_code >= 300:
                raise DataConnectorError("http", "data source returned an error")
            final_url = response.url or url
            final_target_allowed = True
            try:
                self._validate_target_url(final_url)
            except DataConnectorError:
                final_target_allowed = False
            if not final_target_allowed:
                raise DataConnectorError("redirect", "data source redirected to a different origin")
            if len(response.body) > MAX_RESPONSE_BYTES:
                raise DataConnectorError("response_too_large", "data response exceeds the size limit")
            return response
        raise DataConnectorError("transport", "data request failed") from None

    def fetch_research_fields(self, request: DataRequest, *, snapshot_ref, market, units):
        """Explicit fetch through the same authenticated, bounded JSON connector."""
        if request.dataset_kind != "research_fields":
            raise ValueError("research field request must declare its dataset kind")
        return self.fetch(request).to_research_attachment(snapshot_ref, market=market, units=units)

    def fetch(self, request: DataRequest) -> DataBatch:
        if self.config.auth_mode != "no_auth" and not self._transport_supports_no_redirect():
            raise DataConnectorError("transport_policy", "authenticated data transport must disable redirects")
        headers = self._headers()
        records: list[Mapping[str, Any]] = []
        seen_urls: set[str] = set()
        seen_next_targets: set[str] = set()
        url = self.config.base_url
        for page in range(1, MAX_PAGES + 1):
            request_url = _append_query(url, request, page)
            if request_url in seen_urls:
                raise DataConnectorError("pagination", "data source pagination repeated a page")
            seen_urls.add(request_url)
            response = self._request(request_url, headers)
            content_type = next((value for key, value in response.headers.items() if key.lower() == "content-type"), "")
            if content_type and "json" not in content_type.lower():
                raise DataConnectorError("invalid_json", "data source did not return JSON")
            valid_json = True
            try:
                payload = json.loads(response.body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                valid_json = False
                payload = None
            if not valid_json:
                raise DataConnectorError("invalid_json", "data source did not return valid JSON")
            page_records = _path_value(payload, self.config.records_path)
            if not isinstance(page_records, Sequence) or isinstance(page_records, (str, bytes)) or any(not isinstance(item, Mapping) for item in page_records):
                raise DataConnectorError("schema", "data response records must be an array of objects")
            records.extend(page_records)
            if len(records) > MAX_RECORDS:
                raise DataConnectorError("record_limit", "data response exceeds the record limit")
            next_value = payload.get("next") if isinstance(payload, Mapping) else None
            if next_value in (None, "", False):
                break
            if not isinstance(next_value, str) or len(next_value) > 2048:
                raise DataConnectorError("pagination", "data response pagination cursor is invalid")
            next_url = urljoin(url, next_value)
            if next_url in seen_next_targets:
                raise DataConnectorError("pagination", "data source pagination repeated a cursor")
            seen_next_targets.add(next_url)
            next_target_allowed = True
            try:
                self._validate_target_url(next_url)
            except DataConnectorError:
                next_target_allowed = False
            if not next_target_allowed:
                raise DataConnectorError("redirect", "data source pagination changed origin")
            url = next_url
        else:
            raise DataConnectorError("pagination", "data source exceeded the page limit")
        return normalize_records(
            records,
            self.config.field_mapping,
            request.dataset_kind,
            connection_id=self.config.connection_id,
            source_declaration=self.config.source_declaration,
            retrieved_at=datetime.now(UTC),
            as_of=request.as_of,
        )

    def capture(self, request: DataRequest, store, *, units=None) -> str:
        """Explicitly fetch once and retain an immutable replay reference."""
        from finahinking.p8_2.data_sources import snapshot_from_batch

        from finahinking.data.units import snapshot_units

        declaration = snapshot_units(units)
        return store.save(snapshot_from_batch(self.fetch(request), units=declaration))


__all__ = [
    "MAX_PAGES",
    "MAX_RECORDS",
    "MAX_RESPONSE_BYTES",
    "REQUEST_TIMEOUT_SECONDS",
    "DataConnectorError",
    "DataTransport",
    "JsonApiConnector",
    "TransportResponse",
    "UserDataConnector",
]

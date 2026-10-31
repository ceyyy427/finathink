"""Small, provider-neutral HTTP transport for explicit user data tests.

This module owns network policy. It has no provider SDK and deliberately
returns the connector's typed ``TransportResponse`` rather than exposing a
urllib response object. Redirects, private targets, unbounded bodies and
unsafe headers fail closed with secret-free error codes.
"""

from __future__ import annotations

import http.client
import re
import socket
from collections.abc import Mapping
from ipaddress import ip_address
from itertools import pairwise
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .user_api import TransportResponse


class BoundedTransportError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _sensitive_query_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
    if normalized in {"api_key", "apikey", "api_token", "access_token", "authorization", "password", "secret", "token", "key"}:
        return True
    tokens = normalized.split("_")
    return any(left == "api" and right == "key" for left, right in pairwise(tokens)) or any(
        token in {"authorization", "password", "secret", "token", "credential"} for token in tokens
    )


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, new):  # type: ignore[no-untyped-def]
        return None


def _private_address(value: str) -> bool:
    try:
        address = ip_address(value)
    except ValueError:
        return False
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        or address.is_multicast
    )


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError:
        port = -1
    return parsed.scheme.casefold(), (parsed.hostname or "").rstrip(".").casefold(), port


class BoundedHttpTransport:
    """GET-only bounded transport with an explicit no-redirect policy."""

    def __init__(
        self,
        *,
        timeout: float = 15.0,
        max_response_bytes: int = 5 * 1024 * 1024,
        max_header_bytes: int = 16 * 1024,
        allow_local: bool = False,
    ) -> None:
        if timeout <= 0 or timeout > 120:
            raise ValueError("timeout must be between 0 and 120 seconds")
        if max_response_bytes < 1 or max_response_bytes > 50 * 1024 * 1024:
            raise ValueError("max_response_bytes is out of bounds")
        if max_header_bytes < 256 or max_header_bytes > 128 * 1024:
            raise ValueError("max_header_bytes is out of bounds")
        self.timeout = float(timeout)
        self.max_response_bytes = int(max_response_bytes)
        self.max_header_bytes = int(max_header_bytes)
        self.allow_local = bool(allow_local)
        self._opener = build_opener(_NoRedirectHandler())

    def _resolve(self, host: str, port: int | None) -> tuple[str, ...]:
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

    def _validate_url(self, url: str) -> tuple[str, str, int | None]:
        if not isinstance(url, str) or len(url) > 2048:
            raise BoundedTransportError("target", "data source target is not allowed")
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            port = parsed.port
        except ValueError:
            raise BoundedTransportError("target", "data source target is not allowed") from None
        if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password or parsed.fragment:
            raise BoundedTransportError("target", "data source target is not allowed")
        if any(_sensitive_query_key(key) for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
            raise BoundedTransportError("query", "credential query parameters are not allowed")
        host = host.rstrip(".").lower()
        if host in {"localhost", "localhost.localdomain", "metadata.google.internal"} and not self.allow_local:
            raise BoundedTransportError("target", "data source target is not allowed")
        addresses = self._resolve(host, port)
        if not addresses:
            raise BoundedTransportError("dns", "data source DNS lookup failed")
        if not self.allow_local and any(_private_address(value) for value in addresses):
            raise BoundedTransportError("target", "data source target is not allowed")
        return parsed.scheme, host, port

    def _open(self, request: Request, timeout: float):
        return self._opener.open(request, timeout=timeout)

    def request(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: float | None = None,
        allow_redirects: bool = False,
    ) -> TransportResponse:
        if allow_redirects:
            raise BoundedTransportError("redirect", "redirects are disabled by data policy")
        self._validate_url(url)
        if not isinstance(headers, Mapping):
            raise BoundedTransportError("headers", "request headers are invalid")
        clean_headers: dict[str, str] = {}
        total_header_bytes = 0
        for key, value in headers.items():
            if not isinstance(key, str) or not isinstance(value, str) or not key or "\r" in key or "\n" in key or "\r" in value or "\n" in value:
                raise BoundedTransportError("headers", "request headers are invalid")
            total_header_bytes += len(key.encode()) + len(value.encode())
            if total_header_bytes > self.max_header_bytes:
                raise BoundedTransportError("headers", "request headers exceed the size limit")
            clean_headers[key] = value
        request = Request(url, headers=clean_headers, method="GET")
        effective_timeout = self.timeout if timeout is None else float(timeout)
        if effective_timeout <= 0 or effective_timeout > 120:
            raise BoundedTransportError("timeout", "request timeout is out of bounds")
        response = None
        try:
            # Resolve again immediately before opening the socket. A changed
            # or newly private DNS answer is treated as a rebinding attempt.
            self._validate_url(url)
            response = self._open(request, effective_timeout)
            status_value = getattr(response, "status", None)
            status = int(status_value if status_value is not None else response.getcode())
            raw_headers = getattr(response, "headers", {})
            headers_out = {str(key): str(value) for key, value in raw_headers.items()}
            content_type = next((value for key, value in headers_out.items() if key.lower() == "content-type"), "")
            if "text/event-stream" in content_type.lower():
                raise BoundedTransportError("unsupported_stream", "streaming data responses are not supported")
            declared = next((value for key, value in headers_out.items() if key.lower() == "content-length"), None)
            if declared is not None:
                if not declared.isdigit():
                    raise BoundedTransportError("response", "data response headers are invalid")
                if int(declared) > self.max_response_bytes:
                    raise BoundedTransportError("response_too_large", "data response exceeds the size limit")
            body = bytearray()
            while True:
                chunk = response.read(min(64 * 1024, self.max_response_bytes - len(body) + 1))
                if not chunk:
                    break
                if not isinstance(chunk, bytes):
                    raise BoundedTransportError("response", "data response body is invalid")
                body.extend(chunk)
                if len(body) > self.max_response_bytes:
                    raise BoundedTransportError("response_too_large", "data response exceeds the size limit")
            if declared is not None and len(body) != int(declared):
                raise BoundedTransportError("stream_interrupted", "data response transfer was interrupted")
            final_url = response.geturl() if hasattr(response, "geturl") else url
            if _origin(str(final_url)) != _origin(url):
                raise BoundedTransportError("redirect", "data source redirected to a different origin")
            return TransportResponse(status_code=status, headers=headers_out, body=bytes(body), url=str(final_url))
        except BoundedTransportError:
            raise
        except TimeoutError:
            raise BoundedTransportError("timeout", "data request timed out") from None
        except HTTPError as exc:
            # HTTP errors are still typed responses, allowing JsonApiConnector
            # to apply its retry and status policy without exposing body text.
            return TransportResponse(status_code=int(exc.code), headers={str(k): str(v) for k, v in exc.headers.items()}, body=b"", url=str(exc.geturl() or url))
        except (http.client.IncompleteRead, http.client.RemoteDisconnected, ConnectionResetError):
            raise BoundedTransportError("stream_interrupted", "data response transfer was interrupted") from None
        except (URLError, OSError):
            raise BoundedTransportError("transport", "data request failed") from None
        finally:
            if response is not None and hasattr(response, "close"):
                try:
                    response.close()
                except Exception:  # noqa: BLE001 - cleanup must never leak
                    response = None


__all__ = ["BoundedHttpTransport", "BoundedTransportError"]

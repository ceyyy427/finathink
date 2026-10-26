"""Inert-until-called HTTPS JSON transport for user-owned model endpoints.

No ambient proxy, redirects or cookies. DNS is validated once and the socket
is pinned to that address; TLS still authenticates the original hostname.
"""

from __future__ import annotations

import http.client
import ipaddress
import json as json_module
import socket
import time
from urllib.parse import urlsplit

from .provider_config import _endpoint


class _PinnedHttpsConnection(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, timeout=timeout)
        self._address = address

    def connect(self):
        raw = socket.create_connection((self._address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


class ProviderHttpTransport:
    def __init__(self, *, max_response_bytes=1024 * 1024, max_request_bytes=262144):
        if type(max_response_bytes) is not int or not 1 <= max_response_bytes <= 1024 * 1024:
            raise ValueError("provider response limit is invalid")
        if type(max_request_bytes) is not int or not 1 <= max_request_bytes <= 262144:
            raise ValueError("provider request limit is invalid")
        self.max_response_bytes, self.max_request_bytes = max_response_bytes, max_request_bytes

    def _resolve(self, host):
        return tuple(row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))

    def _connect(self, host, address, timeout):
        return _PinnedHttpsConnection(host, address, timeout)

    def request(self, method, url, *, headers, json, timeout):
        if method != "POST" or _endpoint(url) is None or isinstance(timeout, bool) or not 0 < timeout <= 30:
            raise ValueError("provider request is not allowed")
        if not isinstance(headers, dict) or set(headers) - {"Authorization", "Accept", "Content-Type"}:
            raise ValueError("provider headers are invalid")
        if any(type(key) is not str or type(value) is not str or "\r" in value or "\n" in value for key, value in headers.items()) or sum(len(key) + len(value) for key, value in headers.items()) > 8192:
            raise ValueError("provider headers are invalid")
        body = json_module.dumps(json, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(body) > self.max_request_bytes:
            raise ValueError("provider request exceeds its byte limit")
        parsed = urlsplit(url)
        connection = None
        started = time.monotonic()
        try:
            addresses = self._resolve(parsed.hostname)
            if not addresses or any(not ipaddress.ip_address(address).is_global or ipaddress.ip_address(address).is_multicast for address in addresses):
                raise ValueError("provider target is not public")
            connection = self._connect(parsed.hostname, addresses[0], float(timeout))
            connection.request("POST", parsed.path or "/", body=body, headers=headers)
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                return {"status_code": response.status, "json": {}}
            declared = response.headers.get("Content-Length")
            if declared is not None and (not str(declared).isdigit() or int(declared) > self.max_response_bytes):
                raise ValueError("provider response exceeds its byte limit")
            result = bytearray()
            while True:
                remaining = float(timeout) - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError()
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                chunk = response.read(min(65536, self.max_response_bytes - len(result) + 1))
                if not chunk:
                    break
                result.extend(chunk)
                if len(result) > self.max_response_bytes:
                    raise ValueError("provider response exceeds its byte limit")
            return {"status_code": response.status, "json": json_module.loads(bytes(result))}
        except (ValueError, TimeoutError):
            raise
        except Exception:  # noqa: BLE001 - transport details may contain credentials
            raise ValueError("provider transport failed") from None
        finally:
            if connection is not None:
                connection.close()

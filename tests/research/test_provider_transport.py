import io
import json

import pytest


class Response(io.BytesIO):
    status = 200

    def __init__(self, body):
        super().__init__(body)
        self.headers = {}


class Connection:
    def __init__(self, response):
        self.response = response
        self.sent = None
        self.closed = False
        self.sock = None

    def request(self, method, path, body, headers):
        self.sent = method, path, body, headers

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def transport(monkeypatch, addresses=("1.1.1.1",), response=b'{"choices": []}', **kwargs):
    from finahinking.research.provider_transport import ProviderHttpTransport

    instance = ProviderHttpTransport(**kwargs)
    connection = Connection(Response(response))
    targets = []
    monkeypatch.setattr(instance, "_resolve", lambda host: addresses)

    def connect(host, address, timeout):
        targets.append((host, address, timeout))
        return connection

    monkeypatch.setattr(instance, "_connect", connect)
    return instance, connection, targets


def invoke(instance, **kwargs):
    return instance.request("POST", "https://models.acme.com/v1/chat/completions", headers={"Authorization": "Bearer fake-value", "Content-Type": "application/json"}, json={"model": "local-test", "messages": []}, timeout=2, **kwargs)


def test_https_post_pins_public_address_preserves_host_and_closes(monkeypatch):
    instance, connection, targets = transport(monkeypatch)
    result = invoke(instance)
    assert result == {"status_code": 200, "json": {"choices": []}}
    assert targets == [("models.acme.com", "1.1.1.1", 2.0)]
    assert connection.sent[0:2] == ("POST", "/v1/chat/completions")
    assert json.loads(connection.sent[2])["model"] == "local-test" and connection.closed


@pytest.mark.parametrize("addresses", [("127.0.0.1",), ("1.1.1.1", "169.254.169.254"), (), ("::1",), ("224.0.0.1",)])
def test_private_or_mixed_dns_never_receives_authorization(monkeypatch, addresses):
    instance, connection, targets = transport(monkeypatch, addresses=addresses)
    with pytest.raises(ValueError):
        invoke(instance)
    assert targets == [] and connection.sent is None


@pytest.mark.parametrize("status", [301, 302, 307, 401, 500])
def test_redirect_and_error_bodies_are_not_followed_or_exposed(monkeypatch, status):
    instance, connection, targets = transport(monkeypatch, response=b'api_key=do-not-expose')
    connection.response.status = status
    assert invoke(instance) == {"status_code": status, "json": {}}
    assert len(targets) == 1 and connection.closed


def test_streaming_size_cap_closes_before_parsing(monkeypatch):
    instance, connection, _ = transport(monkeypatch, response=b"x" * 65, max_response_bytes=64)
    with pytest.raises(ValueError, match="limit"):
        invoke(instance)
    assert connection.closed


def test_header_injection_is_rejected_before_connection(monkeypatch):
    instance, _, targets = transport(monkeypatch)
    with pytest.raises(ValueError):
        instance.request("POST", "https://models.acme.com/v1", headers={"Authorization": "fake\r\nInjected: true"}, json={}, timeout=2)
    assert targets == []

"""Malformed wire responses retain bounded, non-sensitive failure identities."""
import http.client
import io

import pytest

from finahinking.data.connection_settings import InMemoryDataCredentialStore
from finahinking.data.http_transport import BoundedHttpTransport, BoundedTransportError
from finahinking.data.user_api import DataConnectorError, JsonApiConnector
from finahinking.research.provider_adapters import OpenAICompatibleAdapter
from finahinking.research.providers import ModelEnvelope
from tests.data.test_user_api import PUBLIC_RESOLVER, FakeTransport, config, request, response
from tests.research.test_model_call_journal import journal, record
from tests.research.test_provider_transport import invoke, transport


@pytest.mark.parametrize('kind', ['length', 'chunk', 'disconnect', 'sse', 'json', 'oversize'])
def test_provider_wire_fault_has_typed_public_code_and_closes(monkeypatch, kind):
    instance, connection, _ = transport(monkeypatch, response=b'{}')
    expected = {'length': 'STREAM_INTERRUPTED', 'chunk': 'STREAM_INTERRUPTED', 'disconnect': 'STREAM_INTERRUPTED', 'sse': 'UNSUPPORTED_STREAM', 'json': 'NON_JSON', 'oversize': 'RESPONSE_TOO_LARGE'}[kind]
    if kind == 'length':
        connection.response.headers['Content-Length'] = '99'
    elif kind in {'chunk', 'disconnect'}:
        def read(_):
            if kind == 'chunk':
                raise http.client.IncompleteRead(b'private-wire-value')
            raise http.client.RemoteDisconnected('private-wire-value')
        monkeypatch.setattr(connection.response, 'read', read)
    elif kind == 'sse':
        connection.response.headers['Content-Type'] = 'text/event-stream'
    elif kind == 'json':
        connection.response = io.BytesIO(b'private-wire-value')
        connection.response.status = 200
        connection.response.headers = {}
    else:
        connection.response.headers['Content-Length'] = '1048577'
    with pytest.raises(ValueError) as caught:
        invoke(instance)
    assert getattr(caught.value, 'code', None) == expected
    assert 'private-wire-value' not in str(caught.value)
    assert connection.closed


def test_real_transport_json_failure_is_non_json_through_adapter(monkeypatch):
    instance, _, _ = transport(monkeypatch, response=b'not-json-private')
    adapter = OpenAICompatibleAdapter(model='user-model', endpoint='https://models.acme.com/v1/chat/completions', transport=instance)
    result = adapter.invoke_contract(ModelEnvelope('run', 'technical', 'a'*64, 'b'*64))
    assert result.status == 'NON_JSON' and 'not-json-private' not in str(result)


@pytest.mark.parametrize('status', ['STREAM_INTERRUPTED', 'UNSUPPORTED_STREAM', 'RESPONSE_TOO_LARGE'])
def test_new_provider_outcomes_survive_contract_and_journal(monkeypatch, tmp_path, status):
    from finahinking.research import provider_adapters as adapters
    assert status in adapters.ProviderFailureKind._value2member_map_
    store = journal(tmp_path/'calls.sqlite3')
    call_id = store.begin(record())
    store.dispatched(call_id, 100)
    store.finish(call_id, {'status': status, 'usage': {}, 'duration_ms': 5})
    assert store.records(terminal=True)[0]['status'] == status
    assert store.records(terminal=True)[0]['usage'] is None
    adapter = OpenAICompatibleAdapter(model='user-model', endpoint='https://models.acme.com/v1', transport=lambda *_a, **_k: None)
    def fail(_):
        raise adapters.ProviderAdapterError('controlled failure', kind=adapters.ProviderFailureKind(status))
    monkeypatch.setattr(adapter, 'invoke', fail)
    assert adapter.invoke_contract(ModelEnvelope('run', 'technical', 'a'*64, 'b'*64)).status == status


@pytest.mark.parametrize('kind', ['length', 'chunk', 'sse'])
def test_data_wire_fault_cannot_be_a_valid_batch(monkeypatch, kind):
    class Response(io.BytesIO):
        status = 200
        def geturl(self):
            return 'https://models.acme.com/data'
    peer = Response(b'{"data": []}')
    peer.headers = {'Content-Length': '100'} if kind == 'length' else {'Content-Type': 'text/event-stream'} if kind == 'sse' else {}
    if kind == 'chunk':
        monkeypatch.setattr(peer, 'read', lambda _n: (_ for _ in ()).throw(http.client.IncompleteRead(b'private-wire-value')))
    instance = BoundedHttpTransport()
    monkeypatch.setattr(instance, '_resolve', PUBLIC_RESOLVER)
    monkeypatch.setattr(instance, '_open', lambda *_a: peer)
    with pytest.raises(BoundedTransportError) as caught:
        instance.request('https://models.acme.com/data', headers={})
    assert caught.value.code == ('unsupported_stream' if kind == 'sse' else 'stream_interrupted')
    assert 'private-wire-value' not in str(caught.value) and peer.closed


@pytest.mark.parametrize('fault, expected', [
    (response({}, status=401), 'unauthorized'), (response({}, status=403), 'unauthorized'),
    (response({}, status=429), 'rate_limited'), (TimeoutError('private-wire-value'), 'timeout'),
    (BoundedTransportError('stream_interrupted', 'private-wire-value'), 'stream_interrupted'),
    (BoundedTransportError('response_too_large', 'private-wire-value'), 'response_too_large'),
])
def test_diagnostic_data_limit_is_one_attempt_and_keeps_safe_failure(fault, expected):
    wire = FakeTransport([fault]*3)
    connector = JsonApiConnector(config(), InMemoryDataCredentialStore(), wire, resolver=PUBLIC_RESOLVER, timeout=.2, max_attempts=1, max_pages=1)
    with pytest.raises(DataConnectorError) as caught:
        connector.fetch(request())
    assert len(wire.calls) == 1 and wire.calls[0][2] == .2
    assert caught.value.code == expected and 'private-wire-value' not in str(caught.value)
    if hasattr(fault, 'status_code'):
        assert caught.value.status_code == fault.status_code


@pytest.mark.parametrize('limits', [{'timeout':float('nan')}, {'timeout':True}, {'timeout':0}, {'max_attempts':True}, {'max_attempts':4}, {'max_pages':0}, {'max_pages':21}])
def test_data_limits_validate_before_dns(limits):
    with pytest.raises((ValueError, TypeError)):
        JsonApiConnector(config(), InMemoryDataCredentialStore(), FakeTransport([]), resolver=lambda *_: pytest.fail('DNS before validation'), **limits)


def test_diagnostic_page_limit_does_not_fetch_the_next_page():
    wire = FakeTransport([response({'data':[{'ticker':'AAA','time':'2026-01-02','price':10}], 'next':'?cursor=2'})])
    connector = JsonApiConnector(config(), InMemoryDataCredentialStore(), wire, resolver=PUBLIC_RESOLVER, max_pages=1)
    with pytest.raises(DataConnectorError) as caught:
        connector.fetch(request())
    assert caught.value.code == 'pagination' and len(wire.calls) == 1


def test_wrapped_data_socket_timeout_keeps_timeout_identity(monkeypatch):
    from urllib.error import URLError
    instance = BoundedHttpTransport()
    monkeypatch.setattr(instance, '_resolve', PUBLIC_RESOLVER)
    def open_failed(*_):
        raise URLError(TimeoutError('private-wire-value'))
    monkeypatch.setattr(instance, '_open', open_failed)
    with pytest.raises(BoundedTransportError) as caught:
        instance.request('https://models.acme.com/data', headers={})
    assert caught.value.code == 'timeout' and 'private-wire-value' not in str(caught.value)

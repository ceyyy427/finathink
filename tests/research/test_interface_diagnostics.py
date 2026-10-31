"""Real compatible HTTP bytes and explicitly authorized one-shot probes."""
import json
import socket
from dataclasses import replace

import pytest

from finahinking.data.connection_settings import (
    DataConnectionSettingsStore,
    InMemoryDataCredentialStore,
)
from finahinking.data.user_api_contracts import DataConnectionConfig
from finahinking.p8_2.data_sources import FixtureMarketDataSource
from finahinking.research.credentials import InMemoryCredentialStore
from finahinking.research.provider_config import ProviderConfig, ProviderConfigStore
from finahinking.research.provider_status import ProviderCredentialRef
from tests.data.test_user_api import FakeTransport, response
from tests.research.test_grounded_driver import ModelPeer


def setup(tmp_path, *, data_wire=None, model_wire=None, allowed=True, has_credential=True):
    from finahinking.research.diagnostic_store import DiagnosticStore
    from finahinking.research.interface_diagnostics import InterfaceDiagnostics
    data=DataConnectionSettingsStore(InMemoryDataCredentialStore())
    cfg=DataConnectionConfig('feed','My private data','https://93.184.216.34/api',None,'no_auth', {name:name for name in ('instrument','timestamp','available_at','open','high','low','close','volume')},'data')
    data.save(cfg)
    providers=ProviderConfigStore()
    ref=ProviderCredentialRef('desk',env_var='FINAHINK_TEST_KEY')
    providers.save(ProviderConfig('desk','openai_compatible','user-model',endpoint='https://models.acme.com/v1',credential_ref=ref))
    credentials=InMemoryCredentialStore({ref:'wire-private-value'} if has_credential else {})
    service=InterfaceDiagnostics(data,providers,DiagnosticStore(tmp_path/'diag.sqlite3',scope_id='scope'),data_transport=data_wire,provider_transport=model_wire,credential_store=credentials,allow_model_calls=allowed)
    return service,data,providers


def probe(kind='model', **changes):
    return {'request_key':'my-probe','target_kind':kind,'config_ref':'desk' if kind=='model' else 'feed','authorize':True,'instrument':'AAA',**changes}


def test_compatibility_uses_only_owned_loopback_and_labels_every_scenario(tmp_path,monkeypatch):
    service,_,providers=setup(tmp_path)
    real_connect=socket.create_connection
    real_resolve=socket.getaddrinfo
    def local_resolve(host,*args,**kwargs):
        assert host=='127.0.0.1',host
        return real_resolve(host,*args,**kwargs)
    monkeypatch.setattr(socket,'getaddrinfo',local_resolve)
    monkeypatch.setattr(socket,'getfqdn',lambda *_:pytest.fail('local suite must not request reverse DNS'))
    calls=[]
    def local_only(address,*args,**kwargs):
        assert address[0]=='127.0.0.1',address
        calls.append(address)
        return real_connect(address,*args,**kwargs)
    monkeypatch.setattr(socket,'create_connection',local_only)
    result=service.run_local()
    assert result['status']=='READY' and result['scope']=='LOCAL_COMPATIBILITY'
    assert result['external_account_verified'] is False and result['tls_verified'] is False
    assert len(result['cases'])>=24 and calls
    assert all(case['expected']==case['observed'] and case['status']=='PASS' for case in result['cases'])
    assert {'READY','UNAUTHORIZED','FORBIDDEN','RATE_LIMITED','NON_JSON','MISSING_FIELDS','SCHEMA_ERROR','STREAM_INTERRUPTED','UNSUPPORTED_STREAM','TIMEOUT'} <= {c['observed'] for c in result['cases']}
    assert providers.get('desk') is not None and 'wire-private-value' not in json.dumps(result)
    assert len(service.store.list())==1


def test_model_probe_is_explicit_one_shot_redacted_and_idempotent(tmp_path):
    peer=ModelPeer()
    service,_,_=setup(tmp_path,model_wire=peer)
    assert peer.calls==[]
    with pytest.raises(ValueError):
        service.probe(probe(authorize=False))
    assert peer.calls==[] and service.store.list()==[]
    result=service.probe(probe())
    assert result['status']=='READY' and result['request_count']==1
    assert result['usage']['total_units']==160 and result['estimated_cost'] is None
    assert len(peer.calls)==1 and peer.calls[0]['timeout']==5
    text=json.dumps(result)
    assert all(secret not in text for secret in ('wire-private-value','FINAHINK_TEST_KEY','models.acme','My private data','Selected market snapshot'))
    again=service.probe(probe())
    assert again==result and len(peer.calls)==1
    with pytest.raises(ValueError):
        service.probe(probe(config_ref='changed'))
    assert len(peer.calls)==1


@pytest.mark.parametrize('allowed,has_credential,status',[(False,True,'NOT_RUN'),(True,False,'NOT_CONFIGURED')])
def test_model_guard_blocks_before_outbound(tmp_path,allowed,has_credential,status):
    peer=ModelPeer()
    service,_,_=setup(tmp_path,model_wire=peer,allowed=allowed,has_credential=has_credential)
    result=service.probe(probe())
    assert result['status']==status and result['request_count']==0 and peer.calls==[]


@pytest.mark.parametrize('http_status,expected',[(401,'UNAUTHORIZED'),(403,'FORBIDDEN'),(429,'RATE_LIMITED')])
def test_data_probe_keeps_http_failure_and_never_retries(tmp_path,http_status,expected):
    wire=FakeTransport([response({'error':'wire-private-value'},status=http_status)])
    service,_,_=setup(tmp_path,data_wire=wire)
    result=service.probe(probe('data'))
    assert result['status']==expected and len(wire.calls)==1
    assert 'wire-private-value' not in json.dumps(result)


def test_data_probe_validates_fields_without_saving_a_snapshot(tmp_path):
    snapshot=FixtureMarketDataSource(points=5).snapshot('AAA')
    records=[item.to_dict() for item in snapshot.observations]
    wire=FakeTransport([response({'data':records},url='https://93.184.216.34/api')])
    service,data,_=setup(tmp_path,data_wire=wire)
    result=service.probe(probe('data',as_of='2026-10-01'))
    assert result['status']=='READY' and result['record_count']==5
    assert not list(tmp_path.rglob('*snapshot*'))
    data2=replace(data.get('feed'),connection_id='partial',field_mapping={name:name for name in ('instrument','timestamp','close')})
    data.save(data2)
    wire.responses.append(response({'data':records},url='https://93.184.216.34/api'))
    result=service.probe(probe('data',config_ref='partial',request_key='other'))
    assert result['status']=='MISSING_FIELDS' and result['missing_fields']==['high','low','open','volume']
    assert len(wire.calls)==2


def test_two_concurrent_probes_share_the_same_billable_attempt(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    entered,release=threading.Event(),threading.Event()
    class HeldPeer(ModelPeer):
        def request(self,*args,**kwargs):
            entered.set()
            assert release.wait(3)
            return super().request(*args,**kwargs)
    peer=HeldPeer()
    service,_,_=setup(tmp_path,model_wire=peer)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(service.probe,probe())
        assert entered.wait(3)
        try:
            second=service.probe(probe())
            assert second['state']=='PENDING' and second['usage'] is None
        finally:
            release.set()
        final=first.result(timeout=3)
    assert second['receipt_id']==final['receipt_id'] and len(peer.calls)==1


def test_interrupted_model_usage_remains_unknown_after_reopen(tmp_path):
    from finahinking.research.diagnostic_store import DiagnosticStore
    from tests.research.test_provider_live_contract import SequenceTransport
    peer=SequenceTransport(TimeoutError('wire-private-value'))
    service,_,_=setup(tmp_path,model_wire=peer)
    result=service.probe(probe())
    assert result['status']=='TIMEOUT' and result['request_count']==1
    assert result['usage'] is None and result['estimated_cost'] is None
    reopened=DiagnosticStore(tmp_path/'diag.sqlite3',scope_id='scope')
    assert reopened.get(result['receipt_id'])==result
    assert len(peer.calls)==1

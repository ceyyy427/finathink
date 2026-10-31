"""Actual local HTTP contracts stay explicit, scoped and restart-safe."""
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from finahinking.local_app import LocalAppConfig, LocalApplication, create_server
from finahinking.research.credentials import InMemoryCredentialStore
from finahinking.research.provider_config import ProviderConfig
from finahinking.research.provider_status import ProviderCredentialRef
from tests.research.grounded_worker_peer import install_peer
from tests.research.test_application_service import wait_for


class ProbeWire:
    def __init__(self): self.calls=[]
    def request(self,*args,**kwargs):
        self.calls.append((args,kwargs))
        return {'status_code':401,'json':{'private':'do-not-export'}}


@pytest.fixture
def app_http(tmp_path):
    wire=ProbeWire()
    ref=ProviderCredentialRef('desk',env_var='FINAHINK_TEST_KEY')
    config=LocalAppConfig(db_path=str(tmp_path/'app.sqlite'),offline=False)
    app=LocalApplication(config,provider_transport=wire,provider_credential_store=InMemoryCredentialStore({ref:'private-probe-value'}))
    app._provider_configs.save(ProviderConfig('desk','openai_compatible','user-model',endpoint='https://models.acme.com/v1',credential_ref=ref))
    install_peer(app.research_service)
    server=create_server(app,host='127.0.0.1',port=0)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    base='http://127.0.0.1:'+str(server.server_port)
    def http(path,body=None,method=None):
        headers={'Content-Type':'application/json'} if body is not None else {}
        request=Request(base+path,None if body is None else json.dumps(body).encode(),headers,method=method)
        try: response=urlopen(request,timeout=15)
        except HTTPError as error: response=error
        with response:
            raw=response.read().decode()
            return response.status,json.loads(raw) if 'application/json' in response.headers.get('Content-Type','') else raw
    try: yield app,http,wire
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3); app.close()


def post(app,http,path,values): return http(path,{'_csrf':app.csrf_token,**values})


def test_inert_catalogue_local_receipt_export_and_scope(app_http):
    app,http,wire=app_http
    for path in ('/diagnostics','/api/diagnostics/interfaces','/api/research/quality/cases','/api/research/quality/jobs'):
        assert http(path)[0]==200
    assert not wire.calls and app.research_service.list_jobs()['total']==0
    assert http('/api/diagnostics/interfaces')[1]['records']==[]
    assert http('/api/diagnostics/interfaces/local',{})[0]==403
    assert http('/api/diagnostics/interfaces/local')[0]==405
    status,report=post(app,http,'/api/diagnostics/interfaces/local',{})
    assert status==201 and report['status']=='READY' and len(report['cases'])==34
    assert report['external_account_verified'] is False
    assert http('/api/diagnostics/interfaces/'+report['receipt_id'])==(200,report)
    assert len(http('/api/diagnostics/interfaces')[1]['records'])==1 and not wire.calls
    other=LocalApplication(LocalAppConfig(db_path=app.config.db_path,offline=True,session_id='other'))
    try: assert other.route('GET','/api/diagnostics/interfaces/'+report['receipt_id'])[0]==404
    finally: other.close()


def test_explicit_probe_is_one_shot_idempotent_and_safe_after_restart(app_http):
    app,http,wire=app_http
    values={'request_key':'probe-one','target_kind':'model','config_ref':'desk'}
    assert post(app,http,'/api/diagnostics/interfaces/probe',values)[0]==422 and not wire.calls
    status,receipt=post(app,http,'/api/diagnostics/interfaces/probe',{**values,'authorize':True})
    assert status==201 and receipt['status']=='UNAUTHORIZED' and len(wire.calls)==1
    assert receipt['usage'] is None and receipt['request_count']==1
    assert post(app,http,'/api/diagnostics/interfaces/probe',{**values,'authorize':True})[1]==receipt
    assert len(wire.calls)==1
    reopened=LocalApplication(app.config)
    try: assert reopened.route('GET','/api/diagnostics/interfaces/'+receipt['receipt_id'])[2]==receipt
    finally: reopened.close()
    text=json.dumps(receipt)
    for value in ('private-probe-value','do-not-export','FINAHINK_TEST_KEY','models.acme.com'):
        assert value not in text


def test_quality_http_spawn_evidence_and_comparison(app_http):
    app,http,wire=app_http
    path='/api/research/quality/jobs'
    values={'case_id':'complete','idempotency_key':'quality-one','model_config_ref':'desk','allow_model_calls':True,'budget':{'max_wall_seconds':120}}
    assert http(path,values)[0]==403
    assert post(app,http,path,{**values,'allow_model_calls':False})[0]==422
    for budget in ({'max_wall_seconds':301},{'max_provider_calls':10},None):
        assert post(app,http,path,{**values,'budget':budget})[0]==422
    assert app.research_service.list_jobs()['total']==0
    status,created=post(app,http,path,values)
    assert status==201 and post(app,http,path,values)[1]['job_id']==created['job_id']
    assert wait_for(app.research_service,created['job_id'])['status']=='completed'
    result=http(path+'/'+created['job_id'])[1]
    assert result['status']=='EVALUATED' and result['metrics']['citations']['rate']==1
    assert len(http(path)[1]['jobs'])==1 and not wire.calls
    assert post(app,http,'/api/research/quality/compare',{'job_ids':[created['job_id']]*2})[0]==422
    assert http(path+'/missing')[0]==404
    assert http('/api/research/quality/cases',{})[0]==405
    assert app.route('PUT','/api/research/quality/cases')[0]==405


def test_shareable_bundle_adds_only_safe_interface_counts(app_http):
    app,http,wire=app_http
    _,receipt=post(app,http,'/api/diagnostics/interfaces/probe',{'request_key':'bundle-check','target_kind':'model','config_ref':'desk','authorize':True})
    bundle=http('/api/diagnostics/bundle')[1]
    assert bundle['interface_checks']=={'status':'AVAILABLE','stored_records':1,'pending_records':0,'latest_status':'UNAUTHORIZED'}
    assert receipt['receipt_id'] not in json.dumps(bundle)
    assert 'models.acme.com' not in json.dumps(bundle) and len(wire.calls)==1

"""Owned loopback peer: real HTTP faults without reaching user endpoints.

The injectable transports exist only inside this context. They map one fixed
synthetic public identity to the ephemeral server they own; no target supplied
by a caller is forwarded. This verifies JSON contracts, not TLS or accounts.
"""
from __future__ import annotations

import http.client
import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from urllib.parse import urlsplit

from finahinking.data.connection_settings import InMemoryDataCredentialStore
from finahinking.data.http_transport import BoundedHttpTransport
from finahinking.data.user_api_contracts import DataConnectionConfig, DataRequest
from finahinking.p8_2.data_sources import FixtureMarketDataSource

from .credentials import InMemoryCredentialStore
from .interface_diagnostics import data_probe, model_probe
from .provider_config import ProviderConfig
from .provider_status import ProviderCredentialRef
from .provider_transport import ProviderHttpTransport

_HOST='compat.finathink.io'
_ORIGIN='https://'+_HOST
_SCENARIOS=(('ready','READY'),('unauthorized','UNAUTHORIZED'),('forbidden','FORBIDDEN'),('rate_limited','RATE_LIMITED'),('non_json','NON_JSON'),('schema_error','SCHEMA_ERROR'),('stream_interrupted','STREAM_INTERRUPTED'),('chunk_interrupted','STREAM_INTERRUPTED'),('sse','UNSUPPORTED_STREAM'),('timeout','TIMEOUT'),('response_too_large','RESPONSE_TOO_LARGE'))


@contextmanager
def compatibility_peer():
    records=[row.to_dict() for row in FixtureMarketDataSource(points=5).snapshot('AAA').observations]
    class Server(ThreadingHTTPServer):
        daemon_threads=True
        block_on_close=False
        def server_bind(self):
            # HTTPServer normally calls getfqdn even for a numeric loopback
            # address. This owned peer needs no name service in either direction.
            TCPServer.server_bind(self)
            self.server_name='localhost'
            self.server_port=self.server_address[1]
        def handle_error(self,request,address):
            # Expected broken pipes/reset on bounded fault cases; no raw logs.
            pass
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def do_GET(self):
            self.respond(False)
        def do_POST(self):
            self.respond(True)
        def respond(self,model):
            parts=urlsplit(self.path).path.split('/')
            scenario=parts[2] if len(parts)>2 else 'schema_error'
            with self.server.counter_lock:
                self.server.requests+=1
            size=int(self.headers.get('Content-Length','0'))
            if not 0<=size<=262144:
                self.send_error(413)
                return
            payload=json.loads(self.rfile.read(size)) if size else {}
            status={'unauthorized':401,'forbidden':403,'rate_limited':429}.get(scenario,200)
            if scenario=='timeout': time.sleep(.15)
            if model:
                context=json.loads(payload['messages'][1]['content'])
                content={'status':'READY','claims':['Review this synthetic observation.'],'evidence_refs':list(context['evidence_index']),'limitations':['Synthetic compatibility input.']}
                if context['role']=='research':
                    content.update(hypotheses=['Check the observation on independent inputs.'],disagreements=['The supplied synthetic sources express different views.'])
                body={'choices':[{'message':{'content':json.dumps(content)},'finish_reason':'stop'}],'usage':{'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}}
            else:
                body={'data':records}
            if scenario=='missing_fields': body={'data':[{'instrument':'AAA','timestamp':'2026-01-02','close':10}]}
            if scenario=='schema_error': body={'choices':[{}]} if model else {'data':'not-an-array'}
            raw=json.dumps(body).encode()
            if scenario=='non_json': raw=b'private-wire-value'
            if status!=200: raw=b'private-wire-value'
            if scenario=='stream_interrupted': raw=b'{}'
            self.send_response(status)
            self.send_header('Content-Type','text/event-stream' if scenario=='sse' else 'application/json')
            self.send_header('Connection','close')
            if scenario=='chunk_interrupted':
                self.send_header('Transfer-Encoding','chunked')
            else:
                declared=6*1024*1024 if scenario=='response_too_large' else len(raw)+20 if scenario=='stream_interrupted' else len(raw)
                self.send_header('Content-Length',str(declared))
            self.end_headers()
            try:
                self.wfile.write(b'8\r\n{}\r\n' if scenario=='chunk_interrupted' else raw)
                self.wfile.flush()
            except OSError:
                pass
            self.close_connection=True
    server=Server(('127.0.0.1',0),Handler)
    server.requests=0
    server.counter_lock=threading.Lock()
    thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.05},daemon=True)
    thread.start()
    class ModelWire(ProviderHttpTransport):
        def _resolve(self,host):
            if host!=_HOST: raise ValueError('compatibility target invalid')
            return ('1.1.1.1',)
        def _connect(self,host,address,timeout):
            if host!=_HOST or address!='1.1.1.1': raise ValueError('compatibility target invalid')
            return http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=timeout)
    class DataWire(BoundedHttpTransport):
        def _resolve(self,host,port):
            if host!=_HOST: raise ValueError('compatibility target invalid')
            return ('1.1.1.1',)
        def _open(self,request,timeout):
            parsed=urlsplit(request.full_url)
            if parsed.hostname!=_HOST: raise ValueError('compatibility target invalid')
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=timeout)
            connection.request('GET',parsed.path+('?' + parsed.query if parsed.query else ''),headers=dict(request.header_items()))
            response=connection.getresponse()
            class Wrapped:
                status=response.status
                headers=response.headers
                def read(self,amount): return response.read(amount)
                def geturl(self): return request.full_url
                def close(self):
                    response.close()
                    connection.close()
            return Wrapped()
    class Peer:
        model_transport=ModelWire()
        data_transport=DataWire()
        data_credentials=InMemoryDataCredentialStore()
        credentials=InMemoryCredentialStore({ProviderCredentialRef('compat-'+name,env_var='FINAHINK_SYNTHETIC_VALUE'):'synthetic-only-value' for name in ('openai','deepseek')})
        def model_config(self,scenario='ready',kind='openai'):
            return ProviderConfig('compat-'+kind,kind+'_compatible','synthetic-model',endpoint=_ORIGIN+'/'+kind+'/'+scenario,credential_ref=ProviderCredentialRef('compat-'+kind,env_var='FINAHINK_SYNTHETIC_VALUE'))
        def data_config(self,scenario):
            return DataConnectionConfig('compat-data','Synthetic compatibility data',_ORIGIN+'/data/'+scenario,None,'no_auth',{key:key for key in ('instrument','timestamp','available_at','open','high','low','close','volume')},'data')
        @property
        def request_count(self): return server.requests
    try:
        yield Peer()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def run_compatibility_suite():
    cases=[]
    with compatibility_peer() as peer:
        for name,expected in (*_SCENARIOS,('missing_fields','MISSING_FIELDS')):
            actual=data_probe(peer.data_config(name),peer.data_credentials,peer.data_transport,DataRequest('prices',('AAA',),as_of='2026-10-01'),.04,resolver=lambda *_:('1.1.1.1',))['status']
            cases.append({'case_id':'data-'+name,'expected':expected,'observed':actual,'status':'PASS' if actual==expected else 'FAIL'})
        for kind in ('openai','deepseek'):
            for name,expected in _SCENARIOS:
                actual=model_probe(peer.model_config(name,kind),peer.model_transport,peer.credentials,.04)['status']
                cases.append({'case_id':kind+'-'+name,'expected':expected,'observed':actual,'status':'PASS' if actual==expected else 'FAIL'})
        demo=local_quality_examples(peer)
        count=peer.request_count
    return {'status':'READY' if all(case['status']=='PASS' for case in cases) else 'FAILED','scope':'LOCAL_COMPATIBILITY','external_account_verified':False,'tls_verified':False,'request_count':count,'usage':None,'estimated_cost':None,'suite_version':'interface-compatibility.v1','cases':cases,'quality_demo':demo}


def local_quality_examples(peer):
    from .context_bundle import build_context_bundle
    from .contracts import ResearchPlan, ResearchRequest, to_jsonable
    from .drivers import GroundedApiDriver
    from .quality_cases import ROLES, quality_case
    from .quality_evaluation import score_quality

    examples=[]
    for case_id in ('complete','sparse','conflict'):
        case=quality_case(case_id)
        request=ResearchRequest('quality-demo-'+case_id,'SYNTH',case['as_of'],ResearchPlan(hypotheses=(case['question'],)),ROLES,'EQUITY_OR_ETF','quality-demo.v1','synthetic',snapshot_ref=case['snapshot_ref'])
        driver=GroundedApiDriver(case['snapshot'],peer.model_config(),materials=case['materials'],transport=peer.model_transport,credential_store=peer.credentials)
        reports=tuple(report for role in ROLES for report in driver.propose(request,{'analyst_role':role}).reports)
        driver.propose(request,{'analyst_role':'research','reports':reports})
        contexts={role:build_context_bundle(request,case['snapshot'],role,materials=case['materials']) for role in ROLES}
        contexts['research']=build_context_bundle(request,case['snapshot'],'research',materials=case['materials'],reports=reports)
        metrics=score_quality({'model_research':{'calls':driver.call_records,'roles':to_jsonable(reports),'synthesis':driver.synthesis_summary}},contexts,case['expected_roles'])
        examples.append({'case_id':case_id,'mode':'SYNTHETIC_SCORE_EXAMPLE','attempted_calls':metrics['schema_acceptance']['attempted'],
                         'schema_rate':metrics['schema_acceptance']['rate'],'citation_rate':metrics['citations']['rate'],'insufficient_rate':metrics['insufficient_data']['rate']})
    return examples

"""Explicit one-shot diagnostics; public receipts contain no remote material."""
from __future__ import annotations

import math
import time
import uuid

from finahinking.data.http_transport import BoundedHttpTransport
from finahinking.data.user_api import DataConnectorError, JsonApiConnector
from finahinking.data.user_api_contracts import DataRequest
from finahinking.p8_2.data_sources import (
    FixtureMarketDataSource,
    MissingPriceFields,
    snapshot_from_batch,
)

from .context_bundle import build_context_bundle
from .contracts import ResearchPlan, ResearchRequest, stable_digest
from .credentials import LocalProviderCredentialStore
from .provider_adapters import DeepSeekCompatibleAdapter, OpenAICompatibleAdapter, RetryPolicy
from .provider_transport import ProviderHttpTransport
from .providers import ModelEnvelope

_DATA_CODES={'unauthorized':'UNAUTHORIZED','rate_limited':'RATE_LIMITED','invalid_json':'NON_JSON','schema':'SCHEMA_ERROR','timeout':'TIMEOUT','transport':'TRANSPORT','http':'PROVIDER_ERROR','response_too_large':'RESPONSE_TOO_LARGE','stream_interrupted':'STREAM_INTERRUPTED','unsupported_stream':'UNSUPPORTED_STREAM','target':'TARGET_BLOCKED','redirect':'TARGET_BLOCKED','query':'TARGET_BLOCKED','dns':'TRANSPORT','headers':'TRANSPORT','response':'TRANSPORT','credential':'NOT_CONFIGURED','credentials':'NOT_CONFIGURED','pagination':'RESOURCE_LIMIT','record_limit':'RESOURCE_LIMIT','transport_policy':'TARGET_BLOCKED'}


def technical_envelope():
    snapshot=FixtureMarketDataSource(points=4).snapshot('SYNTH')
    request=ResearchRequest('diagnostic-synthetic','SYNTH','2026-10-01',ResearchPlan(hypotheses=('Review only this explicitly synthetic diagnostic sample.',)),('technical',),'EQUITY_OR_ETF','diagnostic.v1','diagnostic',snapshot_ref=snapshot.fingerprint)
    context=build_context_bundle(request,snapshot,'technical')
    return ModelEnvelope(request.run_id,'technical',stable_digest(request),context.fingerprint,research_context=context)


def model_probe(config,transport,credentials,timeout=5):
    attempts=0
    usage=None
    def dispatch(_):
        nonlocal attempts
        attempts+=1
    def observed(value):
        nonlocal usage
        usage={public:value[native] for native,public in [('prompt_tokens','input_units'),('completion_tokens','generated_units'),('total_tokens','total_units')] if native in value}
    adapter_type=DeepSeekCompatibleAdapter if config.adapter_kind=='deepseek_compatible' else OpenAICompatibleAdapter
    endpoint=config.endpoint.rstrip('/')
    if not endpoint.endswith('/chat/completions'): endpoint+='/chat/completions'
    adapter=adapter_type(model=config.model_for_role('technical'),provider_id=config.provider_id,endpoint=endpoint,transport=transport,credential_ref=config.credential_ref,credential_store=credentials,timeout=timeout,retry_policy=RetryPolicy(max_attempts=1),on_dispatch=dispatch,on_usage=observed)
    result=adapter.invoke_contract(technical_envelope())
    return {'status':result.status,'request_count':attempts,'usage':usage}


def data_probe(config,credentials,transport,request,timeout=5,*,resolver=None):
    calls=0
    class Counted:
        def request(self,*args,**kwargs):
            nonlocal calls
            calls+=1
            return transport.request(*args,**kwargs)
    try:
        connector=JsonApiConnector(config,credentials,Counted(),resolver=resolver,timeout=timeout,max_attempts=1,max_pages=1)
        batch=connector.fetch(request)
        snapshot_from_batch(batch)
        return {'status':'READY','request_count':calls,'record_count':len(batch.records),'quality_issue_count':len(batch.quality_issues),'pit_available':batch.pit_available}
    except MissingPriceFields as error:
        return {'status':'MISSING_FIELDS','request_count':calls,'missing_fields':list(error.fields)}
    except DataConnectorError as error:
        status='FORBIDDEN' if error.status_code==403 else _DATA_CODES.get(error.code,'TRANSPORT')
        return {'status':status,'request_count':calls}
    except (ValueError,TypeError,KeyError):
        return {'status':'SCHEMA_ERROR','request_count':calls}


class InterfaceDiagnostics:
    def __init__(self,data_connections,provider_configs,store,*,data_transport=None,provider_transport=None,credential_store=None,allow_model_calls=False):
        self.data_connections,self.provider_configs,self.store=data_connections,provider_configs,store
        self.data_transport=data_transport if data_transport is not None else BoundedHttpTransport()
        self.provider_transport=provider_transport if provider_transport is not None else ProviderHttpTransport()
        self.credentials=credential_store if credential_store is not None else LocalProviderCredentialStore()
        self.allow_model_calls=allow_model_calls

    def run_local(self):
        from .compatibility_peer import run_compatibility_suite
        receipt,owner=self.store.reserve('local-'+uuid.uuid4().hex,{'suite':'interface-compatibility.v1'},kind='LOCAL_COMPATIBILITY')
        assert owner
        started=time.monotonic()
        report=run_compatibility_suite()
        report['duration_ms']=round((time.monotonic()-started)*1000)
        return self.store.finish(receipt['receipt_id'],report)

    def probe(self,values):
        allowed={'request_key','target_kind','config_ref','authorize','request_timeout_seconds','instrument','start','end','as_of'}
        if not isinstance(values,dict) or set(values)-allowed or values.get('authorize') is not True:
            raise ValueError('请明确授权本次接口检查，原输入已保留。')
        kind=values.get('target_kind')
        reference=values.get('config_ref')
        timeout=values.get('request_timeout_seconds',5)
        if kind not in {'data','model'} or not isinstance(reference,str) or not 1<=len(reference)<=128 or type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=5:
            raise ValueError('请核对接口、标的与请求超时，最多5秒。')
        request=DataRequest('prices',(values.get('instrument','AAA'),),values.get('start'),values.get('end'),values.get('as_of'))
        try:
            config=self.data_connections.get(reference) if kind=='data' else self.provider_configs.get(reference)
        except KeyError:
            config=None
        description=None if config is None else ({**config.redacted(),'endpoint':config.base_url} if kind=='data' else config._stored())
        receipt,owner=self.store.reserve(values.get('request_key'),{'values':values,'configuration':description},kind='USER_DATA' if kind=='data' else 'USER_MODEL')
        if not owner: return receipt
        started=time.monotonic()
        report={'status':'UNKNOWN','scope':'USER_ENDPOINT_SAMPLE','external_account_verified':False,'tls_verified':None,'request_count':0,'duration_ms':None,'usage':None,'estimated_cost':None,'cases':[]}
        if kind=='model' and not self.allow_model_calls:
            outcome={'status':'NOT_RUN'}
        elif config is None or (kind=='model' and (not config.enabled or config.adapter_kind=='offline' or not config.endpoint or not config.credential_ref)):
            outcome={'status':'NOT_CONFIGURED'}
        elif kind=='data':
            outcome=data_probe(config,self.data_connections.credentials,self.data_transport,request,timeout)
        else:
            outcome=model_probe(config,self.provider_transport,self.credentials,timeout)
        report.update(outcome,duration_ms=round((time.monotonic()-started)*1000))
        return self.store.finish(receipt['receipt_id'],report)

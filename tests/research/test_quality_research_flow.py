"""Fixed quality tasks use the real persisted queue and controlled spawn peer."""
import json
import sqlite3

import pytest

from finahinking.research.application_service import ResearchApplicationService
from finahinking.research.provider_config import ProviderConfig, ProviderConfigStore
from finahinking.research.provider_status import ProviderCredentialRef
from tests.research.grounded_worker_peer import install_peer
from tests.research.test_application_service import wait_for


@pytest.fixture
def quality(tmp_path):
    from finahinking.research.diagnostic_store import DiagnosticStore
    from finahinking.research.quality_evaluation import QualityEvaluation
    configs=ProviderConfigStore()
    for name in ('desk','second'):
        configs.save(ProviderConfig(name,'openai_compatible','user-model',endpoint='https://models.acme.com/v1',credential_ref=ProviderCredentialRef(name,env_var='FINAHINK_TEST_KEY')))
    service=ResearchApplicationService(tmp_path/'artifacts',scope_id='eval-scope',provider_configs=configs)
    install_peer(service)
    store=DiagnosticStore(tmp_path/'diag.sqlite3',scope_id=service.scope_id)
    try:
        yield QualityEvaluation(service,store)
    finally:
        service.close()


def values(case='complete',key='task-one',**changes):
    return {'case_id':case,'idempotency_key':key,'model_config_ref':'desk','allow_model_calls':True,'budget':{'max_wall_seconds':120},**changes}


def test_catalog_has_stable_bounded_synthetic_inputs():
    from finahinking.research.quality_cases import quality_case, quality_cases
    catalog=quality_cases()
    assert {item['case_id'] for item in catalog}=={'complete','sparse','conflict'}
    for item in catalog:
        case=quality_case(item['case_id'])
        assert item['case_digest']==case['case_digest'] and item['data_mode']=='SAMPLE'
        assert case['snapshot'].pit_available() and len(case['snapshot'].observations)>=4
        assert len(json.dumps(item,ensure_ascii=False))<32768
    assert sum(value=='INSUFFICIENT_DATA' for value in quality_case('sparse')['expected_roles'].values())==4


def test_submit_needs_consent_and_is_idempotent_even_after_lost_binding(quality):
    with pytest.raises(ValueError): quality.submit(values(allow_model_calls=False))
    assert quality.service.list_jobs()['total']==0 and not quality.service.snapshots.list_snapshots()
    first=quality.submit(values())
    again=quality.submit(values())
    assert first['job_id']==again['job_id'] and quality.service.list_jobs()['total']==1
    with sqlite3.connect(quality.store.path) as db:
        db.execute('DELETE FROM quality_bindings')
    recovered=quality.submit(values())
    assert recovered['job_id']==first['job_id'] and quality.service.list_jobs()['total']==1
    with pytest.raises(ValueError): quality.submit(values(case='sparse'))
    assert quality.service.list_jobs()['total']==1


@pytest.mark.parametrize('case_id', ['complete','sparse','conflict'])
def test_fixed_case_spawn_publication_evaluation_and_reopen(quality,case_id):
    from finahinking.research.quality_evaluation import QualityEvaluation
    created=quality.submit(values(case_id))
    initial=quality.evaluate(created['job_id'])
    assert initial['status']=='NOT_STARTED' and initial['metrics']['schema_acceptance']['rate'] is None
    quality.service.start()
    done=wait_for(quality.service,created['job_id'])
    assert done['status']=='completed'
    result=quality.evaluate(created['job_id'])
    assert result['status']=='EVALUATED' and result['data_mode']=='SAMPLE'
    assert result['metrics']['schema_acceptance']['rate']==1
    assert result['metrics']['citations']['rate']==1
    if case_id=='sparse':
        assert result['metrics']['insufficient_data']['rate']==1
        assert result['metrics']['schema_acceptance']['attempted']==2
    else:
        assert result['metrics']['disagreements']
    assert all(link.startswith('/research/'+done['run_id']+'/report/complete#') for link in result['evidence_links'])
    assert 'test-only-model-value' not in json.dumps(result) and 'FINAHINK_TEST_KEY' not in json.dumps(result)
    quality.service.close()
    reopened=ResearchApplicationService(quality.service.root,scope_id=quality.service.scope_id)
    try:
        again=QualityEvaluation(reopened,quality.store).evaluate(done['job_id'])
        assert again==result
    finally:
        reopened.close()


def test_frozen_conditions_compare_models_but_not_task_or_budget(quality):
    a=quality.submit(values(key='a'))
    b=quality.submit(values(key='b',model_config_ref='second'))
    c=quality.submit(values(case='sparse',key='c'))
    d=quality.submit(values(key='d',budget={'max_wall_seconds':90}))
    quality.service.start()
    for job in (a,b,c,d): assert wait_for(quality.service,job['job_id'])['status']=='completed'
    assert quality.compare([a['job_id'],b['job_id']])['comparable'] is True
    assert quality.compare([a['job_id'],c['job_id']])['comparable'] is False
    assert quality.compare([a['job_id'],d['job_id']])['comparable'] is False
    with pytest.raises(ValueError): quality.compare([a['job_id']]*2)


def test_material_or_manifest_damage_cannot_produce_scores(quality):
    job=quality.submit(values())
    quality.service.start()
    done=wait_for(quality.service,job['job_id'])
    req=quality.service._request(quality.service.queue.get(done['job_id']))
    source=quality.service.root/'model-inputs'/(req.research_plan.validation_spec['context_input_ref']+'.json')
    before=source.read_bytes()
    source.write_text('{}')
    with pytest.raises(ValueError): quality.evaluate(done['job_id'])
    source.write_bytes(before)
    entry=quality.service.runs.get_run(done['run_id'])
    (entry['bundle']/'complete_report.html').write_text('broken')
    result=quality.evaluate(done['job_id'])
    assert result['status']=='UNAVAILABLE' and result['metrics']['schema_acceptance']['rate'] is None


def test_cross_scope_and_cancelled_job_do_not_get_a_quality_grade(quality):
    from finahinking.research.diagnostic_store import DiagnosticStore
    from finahinking.research.quality_evaluation import QualityEvaluation
    job=quality.submit(values())
    quality.service.cancel(job['job_id'])
    result=quality.evaluate(job['job_id'])
    assert result['status']=='NOT_STARTED' and result['metrics']['schema_acceptance']['rate'] is None
    other=QualityEvaluation(quality.service,DiagnosticStore(quality.store.path,scope_id='other'))
    with pytest.raises(KeyError): other.evaluate(job['job_id'])


def test_local_scoring_examples_stay_explicitly_synthetic(tmp_path):
    from tests.research.test_interface_diagnostics import setup
    service,_,_=setup(tmp_path)
    result=service.run_local()
    assert {item['case_id'] for item in result['quality_demo']}=={'complete','sparse','conflict'}
    assert all(item['mode']=='SYNTHETIC_SCORE_EXAMPLE' for item in result['quality_demo'])
    assert result['external_account_verified'] is False


def test_actual_journal_projection_distinguishes_active_finished_cancelled_and_unsent_calls(quality):
    from finahinking.research.context_bundle import build_context_bundle
    from finahinking.research.contracts import stable_digest
    from finahinking.research.model_call_journal import ModelCallJournal
    from finahinking.research.quality_cases import quality_case

    def journal_for(key):
        job=quality.submit(values(key=key))
        request=quality.service._request(quality.service.queue.get(job['job_id']))
        case=quality_case('complete')
        context=build_context_bundle(request,case['snapshot'],'technical',materials=case['materials'])
        journal=ModelCallJournal(quality.service.root/'model-calls.sqlite3',job_id=job['job_id'],run_id=request.run_id,request_digest=stable_digest(request),attempt=1,scope_id=quality.service.scope_id)
        call=journal.begin({'role':'technical','model':'user-model','context_fingerprint':context.fingerprint,'context_bytes':len(context._json.encode())})
        return job,journal,call

    active,journal,call=journal_for('actual-active')
    journal.dispatched(call,100)
    assert journal.records()[0]['status']=='DISPATCHED'
    result=quality.evaluate(active['job_id'])['metrics']
    assert result['schema_acceptance']=={'attempted':1,'accepted':0,'unknown':1,'not_sent':0,'rate':None}
    assert result['usage']['total_units'] is None and result['model_duration_ms'] is None
    journal.finish(call,{'status':'READY','usage':{'total_units':0},'duration_ms':0})
    result=quality.evaluate(active['job_id'])['metrics']
    assert result['schema_acceptance']['rate']==1 and result['schema_acceptance']['unknown']==0
    assert result['usage']['total_units']==0 and result['model_duration_ms']==0

    cancelled,unknown,call=journal_for('actual-cancelled')
    unknown.dispatched(call,100)
    quality.service.cancel(cancelled['job_id'])
    result=quality.evaluate(cancelled['job_id'])['metrics']
    assert result['schema_acceptance']['unknown']==1 and result['schema_acceptance']['rate'] is None
    assert result['usage']['total_units'] is None and len(unknown.records(terminal=True))==1

    unsent,prepared,_=journal_for('actual-unsent')
    quality.service.cancel(unsent['job_id'])
    result=quality.evaluate(unsent['job_id'])['metrics']
    assert result['schema_acceptance']['attempted']==0 and result['schema_acceptance']['not_sent']==1
    assert result['schema_acceptance']['rate'] is None and prepared.records(terminal=True)[0]['status']=='NOT_SENT'
    compared=quality.compare([active['job_id'],cancelled['job_id'],unsent['job_id']])
    assert [row['metrics']['schema_acceptance']['rate'] for row in compared['items']]==[1,None,None]

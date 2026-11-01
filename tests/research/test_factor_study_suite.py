"""Pre-registered suites retain every attempt and final holdout identity."""
import importlib.util


def test_suite_contract_is_available():
    assert importlib.util.find_spec('finahinking.research.factor_study_suite'), 'suite contract absent'


import pytest

from finahinking.research.application_service import ResearchApplicationService
from finahinking.research.factor_library import FactorLibrary
from finahinking.research.factor_study_suite import POLICY, _signal, run_factor_study_suite
from finahinking.research.factor_validation_sample import SAMPLE_SPEC, ValidationSample
from tests.research.test_factor_study_application import wait_study


def payload(key='suite-one', factors=None, **kwargs):
    return {'idempotency_key':key,'family_key':'fixed-global-family','question':'检验预登记的因子与反例',
        'factor_ids':factors or ['lib-trend-roc'],'sample_spec':{**SAMPLE_SPEC,'assets':8,'observations':60},**kwargs}


def test_fixed_sample_is_content_hashed_and_variants_are_distinct():
    sample=ValidationSample({**SAMPLE_SPEC,'assets':8,'observations':60})
    assert sample.fingerprint==ValidationSample(sample.spec).fingerprint
    assert sample.content_digest!=sample.fingerprint
    assert len(sample.times)==60 and len(sample.assets)==8
    hashes={ValidationSample({**sample.spec,'variant':v}).fingerprint for v in ('baseline','suspension','delisting','extreme_return','late_announcement','coverage_gap')}
    assert len(hashes)==6
    with pytest.raises(ValueError):ValidationSample({**sample.spec,'price_basis':'ADJUSTED'})


def test_shard_boundary_matches_full_legal_panel_and_future_is_hidden():
    sample=ValidationSample({**SAMPLE_SPEC,'assets':8,'observations':60});library=FactorLibrary()
    for factor_id in ('lib-trend-ma-spread','lib-industry-employment-growth','lib-growth-rd-capital'):
        spec=library.spec(factor_id);signal,_,_= _signal(library,spec,sample,30)
        full=library.evaluate(spec,sample.reference_panel(library.get(factor_id),0,30))
        for row in full['values']:
            actual=signal.at[__import__('pandas').Timestamp(row['observed_at']),row['instrument']]
            if row['value'] is not None:assert actual==pytest.approx(row['value'])
        assert signal.index[-1]==sample.times[29]


def test_all_trials_including_failure_and_cancel_are_recorded(tmp_path):
    service=ResearchApplicationService(tmp_path,allow_model_calls=False)
    try:
        created=service.create_factor_suite(payload())
        assert service.create_factor_suite(payload())['study_id']==created['study_id']
        trials=service.factor_studies.suite_trials(created['study_id'])
        assert trials[0]['state']=='REGISTERED'
        with service.queue._connect() as db:
            assert db.execute('SELECT count(*) FROM factor_suite_family').fetchone()[0]==80
        service.cancel(created['validation_job']['job_id'])
        service.factor_study(created['study_id'])
        assert service.factor_studies.suite_trials(created['study_id'])[0]['state']=='CANCELLED'
        with pytest.raises(ValueError):service.create_factor_suite(payload(factors=['lib-value-book-market']))
    finally:service.close()


def test_real_worker_scorecard_freeze_restart_and_terminal_test_not_reused(tmp_path):
    service=ResearchApplicationService(tmp_path,allow_model_calls=False)
    try:
        created=service.create_factor_suite(payload());service.start()
        result=wait_study(service,created['study_id'],'validation')
        assert result['state']=='VALIDATED',result['validation_job']
        trial=result['validation']['suite_validation']['trials'][0]
        assert trial['status'] in {'PASS','FALSIFIED','NEEDS_DATA'}
        assert trial['financial_falsification'] is False
        assert len(trial['counterevidence_refs'])>=20
        assert result['validation_job']['report_status']=='AVAILABLE'
        assert result['validation']['test_evaluation'] is None
        service.freeze_factor_study(created['study_id'],result['validation']['ranking'][0])
        service.test_factor_study(created['study_id'],'final-once')
        tested=wait_study(service,created['study_id'],'test')
        assert tested['test_result']['suite_validation']['frozen_test_ref']=='lib-trend-roc'
        with service.queue._connect() as db:assert db.execute('SELECT count(*) FROM factor_test_claims').fetchone()[0]==8
        with pytest.raises(ValueError):service.test_factor_study(created['study_id'],'changed-parameters')
        if tested['test_result']['suite_validation']['trials'][0]['status']!='PASS':
            with pytest.raises(ValueError):service.review_factor_study(created['study_id'],'APPROVE_FOR_RESEARCH','reviewer')
        service.review_factor_study(created['study_id'],'REJECT','reviewer')
        second=service.create_factor_suite(payload(key='suite-two',family_key='second-family'))
        validated=wait_study(service,second['study_id'],'validation')
        service.freeze_factor_study(second['study_id'],validated['validation']['ranking'][0])
        with pytest.raises(ValueError):service.test_factor_study(second['study_id'],'reused-final')
    finally:service.close()
    restarted=ResearchApplicationService(tmp_path,allow_model_calls=False)
    try:assert restarted.factor_study(created['study_id'])['test_job']['report_status']=='AVAILABLE'
    finally:restarted.close()


@pytest.mark.parametrize('variant',['suspension','delisting','extreme_return','late_announcement','coverage_gap'])
def test_boundary_variants_execute_without_market_claims(variant):
    sample=ValidationSample({**SAMPLE_SPEC,'assets':8,'observations':60,'variant':variant})
    result=run_factor_study_suite('study-variant',[FactorLibrary().spec('lib-value-book-market')],sample,POLICY)
    assert len(result['trials'])==1
    assert result['trials'][0]['status']!='EXECUTION_FAILED',result['trials'][0]['failure_reason']
    assert not result['trials'][0]['financial_falsification']


@pytest.mark.parametrize('variant',['baseline','suspension','delisting','extreme_return','late_announcement','coverage_gap'])
@pytest.mark.parametrize('factor_id',['lib-trend-ma-spread','lib-industry-employment-growth','lib-growth-rd-capital'])
def test_sample_fast_projection_matches_general_pit_adapter(variant,factor_id):
    import pandas as pd
    sample=ValidationSample({**SAMPLE_SPEC,'assets':8,'observations':60,'variant':variant})
    entry=FactorLibrary().get(factor_id)
    fast=sample.panel(entry,0,30);reference=sample.reference_panel(entry,0,30)
    pd.testing.assert_frame_equal(fast.frame[reference.frame.columns],reference.frame,check_dtype=False)
    assert fast.status==reference.status
    assert fast.audit['reasons']==reference.audit['reasons']
    assert fast.audit['row_count']<=10000 and fast.audit['cell_count']<=50000


def test_canonical_64_by_756_matches_frozen_content_hash():
    import json
    from pathlib import Path
    frozen=json.loads((Path(__file__).parents[1]/'fixtures/quant_validation/sample-fingerprint.json').read_text())
    sample=ValidationSample()
    assert sample.spec==frozen['sample_spec']
    assert sample.fingerprint==frozen['sample_fingerprint']
    assert sample.content_digest==frozen['sample_content_digest']
    assert len(sample.times)*len(sample.assets)==64*756


def test_same_factor_family_owner_is_atomic_and_does_not_leave_orphan(tmp_path):
    service=ResearchApplicationService(tmp_path,allow_model_calls=False)
    try:
        first=service.create_factor_suite(payload())
        assert service.create_factor_suite(payload())['study_id']==first['study_id']
        with pytest.raises(ValueError):service.create_factor_suite(payload(key='duplicate-attempt'))
        assert len(service.factor_studies.list())==1
        assert service.list_jobs()['total']==1
    finally:service.close()


def test_declared_direction_ranks_pass_before_strong_negative_falsification():
    from finahinking.research.factor_study_suite import rank_suite_experiments
    records=[{'experiment_id':'negative','ic':-.9,'hypothesis_status':'FALSIFIED','failure_reason':None},
        {'experiment_id':'positive','ic':.2,'hypothesis_status':'PASS','failure_reason':None},
        {'experiment_id':'positive-low','ic':.1,'hypothesis_status':'PASS','failure_reason':None}]
    assert rank_suite_experiments(records)==['positive','positive-low','negative']


def test_validation_diagnostics_cannot_read_future_missingness():
    import copy

    import pandas as pd

    from finahinking.data.research_panel import ResearchPanel
    sample=ValidationSample({**SAMPLE_SPEC,'assets':8,'observations':60});library=FactorLibrary()
    spec=library.spec('lib-value-book-market');panel=sample.panel(library.get(spec.factor_id),0,36)
    before=_signal(library,spec,panel,sample.times[25])
    frame=panel.frame;frame.loc[(sample.times[30],sample.assets[0]),'book_equity']=float('nan')
    audit=copy.deepcopy(panel.audit);reason='missing_field：book_equity 在此时点不可用'
    audit.update(status='NEEDS_DATA',reasons=[reason],row_reasons=[{'observed_at':sample.times[30].isoformat(),'instrument':sample.assets[0],'reasons':[reason]}])
    changed=ResearchPanel(frame,audit,panel.contract,'f'*64)
    after=_signal(library,spec,changed,sample.times[25])
    pd.testing.assert_frame_equal(before[0],after[0])
    assert before[2]==after[2]==[]


@pytest.mark.parametrize('mode,expected',[('SAMPLE','SAMPLE'),('SYNTHETIC','SAMPLE'),('CAPTURED','USER_DATA'),('LIVE','USER_DATA'),('PAPER','UNKNOWN')])
def test_panel_origin_comes_from_saved_snapshot_mode(tmp_path,mode,expected):
    from dataclasses import replace

    from finahinking.data.panel_contracts import PanelFieldAttachment
    from finahinking.data.research_fields import ResearchFieldStore
    from finahinking.research.factor_study_suite import prepare_suite
    from tests.research.test_factor_library_journey import prepare
    service=ResearchApplicationService(tmp_path,allow_model_calls=False)
    try:
        values=prepare(service,'lib-value-book-market')
        snapshot=replace(service.snapshots.load(values['contract']['snapshot_ref']),mode=mode)
        service.snapshots.save(snapshot)
        fieldstore=ResearchFieldStore(service.snapshots)
        old=fieldstore.load(values['attachment_refs'][0])
        attachment=PanelFieldAttachment(snapshot.fingerprint,old.units,old.records,'Origin fixture')
        fieldstore.save(attachment)
        contract={k:v for k,v in values['contract'].items() if k not in {'fingerprint','schema_version'}}
        contract['snapshot_ref']=snapshot.fingerprint
        for key in ('calendar_ref','universe_ref','adjustment_ref','trade_rule_ref'):contract[key]=attachment.fingerprint
        from finahinking.data.panel_contracts import PanelContract
        row=prepare_suite({'idempotency_key':'origin','factor_ids':['lib-value-book-market'],'question':'来源验证','contract':PanelContract(**contract).to_dict(),'attachment_refs':[attachment.fingerprint]},service.snapshots,policy_fingerprint='a'*64)
        assert row['data_mode']==expected
    finally:service.close()


@pytest.mark.parametrize('status',['FALSIFIED','NEEDS_DATA','EXECUTION_FAILED','CANCELLED'])
def test_final_research_approval_gate_deterministically_rejects_nonpass(tmp_path,monkeypatch,status):
    """Gate unit test; real frozen/tested worker evidence is tested separately."""
    service=ResearchApplicationService(tmp_path,allow_model_calls=False)
    try:
        verified_shape={'state':'TEST_EVALUATED','test_result':{
            'test_summary':{'failure_reason':None},'suite_validation':{'trials':[{'status':status}]}}}
        monkeypatch.setattr(service,'factor_study',lambda _study_id:verified_shape)
        with pytest.raises(ValueError,match='只有最终测试PASS'):
            service.review_factor_study('study-unit-gate','APPROVE_FOR_RESEARCH','reviewer')
    finally:service.close()


def test_corrupt_published_report_blocks_suite_evidence_and_family_projection(tmp_path):
    from finahinking.research.research_evidence import build_research_evidence
    service=ResearchApplicationService(tmp_path,allow_model_calls=False)
    try:
        created=service.create_factor_suite(payload());service.start()
        completed=wait_study(service,created['study_id'],'validation')
        refs=completed['validation']['suite_validation']['trial_refs']
        before=build_research_evidence(refs,catalogue=service.factor_suite_catalogue())
        assert before['status']=='COMPLETE'
        run=service.runs.get_run(completed['validation_job']['run_id'])
        (run['bundle']/'complete_report.html').write_text('Corrupt actual published report fixture')
        after=build_research_evidence(refs,catalogue=service.factor_suite_catalogue())
        assert after['status']=='BLOCKED'
        assert after['records'][0]['blockers']
        family=service.factor_suite_family(completed['spec']['family_ref'])
        assert family['published_count']==0
        assert family['selection_status']=='INCOMPLETE_WITH_UNKNOWN_ATTEMPTS'
    finally:service.close()

"""Library card → real queue/spawn → evaluator, paper ledger and immutable report."""
import math

import pandas as pd
import pytest

from finahinking.data.panel_contracts import PanelContract, PanelFieldAttachment
from finahinking.data.research_fields import ResearchFieldStore
from finahinking.p8_2.contracts import DatasetSnapshot, MarketObservation
from finahinking.research.application_service import ResearchApplicationService
from finahinking.research.contracts import stable_digest
from finahinking.research.factor_library import FactorLibrary
from tests.research.test_factor_study_application import wait_study

REPRESENTATIVES = ('lib-trend-roc','lib-reversal-rsi','lib-volatility-return-std','lib-liquidity-turnover',
    'lib-value-book-market','lib-quality-operating-margin','lib-growth-asset-growth','lib-industry-book-market',
    'lib-event-equity-finance','lib-etf-macro-correlation')


def prepare(service, factor_id, *, missing=False, lot_size=1):
    row=FactorLibrary().get(factor_id)
    times=pd.date_range('2026-01-01', periods=120, tz='UTC')
    observations=[]
    for a,asset in enumerate(('AAA','BBB','CCC')):
        for i,t in enumerate(times):
            close=30+a*7+i*.03+math.sin(i*1.7+a)*(1+a*.3)
            observations.append(MarketObservation(asset,t,close-.1,close+.2,close-.2,close,10000+i*13,
                amount=close*(10000+i*13),available_at=t,suspended=False))
    snapshot=DatasetSnapshot('synthetic-library-engine-fixture',tuple(observations),provenance={'adjustment':'RAW'})
    service.snapshots.save(snapshot)
    units={'is_session':'boolean','in_universe':'boolean','trade_allowed':'boolean',
        'lot_size':'shares','settlement_days':'sessions','split_factor':'ratio'}
    native={'open','high','low','close','volume','amount','suspended'}
    units.update({k:v for k,v in row['field_units'].items() if k not in native})
    records=[]
    for a,asset in enumerate(snapshot.instruments):
        for i,t in enumerate(times):
            fields={}
            for j,k in enumerate(row['required_fields']):
                if k in native or (missing and k==row['required_fields'][0]): continue
                if k=='industry': value='fixture-sector'
                elif k in {'strategy_return','benchmark_return'}: value=.01*math.sin(i/(3+a)+(0.5 if k=='benchmark_return' else 0))
                elif k=='turnover_rate': value=.02+.005*math.sin(i/(3+a))
                else: value=1000+(j+1)*50+a*100+i*(a+1)+math.sin(i/(3+a))*(j+1)*30
                fields[k]=value
            records.append({'instrument':asset,'timestamp':t.isoformat(),'available_at':t.isoformat(),
                'is_session':True,'in_universe':True,'trade_allowed':True,'lot_size':lot_size,'settlement_days':0,
                'split_factor':1,**fields})
    attachment=PanelFieldAttachment(snapshot.fingerprint,units,records,'Synthetic independent integration input')
    ResearchFieldStore(service.snapshots).save(attachment)
    field_units={'open':'CNY','high':'CNY','low':'CNY','close':'CNY','volume':'shares',**row['field_units']}
    contract=PanelContract(snapshot_ref=snapshot.fingerprint,instrument_ids=snapshot.instruments,
        observed_at=[t.isoformat() for t in times],field_units=field_units,scope_id='library-fixture',
        currencies={a:'CNY' for a in snapshot.instruments},timezones={a:'UTC' for a in snapshot.instruments},
        calendar_ref=attachment.fingerprint,universe_ref=attachment.fingerprint,
        adjustment_ref=attachment.fingerprint,trade_rule_ref=attachment.fingerprint)
    return {'factor_id':factor_id,'factor_version':row['version'],'idempotency_key':factor_id,
        'question':'复现固定公式并检验合成样本中的数值链路','contract':contract.to_dict(),
        'attachment_refs':[attachment.fingerprint]}


@pytest.mark.parametrize('factor_id',REPRESENTATIVES)
def test_each_family_runs_real_spawn_and_publishes_bound_ledger(tmp_path,factor_id):
    service=ResearchApplicationService(tmp_path,allow_model_calls=False)
    try:
        payload=prepare(service,factor_id)
        created=service.create_library_factor_study(payload)
        service.start()
        result=wait_study(service,created['study_id'],'validation')
        evidence=result['validation']['library_validation']
        assert evidence['status']=='COMPLETE', evidence
        assert evidence['factor_spec']['factor_id']==factor_id
        assert evidence['snapshot_ref']==payload['contract']['snapshot_ref']
        assert evidence['panel_fingerprint']==result['spec']['panel_fingerprint']
        assert evidence['policy_fingerprint']==result['spec']['policy_fingerprint']
        assert evidence['validation_policy_fingerprint']==stable_digest(result['spec']['validation_policy'])
        conditions=result['validation']['summaries'][0]['conditions']
        assert conditions['policy_fingerprint']==evidence['policy_fingerprint']
        assert conditions['validation_policy_fingerprint']==evidence['validation_policy_fingerprint']
        assert conditions['instruments']==payload['contract']['instrument_ids']
        from finahinking.research.reports import compare_experiments
        comparison=compare_experiments([{'run_id':'library-check','metrics':{},'conditions':conditions}])
        assert comparison['runs'][0]['comparability'] != 'UNKNOWN'
        assert evidence['backtest']['metric_basis']=='ACTUAL_NET_LEDGER'
        assert len(evidence['backtest']['quant_runs'])==3
        assert all(r['threshold_scope']=='TRAIN_ONLY' for r in evidence['backtest']['quant_runs'])
        assert result['validation']['test_evaluation'] is None
        assert result['validation_job']['report_status']=='AVAILABLE'
        assert result['validation']['production_write'] is False
        assert result['state']=='VALIDATED'
    finally:
        service.close()


def test_library_freeze_test_restart_and_multi_asset_holdout_consumption(tmp_path):
    service=ResearchApplicationService(tmp_path,allow_model_calls=False)
    try:
        payload=prepare(service,REPRESENTATIVES[0]);created=service.create_library_factor_study(payload)
        service.start();validated=wait_study(service,created['study_id'],'validation')
        assert service.create_library_factor_study(payload)['study_id']==created['study_id']
        service.freeze_factor_study(created['study_id'],validated['validation']['ranking'][0])
        service.test_factor_study(created['study_id'],'one-heldout-test')
        tested=wait_study(service,created['study_id'],'test')
        assert tested['state']=='TEST_EVALUATED'
        assert tested['test_result']['library_validation']['phase']=='test'
        service.review_factor_study(created['study_id'],'APPROVE_FOR_RESEARCH','fixture-reviewer')
        assert service.factor_uses.list()==[]
        with service.queue._connect() as db:
            assert {r['instrument'] for r in db.execute('SELECT instrument FROM factor_test_claims')}=={'AAA','BBB','CCC'}
        with pytest.raises(ValueError): service.test_factor_study(created['study_id'],'another-heldout-test')
        contract_values=payload['contract'].copy()
        contract_values.pop('schema_version');contract_values.pop('fingerprint')
        contract_values['instrument_ids']=['BBB','CCC']
        contract_values['currencies']={a:'CNY' for a in ('BBB','CCC')}
        contract_values['timezones']={a:'UTC' for a in ('BBB','CCC')}
        overlapping=service.create_library_factor_study({**payload,'idempotency_key':'overlapping-panel',
            'contract':PanelContract(**contract_values).to_dict()})
        overlap_result=wait_study(service,overlapping['study_id'],'validation')
        service.freeze_factor_study(overlapping['study_id'],overlap_result['validation']['ranking'][0])
        with pytest.raises(ValueError,match='overlap'):
            service.test_factor_study(overlapping['study_id'],'overlapping-test')
    finally: service.close()
    restarted=ResearchApplicationService(tmp_path,allow_model_calls=False)
    try:
        assert restarted.factor_study(created['study_id'])['state']=='REVIEWED'
        assert restarted.factor_study(created['study_id'])['test_job']['report_status']=='AVAILABLE'
    finally: restarted.close()


def test_missing_fields_publish_blocked_report_and_cannot_freeze(tmp_path):
    service=ResearchApplicationService(tmp_path,allow_model_calls=False)
    try:
        payload=prepare(service,'lib-value-book-market',missing=True)
        created=service.create_library_factor_study(payload);service.start()
        result=wait_study(service,created['study_id'],'validation')
        assert result['state']=='VALIDATION_FAILED'
        assert result['validation']['library_validation']['status']=='BLOCKED_DATA'
        with pytest.raises(ValueError): service.freeze_factor_study(created['study_id'],'experiment-library')
    finally: service.close()


def test_immutable_library_version_cancel_and_restart(tmp_path):
    service=ResearchApplicationService(tmp_path,allow_model_calls=False)
    try:
        payload=prepare(service,REPRESENTATIVES[0])
        with pytest.raises(ValueError): service.create_library_factor_study({**payload,'factor_version':'2.0.0'})
        created=service.create_library_factor_study(payload)
        service.cancel(created['validation_job']['job_id'])
        assert service.factor_study(created['study_id'])['state']=='VALIDATION_FAILED'
        with pytest.raises(ValueError): service.create_library_factor_study({**payload,'question':'改变同一幂等输入'})
    finally: service.close()


def test_queued_library_study_recovers_in_real_worker_after_restart(tmp_path):
    service=ResearchApplicationService(tmp_path,allow_model_calls=False)
    payload=prepare(service,REPRESENTATIVES[0])
    created=service.create_library_factor_study(payload)
    service.close()
    restarted=ResearchApplicationService(tmp_path,allow_model_calls=False)
    try:
        restarted.start()
        result=wait_study(restarted,created['study_id'],'validation')
        assert result['state']=='VALIDATED'
        assert result['spec_ref']==created['spec_ref']
        assert result['validation_job']['job_id']==created['validation_job']['job_id']
    finally: restarted.close()


def test_unsupported_real_market_rules_keep_ic_but_block_paper_execution(tmp_path):
    service=ResearchApplicationService(tmp_path,allow_model_calls=False)
    try:
        payload=prepare(service,REPRESENTATIVES[0],lot_size=100)
        created=service.create_library_factor_study(payload);service.start()
        result=wait_study(service,created['study_id'],'validation')
        evidence=result['validation']['library_validation']
        assert evidence['status']=='BLOCKED_DATA' and evidence['backtest'] is None
        assert evidence['evaluations'][0]['sample_size']>8
        assert result['validation']['experiments'][0]['failure_reason']=='UNSUPPORTED_TRADING_RULES'
    finally: service.close()


@pytest.mark.parametrize('url',['http://example.com/source','javascript:alert(1)','https://user:password@example.com/source','https://[bad'])
def test_source_card_links_require_safe_https_and_escape_source_code(url):
    from finahinking.research.factor_library_page import _source_card
    rendered=_source_card({'card_id':'source-only','title':'<script>alert(1)</script>',
        'source_url':url,'formula_status':'EXPLICIT','adaptation_status':'REFERENCE_ONLY',
        'evidence_status':'SOURCE_REPORTED','original_formula':'<svg onload=alert(1)>'})
    assert '<script>' not in rendered and '<svg' not in rendered
    assert '&lt;svg' in rendered and '仅作来源参考' in rendered
    assert 'data-source-link' not in rendered

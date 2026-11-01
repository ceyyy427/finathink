"""Pre-registered, bounded factor diagnostics in the existing study worker."""
from __future__ import annotations

import copy
from collections import Counter

import pandas as pd

from finahinking.data.panel_contracts import PanelContract
from finahinking.data.research_panel import ResearchPanel, build_research_panel
from finahinking.quant.evaluation.robustness import adjust_pvalues, score_factor

from .contracts import stable_digest
from .factor_library import FactorLibrary
from .factor_validation_sample import ValidationSample
from .factor_workbench import ExperimentSummary
from .observability import current_runtime_budget
from .temporal_validation import purged_walk_forward

VERSION='factor-workbench.suite.v1'
POLICY={'version':'factor-suite-policy.v1','fee_bps':5.,'slippage_bps':5.,'min_ic':.02,
        'min_net_return':0.,'direction':1,'quantiles':5,'horizons':[1,5,20],'min_assets':4,'min_periods':8,
        'selection_family_size':80,'correction':'holm','interpretation':'exploratory diagnostics; not an execution ledger'}


def prepare_suite(values, snapshots, *, policy_fingerprint):
    if not isinstance(values,dict) or set(values)-{'idempotency_key','family_key','factor_ids','question','sample_spec','contract','attachment_refs'}:
        raise ValueError('批量研究接受固定因子、样例或用户面板引用')
    ids=values.get('factor_ids');library=FactorLibrary()
    if not isinstance(ids,list) or not 1<=len(ids)<=24 or len(set(ids))!=len(ids):raise ValueError('每批需要1到24个不同因子')
    factors=[library.spec(f).to_dict() for f in ids]
    question=values.get('question')
    if not isinstance(question,str) or not 1<=len(question.strip())<=2000:raise ValueError('请填写有界研究问题')
    if 'sample_spec' in values:
        if 'contract' in values or values.get('attachment_refs'):raise ValueError('样例与用户面板不能混用')
        sample=ValidationSample(values['sample_spec']);snapshot=sample.anchor();snapshots.save(snapshot)
        times,assets=sample.times,sample.assets
        source={'sample_spec':sample.spec,'sample_fingerprint':sample.fingerprint,'sample_content_digest':sample.content_digest,'data_mode':'SAMPLE'}
    else:
        contract=PanelContract.from_dict(values['contract']);refs=values.get('attachment_refs',[])
        panel=snapshots.build_panel(contract,refs);snapshot=snapshots.load(contract.snapshot_ref)
        times=panel.frame.index.get_level_values('observed_at').unique().sort_values();assets=contract.instrument_ids
        source={'panel_contract':contract.to_dict(),'attachment_refs':sorted(refs),'panel_fingerprint':panel.fingerprint,'data_mode':'SAMPLE' if snapshot.mode in {'SAMPLE','SYNTHETIC'} else 'USER_DATA' if snapshot.mode in {'CAPTURED','LIVE'} else 'UNKNOWN'}
    split=purged_walk_forward(times,horizon=1,embargo=1,n_splits=2)
    if len(split['validation'])<8 or len(split['test'])<8:raise ValueError('切分后样本不足')
    family_key=values.get('family_key',values['idempotency_key'])
    if not isinstance(family_key,str) or not 1<=len(family_key)<=128:raise ValueError('试验家族标识无效')
    family_ref=stable_digest({'family_key':family_key,'source':source,'policy':POLICY})
    return {'version':VERSION,'snapshot_ref':snapshot.fingerprint,'instrument':assets[0],
        'instrument_ids':list(assets),'as_of':times[-1].date().isoformat(),'question':question.strip(),
        'factors':factors,'family_ref':family_ref,'declared_family':[library.spec(r['factor_id']).to_dict() for r in library.entries()],
        'validation_policy':copy.deepcopy(POLICY),'policy_fingerprint':policy_fingerprint,
        'sample_period':[times[0].isoformat(),times[-1].isoformat()],
        'test_period':[times[split['validation_end']].isoformat(),times[-1].isoformat()],
        'evaluation_units':len(ids)+2,'split':{'train':.6,'validation':.2,'test':.2,'embargo':1,'horizon':1},**source}


def _signal(library, spec, panel, end):
    """SAMPLE shards keep the complete cross section and 8 historical sessions."""
    entry=library.get(spec.factor_id)
    if isinstance(panel,ValidationSample):
        pieces=[];refs=[];diagnostics=set()
        step,history=(10,24) if panel.spec['variant']=='late_announcement' else (20,8)
        for start in range(0,end,step):
            shard=panel.panel(entry,max(0,start-history),min(end,start+step))
            result=library.evaluate(spec,shard)
            diagnostics.update(result['missing_reason'])
            diagnostics.update(shard.audit['reasons'])
            values=pd.DataFrame(result['values']);values['observed_at']=pd.to_datetime(values['observed_at'])
            wide=values.pivot(index='observed_at',columns='instrument',values='value').astype(float)
            pieces.append(wide.loc[panel.times[start:min(end,start+step)]])
            refs.append(shard.fingerprint)
        return pd.concat(pieces),stable_digest(refs),sorted(diagnostics)
    bounded_frame=panel.frame.loc[panel.frame.index.get_level_values('observed_at')<=end]
    audit=panel.audit
    rows=audit.get('row_reasons',[])
    localized={reason for row in rows for reason in row['reasons']}
    reasons=set(audit['reasons'])-localized
    for row in rows:
        if pd.Timestamp(row['observed_at'])<=end:reasons.update(row['reasons'])
    audit['row_reasons']=[row for row in rows if pd.Timestamp(row['observed_at'])<=end]
    audit['reasons']=sorted(reasons);audit['status']='NEEDS_DATA' if reasons else 'READY'
    audit['coverage']={name:int(bounded_frame[name].notna().sum()) for name in audit['units'] if name in bounded_frame}
    audit['selections']=[row for row in audit.get('selections',[]) if pd.Timestamp(row['observed_at'])<=end]
    audit['selection_count']=len(audit['selections'])
    bounded=ResearchPanel(bounded_frame,audit,panel.contract,panel.fingerprint)
    result=library.evaluate(spec,bounded)
    values=pd.DataFrame(result['values']);values['observed_at']=pd.to_datetime(values['observed_at'])
    return values.pivot(index='observed_at',columns='instrument',values='value').astype(float),panel.fingerprint,sorted(set(result['missing_reason']+audit['reasons']))


def run_factor_study_suite(study_id, specs, panel, validation_policy, *, phase='validation', store=None, spec_ref='',
                           family_ref='', snapshot_ref='', policy_fingerprint='', frozen_factor=None,data_mode=None):
    if not 1<=len(specs)<=24:raise ValueError('每轮最多24个公式')
    library=FactorLibrary();sample=isinstance(panel,ValidationSample)
    data_mode='SAMPLE' if sample else data_mode or 'UNKNOWN'
    frame=None if sample else panel.frame
    times=panel.times if sample else frame.index.get_level_values('observed_at').unique().sort_values()
    split=purged_walk_forward(times,horizon=1,embargo=1,n_splits=2)
    if phase not in {'validation','test'}:raise ValueError('未知研究阶段')
    end=split['validation_end'] if phase=='validation' else len(times)
    selected=times[split[phase]]
    bound_times=times[:end]
    def field(name, default=None):
        if sample:return panel.field(name).iloc[:end]
        if name in frame:return frame[name].unstack('instrument').reindex(bound_times)
        return pd.DataFrame(default,index=bound_times,columns=panel.contract.instrument_ids)
    prices=field('close');eligible=pd.DataFrame(True,index=prices.index,columns=prices.columns) if sample else field('execution_eligible',False).fillna(False).astype(bool)
    refs=[];trials=[];signals={};budget=current_runtime_budget()
    from .research_evidence import PackagedCounterexamples
    counterrefs=PackagedCounterexamples().refs()
    # Baselines are predeclared economic definitions, never selected using holdout.
    references={'price_momentum':prices.pct_change(fill_method=None)}
    for spec in specs:
        if phase=='test' and spec.factor_id!=frozen_factor:continue
        record_id=f'{study_id}:{phase}:{spec.factor_id}'
        if store:store.suite_trial_state(study_id,phase,spec.factor_id,'RUNNING')
        if budget:budget.charge_experiments(1)
        try:
            signal,panel_ref,diagnostics=_signal(library,spec,panel,end if sample else bound_times[-1])
            signal=signal.reindex(index=prices.index,columns=prices.columns)
            scored=score_factor(signal,prices,evaluation_index=selected,train_index=times[split['train']],eligible=eligible,
                groups=field('industry') if sample or 'industry' in frame else None,
                size=field('market_cap') if sample or 'market_cap' in frame else None,
                amount=field('amount') if sample or 'amount' in frame else None,
                reference_signals=references,panel_fingerprint=panel_ref,
                **{k:validation_policy[k] for k in ('fee_bps','slippage_bps','min_ic','min_net_return','direction','quantiles','horizons','min_assets','min_periods')})
            signals[spec.factor_id]=signal.loc[selected]
            diagnostics=sorted(set(diagnostics+scored.get('missing_reasons',[])))
            status='NEEDS_DATA' if diagnostics else scored['status'];reason=None
        except (ValueError,TypeError,KeyError,ArithmeticError) as exc:
            status='EXECUTION_FAILED';scored=None;panel_ref=None;diagnostics=[];reason=f'{type(exc).__name__}: {str(exc)[:240]}'
        row={'record_id':record_id,'factor_id':spec.factor_id,'formula_fingerprint':library.get(spec.factor_id)['formula_fingerprint'],
            'family':library.get(spec.factor_id)['family'],'factor_spec':spec.to_dict(),'data_mode':data_mode,
            'status':status,'failure_reason':reason,'missing_reason':diagnostics,'snapshot_ref':snapshot_ref,'panel_fingerprint':panel_ref,
            'policy_fingerprint':policy_fingerprint,'validation_policy_fingerprint':stable_digest(validation_policy),
            'phase':phase,'metrics':scored,'counterevidence_refs':counterrefs,
            'financial_falsification':False,'production_write':False,'use_status':'RESEARCH_ONLY'}
        row['fingerprint']=stable_digest(row);trials.append(row)
        refs.append({'record_id':record_id,'digest':row['fingerprint']})
        if store:store.suite_trial_state(study_id,phase,spec.factor_id,status,result_ref=row['fingerprint'])
    family_correlation={}
    for left,left_signal in signals.items():
        for right,right_signal in signals.items():
            if left<right:
                pair=pd.concat([left_signal.stack(),right_signal.stack()],axis=1).dropna()
                value=pair.iloc[:,0].corr(pair.iloc[:,1]) if len(pair)>2 else None
                family_correlation[f'{left}|{right}']=None if value is None or pd.isna(value) else float(value)
    # Unknown and failed attempts still occupy the full predeclared 80-trial family.
    pvalues=[None if t['metrics'] is None else t['metrics'].get('pvalue') for t in trials]
    adjusted=adjust_pvalues(pvalues+[None]*(validation_policy['selection_family_size']-len(pvalues)))[:len(pvalues)]
    payload={'schema_version':'factor-study-suite.v1','study_id':study_id,'spec_ref':spec_ref,'family_ref':family_ref,
        'trials':trials,'trial_refs':refs,'metric_refs':[t['metrics']['fingerprint'] for t in trials if t['metrics']],
        'status_counts':dict(Counter(t['status'] for t in trials)),'family_correlation':family_correlation,
        'multiple_testing':{'method':'holm','registered_family_size':validation_policy['selection_family_size'],
            'unknown_included':validation_policy['selection_family_size']-sum(v is not None for v in pvalues),
            'local_batch_adjusted_pvalues':dict(zip([t['factor_id'] for t in trials],adjusted)),
            'limitation':'batch values are conservative diagnostics; global ledger projection recomputes all registered attempts'},
        'frozen_test_ref':frozen_factor if phase=='test' else None,'phase':phase,'production_write':False,
        'execution_ledger_status':'NOT_RUN','data_mode':data_mode}
    return {**payload,'fingerprint':stable_digest(payload)}


def compute_suite(snapshot,study,*,phase,validation,research_fields_store,study_store):
    spec=study['spec'];library=FactorLibrary()
    if spec['version']!=VERSION or snapshot.fingerprint!=spec['snapshot_ref']:raise ValueError('批量章程与快照不匹配')
    if phase=='test':
        if not validation or study['state'] not in {'TEST_RESERVED','TESTING'} or not study['test_key']:raise ValueError('测试需要冻结授权')
        frozen=next((r for r in validation['experiments'] if r['experiment_id']==study['candidate_id']),None)
        if not frozen or frozen['digest']!=study['candidate_fingerprint']:raise ValueError('冻结候选绑定已改变')
    else:frozen=None
    if 'sample_spec' in spec:panel=ValidationSample(spec['sample_spec'])
    else:panel=build_research_panel(snapshot,[research_fields_store.load(r) for r in spec['attachment_refs']],PanelContract.from_dict(spec['panel_contract']))
    factors=[library.spec(f['factor_id']) for f in spec['factors']]
    if [f.to_dict() for f in factors]!=spec['factors']:raise ValueError('已登记公式版本改变')
    suite=run_factor_study_suite(study['study_id'],factors,panel,spec['validation_policy'],phase=phase,store=study_store,
        spec_ref=study['spec_ref'],family_ref=spec['family_ref'],snapshot_ref=snapshot.fingerprint,
        policy_fingerprint=spec['policy_fingerprint'],data_mode=spec['data_mode'],frozen_factor=None if frozen is None else frozen['parameters']['factor_id'])
    experiments=[];summaries=[]
    for trial in suite['trials']:
        metrics=trial['metrics']['metrics'] if trial['metrics'] else {}
        item={'experiment_id':trial['factor_id'],'parameters':{'factor_id':trial['factor_id']},
            'evaluation':{'information_coefficient':metrics.get('mean_ic'),'status':'VALID' if trial['status'] in {'PASS','FALSIFIED'} else 'INVALID'},
            'ic':metrics.get('mean_ic'),'hypothesis_status':trial['status'],'failure_reason':None if trial['status'] in {'PASS','FALSIFIED'} else trial['status']}
        item['digest']=stable_digest(item);experiments.append(item)
        summaries.append(ExperimentSummary.build(snapshot_ref=snapshot.fingerprint,hypothesis=spec['question'],parameters=item['parameters'],
            sample_period=spec['sample_period'],split={**spec['split'],'phase':phase},cost=spec['validation_policy'],metrics=metrics,
            failure_reason=item['failure_reason'],version=VERSION,evidence_refs=[trial['fingerprint']],experiment_id=item['experiment_id'],
            conditions={'dataset_fingerprint':snapshot.fingerprint,'instrument':spec['instrument'],'instruments':spec['instrument_ids'],
                'sample_period':spec['sample_period'],'split':spec['split'],'cost':spec['validation_policy'],'version':VERSION,
                'policy_fingerprint':spec['policy_fingerprint'],'metric_basis':'OBSERVATION'},phase=phase.upper()).to_dict())
    result={'study_id':study['study_id'],'spec_ref':study['spec_ref'],'snapshot_ref':snapshot.fingerprint,'charter':spec,
        'suite_validation':suite,'phase':phase.upper(),'experiments':experiments,'summaries':summaries,
        'ranking':rank_suite_experiments(experiments,direction=spec['validation_policy']['direction']),
        'ranking_groups':[],'comparison':{'groups':[]},'surfaces':[],'state':'CANDIDATE_POOL','selected_candidate':None,'test_evaluation':None,
        'paper_only':True,'production_write':False,'requires_human_admission':True,
        'limitations':['分层收益与换手为诊断口径；实际交易账本NOT_RUN，可通过原因子工作流另做纸面成交研究。','SAMPLE只验证工程行为；金融用途需要独立审核。']}
    if phase=='test':result.update(experiments=validation['experiments'],summaries=validation['summaries'],ranking=validation['ranking'],
        selected_candidate=study['candidate_id'],state='TEST_EVALUATED',test_evaluation=experiments[0]['evaluation'] if experiments else None,test_summary=summaries[0] if summaries else None)
    return {**result,'fingerprint':stable_digest(result)}


def rank_suite_experiments(experiments,*,direction=1):
    return [r['experiment_id'] for r in sorted(experiments,key=lambda r:(r.get('hypothesis_status')!='PASS',
        -(direction*r['ic']) if r.get('ic') is not None else float('inf'),r['experiment_id'])) if r['failure_reason'] is None]

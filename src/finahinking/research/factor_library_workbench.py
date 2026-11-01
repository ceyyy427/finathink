"""Library cards use the durable study queue and existing numeric engines."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pandas as pd

from finahinking.data.panel_contracts import PanelContract
from finahinking.factors.evaluation import evaluate_factor_candidate
from finahinking.factors.mining import FactorCandidate

from .contracts import stable_digest
from .factor_library import FactorLibrary
from .factor_validation import FactorValidationGateway, factor_validation_policy
from .factor_workbench import ExperimentSummary
from .observability import current_runtime_budget
from .temporal_validation import purged_walk_forward

VERSION = 'factor-workbench.library.v1'


def prepare_library_study(values, snapshots, *, policy_fingerprint):
    allowed = {'idempotency_key','factor_id','factor_version','contract','attachment_refs','question'}
    if not isinstance(values, dict) or set(values) - allowed:
        raise ValueError('因子库研究只接受固定定义、面板合同和附件引用')
    library = FactorLibrary()
    factor = library.spec(values.get('factor_id'))
    if values.get('factor_version', factor.version) != factor.version:
        raise ValueError('因子版本不匹配，请重新读取目录')
    question = values.get('question')
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 2000:
        raise ValueError('请填写有界的研究问题')
    contract = PanelContract.from_dict(values['contract'])
    refs = values.get('attachment_refs', [])
    panel = snapshots.build_panel(contract, refs)
    index = panel.frame.index.get_level_values('observed_at').unique().sort_values()
    split = purged_walk_forward(index, horizon=1, embargo=1, n_splits=2)
    if len(split['validation']) < 8 or len(split['test']) < 8:
        raise ValueError('样本不足，请准备更长的历史面板')
    policy = factor_validation_policy(n_splits=2)
    row = library.get(factor.factor_id)
    return {'version':VERSION, 'snapshot_ref':contract.snapshot_ref, 'instrument':contract.instrument_ids[0],
        'as_of':index[-1].date().isoformat(), 'question':question.strip(), 'factor_spec':factor.to_dict(),
        'family':row['family'], 'source_card_ids':row['source_card_ids'],
        'panel_contract':contract.to_dict(), 'attachment_refs':sorted(refs), 'panel_fingerprint':panel.fingerprint,
        'validation_policy':policy, 'policy_fingerprint':policy_fingerprint,
        'sample_period':[index[0].isoformat(),index[-1].isoformat()],
        'test_period':[index[split['validation_end']].isoformat(),index[-1].isoformat()],
        'split':{'train':.6,'validation':.2,'test':.2}, 'label_horizon':1, 'embargo':1, 'n_splits':2,
        'cost':{'fee_bps':policy['fee_bps'],'slippage_bps':policy['slippage_bps']},
        'metric_basis':'OBSERVATION', 'evaluation_units':min(256, 4*len(contract.instrument_ids)+8)}


def compute_library_study(snapshot, study, *, phase, validation, research_fields_store, study_store):
    from finahinking.data.research_panel import build_research_panel
    spec = study['spec']
    if snapshot.fingerprint != spec['snapshot_ref'] or phase not in {'validation','test'}:
        raise ValueError('因子研究快照或阶段不一致')
    library = FactorLibrary()
    factor = library.spec(spec['factor_spec']['factor_id'])
    if factor.to_dict() != spec['factor_spec']:
        raise ValueError('已冻结的因子定义已改变')
    contract = PanelContract.from_dict(spec['panel_contract'])
    attachments = [research_fields_store.load(ref) for ref in spec['attachment_refs']]
    panel = build_research_panel(snapshot, attachments, contract)
    if panel.fingerprint != spec['panel_fingerprint']:
        raise ValueError('已冻结的面板证据已改变')
    if phase == 'test':
        if (not validation or validation.get('spec_ref') != study['spec_ref']
                or study['state'] not in {'TEST_RESERVED','TESTING'} or not study['test_key']):
            raise ValueError('测试需要原始验证报告与一次性冻结授权')
        candidate = next((r for r in validation['experiments'] if r['experiment_id'] == study['candidate_id']), None)
        if candidate is None or candidate['digest'] != study['candidate_fingerprint'] or candidate['failure_reason']:
            raise ValueError('冻结候选绑定不一致')
    frame = panel.frame
    index = frame.index.get_level_values('observed_at').unique().sort_values()
    temporal = purged_walk_forward(index, horizon=1, embargo=1, n_splits=2)
    # Validation never evaluates factor rows or labels from the reserved holdout.
    end = index[temporal['validation_end']-1] if phase == 'validation' else index[-1]
    bounded_values = contract.to_dict()
    bounded_values.pop('fingerprint', None)
    bounded_values.pop('schema_version', None)
    bounded_values['observed_at'] = [t.isoformat() for t in index if t <= end]
    bounded = build_research_panel(snapshot, attachments, PanelContract(**bounded_values))
    evaluated = library.evaluate(factor, bounded)
    rows = evaluated['values']
    signal = pd.Series([r['value'] for r in rows], index=pd.MultiIndex.from_tuples(
        [(pd.Timestamp(r['observed_at']),r['instrument']) for r in rows], names=['observed_at','instrument']), dtype=float)
    frames = {}
    scoring = index[temporal[phase if phase == 'test' else 'validation']]
    results = []
    budget = current_runtime_budget()
    for asset in contract.instrument_ids:
        if budget is not None:
            budget.charge_experiments(1)
        asset_frame = frame.xs(asset, level='instrument').loc[:end].copy()
        asset_frame['library_signal'] = signal.xs(asset, level='instrument')
        # The shared ledger respects suspension; unavailable trading sessions are non-executable.
        asset_frame['suspended'] = asset_frame['suspended'].fillna(True).astype(bool) | ~asset_frame['execution_eligible'].astype(bool)
        if factor.direction == 'LOWER':
            asset_frame['library_signal'] *= -1
        frames[asset] = asset_frame
        forward = asset_frame['close'].shift(-1) / asset_frame['close'] - 1 if 'close' in asset_frame else pd.Series(float('nan'),index=asset_frame.index)
        candidate = FactorCandidate(factor.factor_id, 'library_signal', spec['question'], 'finathink-library',
                                    {'factor_spec_fingerprint':factor.fingerprint})
        item = evaluate_factor_candidate(candidate, asset_frame, forward, {'phase':phase,'oos':phase=='test',
            'shift_periods':1,'min_samples':8,'label_horizon':1,'scoring_index':scoring,
            'evaluation_start':scoring[0], 'evaluation_end':scoring[-1],
            'cost_per_turnover':(spec['cost']['fee_bps']+spec['cost']['slippage_bps'])/10000}).to_dict()
        results.append({'instrument':asset, **item})
    blocked = evaluated['status'] != 'READY'
    valid = [r for r in results if r['status']=='VALID']
    failure = 'NEEDS_DATA' if blocked else None if len(valid)==len(results) else 'UNDEFINED_OR_INSUFFICIENT_EVIDENCE'
    backtest = None
    unsupported_rules = any(not f['lot_size'].eq(1).all() or not f['settlement_days'].eq(0).all() for f in frames.values())
    if unsupported_rules:
        failure = failure or 'UNSUPPORTED_TRADING_RULES'
    if not blocked and not unsupported_rules and all({'open','high','low','close','volume','suspended'} <= set(f.columns) for f in frames.values()):
        # Reuse the real ledger engine with a materialized, immutable signal; no second DSL implementation.
        adapter = SimpleNamespace(formula='library_signal',direction='HIGHER',required_fields=('library_signal',),fingerprint=factor.fingerprint)
        try:
            backtest = FactorValidationGateway().backtest(adapter, frames, spec['validation_policy'],
                train_index=index[temporal['train']], evaluation_index=scoring, snapshot_ref=snapshot.fingerprint)
        except ValueError:
            failure = failure or 'BACKTEST_INPUTS_UNAVAILABLE'
    else:
        failure = failure or 'BACKTEST_INPUTS_UNAVAILABLE'
    metrics = {'information_coefficient':sum(r['information_coefficient'] for r in valid)/len(valid) if valid else None,
        'information_ratio':None, 'sample_size':sum(r['sample_size'] for r in results),
        'coverage':sum(r['coverage'] for r in results)/len(results),
        'turnover':None,'transaction_cost':None}
    evidence = {'schema_version':'library-validation.v1','status':'COMPLETE' if failure is None else 'BLOCKED_DATA',
        'factor_spec':factor.to_dict(),'formula_fingerprint':evaluated['formula_fingerprint'],
        'snapshot_ref':snapshot.fingerprint,'panel_fingerprint':panel.fingerprint,
        'policy_fingerprint':spec['policy_fingerprint'],
        'validation_policy_fingerprint':stable_digest(spec['validation_policy']), 'phase':phase,
        'source_card_ids':spec['source_card_ids'],'evaluations':results,'backtest':backtest,
        'missing_reason':sorted(set(evaluated['missing_reason'] + bounded.audit['reasons'] + (['当前纸面引擎仅支持声明的单位手数与当日结算；保留IC证据，停止不适用的成交假设。'] if unsupported_rules else []))),'financial_falsification':False,'use_status':'RESEARCH_ONLY'}
    evidence['fingerprint'] = stable_digest(evidence)
    conditions = {'dataset_fingerprint':snapshot.fingerprint,'panel_fingerprint':panel.fingerprint,
        'instrument':spec['instrument'],'instruments':list(contract.instrument_ids),
        'sample_period':spec['sample_period'],'scoring_index':stable_digest([t.isoformat() for t in scoring]),
        'split':{**spec['split'],'phase':phase},'cost':spec['cost'],'version':VERSION,
        'metric_basis':'OBSERVATION','policy_fingerprint':evidence['policy_fingerprint'],
        'validation_policy_fingerprint':evidence['validation_policy_fingerprint']}
    record = {'experiment_id':'experiment-library','parameters':{'factor_id':factor.factor_id,'window':factor.window},
        'evaluation':{**metrics,'status':'VALID' if failure is None else 'INVALID','oos_status':('PASS' if failure is None else 'BLOCKED') if phase=='test' else 'HIDDEN','fingerprint':evidence['fingerprint']},
        'ic':metrics['information_coefficient'],'failure_reason':failure}
    record['digest'] = stable_digest(record)
    summary = ExperimentSummary.build(snapshot_ref=snapshot.fingerprint,hypothesis=spec['question'],parameters=record['parameters'],
        sample_period=spec['sample_period'],split=conditions['split'],cost=spec['cost'],metrics=metrics,failure_reason=failure,
        version=VERSION,evidence_refs=spec['source_card_ids']+[evidence['fingerprint']],experiment_id=record['experiment_id'],conditions=conditions,phase=phase.upper()).to_dict()
    result = {'study_id':study['study_id'],'spec_ref':study['spec_ref'],'snapshot_ref':snapshot.fingerprint,'charter':spec,
        'phase':phase.upper(),'library_validation':evidence,'summaries':[summary],'experiments':[record],
        'comparison':{'groups':[]},'ranking_groups':[],'ranking':[record['experiment_id']] if failure is None else [],
        'surfaces':[],'state':'CANDIDATE_POOL' if failure is None else 'BLOCKED','selected_candidate':None,
        'test_evaluation':None,'paper_only':True,'requires_human_admission':True,'production_write':False,
        'limitations':['合成数据复现只验证工程计算；来源引用、定义计算与金融用途分别判断。',
            '按资产时间序列 Pearson IC 的平均值不代表横截面 IC；净值为固定训练阈值纸面组合。',
            '纸面阈值固定假设为高值组；RESEARCH 定义尚未证实收益方向。',
            '测试证据不重新排名，研究审核不授予金融用途。']}
    if phase == 'test':
        result.update(experiments=copy.deepcopy(validation['experiments']),summaries=copy.deepcopy(validation['summaries']),
            ranking=copy.deepcopy(validation['ranking']),state='TEST_EVALUATED' if failure is None else 'BLOCKED',
            selected_candidate=study['candidate_id'],test_evaluation=record['evaluation'],test_summary=summary)
    result['fingerprint'] = stable_digest(result)
    return result

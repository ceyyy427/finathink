"""Checked projections of immutable research records and executed counterevidence.

This module never changes a trial, grants use approval, or fabricates settlement.
"""
from __future__ import annotations

import copy
import json
from functools import cached_property

from .contracts import stable_digest

STATUSES = {'PASS', 'FALSIFIED', 'NEEDS_DATA', 'EXECUTION_FAILED', 'CANCELLED'}


def _load(ref, catalogue):
    if not isinstance(ref, dict) or set(ref) != {'record_id', 'digest'}:
        raise ValueError('证据引用需要固定记录和摘要')
    row = catalogue.load_record(ref['record_id'])
    if not isinstance(row, dict) or row.get('record_id') != ref['record_id']:
        raise ValueError('证据身份不一致')
    digest = stable_digest({k:v for k,v in row.items() if k != 'fingerprint'})
    if row.get('fingerprint') != digest or ref['digest'] != digest:
        raise ValueError('证据内容摘要不一致')
    return row


def build_research_evidence(record_refs, *, catalogue):
    """Verify the record/manifest chain through the caller's durable catalogue.

    The catalogue is a trusted reader, not HTTP supplied JSON. A suite catalogue
    resolves published trial IDs and checks the report manifest before returning.
    """
    if not isinstance(record_refs, (tuple, list)) or not 1 <= len(record_refs) <= 80:
        raise ValueError('证据投影每次接受1至80条记录')
    rows, seen = [], set()
    for ref in record_refs:
        blockers, row = [], None
        try:
            row = _load(ref, catalogue)
            if row['record_id'] in seen:
                raise ValueError('重复记录不构成独立证据')
            seen.add(row['record_id'])
            if row.get('status') not in STATUSES:
                raise ValueError('研究状态无效')
            if not all(isinstance(row.get(k), str) and row[k] for k in
                       ('factor_id','formula_fingerprint','snapshot_ref','policy_fingerprint')):
                raise ValueError('研究证据缺少绑定')
            counters = row.get('counterevidence_refs', [])
            if len(counters) < 20 or len({c['record_id'] for c in counters}) != len(counters):
                raise ValueError('反证记录不完整')
            for counter in counters:
                checked = _load(counter, catalogue)
                if checked.get('kind') != 'COUNTEREXAMPLE' or checked.get('status') != 'REPRODUCED':
                    raise ValueError('反证尚未复现')
        except (KeyError, TypeError, ValueError, OSError) as exc:
            blockers.append(str(exc)[:240])
        status = row.get('status', 'EXECUTION_FAILED') if row else 'EXECUTION_FAILED'
        executed = status in {'PASS','FALSIFIED'} and not blockers
        level = ('SYNTHETIC_REPRODUCED' if row.get('data_mode') == 'SAMPLE' else
                 'USER_DATA_EVALUATED' if row.get('data_mode') in {'USER_DATA','CAPTURED'} else
                 'DEFINITION_CHECKED') if executed else 'DEFINITION_CHECKED'
        if executed and level == 'DEFINITION_CHECKED': blockers.append('未知数据模式')
        rows.append({'record_ref':copy.deepcopy(ref), 'factor_id':row.get('factor_id') if row else None,
                     'formula_fingerprint':row.get('formula_fingerprint') if row else None, 'status':status, 'evidence_level':level,
                     'financial_falsification':executed and not blockers and level == 'USER_DATA_EVALUATED' and status == 'FALSIFIED',
                     'hypothesis_outcome':status if executed and not blockers else None,
                     'blockers':blockers, 'use_approved':False})
    payload = {'schema_version':'research-evidence.v1',
               'status':'BLOCKED' if any(r['blockers'] for r in rows) else 'COMPLETE',
               'records':rows, 'independent_formula_count':len({r['formula_fingerprint'] for r in rows if r['formula_fingerprint']}),
               'status_counts':{s:sum(r['status']==s for r in rows) for s in sorted(STATUSES)},
               'learning_eligible':False, 'use_approved':False, 'production_write':False,
               'learning_reason':'因子研究不是已结算纸面成交；学习仍需既有ledger与settlement核验。'}
    return {**payload,'fingerprint':stable_digest(payload)}


class PackagedCounterexamples:
    """Run the bounded, reviewed scenarios once per reader, retaining input/output."""
    def __init__(self, catalogue=None):
        if catalogue is None:
            from .asset_catalog import ResearchAssetCatalog
            catalogue = ResearchAssetCatalog.packaged()
        self.catalogue = catalogue

    @cached_property
    def records(self):
        content = json.loads(self.catalogue.read_text('factor-counterexamples', max_bytes=1048576))
        if content.get('schema_version') != 'factor-counterexamples.v1' or len(content.get('cases', [])) < 20:
            raise ValueError('反例目录不完整')
        result = {}
        for case in content['cases']:
            row = run_counterexample(case)
            if row['record_id'] in result:
                raise ValueError('反例标识重复')
            result[row['record_id']] = row
        return result

    def load_record(self, record_id):
        return copy.deepcopy(self.records[record_id])

    def refs(self):
        return [{'record_id':key,'digest':row['fingerprint']} for key,row in sorted(self.records.items())]


def counterexample_inputs():
    """Fixed tiny input, independent of user data and default study snapshots."""
    import math

    import pandas as pd
    index = pd.date_range('2025-01-01', periods=20, tz='UTC')
    columns = [f'A{i}' for i in range(8)]
    signal = pd.DataFrame({a:[float((j+i//3)%8) for i in range(20)] for j,a in enumerate(columns)},index=index)
    prices = {}
    for j,a in enumerate(columns):
        value, values = 100., []
        for i in range(20):
            value *= 1+(j+1)*.001+math.sin((i+j)*1.3)*.002
            values.append(value)
        prices[a] = values
    return signal, pd.DataFrame(prices,index=index)


def run_counterexample(case):
    """Execute a reviewed bounded scenario and compare its declared expectation."""

    from finahinking.quant.evaluation.robustness import adjust_pvalues, score_factor

    signal, prices = counterexample_inputs()
    code = case['scenario']
    options = {'evaluation_index':signal.index[4:18], 'train_index':signal.index[:4],
               'horizons':(1,3), 'quantiles':4,'min_periods':8}
    if code == 'late_announcement':
        from finahinking.data.panel_contracts import PanelContract, PanelFieldAttachment
        from finahinking.data.research_panel import build_research_panel
        from finahinking.p8_2.contracts import DatasetSnapshot, MarketObservation
        times = signal.index[:3]
        snapshot = DatasetSnapshot('late-announcement-counterexample', tuple(
            MarketObservation('A0',t,100.,101.,99.,100.,100.,available_at=t,suspended=False)
            for t in times), provenance={'adjustment':'RAW'})
        units = {'is_session':'boolean','in_universe':'boolean','trade_allowed':'boolean',
                 'lot_size':'shares','settlement_days':'sessions','split_factor':'ratio','book_equity':'CNY'}
        rows = []
        for t in times:
            rows.append({'instrument':'A0','timestamp':t.isoformat(),'available_at':t.isoformat(),
                         'is_session':True,'in_universe':True,'trade_allowed':True,'lot_size':1,
                         'settlement_days':0,'split_factor':1})
            rows.append({'instrument':'A0','timestamp':t.isoformat(),'available_at':'2027-01-01T00:00:00Z',
                         'book_equity':1000000.})
        attachment = PanelFieldAttachment(snapshot.fingerprint,units,rows,'late announcement synthetic counterexample')
        contract = PanelContract(snapshot_ref=snapshot.fingerprint,instrument_ids=('A0',),observed_at=[t.isoformat() for t in times],
                                 field_units={'book_equity':'CNY'},scope_id='counterexample',currencies={'A0':'CNY'},timezones={'A0':'UTC'},
                                 calendar_ref=attachment.fingerprint,universe_ref=attachment.fingerprint,
                                 adjustment_ref=attachment.fingerprint,trade_rule_ref=attachment.fingerprint)
        panel = build_research_panel(snapshot,[attachment],contract)
        observed = {'historical_values_remain_missing':bool(panel.frame['book_equity'].isna().all()),
                    'panel_status':panel.status}
    elif code == 'delisted_name_retained':
        prices.iloc[8:,4] = float('nan')
        options.update(min_ic=-1,min_net_return=-1)
        full = score_factor(signal,prices,**options)
        survivors = score_factor(signal.drop(columns='A4'),prices.drop(columns='A4'),**options)
        observed = {'retained_status':full['status'],'survivor_only_status':survivors['status'],
                    'retained_coverage':full['metrics']['coverage'],'survivor_only_coverage':survivors['metrics']['coverage']}
    elif code == 'adverse_direction': options.update(direction=-1,min_ic=.99)
    elif code == 'cost_destroys_edge': options.update(fee_bps=10000,min_ic=-1,min_net_return=.01)
    elif code == 'missing_marks': prices.iloc[5:17] = float('nan')
    elif code == 'low_coverage': signal.iloc[:,2:] = float('nan')
    elif code == 'constant_factor': signal[:] = 1.
    elif code == 'constant_returns': prices[:] = 100.
    elif code == 'all_suspended': options['eligible'] = signal.eq(-999)
    elif code == 'short_window': options['evaluation_index'] = signal.index[4:8]
    elif code == 'unknown_capacity': options.update(capital=1e6,min_ic=-1)
    elif code == 'exceeded_capacity': options.update(capital=1e9, amount=signal*0+100.,min_ic=-1)
    elif code == 'duplicate_signal' or code == 'zero_incremental': options.update(reference_signals={'same':signal.copy()},min_ic=-1)
    elif code == 'negative_cost': options['fee_bps'] = -1.
    elif code == 'nonpositive_price': prices.iloc[8,1] = 0.
    elif code == 'overlap_fit': options['train_index'] = signal.index[:8]
    elif code == 'unordered_dates': options['evaluation_index'] = signal.index[4:18][::-1]
    elif code == 'timezone_unknown':
        signal.index = signal.index.tz_localize(None)
        prices.index = signal.index
    elif code == 'unknown_tradability': options['eligible'] = signal*0+1.
    elif code == 'hidden_trial':
        actual = adjust_pvalues([.01,None,.04])
        observed = {'adjusted':actual,'trial_count':3,'unknown_count':1}
    elif code == 'future_mutation':
        original = score_factor(signal,prices,**options)
        changed = prices.copy(); changed.iloc[18:] *= 1000
        rerun = score_factor(signal,changed,**options)
        observed = {'historical_metrics_unchanged':original['metrics']==rerun['metrics']}
    else:
        if code not in {'adverse_direction','cost_destroys_edge','missing_marks','low_coverage','constant_factor',
                        'constant_returns','all_suspended','short_window','unknown_capacity','exceeded_capacity',
                        'duplicate_signal','zero_incremental','negative_cost','nonpositive_price','overlap_fit',
                        'unordered_dates','timezone_unknown','unknown_tradability'}:
            raise ValueError('未知反例场景')
    if code not in {'hidden_trial','future_mutation','late_announcement','delisted_name_retained'}:
        try:
            score = score_factor(signal,prices,**options)
            observed = {'status':score['status'],'metrics':score['metrics'], 'capacity':score['capacity'],
                        'family_correlation':score['family_correlation'],'incremental':score['incremental']}
        except ValueError as exc:
            observed = {'status':'EXECUTION_FAILED','error':str(exc)}
    expected = case['expected']
    selected = observed
    for name in expected['path']: selected = selected[name]
    target = expected['value']
    if isinstance(target,float):
        matched = selected is not None and abs(selected-target) <= expected.get('tolerance',1e-12)
    else: matched = selected == target
    row = {'record_id':'counterexample:'+case['case_id'], 'kind':'COUNTEREXAMPLE',
           'case_id':case['case_id'],'status':'REPRODUCED' if matched else 'MISMATCH',
           'data_mode':'SAMPLE','input':case['input'],'scenario':code,'observed':observed,
           'expected':expected,'correction':case['correction'],'scope':case['scope'],
           'financial_falsification':False,
           'hypothesis_outcome':observed.get('status') if code in {'adverse_direction','cost_destroys_edge'} else None}
    return {**row,'fingerprint':stable_digest(row)}

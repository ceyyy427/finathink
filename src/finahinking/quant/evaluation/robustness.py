"""Causal cross-sectional research diagnostics, separate from executable ledgers.

Signals observed at t-1 form equal-weight cohorts at t; a horizon h label is
close[t+h]/close[t]-1. Quantiles preserve ties. Costs charge each buy/sell leg
at fee_bps + slippage_bps, not an annualized rate. No leverage, fill or market
impact model is implied by these diagnostic cohort returns.
"""
from __future__ import annotations

import hashlib
import json
import math

import numpy as np
import pandas as pd

from finahinking.research.temporal_validation import block_sign_pvalue

VERSION = 'factor-scorecard.v1'


def _finite(value):
    return float(value) if value is not None and np.isfinite(value) else None


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def adjust_pvalues(values, method='holm'):
    """Holm step-down FWER control; unknown attempts still count in family size."""
    values = list(values)
    if method != 'holm' or len(values) > 10000:
        raise ValueError('仅接受有界 Holm 校正')
    if any(v is not None and (isinstance(v, (bool, np.bool_)) or not isinstance(v, (int, float, np.number))
                              or not math.isfinite(v) or not 0 <= v <= 1) for v in values):
        raise ValueError('p 值必须是有限概率或未知')
    result = [None] * len(values)
    maximum = 0.
    for rank, (i, value) in enumerate(sorted(((i, v) for i, v in enumerate(values) if v is not None), key=lambda p: p[1])):
        maximum = max(maximum, min(1., (len(values)-rank)*value))
        result[i] = float(maximum)
    return result


def _frame(value, reference, name, *, numeric=True):
    if not isinstance(value, pd.DataFrame) or not value.index.equals(reference.index) or not value.columns.equals(reference.columns):
        raise ValueError(f'{name} 必须与信号共享有序日期和资产列')
    if numeric and any(not pd.api.types.is_numeric_dtype(dtype) or pd.api.types.is_bool_dtype(dtype) for dtype in value.dtypes):
        raise ValueError(f'{name} 必须使用数值字段')
    return value.replace([np.inf, -np.inf], np.nan) if numeric else value


def _corr(left, right, *, rank=True, minimum=4):
    good = np.isfinite(left) & np.isfinite(right)
    if good.sum() < minimum:
        return None
    x, y = left[good], right[good]
    if x.nunique() < 2 or y.nunique() < 2:
        return None
    if rank:
        x, y = x.rank(method='average'), y.rank(method='average')
    return _finite(np.corrcoef(x, y)[0, 1])


def _daily_ic(signal, forward, eligible, dates, minimum):
    values = []
    pairs = 0
    for t in dates:
        s, r = signal.loc[t].where(eligible.loc[t]), forward.loc[t]
        pairs += int((s.notna() & r.notna()).sum())
        values.append({'observed_at': t.isoformat(), 'value': _corr(s, r, minimum=minimum)})
    return values, pairs


def _mean(values):
    finite = [v for v in values if v is not None and math.isfinite(v)]
    return float(np.mean(finite)) if finite else None


def _ic_summary(rows):
    values = [r['value'] for r in rows if r['value'] is not None]
    mean = _mean(values)
    std = float(np.std(values, ddof=1)) if len(values) >= 2 else None
    return {'mean_ic': mean, 'icir': None if std is None or std <= 1e-12 else mean/std,
            'n_dates': len(values)}


def _quantile_labels(row, quantiles):
    # Equal ranks remain in one bin, including a completely tied cross-section.
    return np.ceil(row.rank(method='average', pct=True)*quantiles).clip(1, quantiles)


def score_factor(signal, prices, *, evaluation_index, train_index=None, eligible=None,
                 groups=None, size=None, amount=None, reference_signals=None, fee_bps=5.,
                 slippage_bps=5., min_ic=.02, min_net_return=0., quantiles=5,
                 horizons=(1, 5, 20), direction=1, min_assets=4, min_periods=8,
                 panel_fingerprint='', capital=None, participation_rate=.1, min_coverage=.8):
    """Return a bound, JSON-safe scorecard with explicit unavailable diagnostics.

    PASS/FALSIFIED concern the preregistered *oriented* mean IC and top cohort
    mean net return thresholds on this input, not investment-use approval.
    NEEDS_DATA never counts as financial falsification. Returns and IC are not
    annualized, and the exact block sign p-value discloses its assumptions.
    """
    if (not isinstance(signal, pd.DataFrame) or not isinstance(signal.index, pd.DatetimeIndex)
            or signal.index.tz is None or not signal.index.is_unique or not signal.index.is_monotonic_increasing
            or signal.index.hasnans or not signal.columns.is_unique or signal.empty or signal.size > 250000
            or not all(isinstance(c, str) and c for c in signal.columns)):
        raise ValueError('信号需要有界、时区明确、日期唯一有序的资产面板')
    signal = _frame(signal, signal, 'signal')
    prices = _frame(prices, signal, 'prices')
    if (prices.le(0) & prices.notna()).any().any():
        raise ValueError('价格必须为正数或缺失')
    for value, low, high in ((fee_bps,0,10000),(slippage_bps,0,10000),(min_ic,-1,1),
                              (min_net_return,-1,1),(participation_rate,0,1),(min_coverage,0,1)):
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int,float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError('评分阈值或成本单位无效')
    if (type(quantiles) is not int or not 2 <= quantiles <= 10 or type(min_assets) is not int or min_assets < 2
            or type(min_periods) is not int or min_periods < 2 or type(direction) is not int or direction not in (-1,1)):
        raise ValueError('评分参数无效')
    horizons = tuple(horizons)
    if not horizons or len(horizons) > 12 or any(type(h) is not int or not 1 <= h <= 252 for h in horizons) or len(set(horizons)) != len(horizons):
        raise ValueError('衰减观察期无效')
    dates = pd.DatetimeIndex(evaluation_index)
    if dates.empty or not dates.is_unique or not dates.is_monotonic_increasing or not dates.isin(signal.index).all():
        raise ValueError('评估时点必须是面板中的有序唯一日期')
    train = pd.DatetimeIndex([]) if train_index is None else pd.DatetimeIndex(train_index)
    if len(train) and (not train.is_unique or not train.is_monotonic_increasing or not train.isin(signal.index).all() or train[-1] >= dates[0]):
        raise ValueError('拟合期必须严格早于评估期')
    cutoff = dates[-1]
    if eligible is None:
        eligible = pd.DataFrame(True, index=signal.index, columns=signal.columns)
    else:
        eligible = _frame(eligible, signal, 'eligible', numeric=False)
        if eligible.isna().any().any() or any(not pd.api.types.is_bool_dtype(d) for d in eligible.dtypes):
            raise ValueError('可交易性需要完整布尔声明')
    for name, value in (('groups',groups),('size',size),('amount',amount)):
        if value is not None:
            _frame(value, signal, name, numeric=name!='groups')
    # A view of the past prevents label computations from touching future holdout.
    s = signal.loc[:cutoff].shift(1)*direction
    p = prices.loc[:cutoff]
    e = eligible.loc[:cutoff]
    fwd = p.shift(-1)/p-1
    ends = pd.Series(p.index, index=p.index).shift(-1)
    valid_dates = dates[ends.reindex(dates).isin(dates).to_numpy()]
    ic_rows, pairs = _daily_ic(s, fwd, e, valid_dates, min_assets)
    summary = _ic_summary(ic_rows)
    # Diagnostic target allocations, not executable fills. Every price mark is
    # required for the selected cohort; missing marks never silently renormalize.
    previous = pd.Series(0., index=signal.columns)
    previous_cash = 1.
    quantile_rows, group_returns, allocation_rows = [], {q: [] for q in range(1,quantiles+1)}, []
    missing_marks = False
    for t in valid_dates:
        row = s.loc[t].where(e.loc[t])
        labels = _quantile_labels(row, quantiles)
        returns = fwd.loc[t]
        qrets = {}
        for q, bucket_returns in group_returns.items():
            selected = labels.eq(q)
            value = float(returns[selected].mean()) if selected.sum() and returns[selected].notna().all() else None
            qrets[str(q)] = value
            if value is not None:
                bucket_returns.append(value)
        selected = labels.eq(quantiles)
        weights = selected.astype(float)/max(int(selected.sum()),1)
        cash = 1.-float(weights.sum())
        turnover = float((weights-previous).abs().sum()+abs(cash-previous_cash))/2
        traded = float((weights-previous).abs().sum())
        cost = traded*(fee_bps+slippage_bps)/10000
        top, bottom = qrets[str(quantiles)], qrets['1']
        if top is None:
            missing_marks = True
        quantile_rows.append({'observed_at':t.isoformat(),'groups':qrets,'gross_top':top,'gross_bottom':bottom,
                              'spread':None if top is None or bottom is None else top-bottom,
                              'turnover':turnover,'traded_notional_fraction':traded,'cost':cost,
                              'net_top':None if top is None else top-cost})
        allocation_rows.append((t, weights))
        previous, previous_cash = weights, cash
    group_means = {str(q):_mean(values) for q,values in group_returns.items()}
    monotonicity = _corr(pd.Series(list(range(1,quantiles+1)), dtype=float),pd.Series(list(group_means.values()),dtype=float),minimum=2)
    eligible_pairs=sum(int(e.loc[t].sum()) for t in valid_dates)
    coverage = pairs/max(1,eligible_pairs)
    metrics = {**summary,'mean_gross_top_return':_mean(r['gross_top'] for r in quantile_rows),
               'mean_gross_bottom_return':_mean(r['gross_bottom'] for r in quantile_rows),
               'mean_net_top_return':_mean(r['net_top'] for r in quantile_rows),
               'mean_spread':_mean(r['spread'] for r in quantile_rows),
               'mean_turnover':_mean(r['turnover'] for r in quantile_rows),
               'mean_cost':_mean(r['cost'] for r in quantile_rows),'coverage':coverage,
               'n_pairs':pairs,'eligible_pairs':eligible_pairs,'n_evaluation_dates':len(valid_dates),'monotonicity':monotonicity,
               'coverage_basis':'DECLARED_ELIGIBLE_PAIRS','quantile_observations':{str(q):len(v) for q,v in group_returns.items()},
               'quantile_mean_returns':group_means}
    decay = []
    for horizon in horizons:
        future = p.shift(-horizon)/p-1
        label_end = pd.Series(p.index,index=p.index).shift(-horizon)
        usable = dates[label_end.reindex(dates).isin(dates).to_numpy()]
        values, n = _daily_ic(s,future,e,usable,min_assets)
        decay.append({'horizon':horizon,**_ic_summary(values),'n_pairs':n})
    stability = {'time':[], 'industry':{'status':'UNAVAILABLE','groups':[]}, 'size':{'status':'UNAVAILABLE','groups':[]}}
    for part in np.array_split(np.arange(len(valid_dates)),3):
        subset=[ic_rows[int(i)] for i in part]
        stability['time'].append(_ic_summary(subset))
    if groups is not None:
        # Each observation uses its as-of classification; no last-date map is backfilled.
        labels_all = sorted({str(v) for v in groups.loc[valid_dates].stack().dropna().unique()})
        for group in labels_all[:100]:
            mask=groups.loc[:cutoff].astype('string').eq(group).fillna(False) & e
            values,n=_daily_ic(s,fwd,mask,valid_dates,min_assets)
            stability['industry']['groups'].append({'group':group,**_ic_summary(values),'n_pairs':n})
        stability['industry']['status']='AVAILABLE' if labels_all else 'UNAVAILABLE'
    if size is not None:
        size_values=size.loc[:cutoff]
        rank=size_values.rank(axis=1,method='average',pct=True)
        for label,mask in [('small',rank.le(.5)),('large',rank.gt(.5))]:
            values,n=_daily_ic(s,fwd,e & mask,valid_dates,min_assets)
            stability['size']['groups'].append({'group':label,**_ic_summary(values),'n_pairs':n})
        stability['size']['status']='AVAILABLE'
    capacity={'status':'UNAVAILABLE','capital_capacity':None,'assumption':'max participation in declared one-observation traded amount; not an impact/fill model'}
    if capital is not None and (isinstance(capital,bool) or not isinstance(capital,(int,float)) or not math.isfinite(capital) or capital <= 0):
        raise ValueError('组合规模必须为正有限金额')
    if amount is not None:
        caps=[]
        missing_capacity=False
        for t,weights in allocation_rows:
            selected=weights.gt(0)
            amounts=amount.loc[t,selected]
            if amounts.empty or amounts.isna().any() or amounts.le(0).any():
                missing_capacity=True
                continue
            caps.append(float((amounts*participation_rate/weights[selected]).min()))
        if caps and not missing_capacity:
            cap=min(caps)
            capacity.update(status='AVAILABLE',capital_capacity=cap,
                            requested_capital=capital,within_capacity=None if capital is None else capital<=cap)
    reference_signals = {} if reference_signals is None else reference_signals
    if not isinstance(reference_signals,dict) or len(reference_signals)>24:
        raise ValueError('参考信号必须为有界字典')
    family_corr={}; references={}
    for name, reference in sorted(reference_signals.items()):
        if not isinstance(name,str) or not name:
            raise ValueError('参考信号名称无效')
        references[name]=_frame(reference,signal,'reference').loc[:cutoff].shift(1)
        coefficients=[]
        for t in valid_dates:
            coefficients.append(_corr(s.loc[t].where(e.loc[t]),references[name].loc[t],minimum=min_assets))
        family_corr[name]=_mean(coefficients)
    incremental={'status':'UNAVAILABLE','mean_residual_ic':None,'fit_start':None,'fit_end':None}
    if references and len(train):
        # Fit on past cross-sectional ranks only; a changed future reference
        # cannot alter coefficients. Collinear references never create evidence.
        names=list(references)
        xframes=[references[n].where(e).reindex(train).rank(axis=1,pct=True) for n in names]
        y=s.where(e).reindex(train).rank(axis=1,pct=True)
        x=np.column_stack([f.to_numpy().ravel() for f in xframes]); yv=y.to_numpy().ravel()
        good=np.isfinite(yv)&np.isfinite(x).all(axis=1)
        incremental.update(fit_start=train[0].isoformat(),fit_end=train[-1].isoformat(),n_fit=int(good.sum()))
        if good.sum()>=max(min_assets,len(names)+2):
            design=np.column_stack([np.ones(good.sum()),x[good]])
            coefficients=np.linalg.lstsq(design,yv[good],rcond=None)[0]
            residual=s.where(e).rank(axis=1,pct=True).copy()
            prediction=pd.DataFrame(coefficients[0],index=s.index,columns=s.columns)
            for n,c in zip(names,coefficients[1:]):
                prediction+=c*references[n].where(e).rank(axis=1,pct=True)
            residual-=prediction
            residual=residual.mask(residual.abs()<1e-12)
            values,n=_daily_ic(residual,fwd,e,valid_dates,min_assets)
            value=_ic_summary(values)
            baseline_values,_=_daily_ic(prediction,fwd,e,valid_dates,min_assets)
            baseline_ic=_ic_summary(baseline_values)['mean_ic']
            incremental.update(status='AVAILABLE' if value['n_dates'] else 'DEGENERATE_RESIDUAL',
                               mean_residual_ic=value['mean_ic'],n_dates=value['n_dates'],n_pairs=n,
                               baseline_mean_ic=baseline_ic,
                               incremental_ic_difference=None if baseline_ic is None or summary['mean_ic'] is None else summary['mean_ic']-baseline_ic,
                               coefficients=[float(v) for v in coefficients])
        else:
            incremental['status']='NEEDS_DATA'
    universe_counts=[int(e.loc[t].sum()) for t in valid_dates]
    stability['universe']={'status':'AVAILABLE','min_eligible':min(universe_counts,default=0),
                           'max_eligible':max(universe_counts,default=0),'declared_assets':len(signal.columns)}
    cost_sensitivity=[]
    for multiplier in (0,1,2,5):
        net=[r['gross_top']-multiplier*r['cost'] for r in quantile_rows if r['gross_top'] is not None]
        cost_sensitivity.append({'cost_multiplier':multiplier,'combined_bps':(fee_bps+slippage_bps)*multiplier,
                                 'mean_net_top_return':_mean(net)})
    reasons=[]
    if summary['n_dates']<min_periods: reasons.append('INSUFFICIENT_IC_DATES')
    if coverage<min_coverage: reasons.append('LOW_COVERAGE')
    if missing_marks or metrics['mean_net_top_return'] is None: reasons.append('MISSING_COHORT_MARKS')
    if metrics['monotonicity'] is None: reasons.append('INSUFFICIENT_QUANTILE_SPREAD')
    status='NEEDS_DATA' if reasons else ('PASS' if metrics['mean_ic']>=min_ic and metrics['mean_net_top_return']>=min_net_return else 'FALSIFIED')
    products=[r['value'] for r in ic_rows if r['value'] is not None]
    result={'schema_version':VERSION,'status':status,'metrics':metrics,'daily_ic':ic_rows,
            'quantile_returns':quantile_rows,'decay':decay,'stability':stability,'capacity':capacity,
            'family_correlation':family_corr,'incremental':incremental,'cost_sensitivity':cost_sensitivity,'missing_reasons':reasons,
            'pvalue':block_sign_pvalue(products),'pvalue_method':'two-sided exact block-sign; independent sign-symmetric blocks assumed; exploratory only',
            'policy':{'fee_bps':fee_bps,'slippage_bps':slippage_bps,'quantiles':quantiles,'horizons':list(horizons),
                      'direction':direction,'min_assets':min_assets,'min_periods':min_periods,'min_coverage':min_coverage,
                      'min_ic':min_ic,'min_net_return':min_net_return,'participation_rate':participation_rate,
                      'signal_lag':1,'turnover_basis':'0.5*(sum(abs(target_weight-change))+abs(cash-change))',
                      'cost_basis':'sum(abs(asset-target-change))*(fee_bps+slippage_bps)/10000'},
            'panel_fingerprint':panel_fingerprint,'evaluation_start':dates[0].isoformat(),
            'evaluation_end':dates[-1].isoformat(),'metric_basis':'LAGGED_COHORT_DIAGNOSTIC',
            'hypothesis_falsified':status=='FALSIFIED','financial_falsification':False,'use_status':'RESEARCH_ONLY','production_write':False,
            'limitations':['Cohort diagnostics are not executable fills or the actual paper ledger.',
                           'No annualization or market efficacy follows from synthetic observations.',
                           'Ties stay together; unavailable groups and capacity remain unavailable.',
                           'Target-weight turnover excludes price-drift rebalancing; fees charge both traded legs.']}
    return {**result,'fingerprint':_digest(result)}

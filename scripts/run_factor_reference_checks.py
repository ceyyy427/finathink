"""Independent scalar scorecard oracles; optional isolated pinned Alphalens probe.

Default uses the project runtime only. --alphalens-python runs an already
installed isolated environment; it never installs packages or fetches data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))


def ranks(values):
    return [1+sum(y<x for y in values)+(sum(y==x for y in values)-1)/2 for x in values]


def corr(x,y,rank=True):
    if rank: x,y=ranks(x),ranks(y)
    xm,ym=statistics.mean(x),statistics.mean(y)
    numerator=sum((a-xm)*(b-ym) for a,b in zip(x,y))
    denominator=math.sqrt(sum((a-xm)**2 for a in x)*sum((b-ym)**2 for b in y))
    return numerator/denominator if denominator else None


def fixture():
    # A scalar definition, not production score_factor output.
    signals=[[float((a+t//3)%8) for a in range(8)] for t in range(20)]
    prices=[[0.]*8 for _ in range(20)]
    for a in range(8):
        value=100.
        for t in range(20):
            value*=1+(a+1)*.001+math.sin((t+a)*1.3)*.002
            prices[t][a]=value
    return signals,prices


def scalar_oracle():
    signals,prices=fixture(); daily=[];qs=[];turnovers=[];costs=[];previous=[0.]*8;cash=1.
    for t in range(4,17):
        signal=signals[t-1]
        ret=[prices[t+1][a]/prices[t][a]-1 for a in range(8)]
        daily.append(corr(signal,ret))
        labels=[math.ceil(rank/8*4) for rank in ranks(signal)]
        quantiles=[statistics.mean([ret[a] for a in range(8) if labels[a]==q]) for q in range(1,5)]
        qs.append(quantiles)
        selected=[a for a in range(8) if labels[a]==4]
        weights=[1/len(selected) if a in selected else 0. for a in range(8)]
        traded=sum(abs(x-y) for x,y in zip(weights,previous));newcash=1-sum(weights)
        turnovers.append((traded+abs(newcash-cash))/2);costs.append(traded*.001)
        previous,cash=weights,newcash
    mean=lambda x:statistics.mean(x)
    group_means=[mean([r[q] for r in qs]) for q in range(4)]
    decay=[corr(signals[t-1],[prices[t+3][a]/prices[t][a]-1 for a in range(8)]) for t in range(4,15)]
    return {'mean_ic':mean(daily),'icir':mean(daily)/statistics.stdev(daily),
            'mean_gross_top_return':group_means[-1],'mean_gross_bottom_return':group_means[0],
            'mean_net_top_return':mean([q[-1]-c for q,c in zip(qs,costs)]),
            'mean_spread':mean([q[-1]-q[0] for q in qs]),'mean_turnover':mean(turnovers),
            'mean_cost':mean(costs),'coverage':1.,'monotonicity':corr([1,2,3,4],group_means),
            'decay_3':mean(decay),'capital_capacity':2000.,'holm_second':.06},


def production():
    import pandas as pd

    from finahinking.quant.evaluation.robustness import adjust_pvalues, score_factor
    signals,prices=fixture();index=pd.date_range('2025-01-01',periods=20,tz='UTC');columns=[f'A{i}' for i in range(8)]
    s,p=[pd.DataFrame(v,index=index,columns=columns) for v in (signals,prices)]
    result=score_factor(s,p,evaluation_index=index[4:18],train_index=index[:4],quantiles=4,horizons=(1,3),
                        amount=s*0+10000.,min_ic=-1)
    actual={k:result['metrics'][k] for k in ('mean_ic','icir','mean_gross_top_return','mean_gross_bottom_return',
            'mean_net_top_return','mean_spread','mean_turnover','mean_cost','coverage','monotonicity')}
    actual.update(decay_3=result['decay'][1]['mean_ic'],capital_capacity=result['capacity']['capital_capacity'],
                  holm_second=adjust_pvalues([.01,.03,.04])[1])
    return actual,result


def run_checks():
    expected=scalar_oracle()[0];actual,_=production()
    checks=[{'metric':k,'expected':v,'actual':actual[k],'absolute_tolerance':1e-12,
             'pass':actual[k] is not None and abs(v-actual[k])<=1e-12,
             'oracle':'Python standard-library ranks, scalar arithmetic and sample moments'} for k,v in expected.items()]
    return {'schema_version':'factor-reference-checks.v1','status':'PASS' if all(r['pass'] for r in checks) else 'FAIL',
            'independent_calculations':len(checks),'checks':checks,'financial_use_approved':False}


def alphalens_checks():
    import importlib.metadata

    import numpy as np
    import pandas as pd
    from alphalens import performance as ap
    signals,prices=fixture();times=pd.date_range('2025-01-01',periods=20,tz='UTC'); rows=[]
    for t in range(4,17):
        label=[math.ceil(r/8*4) for r in ranks(signals[t-1])]
        for a in range(8):
            rows.append({'date':times[t],'asset':f'A{a}','factor':signals[t-1][a],
                         '1D':prices[t+1][a]/prices[t][a]-1,'factor_quantile':label[a],
                         'group':'left' if a<4 else 'right'})
    data=pd.DataFrame(rows).set_index(['date','asset'])
    checks=[]
    def add(name,actual,expected,scope='ALIGNED_PRODUCTION'):
        av=np.asarray(actual,dtype=float);ev=np.asarray(expected,dtype=float)
        passed=av.shape==ev.shape and bool(np.allclose(av,ev,atol=1e-12,rtol=1e-12,equal_nan=True))
        checks.append({'metric':name,'pass':passed,'absolute_tolerance':1e-12,'scope':scope,
                       'actual':av.tolist(),'expected':ev.tolist()})
    expected=scalar_oracle()[0]
    raw_ic=[corr(signals[t-1],[prices[t+1][a]/prices[t][a]-1 for a in range(8)]) for t in range(4,17)]
    ic=ap.factor_information_coefficient(data)['1D']
    add('daily_rank_ic',ic,raw_ic)
    add('mean_ic',ap.mean_information_coefficient(data)['1D'],expected['mean_ic'])
    add('sample_icir',float(ic.mean()/ic.std(ddof=1)),expected['icir'])
    group_expected=[]
    for t in range(4,17):
        for subset in (range(4),range(4,8)):
            group_expected.append(corr([signals[t-1][a] for a in subset],[prices[t+1][a]/prices[t][a]-1 for a in subset]))
    add('group_rank_ic',ap.factor_information_coefficient(data,by_group=True)['1D'],group_expected,'REFERENCE_ONLY')
    adjusted=[]
    for t in range(4,17):
        values=[prices[t+1][a]/prices[t][a]-1 for a in range(8)]
        adjusted_values=[values[a]-statistics.mean(values[:4] if a<4 else values[4:]) for a in range(8)]
        adjusted.append(corr(signals[t-1],adjusted_values))
    add('group_adjusted_rank_ic',ap.factor_information_coefficient(data,group_adjust=True)['1D'],adjusted,'REFERENCE_ONLY')
    bydate,error=ap.mean_return_by_quantile(data,by_date=True,demeaned=False)
    oracle_means=[];oracle_errors=[]
    for q in range(1,5):
        for t in range(4,17):
            labels=[math.ceil(r/8*4) for r in ranks(signals[t-1])]
            values=[prices[t+1][a]/prices[t][a]-1 for a in range(8) if labels[a]==q]
            oracle_means.append(statistics.mean(values));oracle_errors.append(statistics.stdev(values)/math.sqrt(len(values)))
    add('quantile_return_by_date',bydate['1D'],oracle_means)
    add('quantile_standard_error',error['1D'],oracle_errors,'REFERENCE_ONLY')
    aggregate,_=ap.mean_return_by_quantile(data,demeaned=False)
    add('quantile_mean_returns',aggregate['1D'],[statistics.mean(oracle_means[q*13:(q+1)*13]) for q in range(4)])
    spread,_=ap.compute_mean_returns_spread(bydate,4,1)
    add('half_gross_long_short_spread',spread['1D'],[(oracle_means[39+i]-oracle_means[i])/2 for i in range(13)])
    turnovers=ap.quantile_turnover(data['factor_quantile'],4).dropna()
    previous=None;values=[]
    for t in range(4,17):
        labels=[math.ceil(r/8*4) for r in ranks(signals[t-1])];now={a for a in range(8) if labels[a]==4}
        if previous is not None: values.append(len(now-previous)/len(now))
        previous=now
    add('quantile_membership_turnover',turnovers,values)
    auto=ap.factor_rank_autocorrelation(data).dropna()
    add('rank_autocorrelation',auto,[corr(signals[t-1],signals[t-2]) for t in range(5,17)],'REFERENCE_ONLY')
    top=bydate['1D'].xs(4,level='factor_quantile');product=1.;cumulative=[]
    for r in top: product*=1+r;cumulative.append(product)
    add('cumulative_simple_returns',ap.cumulative_returns(top),cumulative,'REFERENCE_ONLY')
    return {'status':'PASS' if all(c['pass'] for c in checks) else 'FAIL','reference_commit':'60755d1cc7d9d36a4c70c8344cbdb168fe99df24',
            'versions':{k:importlib.metadata.version(k) for k in ('alphalens-reloaded','numpy','pandas','scipy','statsmodels')},
            'checks':checks,'comparison_count':len(checks),'financial_use_approved':False,
            'differences':['Alphalens at the pinned commit scales long-short spread by 0.5; Finathink raw top-minus-bottom is twice that spread.',
                           'Membership turnover excludes first cash entry; Finathink charges initial cash-to-cohort and both asset trading legs.',
                           'REFERENCE_ONLY statistics validate reference implementation semantics and are not new product capabilities.']}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--alphalens-python');parser.add_argument('--use-alphalens',action='store_true');parser.add_argument('--output')
    args=parser.parse_args()
    if args.alphalens_python:
        command=[args.alphalens_python,str(Path(__file__).resolve()),'--use-alphalens']
        if args.output:command+=['--output',args.output]
        return subprocess.run(command,check=False).returncode
    result=alphalens_checks() if args.use_alphalens else run_checks()
    result['script_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    output=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    if args.output:Path(args.output).write_text(output)
    print(output,end='')
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())

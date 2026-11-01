"""Independent small-panel expectations for the Phase25 scorecard."""
import importlib.util

import numpy as np
import pandas as pd
import pytest


def api():
    assert importlib.util.find_spec('finahinking.quant.evaluation.robustness'), 'Phase25 scorecard is absent'
    from finahinking.quant.evaluation.robustness import adjust_pvalues, score_factor
    return adjust_pvalues, score_factor


def sample(n=18):
    index = pd.date_range('2026-01-01', periods=n, tz='UTC')
    signal = pd.DataFrame(np.tile([1., 2., 3., 4.], (n, 1)), index=index, columns=list('ABCD'))
    # Four known geometric returns. Each cross-section has Spearman rho exactly 1.
    prices = pd.DataFrame({a: 10*(1+r)**np.arange(n) for a,r in zip('ABCD', [.01,.02,.03,.04])},index=index)
    return signal, prices


def test_holm_known_example_and_input_order():
    adjust, _ = api()
    assert adjust([.01,.03,.04]) == pytest.approx([.03,.06,.06])
    assert adjust([.04,.01,.03]) == pytest.approx([.06,.03,.06])
    assert adjust([.01,None,.03]) == [.03,None,.06]
    for invalid in ([True],[-.1],[float('nan')],[1.1]):
        with pytest.raises(ValueError): adjust(invalid)


def test_lagged_quantile_gross_cost_turnover_and_exact_ic():
    _, score = api(); signal, prices = sample()
    out=score(signal,prices,evaluation_index=prices.index[2:],quantiles=2,min_periods=4,fee_bps=10,slippage_bps=0)
    assert out['metrics']['mean_ic'] == pytest.approx(1)
    assert out['metrics']['mean_gross_top_return'] == pytest.approx(.035)
    assert out['metrics']['mean_gross_bottom_return'] == pytest.approx(.015)
    assert out['metrics']['mean_spread'] == pytest.approx(.02)
    assert out['metrics']['monotonicity'] == pytest.approx(1)
    first=out['quantile_returns'][0]
    assert first['turnover'] == pytest.approx(1) # From cash to a fully invested top cohort.
    assert first['cost'] == pytest.approx(.001) # One entry leg, 10bps of portfolio notional.
    assert first['net_top'] == pytest.approx(.034)
    assert out['quantile_returns'][1]['turnover'] == 0
    assert out['metrics']['icir'] is None # A constant IC has no estimable dispersion ratio.


def test_full_switch_has_two_cost_legs_and_no_same_day_signal():
    _, score=api();signal,prices=sample()
    signal.iloc[3:]=signal.iloc[3:].iloc[:,::-1].to_numpy()
    out=score(signal,prices,evaluation_index=prices.index[2:7],quantiles=2,min_periods=2,fee_bps=10,slippage_bps=10)
    rows=out['quantile_returns']
    assert rows[1]['gross_top']==pytest.approx(.035) # Signal at t=3 cannot trade at t=3.
    assert rows[2]['gross_top']==pytest.approx(.015)
    assert rows[2]['turnover']==pytest.approx(1)
    assert rows[2]['cost']==pytest.approx(.004) # Sell old 100%, buy new 100%, each at20bps.


def test_terminal_prices_and_signals_cannot_change_prior_score():
    _, score=api();signal,prices=sample()
    eval_index=prices.index[2:12]
    first=score(signal,prices,evaluation_index=eval_index,quantiles=2,min_periods=2)
    signal2=signal.copy();price2=prices.copy();signal2.iloc[12:]*=-100;price2.iloc[12:]*=3
    assert first==score(signal2,price2,evaluation_index=eval_index,quantiles=2,min_periods=2)
    assert first['quantile_returns'][-1]['observed_at']==prices.index[10].isoformat()


def test_ties_are_not_split_by_asset_identifier_and_low_coverage_blocks():
    _, score=api();signal,prices=sample();signal[:]=1
    result=score(signal,prices,evaluation_index=prices.index[2:],quantiles=2,min_periods=2)
    assert result['status']=='NEEDS_DATA'
    assert result['metrics']['mean_ic'] is None
    signal,prices=sample();signal.iloc[:,1:]=np.nan
    result=score(signal,prices,evaluation_index=prices.index[2:],quantiles=2,min_periods=2)
    assert result['status']=='NEEDS_DATA'
    assert result['metrics']['coverage']==pytest.approx(.25)


def test_missing_capacity_and_groups_are_explicit_not_zero():
    _, score=api();signal,prices=sample()
    out=score(signal,prices,evaluation_index=prices.index[2:],quantiles=2,min_periods=2)
    assert out['capacity']['status']=='UNAVAILABLE'
    assert out['capacity']['capital_capacity'] is None
    assert out['stability']['industry']['status']=='UNAVAILABLE'
    assert out['incremental']['status']=='UNAVAILABLE'


def test_capacity_industry_size_family_and_train_only_incremental():
    _, score=api();signal,prices=sample(30)
    groups=pd.DataFrame(np.tile(['x','x','y','y'],(30,1)),index=prices.index,columns=prices.columns)
    size=signal*1000;amount=signal*0+100000
    out=score(signal,prices,evaluation_index=prices.index[15:],train_index=prices.index[:12],groups=groups,size=size,amount=amount,
              reference_signals={'same':signal},quantiles=2,min_assets=2,min_periods=4)
    assert out['family_correlation']['same']==pytest.approx(1)
    assert out['incremental']['status']=='DEGENERATE_RESIDUAL'
    assert out['capacity']['status']=='AVAILABLE'
    assert out['capacity']['capital_capacity']==pytest.approx(20000)
    assert out['stability']['industry']['status']=='AVAILABLE'
    assert out['stability']['size']['status']=='AVAILABLE'
    assert out['incremental']['fit_end']==prices.index[11].isoformat()


def test_invalid_contract_and_training_overlap_are_rejected():
    _, score=api();signal,prices=sample()
    with pytest.raises(ValueError): score(signal,prices,evaluation_index=prices.index[2:],train_index=prices.index[:4])
    with pytest.raises(ValueError): score(signal,prices.iloc[:,::-1],evaluation_index=prices.index[2:])
    for value in (-1,float('nan'),True):
        with pytest.raises(ValueError): score(signal,prices,evaluation_index=prices.index[2:],fee_bps=value)


def test_negative_signal_is_falsified_but_invalid_mark_is_needs_data():
    _, score=api();signal,prices=sample()
    result=score(-signal,prices,evaluation_index=prices.index[2:],quantiles=2,min_periods=2)
    assert result['status']=='FALSIFIED'
    assert result['hypothesis_falsified'] is True
    assert result['financial_falsification'] is False
    prices.loc[prices.index[7],'D']=np.nan
    blocked=score(signal,prices,evaluation_index=prices.index[2:],quantiles=2,min_periods=2)
    assert blocked['status']=='NEEDS_DATA'
    assert blocked['financial_falsification'] is False


def test_noneligible_assets_never_affect_cohort_or_incremental_rank_fit():
    _,score=api();signal,prices=sample(30)
    eligible=signal.gt(0);eligible['D']=False
    opts={'evaluation_index':prices.index[15:],'train_index':prices.index[:12],
          'eligible':eligible,'reference_signals':{'same':signal},'quantiles':2,'min_assets':2,'min_periods':2,'min_coverage':.7}
    first=score(signal,prices,**opts)
    changed=signal.copy();changed['D']=-100000
    second=score(changed,prices,**{**opts,'reference_signals':{'same':changed}})
    assert first['metrics']==second['metrics']
    assert first['incremental']==second['incremental']
    assert first['stability']['universe']['max_eligible']==3
    assert first['metrics']['coverage']==1


def test_cost_sensitivity_and_capacity_shortfall_are_visible():
    _,score=api();signal,prices=sample()
    amount=signal*0+100000
    out=score(signal,prices,evaluation_index=prices.index[2:],quantiles=2,min_periods=2,amount=amount,capital=30000)
    assert out['capacity']['within_capacity'] is False
    assert out['cost_sensitivity'][0]['mean_net_top_return']==pytest.approx(.035)
    assert out['cost_sensitivity'][1]['mean_net_top_return']==out['metrics']['mean_net_top_return']
    assert out['cost_sensitivity'][-1]['mean_net_top_return']<out['cost_sensitivity'][1]['mean_net_top_return']


def test_disjoint_evaluation_dates_do_not_use_gap_labels():
    _,score=api();signal,prices=sample()
    dates=prices.index[[2,3,7,8,9,10]]
    out=score(signal,prices,evaluation_index=dates,quantiles=2,min_periods=2)
    assert [r['observed_at'] for r in out['quantile_returns']]==[prices.index[i].isoformat() for i in (2,7,8,9)]
    assert out['metrics']['n_evaluation_dates']==4

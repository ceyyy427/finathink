"""Strategy definitions exercise distinct deterministic decision paths."""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


class SourceAssets:
    """Exercise source bytes before registering the approved wheel resource hashes."""

    def read_text(self, resource_id, **kwargs):
        assets = Path(__file__).resolve().parents[2] / 'src/finahinking/_package_data/research_assets/strategies'
        name = {'strategy-templates': 'templates.json', 'strategy-risk-rules': 'risk-rules.json'}[resource_id]
        return (assets / name).read_text()


def catalog():
    assert importlib.util.find_spec('finahinking.research.strategy_catalog'), 'strategy catalog is not implemented'
    from finahinking.research.strategy_catalog import StrategyCatalog

    return StrategyCatalog(SourceAssets())


def panel_fixture():
    dates = pd.bdate_range('2024-01-02', periods=30, tz='UTC')
    names = ['A', 'B', 'C', 'D', 'E', 'F']
    rows = []
    for index, date in enumerate(dates):
        for offset, asset in enumerate(names):
            close = 100 + index * (offset + 1) * 0.4 + ((index * (offset + 2)) % 5) * .2
            rows.append({
                'observed_at': date, 'instrument': asset, 'close': close,
                'is_session': True, 'in_universe': True, 'trade_allowed': True, 'suspended': False,
                'split_factor': 1., 'amount': 1e6, 'industry': 'sector-' + str(offset % 3),
                'market_cap': 2000 + offset * 400, 'total_assets': 900 + offset * 23,
                'book_equity': 350 + offset * 72, 'net_profit': 20 + offset * offset,
                'style_beta': [0.8, 0.9, 1.1, 1.5, 1.1, 1.2][offset],
                'nav': close, 'tracking_error': .01 + offset / 1000,
                'policy_regime': 'RISK_ON', 'event_type': 'EARNINGS' if index in (15, 21) else 'NONE',
            })
    return pd.DataFrame(rows).set_index(['observed_at', 'instrument']), {
        name: 'EQUITY' if name in 'ABCD' else 'ETF' for name in names
    }


def test_each_template_has_a_distinct_executable_or_theoretical_path():
    definitions = catalog()
    from finahinking.research.strategy_catalog import bind_parameters
    from finahinking.research.strategy_selection import select_strategy

    frame, classes = panel_fixture()
    results = {}
    for template in definitions.templates():
        params = bind_parameters(template, {})
        result = select_strategy(template, frame, params, classes)
        results[template['template_id']] = result
    assert len(results) == 12
    for template_id, result in results.items():
        if template_id in {'multifactor-neutral', 'event-study', 'pairs-mean-reversion'}:
            assert result['status'] == 'RESEARCH_ONLY', (template_id, result)
            assert result['target_weights'] == {}
            assert result['diagnostics']
        else:
            assert result['status'] == 'READY', (template_id, result)
            assert sum(result['target_weights'].values()) <= .900000001
            assert all(0 <= weight <= .5 for weight in result['target_weights'].values())
    assert set(results['etf-risk-budget']['target_weights']) == {'E', 'F'}
    assert results['equal-weight']['target_weights'] != results['short-term-reversal']['target_weights']
    assert abs(sum(results['multifactor-neutral']['diagnostics']['theoretical_weights'].values())) < 1e-10
    assert results['event-study']['diagnostics']['matured_events'] == 12


@pytest.mark.parametrize(('field', 'safe', 'unsafe'), [
    ('pit_valid', True, False), ('financial_anomaly', .1, .9),
    ('tradeable', True, False), ('liquidity_participation', .01, .9),
    ('concentration', .2, .99), ('industry_concentration', .3, .99),
    ('collinearity', .5, .9999), ('volatility', .02, .9),
    ('tail_loss', .02, .9), ('logic_loss', .02, .9),
    ('drawdown', .05, .99), ('stress_loss', .03, .99),
])
def test_twelve_risk_rules_pass_trigger_and_need_data(field, safe, unsafe):
    definitions = catalog()
    from finahinking.research.strategy_risk import evaluate_risk_rules

    rule = next(rule for rule in definitions.risk_rules() if rule['metric'] == field)
    for value, expected in [(safe, 'PASS'), (unsafe, 'TRIGGERED'), (None, 'NEEDS_DATA')]:
        item = evaluate_risk_rules([rule], {field: value})[0]
        assert item['status'] == expected and item['reason']
    assert evaluate_risk_rules([rule], {})[0]['status'] == 'NEEDS_DATA'


def test_bind_parameters_uses_frozen_identity_and_rejects_unbounded_values():
    definitions = catalog()
    from finahinking.research.strategy_catalog import bind_parameters

    template = definitions.template('trend-breakout')
    original = template['fingerprint']
    assert bind_parameters(template, {'window': 7})['window'] == 7
    for values in ({'window': True}, {'window': 99999}, {'fee_bps': float('nan')}, {'code': 'print(0)'}):
        with pytest.raises(ValueError):
            bind_parameters(template, values)
    template['parameter_bounds']['window']['default'] = 8
    assert definitions.template('trend-breakout')['fingerprint'] == original
    with pytest.raises(ValueError):
        bind_parameters(template, {})


def test_split_jump_does_not_change_signal_and_missing_ratio_is_unknown():
    template = catalog().template('single-factor-quantile')
    from finahinking.research.strategy_catalog import bind_parameters
    from finahinking.research.strategy_selection import select_strategy

    params = bind_parameters(template, {})
    frame, classes = panel_fixture()
    result = select_strategy(template, frame, params, classes)
    changed = frame.copy()
    cutoff = changed.index.get_level_values('observed_at').unique()[-3]
    after_split = changed.index.get_level_values('observed_at') >= cutoff
    leading_fund = changed.index.get_level_values('instrument') == 'F'
    assert 'F' in result['target_weights']
    changed.loc[after_split & leading_fund, 'close'] /= 2
    changed.loc[after_split & leading_fund, 'split_factor'] = 2.
    assert select_strategy(template, changed, params, classes) == result
    changed['split_factor'] = np.nan
    assert select_strategy(template, changed, params, classes)['status'] == 'NEEDS_DATA'


def test_missing_financials_remain_needs_data_and_do_not_assert_anomaly():
    definitions = catalog()
    from finahinking.research.strategy_risk import evaluate_risk_rules, strategy_risk_inputs

    frame, classes = panel_fixture()
    frame.loc[frame.index.get_level_values('instrument') == 'A', 'net_profit'] = np.nan
    inputs = strategy_risk_inputs(frame, {'A': .4}, [10000, 10200], 10200., classes, pit_valid=True)
    results = {item['rule_id']: item for item in evaluate_risk_rules(definitions.risk_rules(), inputs)}
    assert results['financial-abnormality']['status'] == 'NEEDS_DATA'
    assert results['financial-abnormality']['value'] is None
    assert json.dumps(results, allow_nan=False)


def test_financial_single_factor_needs_only_its_declared_ratio_inputs():
    template = catalog().template('single-factor-quantile')
    from finahinking.research.strategy_catalog import bind_parameters
    from finahinking.research.strategy_selection import select_strategy

    frame, classes = panel_fixture()
    equities = frame.index.get_level_values('instrument').isin(['A', 'B', 'C', 'D'])
    value_only = frame.loc[equities].drop(columns=['net_profit', 'total_assets'])
    params = bind_parameters(template, {'factor': 'book_to_market'})
    assert select_strategy(template, value_only, params, classes)['status'] == 'READY'
    params = bind_parameters(template, {'factor': 'return_on_assets'})
    assert select_strategy(template, value_only, params, classes)['status'] == 'NEEDS_DATA'


def test_event_history_preserves_delisted_events_and_unavailable_marks():
    template = catalog().template('event-study')
    from finahinking.research.strategy_catalog import bind_parameters
    from finahinking.research.strategy_selection import select_strategy

    frame, classes = panel_fixture()
    dates = frame.index.get_level_values('observed_at').unique()
    frame.loc[(dates[-1], 'A'), 'in_universe'] = False
    frame.loc[(dates[26], 'A'), 'close'] = np.nan
    result = select_strategy(template, frame, bind_parameters(template, {}), classes)
    assert result['status'] == 'RESEARCH_ONLY'
    assert result['diagnostics']['matured_events'] == 11
    assert result['diagnostics']['missing_marks'] == 1
    assert result['target_weights'] == {}

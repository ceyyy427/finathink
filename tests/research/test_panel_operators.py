"""Hand-computed operator expectations; no expected values from the evaluator."""
import importlib.util
import math

import numpy as np
import pandas as pd
import pytest

from tests.data.test_research_panel import fixture


def evaluate(expression, panel=None):
    assert importlib.util.find_spec('finahinking.factors.panel_operators'), 'Phase23 scoped operators are missing'
    from finahinking.factors.dsl import evaluate_panel_expression
    if panel is None:
        snap, rules, contract, build = fixture()
        panel = build(snap, [rules], contract)
    return evaluate_panel_expression(expression, panel)


@pytest.mark.parametrize('expression,expected', [
    ('ts_lag(close,2)', 3), ('ts_return(close,2)', 1/3), ('ts_diff(close,2)', 1),
    ('ts_sum(close,3,3)', 9), ('ts_mean(close,3,3)', 3), ('ts_std(close,3,3)', math.sqrt(2/3)),
    ('ts_min(close,3,3)', 2), ('ts_max(close,3,3)', 4), ('ts_rank(close,3,3)', 1),
    ('ts_corr(close,close,3,3)', 1), ('ts_cov(close,close,3,3)', 2/3),
    ('ts_slope(close,3,3)', .5), ('ts_linear_decay(close,3,3)', 19/6),
    ('ts_argmin(close,3,3)', 1), ('ts_argmax(close,3,3)', 2),
    ('ts_zscore(close,3,3)', math.sqrt(1.5)),
    ('cs_rank(close,3)', 2/3), ('cs_zscore(close,3)', -11/math.sqrt(314)),
    ('cs_winsorize(close,0.25,0.75,3)', 4), ('cs_demean(close,3)', -11),
    ('cs_neutralize(close,volume,3)', None),
    ('group_rank(close,in_universe,3)', 2/3),
    ('group_zscore(close,in_universe,3)', -11/math.sqrt(314)),
    ('group_demean(close,in_universe,3)', -11),
])
def test_all_24_operator_hand_values(expression, expected):
    result = evaluate(expression)
    value = result.loc[(pd.Timestamp('2026-01-05T16:00Z'), 'AAA')]
    if expected is None:
        assert pd.isna(value)
        assert 'DEGENERATE_EXPOSURE' in result.attrs['diagnostics']
    else:
        assert value == pytest.approx(expected)
    assert result.attrs['semantics_version'] == 'bounded-panel-dsl.v1'


def test_rolling_rank_differs_from_cross_section_rank():
    ts = evaluate('ts_rank(close,3,3)').xs('AAA', level='instrument')
    cs = evaluate('cs_rank(close,3)').xs('AAA', level='instrument')
    assert math.isnan(ts.iloc[0]) and math.isnan(ts.iloc[1])
    assert ts.iloc[2:].tolist() == [2/3, 1]
    assert cs.tolist() == [1/3, 1/3, .5, 2/3]


def test_future_mutation_and_asset_permutation_invariance():
    from finahinking.data.research_panel import ResearchPanel
    snap, rules, contract, build = fixture()
    panel = build(snap, [rules], contract)
    frame = panel.frame
    frame.loc[frame.index.get_level_values(0).max(), 'close'] *= 100
    changed = ResearchPanel(frame, panel.audit, contract, panel.fingerprint)
    for expression in ['ts_mean(close,3,2)', 'cs_zscore(close,2)', 'cs_neutralize(close,volume,3)']:
        a, b = evaluate(expression, panel), evaluate(expression, changed)
        pd.testing.assert_series_equal(a.iloc[:-3], b.iloc[:-3])
        reordered = ResearchPanel(panel.frame.iloc[::-1], panel.audit, contract, panel.fingerprint)
        pd.testing.assert_series_equal(a, evaluate(expression, reordered))


def test_missing_values_min_periods_ties_and_units():
    from finahinking.data.research_panel import ResearchPanel
    snap, rules, contract, build = fixture()
    panel = build(snap, [rules], contract)
    frame = panel.frame
    frame.loc[(pd.Timestamp('2026-01-03T16:00Z'), 'AAA'), 'close'] = np.nan
    altered = ResearchPanel(frame, panel.audit, contract, panel.fingerprint)
    assert evaluate('ts_mean(close,3,2)', altered).xs('AAA', level='instrument').iloc[2] == 1.5
    assert pd.isna(evaluate('ts_mean(close,3,3)', altered).xs('AAA', level='instrument').iloc[2])
    assert evaluate('ts_cov(close,close,3,3)').attrs['unit'] == 'CNY*CNY'
    assert evaluate('ts_return(close,1)').attrs['unit'] == 'ratio'


def test_nonconstant_neutralization_and_separate_industry_groups():
    from finahinking.data.research_panel import ResearchPanel
    snap, rules, contract, build = fixture()
    panel = build(snap, [rules], contract)
    frame = panel.frame
    frame['volume'] = [1.,2.,3.] * 4
    frame['industry'] = ['alpha','alpha','beta'] * 4
    audit = panel.audit; audit['units']['industry'] = 'historical_classification'
    altered = ResearchPanel(frame, audit, contract, panel.fingerprint)
    residuals = evaluate('cs_neutralize(close,volume,3)', altered).iloc[-3:]
    assert residuals.to_list() == pytest.approx([-12.5,25,-12.5])
    assert residuals.sum() == pytest.approx(0, abs=1e-12)
    assert evaluate('group_rank(close,industry,2)', altered).iloc[-3:-1].tolist() == [.5,1.]
    assert pd.isna(evaluate('group_rank(close,industry,2)', altered).iloc[-1])


def test_every_operator_future_invariance_and_permutation():
    from finahinking.data.research_panel import ResearchPanel
    from finahinking.factors.panel_operators import OPERATOR_CONTRACTS
    snap, rules, contract, build = fixture()
    original = build(snap, [rules], contract)
    frame = original.frame
    frame.loc[frame.index.get_level_values(0).max(), ['close','volume']] *= 900
    mutated = ResearchPanel(frame, original.audit, contract, original.fingerprint)
    shuffled = ResearchPanel(original.frame.sample(frac=1, random_state=23), original.audit, contract, original.fingerprint)
    for op in OPERATOR_CONTRACTS:
        if op in {'ts_lag','ts_return','ts_diff'}:
            args = 'close,1'
        elif op in {'ts_corr','ts_cov'}:
            args = 'close,volume,3,2'
        elif op.startswith('ts_'):
            args = 'close,3,2'
        elif op.startswith('group_'):
            args = 'close,in_universe,2'
        elif op == 'cs_neutralize':
            args = 'close,volume,3'
        elif op == 'cs_winsorize':
            args = 'close,0.1,0.9,2'
        else:
            args = 'close,2'
        expression = f'{op}({args})'
        baseline = evaluate(expression, original)
        pd.testing.assert_series_equal(baseline.iloc[:-3], evaluate(expression, mutated).iloc[:-3])
        pd.testing.assert_series_equal(baseline, evaluate(expression, shuffled))

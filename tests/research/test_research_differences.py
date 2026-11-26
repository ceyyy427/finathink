"""Frozen inputs, never today's configuration, explain published differences."""
import copy
import importlib.util

import pytest

from finahinking.p8_2.data_sources import FixtureMarketDataSource
from finahinking.research.snapshot_tools import local_research_policy
from tests.research.test_phase7_portfolio_workflow import run


def modules():
    assert importlib.util.find_spec('finahinking.research.research_differences'), 'missing explanatory comparison'
    from finahinking.research.research_differences import compare_research
    from finahinking.research.research_identity import execution_identity
    return compare_research, execution_identity


def source():
    _, identity = modules()
    policy = local_research_policy()
    return {'dataset_fingerprint': 'a' * 64, 'as_of': '2026-10-01',
        'quant_analytics': {'metrics': {'total_return': 0.1}, 'comparison_conditions': {
            'instrument': 'AAA', 'sample_period': ['2026-01-01', '2026-02-01'],
            'scoring_index': 'b' * 64, 'split': {'oos': False}, 'metric_basis': 'OBSERVATION'}},
        'reproduction': {'identity': identity(policy), 'policy': policy, 'publication_before': None}}


@pytest.mark.parametrize('category,key,value', [
    ('parameters', 'threshold', 0.01), ('costs', 'fee_bps', 10),
    ('risk', 'max_cvar', 0.02), ('method', 'engine_version', 'different.v2'),
])
def test_changed_frozen_input_explains_incompatibility(category, key, value):
    compare, _ = modules()
    a = source()
    b = copy.deepcopy(a)
    b['reproduction']['policy'][key] = value
    result = compare(a, b)
    assert result['status'] == 'INCOMPATIBLE'
    assert next(row for row in result['categories'] if row['id'] == category)['status'] == 'CHANGED'
    assert result['metric_differences'] == {}


def test_same_conditions_allow_finite_metric_differences_but_unknowns_never_equal():
    compare, _ = modules()
    a, b = source(), source()
    b['quant_analytics']['metrics'] = {'total_return': 0.3, 'volatility': None}
    result = compare(a, b)
    assert result['status'] == 'COMPATIBLE'
    assert result['metric_differences']['total_return'] == pytest.approx(0.2)
    assert 'volatility' not in result['metric_differences']
    for item in (a, b):
        item.pop('reproduction')
    unknown = compare(a, b)
    assert unknown['status'] == 'UNKNOWN' and unknown['metric_differences'] == {}
    assert next(row for row in unknown['categories'] if row['id'] == 'code')['status'] == 'UNKNOWN'


def test_data_and_code_differences_and_nested_policy_are_not_ignored():
    compare, _ = modules()
    a, b = source(), source()
    b['dataset_fingerprint'] = 'c' * 64
    b['reproduction']['identity']['source_digest'] = 'd' * 64
    result = compare(a, b)
    assert {row['id'] for row in result['categories'] if row['status'] == 'CHANGED'} == {'data', 'code'}
    a, b = source(), source()
    b['reproduction']['policy']['unrecognized_risk_rule'] = 1
    assert compare(a, b)['status'] == 'INCOMPATIBLE'


def test_real_workflow_freezes_policy_code_dependencies_in_report(tmp_path):
    from finahinking.research.reports import ReportBundleWriter
    _, identity = modules()
    result, tools = run(FixtureMarketDataSource(points=20).snapshot('AAA'))
    frozen = tools.analysis['reproduction']
    assert frozen['policy'] == local_research_policy()
    assert frozen['identity'] == identity(local_research_policy())
    assert set(frozen['identity']['dependencies']) == {'python', 'numpy', 'pandas'}
    manifest = ReportBundleWriter().write(result, tmp_path, quant_analytics=tools.analysis)
    assert manifest.source_snapshot['reproduction'] == frozen
    assert len(frozen['identity']['source_digest']) == 64
    assert '/Users/' not in str(frozen) and 'endpoint' not in str(frozen)


def test_same_controlled_recipe_is_known_without_optional_method_attachment():
    compare, _ = modules()
    a = source()
    a['factor_study'] = {'phase': 'VALIDATION', 'charter': {'version': 'factor-workbench.v1',
        'recipe_id': 'close_reversal', 'recipe_version': 'v1', 'windows': [3, 5, 8]}}
    assert compare(a, copy.deepcopy(a))['status'] == 'COMPATIBLE'


def studio_source():
    from finahinking.research.research_differences import validation_comparison_summary

    plan = {
        'source': {'panel_ref': 'panel', 'policy_digest': 'policy'}, 'original_ledger': 'ledger',
        'original_report': 'published', 'studio_version': 'studio.v1', 'run_id': 'paper-run', 'template_id': 'equal-weight',
        'plan_fingerprint': 'f' * 64, 'objective': 'net_return', 'method': 'walk-forward.v1',
        'selection_rule': 'MAX_TRAIN_SCORE', 'stability_rule': 'GRID', 'account_initialization': 'FRESH_CASH',
        'warmup': 'TRAIN', 'parameter': 'rebalance_every', 'parameter_values': [3, 5], 'windows': [
            {'index': 1, 'train_start': '2025-01-02', 'test_start': None, 'test_observations': 0}],
        'options': {'stability_tolerance': .002, 'train_size': 32, 'test_size': 16},
        'scenarios': [{'id': 'baseline', 'fee_bps': 5., 'max_volume_participation': .01}],
        'portfolio': {'target_exposure': .8, 'max_single_weight': .4},
    }
    return {**source(), 'strategy_validation': validation_comparison_summary(plan)}


def test_same_studio_plan_describes_actual_validation_conditions_and_suppresses_auxiliary_baseline():
    compare, _ = modules()
    a = studio_source()
    b = copy.deepcopy(a)
    b['quant_analytics']['metrics'] = {'total_return': .7}
    result = compare(a, b)
    assert result['status'] == 'COMPATIBLE' and result['metric_differences'] == {}
    assert '逐窗收益' in result['basis']
    categories = {row['id']: row['right'] for row in result['categories']}
    assert categories['parameters']['grid'] == [3, 5]
    assert categories['costs']['execution_scenarios'][0]['fee_bps'] == 5.
    assert categories['risk']['portfolio']['target_exposure'] == .8
    assert categories['data']['source_run'] == 'paper-run'
    assert compare(a, source())['status'] == 'INCOMPATIBLE'
    assert compare(source(), a)['metric_differences'] == {}


@pytest.mark.parametrize(('category', 'key', 'change'), [
    ('data', 'ledger', 'new ledger'), ('method', 'objective', 'spread'),
    ('parameters', 'grid', [3, 5, 7]), ('risk', 'portfolio', {'target_exposure': .9}),
    ('costs', 'execution_scenarios', [{'id': 'lower_capacity', 'max_volume_participation': .001}]),
])
def test_different_studio_sources_grids_settings_and_costs_are_incomparable(category, key, change):
    compare, _ = modules()
    a = studio_source()
    b = copy.deepcopy(a)
    b['strategy_validation']['conditions'][category][key] = change
    result = compare(a, b)
    assert result['status'] == 'INCOMPATIBLE' and result['metric_differences'] == {}
    assert next(row['status'] for row in result['categories'] if row['id'] == category) == 'CHANGED'


def test_archived_studio_identity_remains_distinguishable_from_baseline_and_new_freezes():
    compare, _ = modules()
    a = source()
    a['strategy_validation'] = {'run_id': 'paper-1', 'template_id': 'equal-weight', 'plan_fingerprint': 'f' * 64}
    assert compare(a, a)['status'] == 'UNKNOWN' and compare(a, a)['metric_differences'] == {}
    assert compare(a, source())['status'] == 'INCOMPATIBLE'
    detailed = studio_source()
    detailed['strategy_validation'].update(a['strategy_validation'])
    assert compare(a, detailed)['status'] == 'UNKNOWN'
    for key, value in [('run_id', 'paper-2'), ('plan_fingerprint', 'b' * 64)]:
        b = copy.deepcopy(a)
        b['strategy_validation'][key] = value
        assert compare(a, b)['status'] == 'INCOMPATIBLE'

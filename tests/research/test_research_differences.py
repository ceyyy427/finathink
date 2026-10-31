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

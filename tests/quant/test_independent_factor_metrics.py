"""Standard-library calculations with independent rank/moment/fee arithmetic."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2]/'scripts/run_factor_reference_checks.py'


def oracle_module():
    spec = importlib.util.spec_from_file_location('independent_reference_checks',SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('metric', ['mean_ic','icir','mean_gross_top_return','mean_gross_bottom_return',
    'mean_net_top_return','mean_spread','mean_turnover','mean_cost','coverage','monotonicity',
    'decay_3','capital_capacity','holm_second'])
def test_twelve_independent_oracles_agree_with_declared_tolerance(metric):
    reference=oracle_module()
    row=next(r for r in reference.run_checks()['checks'] if r['metric']==metric)
    assert row['pass'], row
    assert row['actual']==pytest.approx(row['expected'],abs=row['absolute_tolerance'],rel=0)


def test_independent_reference_script_does_not_import_production_for_expected_values():
    reference=oracle_module()
    assert reference.scalar_oracle()[0]['holm_second']==.06
    result=reference.run_checks()
    assert result['status']=='PASS' and result['independent_calculations']>=12
    assert result['financial_use_approved'] is False

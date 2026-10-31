"""Hand-computed multi-asset calibration, not market efficacy claims."""
import importlib.util
from dataclasses import replace

import pandas as pd
import pytest

from finahinking.p8_2.contracts import DatasetSnapshot, MarketObservation


def compute(snapshot, **kwargs):
    assert importlib.util.find_spec('finahinking.research.quant_evidence'), 'missing multi-asset evidence'
    from finahinking.research.quant_evidence import compute_quant_evidence
    return compute_quant_evidence(snapshot, '2026-10-01', **kwargs)


def sample(a=(100, 110, 99, 108.9), b=(100, 95, 99.75, 94.7625)):
    rows = []
    for asset, prices in [('AAA', a), ('ETF', b)]:
        for day, close in enumerate(prices):
            when = pd.Timestamp('2026-01-01', tz='UTC') + pd.Timedelta(days=day)
            rows.append(MarketObservation(asset, when, close, close, close, close, 10000,
                        available_at=when, industry='technology' if asset == 'AAA' else 'fund', suspended=False))
    return DatasetSnapshot('multi-sample', tuple(rows), provenance={
        'units': {'price': 'USD', 'cash': 'USD', 'volume': 'shares'},
        'asset_classes': {'AAA': 'EQUITY', 'ETF': 'ETF'}})


def test_variance_euler_negative_contribution_and_stress_match_hand_calculation():
    out = compute(sample())
    assert out['status'] == 'READY'
    assert out['coverage']['common_intervals'] == 3
    assert out['weights'] == {'AAA': .5, 'ETF': .5}
    # Portfolio returns [.025,-.025,.025]; sample variance .0008333333333.
    assert out['variance']['total'] == pytest.approx(1 / 1200)
    assert out['variance']['contributions'] == pytest.approx({'AAA': 1 / 600, 'ETF': -1 / 1200})
    assert out['variance']['residual'] == pytest.approx(0, abs=1e-15)
    assert out['tail_risk']['cvar'] == pytest.approx(.025)
    assert out['stress']['scenarios']['market_20']['loss'] == pytest.approx(.2)
    assert out['stress']['scenarios']['industry_technology']['loss'] == pytest.approx(.1)
    assert out['sensitivity']['stable_tested_shocks'] == [-.1]
    assert out['uncertainty']['interval'] is None
    assert out['uncertainty']['status'] == 'INSUFFICIENT_SAMPLE'
    assert out['source_claims_status'] == 'NOT_VALIDATED'


@pytest.mark.parametrize('kind,reason', [('units', 'UNITS_REQUIRED'), ('currency', 'UNITS_REQUIRED'),
    ('classes', 'ASSET_CLASS_REQUIRED'), ('pit', 'PIT_UNAVAILABLE'), ('delayed', 'HISTORICAL_AVAILABILITY')])
def test_missing_or_ambiguous_inputs_block_evidence_not_zero(kind, reason):
    value = sample()
    if kind == 'units':
        value = replace(value, provenance={})
    elif kind == 'currency':
        value = replace(value, provenance={**value.provenance, 'units': {'price': 'USD', 'cash': 'CNY', 'volume': 'shares'}})
    elif kind == 'classes':
        value = replace(value, provenance={**value.provenance, 'asset_classes': {}})
    elif kind == 'pit':
        value = replace(value, observations=(replace(value.observations[0], available_at=None), *value.observations[1:]))
    else:
        value = replace(value, observations=(replace(value.observations[0], available_at='2026-02-01T00:00:00Z'), *value.observations[1:]))
    out = compute(value)
    assert out['status'] == 'BLOCKED' and reason in out['blocking_reasons']
    assert out['variance'] is None and out['uncertainty']['interval'] is None


def test_missing_calendar_observation_does_not_become_equal_daily_return():
    value = sample()
    value = replace(value, observations=tuple(row for row in value.observations if not (row.instrument == 'ETF' and row.timestamp.day == 2)))
    out = compute(value)
    assert out['coverage']['common_intervals'] == 1
    assert out['status'] == 'BLOCKED' and 'COMMON_SAMPLE_REQUIRED' in out['blocking_reasons']


def test_bootstrap_is_deterministic_and_future_prefix_cannot_change_evidence():
    prices = [100 * (1.01 ** day) for day in range(35)]
    value = sample(prices, prices)
    out = compute(value)
    assert out['uncertainty']['status'] == 'ESTIMATED'
    assert out['uncertainty']['interval'] == pytest.approx([.01, .01])
    assert out['uncertainty']['replicates'] == 200
    assert compute(value) == out
    changed = replace(value, observations=tuple(replace(row, close=row.close*2, high=row.high*2) if row.timestamp.day >= 30 else row for row in value.observations))
    cutoff = '2026-01-30T00:00:00Z'
    assert compute(value, publication_before=cutoff)['variance'] == compute(changed, publication_before=cutoff)['variance']
    assert compute(value, publication_before=cutoff)['uncertainty'] == compute(changed, publication_before=cutoff)['uncertainty']


def test_constant_series_keeps_zero_variance_but_no_percentage_contribution():
    out = compute(sample([100]*30, [100]*30))
    assert out['variance']['total'] == 0
    assert out['variance']['shares'] is None
    assert out['uncertainty']['interval'] == [0, 0]


def test_oversized_asset_universe_is_explicitly_bounded():
    base = sample()
    rows = tuple(replace(row, instrument=f'A{index:02}') for index in range(21) for row in base.observations[:4])
    out = compute(replace(base, observations=rows))
    assert out['status'] == 'BLOCKED' and 'RESOURCE_LIMIT' in out['blocking_reasons']


def test_exposure_requires_data_available_at_its_common_endpoint():
    value = sample()
    value = replace(value, observations=tuple(replace(row, available_at='2026-01-05T00:00:00Z') if row.timestamp.day == 4 else row for row in value.observations))
    out = compute(value)
    assert out['status'] == 'BLOCKED' and 'EXPOSURE_AVAILABILITY' in out['blocking_reasons']


def test_return_overflow_is_blocked_instead_of_nonfinite_evidence():
    out = compute(sample([1e-200, 1e200, 1e-200], [100, 101, 102]))
    assert out['status'] == 'BLOCKED' and 'NONFINITE_RETURN' in out['blocking_reasons']

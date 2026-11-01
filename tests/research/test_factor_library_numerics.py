"""Frozen independent small-panel arithmetic, causality and missing-field gates.

The JSON expectations were authored with Python scalar arithmetic before evaluating
any production expression. This file only executes the production side.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from finahinking.data.panel_contracts import PanelContract, PanelFieldAttachment
from finahinking.data.research_panel import ResearchPanel, build_research_panel
from finahinking.p8_2.contracts import DatasetSnapshot, MarketObservation
from finahinking.research.factor_library import FactorLibrary

ROOT = Path(__file__).parents[2]
GOLDENS = json.loads((ROOT / 'tests/fixtures/quant_factors/numerical-goldens.json').read_text())
ASSETS = ROOT / 'src/finahinking/_package_data/research_assets/factors'


class FrozenCatalog:
    """Exercise content even before the final package manifest is regenerated."""

    def knowledge_content(self):
        return json.loads((ASSETS.parent / 'knowledge/catalog.json').read_text())

    def read_text(self, resource, **kwargs):
        return (ASSETS / {'factor-library-sources': 'library.json',
                          'factor-library-formulas': 'formulas.json'}[resource]).read_text()


@pytest.fixture(scope='module')
def library():
    return FactorLibrary(FrozenCatalog())


def panel_fixture(entry, *, future_value=None, delayed_field=None):
    """Real snapshot+availability projection, limited to this formula's fields."""
    times = GOLDENS['times']
    native = {'open', 'high', 'low', 'close', 'volume', 'amount'}
    observations = []
    for asset, values in GOLDENS['input'].items():
        for index, time in enumerate(times):
            row = {name: values[name][index] for name in native}
            # Future price mutation is self-consistent OHLC, not an invalid fixture.
            if future_value is not None and index == len(times) - 1:
                for name in native:
                    row[name] *= future_value
            observations.append(MarketObservation(asset, time, **row, available_at=time,
                                                  suspended=False, source='independent-synthetic-golden'))
    snapshot = DatasetSnapshot('phase24-independent-small-panel', tuple(observations),
                               provenance={'adjustment': 'RAW'})
    rules_units = {'is_session': 'boolean', 'in_universe': 'boolean', 'trade_allowed': 'boolean',
                   'lot_size': 'shares', 'settlement_days': 'sessions', 'split_factor': 'ratio'}
    units = {field: unit for field, unit in entry['field_units'].items() if field not in native}
    records = []
    for asset, values in GOLDENS['input'].items():
        for index, time in enumerate(times):
            record = {'instrument': asset, 'timestamp': time, 'available_at': time,
                      'is_session': True, 'in_universe': True, 'trade_allowed': True,
                      'lot_size': 1, 'settlement_days': 0, 'split_factor': 1}
            for field in units:
                value = values[field][index]
                if future_value is not None and index == len(times) - 1:
                    value = value * future_value if isinstance(value, (int, float)) else 'FUTURE-CLASS'
                if field != delayed_field:
                    record[field] = value
            records.append(record)
            if delayed_field is not None:
                # A revision dated in the past but available only after the cutoff.
                records.append({'instrument': asset, 'timestamp': time,
                                'available_at': '2027-01-01T00:00:00Z',
                                delayed_field: values[delayed_field][index]})
    attachment = PanelFieldAttachment(snapshot.fingerprint, {**units, **rules_units}, records,
                                      'Independently authored synthetic formula boundaries')
    contract = PanelContract(snapshot_ref=snapshot.fingerprint, instrument_ids=snapshot.instruments,
                             observed_at=times, field_units=entry['field_units'], scope_id='phase24-numerics',
                             currencies={asset: 'CNY' for asset in snapshot.instruments},
                             timezones={asset: 'UTC' for asset in snapshot.instruments},
                             calendar_ref=attachment.fingerprint, universe_ref=attachment.fingerprint,
                             adjustment_ref=attachment.fingerprint, trade_rule_ref=attachment.fingerprint)
    return build_research_panel(snapshot, [attachment], contract)


def last_value(result, asset='AAA'):
    return [row['value'] for row in result['values'] if row['instrument'] == asset][-1]


@pytest.mark.parametrize('case', GOLDENS['cases'], ids=lambda c: c['factor_id'])
def test_all_eighty_formulas_match_independent_goldens(library, case):
    entry = library.get(case['factor_id'])
    panel = panel_fixture(entry)
    result = library.evaluate(case['factor_id'], panel)
    assert last_value(result) == pytest.approx(case['expected'], rel=case['relative_tolerance'],
                                             abs=case['absolute_tolerance'])
    assert result['production_write'] is False
    assert result['evidence_refs'] == case['source_card_ids']
    assert result['factor_spec']['version'] == '1.0.0'


def test_each_family_has_at_least_four_distinct_formulas(library):
    inventory = library.inventory()
    assert inventory['source_card_count'] >= 120
    assert inventory['executable_formula_count'] == 80
    assert inventory['alias_count'] == 0
    assert inventory['family_count'] == 10
    assert all(count >= 4 for count in inventory['families'].values())
    assert {case['factor_id'] for case in GOLDENS['cases']} == {
        entry['factor_id'] for entry in library.entries()}


@pytest.mark.parametrize('case', GOLDENS['cases'], ids=lambda c: c['factor_id'])
def test_every_formula_enforces_pit_units_and_missingness(library, case):
    entry = library.get(case['factor_id'])
    panel = panel_fixture(entry)
    original = library.evaluate(case['factor_id'], panel)
    # The third asset deliberately has a singleton industry; AAA/BBB remain
    # computable even when that separate group reports insufficient breadth.
    assert last_value(original) == pytest.approx(case['expected'])
    for field in entry['required_fields']:
        assert panel.frame[list(entry['required_fields'])].notna().all().all()
        absent = ResearchPanel(panel.frame.drop(columns=field), panel.audit, panel.contract, panel.fingerprint)
        result = library.evaluate(case['factor_id'], absent)
        assert result['status'] == 'NEEDS_DATA', field
        assert all(row['value'] is None for row in result['values']), field
        empty_frame = panel.frame.copy(deep=True)
        empty_frame[field] = None if field == 'industry' else float('nan')
        empty = ResearchPanel(empty_frame, panel.audit, panel.contract, panel.fingerprint)
        result = library.evaluate(case['factor_id'], empty)
        assert all(row['value'] is None for row in result['values']), field
        wrong = copy.deepcopy(panel.audit)
        wrong['units'][field] = 'USD' if entry['field_units'][field] != 'USD' else 'CNY'
        invalid = ResearchPanel(panel.frame, wrong, panel.contract, panel.fingerprint)
        result = library.evaluate(case['factor_id'], invalid)
        assert result['status'] == 'NEEDS_DATA', field
        assert all(row['value'] is None for row in result['values']), field
    assert library.evaluate(case['factor_id'], panel)['values'] == original['values']
    changed = library.evaluate(case['factor_id'], panel_fixture(entry, future_value=10))
    assert original['values'][:-3] == changed['values'][:-3]
    assert original['panel_fingerprint'] != changed['panel_fingerprint']


@pytest.mark.parametrize('factor_id, field', [
    ('lib-value-book-market', 'book_equity'),
    ('lib-event-equity-finance', 'share_issuance'),
    ('lib-etf-macro-term-spread', 'bond_yield_long'),
    ('lib-industry-book-market', 'industry'),
])
def test_delayed_financial_event_macro_and_classification_are_not_backfilled(library, factor_id, field):
    result = library.evaluate(factor_id, panel_fixture(library.get(factor_id), delayed_field=field))
    assert all(row['value'] is None for row in result['values'])
    assert result['status'] == 'NEEDS_DATA'


@pytest.mark.parametrize('factor_id, zero_field', [
    ('lib-value-book-market', 'market_cap'),
    ('lib-quality-roe', 'book_equity'),
    ('lib-etf-macro-beta', 'benchmark_return'),
    ('lib-reversal-williams', None),
])
def test_economic_zero_denominators_produce_null_instead_of_success(library, factor_id, zero_field):
    panel = panel_fixture(library.get(factor_id))
    frame = panel.frame
    if zero_field:
        frame[zero_field] = 0.0
    else:
        frame['high'] = frame['low'] = frame['close'] = 10.0
    zero = ResearchPanel(frame, panel.audit, panel.contract, panel.fingerprint)
    result = library.evaluate(factor_id, zero)
    assert result['status'] == 'NEEDS_DATA'
    assert last_value(result) is None
    assert 'ZERO_DENOMINATOR' in result['missing_reason']


def test_source_only_cards_are_not_runnable_or_claimed_as_verified(library):
    sources = {entry['card_id']: entry for entry in library.cards()}
    assert any(card['formula_status'] == 'UNRESOLVED' for card in sources.values())
    for card in sources.values():
        assert card['line_start'] <= card['line_end']
        assert card['sha256'] and card['commit'] and card['license_path']
        if card['factor_id'] is None:
            assert card['adaptation_status'] == 'REFERENCE_ONLY'
            assert card['evidence_status'] == 'SOURCE_REPORTED'
            with pytest.raises(ValueError):
                library.get(card['card_id'])
    for entry in library.entries():
        assert entry['economic_definition'] and entry['deduplication_reason']
        assert entry['pit_policy'] and entry['missing_policy'] and entry['knowledge_refs']
        assert entry['numeric_example']['expected'] == next(
            case['expected'] for case in GOLDENS['cases'] if case['factor_id'] == entry['factor_id'])


@pytest.mark.parametrize('case', GOLDENS['cases'], ids=lambda c: c['factor_id'])
def test_packaged_numeric_examples_are_self_contained_and_match_frozen_inputs(case):
    """The installed package example contains its own bounded numeric input."""
    import importlib.resources

    import pandas as pd

    from finahinking.research.contracts import stable_digest

    packaged = importlib.resources.files('finahinking').joinpath(
        '_package_data/research_assets/factors/formulas.json').read_bytes()
    assert len(packaged) < 1024 * 1024
    entries = json.loads(packaged)['formulas']
    entry = next(row for row in entries if row['factor_id'] == case['factor_id'])
    example = entry['numeric_example']
    inputs = example['inputs']
    assert inputs['field_units'] == entry['field_units']
    assert inputs['times'] == GOLDENS['times'][-entry['window']:]
    assert 1 <= len(inputs['times']) <= 6
    assert set(inputs['instruments']) == ({'AAA', 'BBB', 'CCC'} if entry['scope'] in {'cs', 'mixed'} else {'AAA'})
    records = []
    for asset, fields in inputs['instruments'].items():
        assert set(fields) == set(entry['required_fields'])
        for field, values in fields.items():
            assert values == GOLDENS['input'][asset][field][-entry['window']:]
        for index, time in enumerate(inputs['times']):
            records.append({'observed_at': pd.Timestamp(time), 'instrument': asset,
                            'is_session': True, 'in_universe': True,
                            **{field: values[index] for field, values in fields.items()}})
    # This evaluation builds its frame exclusively from the installed example;
    # neither panel_fixture nor the test-only shared input is used as input.
    frame = pd.DataFrame(records).set_index(['observed_at', 'instrument']).sort_index()
    contract = PanelContract(snapshot_ref=stable_digest(inputs), instrument_ids=list(inputs['instruments']),
                             observed_at=inputs['times'], field_units=inputs['field_units'],
                             scope_id='packaged-independent-example',
                             currencies={a: 'CNY' for a in inputs['instruments']},
                             timezones={a: 'UTC' for a in inputs['instruments']})
    panel = ResearchPanel(frame, {'units': inputs['field_units'], 'status': 'READY'}, contract,
                          stable_digest(inputs))
    result = FactorLibrary().evaluate(case['factor_id'], panel)
    assert last_value(result) == pytest.approx(example['expected'], abs=1e-10, rel=1e-10)
    assert example['expected'] == case['expected']
    assert example['calculation_reason'] == case['calculation_reason']

"""Independent temporal expectations for the Phase23 panel contract."""
import importlib.util
from dataclasses import replace

import pandas as pd
import pytest

from finahinking.p8_2.contracts import DatasetSnapshot, MarketObservation


def api():
    assert importlib.util.find_spec('finahinking.data.panel_contracts'), 'Phase23 typed panel contract is missing'
    from finahinking.data.panel_contracts import FIELD_PACKS, PanelContract, PanelFieldAttachment
    from finahinking.data.research_panel import build_research_panel
    return FIELD_PACKS, PanelContract, PanelFieldAttachment, build_research_panel


def fixture(*, missing=(), currencies=None):
    _, Contract, Attachment, build = api()
    times = [f'2026-01-0{d}T16:00:00Z' for d in range(2, 6)]
    observations = tuple(MarketObservation(a, t, c, c, c, c, 100, available_at=t, suspended=False)
                         for a, values in [('AAA', [1, 3, 2, 4]), ('BBB', [10, 20, 30, 40]), ('CCC', [4, 4, 2, 1])]
                         for t, c in zip(times, values))
    snap = DatasetSnapshot('panel-boundary', observations, provenance={'adjustment': 'RAW'})
    records = []
    for a in snap.instruments:
        for t in times:
            records.append({'instrument': a, 'timestamp': t, 'available_at': t, 'is_session': True})
        records.append({'instrument': a, 'timestamp': times[0], 'available_at': times[0], 'in_universe': True,
                            'trade_allowed': True, 'lot_size': 1, 'settlement_days': 0, 'split_factor': 1})
    units = {'is_session': 'boolean', 'in_universe': 'boolean', 'trade_allowed': 'boolean',
             'lot_size': 'shares', 'settlement_days': 'sessions', 'split_factor': 'ratio'}
    attachment = Attachment(snap.fingerprint, units, records, 'Synthetic boundary fixture')
    kwargs = {'snapshot_ref': snap.fingerprint, 'instrument_ids': snap.instruments, 'observed_at': times,
                  'field_units': {'close': 'CNY', 'volume': 'shares', 'net_profit': 'CNY'}, 'scope_id': 'fixture',
                  'currencies': currencies or {a:'CNY' for a in snap.instruments},
                  'timezones': {a:'UTC' for a in snap.instruments}, 'calendar_ref': attachment.fingerprint,
                  'universe_ref': attachment.fingerprint, 'adjustment_ref': attachment.fingerprint,
                  'trade_rule_ref': attachment.fingerprint}
    for key in missing:
        kwargs[key] = None
    return snap, attachment, Contract(**kwargs), build


def financial(snapshot, records):
    return api()[2](snapshot.fingerprint, {'net_profit':'CNY'}, records, 'Synthetic announcements')


def test_late_revision_does_not_rewrite_history():
    snap, rules, contract, build = fixture()
    rows = [{'instrument': 'AAA', 'timestamp': '2025-12-31T00:00:00Z', 'available_at': '2026-01-03T16:00:00Z', 'net_profit': 10},
            {'instrument': 'AAA', 'timestamp': '2025-12-31T00:00:00Z', 'available_at': '2026-01-05T16:00:00Z', 'net_profit': 99}]
    panel = build(snap, [rules, financial(snap, rows)], contract)
    values = panel.frame.xs('AAA', level='instrument').net_profit
    assert pd.isna(values.iloc[0])
    assert values.iloc[1:].tolist() == [10, 10, 99]
    rows[-1]['net_profit'] = -123
    future = build(snap, [rules, financial(snap, rows)], contract)
    pd.testing.assert_frame_equal(panel.frame.iloc[:-3], future.frame.iloc[:-3])
    assert panel.audit['selection_count'] > 0


def test_future_split_and_delisting_preserve_asof_universe():
    snap, rules, contract, build = fixture()
    records = list(rules.records) + [{'instrument': 'AAA', 'timestamp': '2026-01-05T16:00:00Z',
        'available_at': '2026-01-05T16:00:00Z', 'in_universe': False, 'split_factor': 2}]
    changed = api()[2](snap.fingerprint, rules.units, records, rules.source_declaration)
    c = contract.to_dict(); c.pop('fingerprint'); c.pop('schema_version')
    for key in ['calendar_ref','universe_ref','adjustment_ref','trade_rule_ref']:
        c[key] = changed.fingerprint
    new = build(snap, [changed], api()[1](**c))
    old = build(snap, [rules], contract)
    pd.testing.assert_frame_equal(old.frame.iloc[:-3], new.frame.iloc[:-3])
    assert new.frame.loc[(pd.Timestamp('2026-01-05T16:00Z'),'AAA'),'in_universe'] == False
    assert new.frame.loc[(pd.Timestamp('2026-01-05T16:00Z'),'AAA'),'close'] == 4


@pytest.mark.parametrize('missing', ['calendar_ref','universe_ref','adjustment_ref','trade_rule_ref'])
def test_unknown_currency_calendar_or_trade_rules_block_execution(missing):
    snap, rules, contract, build = fixture(missing=(missing,))
    panel = build(snap, [rules], contract)
    assert panel.status == 'NEEDS_DATA'
    assert not panel.frame.execution_eligible.any()
    assert any(missing in reason for reason in panel.audit['reasons'])


def test_cross_instrument_and_timezone_boundaries():
    snap, rules, contract, build = fixture()
    panel = build(snap, [rules], contract)
    assert panel.frame.xs('AAA', level='instrument').close.tolist() == [1, 3, 2, 4]
    assert panel.frame.xs('BBB', level='instrument').close.tolist() == [10, 20, 30, 40]
    rows = list(rules.records)
    rows[0]['timestamp'] = '2026-01-02T08:00:00-08:00'
    rows[0]['available_at'] = '2026-01-02T08:00:00-08:00'
    assert api()[2](snap.fingerprint, rules.units, rows, rules.source_declaration).fingerprint == rules.fingerprint
    rows[0]['timestamp'] = '2026-01-02'
    with pytest.raises(ValueError, match='时区'):
        api()[2](snap.fingerprint, rules.units, rows, rules.source_declaration)


def test_missing_availability_suspension_and_currency_are_not_permission():
    snap, rules, contract, build = fixture(currencies={'AAA':'USD','BBB':'CNY','CCC':'CNY'})
    panel = build(snap, [rules], contract)
    assert not panel.frame.xs('AAA', level='instrument').execution_eligible.any()
    changed = replace(snap, observations=tuple(replace(r, available_at=None, suspended=None) for r in snap.observations))
    c = contract.to_dict(); c.pop('fingerprint'); c.pop('schema_version'); c['snapshot_ref']=changed.fingerprint
    panel = build(changed, [], api()[1](**c))
    assert panel.frame.close.isna().all() and panel.status == 'NEEDS_DATA'
    assert not panel.frame.execution_eligible.any()


def test_nine_packs_and_immutable_roundtrip_and_field_store(tmp_path):
    packs, Contract, _, build = api()
    assert len(packs) == 9
    snap, rules, contract, _ = fixture()
    from finahinking.data.research_fields import ResearchFieldStore
    from finahinking.data.snapshot_store import SnapshotStore
    store = SnapshotStore(tmp_path)
    store.save(snap)
    fields = ResearchFieldStore(store)
    assert fields.load(fields.save(rules)).to_dict() == rules.to_dict()
    assert Contract.from_dict(contract.to_dict()).fingerprint == contract.fingerprint
    loaded = store.build_panel(contract.to_dict(), [rules.fingerprint])
    assert loaded.fingerprint == build(snap, [rules], contract).fingerprint
    frame = loaded.frame; frame['close'] = 0
    assert loaded.frame.close.iloc[0] == 1


def boundary_cases():
    import json
    from pathlib import Path
    return json.loads((Path(__file__).parents[1] / 'fixtures/quant_panel/boundaries.json').read_text())['cases']


@pytest.mark.parametrize('case', boundary_cases(), ids=lambda c: c['id'])
def test_independent_boundary_fixture(case):
    snap, rules, contract, build = fixture()
    values = contract.to_dict(); values.pop('fingerprint'); values.pop('schema_version')
    attachments = [rules]
    mode = case['mode']
    if mode == 'financial':
        attachments.append(financial(snap, case['records']))
    elif mode in {'rule', 'calendar'}:
        records = list(rules.records)
        if mode == 'rule':
            records.append(case['record'])
        else:
            next(r for r in records if r['instrument']=='AAA' and r['timestamp']==contract.observed_at[1])['is_session'] = False
        replacement = api()[2](snap.fingerprint, rules.units, records, rules.source_declaration)
        attachments[0] = replacement
        for key in ['calendar_ref','universe_ref','adjustment_ref','trade_rule_ref']:
            values[key] = replacement.fingerprint
    elif mode == 'suspended':
        snap = replace(snap, observations=tuple(replace(o, suspended=True) for o in snap.observations))
    elif mode == 'adjustment':
        snap = replace(snap, provenance={'adjustment':'UNKNOWN'})
    elif mode == 'unknown_rule':
        values['trade_rule_ref'] = None
    elif mode == 'currency':
        values['currencies']['AAA'] = 'USD'
    elif mode == 'timezone':
        values['timezones']['AAA'] = 'America/Los_Angeles'
        values['observed_at'] = [t.replace('16:00:00+00:00','08:00:00-08:00') for t in values['observed_at']]
    if snap.fingerprint != values['snapshot_ref']:
        values['snapshot_ref'] = snap.fingerprint
        replacement = api()[2](snap.fingerprint, rules.units, rules.records, rules.source_declaration)
        attachments = [replacement]
        for key in ['calendar_ref','universe_ref','adjustment_ref','trade_rule_ref']:
            values[key] = replacement.fingerprint
    panel = build(snap, attachments, api()[1](**values))
    actual = panel.frame.xs(case['asset'], level='instrument')[case['field']]
    assert [None if pd.isna(v) else v for v in actual] == case['expected']


def test_new_api_field_mapping_and_strict_units():
    from finahinking.data.field_mapping import normalize_records
    snap, _, _, _ = fixture()
    record = {'id':'AAA','t':'2026-01-02T16:00Z','a':'2026-01-02T16:00Z','nav':2.5}
    batch = normalize_records([record], {'instrument':'id','timestamp':'t','available_at':'a','nav':'nav'}, 'panel_fields')
    att = batch.to_panel_attachment(snap.fingerprint, units={'nav':'CNY_per_share'})
    assert att.records[0]['nav'] == 2.5
    with pytest.raises(ValueError, match='单位'):
        batch.to_panel_attachment(snap.fingerprint, units={'nav':'CNY'})
    record['t'] = '2026-01-02'
    with pytest.raises(ValueError, match='时区'):
        normalize_records([record], {'instrument':'id','timestamp':'t','available_at':'a','nav':'nav'}, 'panel_fields')


def test_legacy_expiry_does_not_revive_prior_classification():
    from finahinking.data.research_fields import ResearchFieldAttachment
    snap, rules, contract, build = fixture()
    rows = [{'instrument':'AAA','timestamp':contract.observed_at[0], 'available_at':contract.observed_at[0], 'industry':'alpha'},
            {'instrument':'AAA','timestamp':contract.observed_at[1], 'available_at':contract.observed_at[1],
             'effective_until':contract.observed_at[2], 'industry':'beta'}]
    attachment = ResearchFieldAttachment(snap.fingerprint, 'CN', {'industry':'historical_classification'}, rows)
    panel = build(snap, [rules, attachment], contract)
    actual = panel.frame.xs('AAA', level='instrument').industry
    assert actual.iloc[:2].tolist() == ['alpha','beta']
    assert actual.iloc[2:].isna().all()


def test_snapshot_units_and_ambiguous_records_are_rejected():
    snap, rules, contract, build = fixture()
    with pytest.raises(ValueError, match='冲突'):
        build(snap, [rules, api()[2](snap.fingerprint, rules.units, rules.records, 'Another source')], contract)
    declared = replace(snap, provenance={'adjustment':'RAW', 'units':{'price_currency':'USD','volume_unit':'shares'}})
    values = contract.to_dict(); values.pop('fingerprint'); values.pop('schema_version'); values['snapshot_ref']=declared.fingerprint
    with pytest.raises(ValueError, match='单位'):
        build(declared, [], api()[1](**values))


def test_all_nine_field_packs_preserve_declared_units_and_source():
    snap, rules, contract, build = fixture()
    fields = {'cash_dividend':('CNY_per_share',.1), 'net_profit':('CNY', 100), 'industry':('historical_classification','tech'),
              'institutional_net_buy':('CNY',10), 'news_count':('count',2), 'nav':('CNY_per_share',1.2),
              'holdings_scope':('declared_basket','top10'), 'holdings_weight':('ratio',.5), 'policy_rate':('ratio',.03)}
    rows = [{'instrument':'AAA','timestamp':contract.observed_at[0], 'available_at':contract.observed_at[0], **{k:v[1] for k,v in fields.items()}}]
    att = api()[2](snap.fingerprint, {k:v[0] for k,v in fields.items()}, rows, 'Synthetic nine-pack input')
    panel = build(snap, [rules, att], contract)
    for field, (unit, value) in fields.items():
        assert panel.audit['units'][field] == unit
        assert panel.frame.loc[(pd.Timestamp(contract.observed_at[0]),'AAA'), field] == value
    assert len({name for name in api()[0]}) == 9


@pytest.mark.parametrize('field,unit,value', [
    ('is_session','boolean',True), ('trade_allowed','boolean',False),
    ('event_type','event_category','earnings'), ('holdings_scope','declared_basket','top10'),
    ('policy_regime','policy_category','expansion'),
])
def test_review_api_mapping_preserves_boolean_and_text_packs(field, unit, value):
    from finahinking.data.field_mapping import normalize_records
    snap, _, _, _ = fixture()
    record = {'instrument':'AAA', 'timestamp':'2026-01-02T16:00Z', 'available_at':'2026-01-02T16:00Z', field:value}
    batch = normalize_records([record], {k:k for k in record}, 'panel_fields')
    assert batch.to_panel_attachment(snap.fingerprint, units={field:unit}).records[0][field] == value


def test_review_large_integer_is_controlled_validation_error():
    snap, _, _, _ = fixture()
    with pytest.raises(ValueError, match='数值'):
        financial(snap, [{'instrument':'AAA', 'timestamp':'2026-01-02T16:00Z',
                          'available_at':'2026-01-02T16:00Z','net_profit':10**1000}])


def test_api_explicit_unknown_availability_remains_unknown():
    from finahinking.data.field_mapping import normalize_records
    snap, rules, contract, build = fixture()
    row = {'instrument':'AAA', 'timestamp':'2026-01-02T16:00Z', 'available_at':None, 'net_profit':10}
    batch = normalize_records([row], {k:k for k in row}, 'panel_fields')
    assert batch.pit_available == 'UNKNOWN'
    fields = batch.to_panel_attachment(snap.fingerprint, units={'net_profit':'CNY'})
    assert fields.records[0]['available_at'] is None
    assert build(snap, [rules, fields], contract).frame.net_profit.isna().all()

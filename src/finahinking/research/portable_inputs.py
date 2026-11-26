"""Project published input rows into an inert, destination-independent replay dataset."""
from __future__ import annotations

import re
from collections import defaultdict

import pandas as pd

from finahinking.data.panel_contracts import (
    CURRENCIES,
    NATIVE_PRICE_FIELDS,
    PANEL_RULE_FIELDS,
    PanelContract,
    PanelFieldAttachment,
    panel_time,
)
from finahinking.data.research_fields import DATE_RESEARCH_FIELDS, ResearchFieldAttachment
from finahinking.data.research_panel import (
    build_research_panel,
    field_clocks,
    native_observation_index,
    native_quote_at,
)
from finahinking.p8_2.contracts import DatasetSnapshot, MarketObservation

from .reports import _sanitize_public

PRICE_FIELDS = {'instrument', 'timestamp', 'available_at', 'industry', *NATIVE_PRICE_FIELDS}
REQUIRED_QUOTE = {'instrument', 'timestamp', 'available_at', 'open', 'high', 'low', 'close', 'volume'}
UNIT_FIELDS = {'price_currency', 'volume_unit', 'price', 'cash', *NATIVE_PRICE_FIELDS}
ROW_KEYS = {'instrument', 'timestamp', 'available_at', 'market', 'exchange', *DATE_RESEARCH_FIELDS}
MAX_INPUT_ROWS = 50_000


class PortableUnavailable(ValueError):
    """A known missing dependency that can be disclosed in a portable receipt."""


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('原始数据引用需要有效指纹')
    return value


def _time(value):
    if isinstance(value, str) and len(value) == 10:
        return pd.Timestamp(value, tz='UTC') + pd.Timedelta(days=1, nanoseconds=-1)
    return panel_time(value)


def _bound_rows(snapshot, instruments, limit, contract):
    if contract is None:
        return [row for row in snapshot.observations if row.instrument in instruments and row.available_at_time(limit)]
    zones = contract.timezones
    index = native_observation_index(snapshot, zones)
    rows = {}
    for stamp in map(panel_time, contract.observed_at):
        for asset in instruments:
            quote = native_quote_at(index, asset, stamp, zones.get(asset), limit)
            if quote is not None:
                rows[asset, quote.timestamp] = quote
    return [rows[key] for key in sorted(rows)]


def _chosen_fields(panel):
    chosen = defaultdict(set)
    for row in panel.audit['selections']:
        key = (row['attachment_ref'], row['instrument'], row['source_observed_at'], row['available_at'])
        chosen[key].add(row['field'])
    return chosen


def freeze_inputs(snapshot, attachments=(), *, instruments, through, contract=None):
    """Carry only observations in the published scope and selected PIT revisions."""
    instruments = sorted(set(instruments))
    deadline = _time(through)
    through = min(deadline, snapshot.as_of)
    if contract is not None and not isinstance(contract, PanelContract):
        contract = PanelContract.from_dict(contract)
    chosen = {}
    if contract is not None:
        if sorted(contract.instrument_ids) != instruments or panel_time(contract.observed_at[-1]) > deadline:
            raise ValueError('冻结面板与所选发表范围不一致')
        through = min(through, panel_time(contract.observed_at[-1]))
        chosen = _chosen_fields(build_research_panel(snapshot, attachments, contract))
    raw = _bound_rows(snapshot, instruments, through, contract)
    if not raw or {row.instrument for row in raw} != set(instruments):
        raise PortableUnavailable('PUBLISHED_OBSERVATIONS_UNAVAILABLE')
    provenance = snapshot.provenance
    values = {
        'version': 'published-inputs.v1', 'original_snapshot': snapshot.fingerprint,
        'through': through.isoformat(), 'instruments': instruments, 'mode': snapshot.mode,
        'units': {key: v for key, v in provenance.get('units', {}).items() if key in UNIT_FIELDS},
        'asset_classes': {key: v for key, v in provenance.get('asset_classes', {}).items() if key in instruments},
        'adjustment': str(provenance.get('adjustment', 'UNKNOWN')).upper(),
        'observations': [{key: v for key, v in row.to_dict().items() if key in PRICE_FIELDS} for row in raw],
        'contract': contract.to_dict() if contract else None, 'attachments': [],
    }
    for att in attachments:
        if att.snapshot_ref != snapshot.fingerprint:
            raise ValueError('字段附件不属于原始快照')
        original = att.to_dict()
        ref, units = original["fingerprint"], set(original["units"])
        rows = []
        for row in original['records']:
            observed, available = field_clocks(row)
            if row['instrument'] not in instruments or available is None or max(observed, available) > through:
                continue
            fields = units & row.keys()
            if contract is not None:
                fields &= chosen.get((ref, row['instrument'], observed.isoformat(), available.isoformat()), set())
            if fields:
                rows.append({key: v for key, v in row.items() if key in fields or key in ROW_KEYS})
        values['attachments'].append({'original_ref': att.fingerprint, 'kind': original['schema_version'],
                                     'units': original['units'], 'records': rows,
                                     **({'market': original['market']} if 'market' in original else {})})
    _sanitize_public(values)
    _check_declarations(values)
    return values


def _check_declarations(data):
    allowed = {'volume_unit': {'shares', 'units', 'UNKNOWN'}, 'volume': {'shares', 'units', 'UNKNOWN'},
               'suspended': {'boolean', 'UNKNOWN'}}
    for name, value in data['units'].items():
        if not isinstance(value, str) or value not in allowed.get(name, CURRENCIES | {'UNKNOWN'}):
            raise ValueError('迁移行情的单位声明无效')
    if (any(not isinstance(value, str) or value not in {'EQUITY', 'ETF', 'STOCK', 'UNKNOWN'}
            for value in data['asset_classes'].values())
            or not isinstance(data['adjustment'], str) or not re.fullmatch(r'[A-Z][A-Z0-9_.-]{0,63}', data['adjustment'])):
        raise ValueError('迁移行情的资产或价格口径无效')


def _checked_panel(snapshot, fields, contract):
    if contract is None:
        return None
    panel = build_research_panel(snapshot, fields, contract)
    if len(_bound_rows(snapshot, contract.instrument_ids, snapshot.as_of, contract)) != len(snapshot.observations):
        raise ValueError('迁移行情含有面板未选择的观测')
    chosen = _chosen_fields(panel)
    for attachment in fields:
        ref, units = attachment.fingerprint, set(attachment.units)
        for row in attachment.records:
            observed, available = field_clocks(row)
            values = units & row.keys()
            if not values or not values <= chosen.get((ref, row['instrument'], observed.isoformat(), available.isoformat()), set()):
                raise ValueError('迁移附件含有面板未选择的字段修订')
    return panel


def rebuild_inputs(data, *, with_panel=False):
    """Recreate typed local objects; original snapshot and attachment IDs remain provenance only."""
    fields = {'version', 'original_snapshot', 'through', 'instruments', 'mode', 'units', 'asset_classes',
              'adjustment', 'observations', 'contract', 'attachments'}
    if not isinstance(data, dict) or set(data) != fields or data['version'] != 'published-inputs.v1':
        raise ValueError('迁移面板结构不正确')
    _sanitize_public(data)
    _sha(data['original_snapshot'])
    ids, observations, attachments = data['instruments'], data['observations'], data['attachments']
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 128 or sorted(set(ids)) != ids
            or not isinstance(observations, list) or not 1 <= len(observations) <= MAX_INPUT_ROWS
            or not isinstance(attachments, list) or len(attachments) > 32
            or not isinstance(data['units'], dict) or set(data['units']) - UNIT_FIELDS
            or not isinstance(data['asset_classes'], dict) or set(data['asset_classes']) - set(ids)):
        raise ValueError('迁移数据数量、单位或资产范围无效')
    _check_declarations(data)
    through = panel_time(data['through'])
    rows = []
    for value in observations:
        if not isinstance(value, dict) or set(value) - PRICE_FIELDS or value.get('instrument') not in ids:
            raise ValueError('迁移行情存在未声明字段或资产')
        if (not REQUIRED_QUOTE <= value.keys() or
                any(type(value[name]) not in (int, float) for name in ('open', 'high', 'low', 'close', 'volume')) or
                (value.get('amount') is not None and type(value['amount']) not in (int, float))):
            raise ValueError('迁移行情需要有限的数值与明确时钟')
        panel_time(value['timestamp'])
        panel_time(value['available_at'])
        row = MarketObservation(**value, source='portable', provider='portable')
        if not row.available_at_time(through):
            raise ValueError('行情超出已发表可用时间')
        rows.append(row)
    if set(ids) != {row.instrument for row in rows}:
        raise ValueError('迁移行情缺少所选资产')
    snapshot = DatasetSnapshot('portable-research', tuple(rows), mode=data['mode'], as_of=through,
                               provenance={key: data[key] for key in ('adjustment', 'units', 'asset_classes')})
    snapshot_id = snapshot.fingerprint
    refs, rebuilt = {}, []
    total_records = 0
    for att in attachments:
        if not isinstance(att, dict):
            raise TypeError('迁移附件结构无效')
        legacy = att.get('kind') == 'research-fields.v1'
        if (att.get('kind') not in {'research-fields.v1', 'panel-fields.v1'} or
                set(att) != {'original_ref', 'units', 'records', 'kind'} | ({'market'} if legacy else set())):
            raise ValueError('迁移附件版本或字段无效')
        original = _sha(att['original_ref'])
        if original in refs or not isinstance(att['records'], list) or len(att['records']) > 10000:
            raise ValueError('附件身份重复或行数超限')
        total_records += len(att['records'])
        if total_records > MAX_INPUT_ROWS:
            raise ValueError('迁移字段记录总数超限')
        for row in att['records']:
            if not isinstance(row, dict) or not {'instrument', 'timestamp', 'available_at'} <= row.keys():
                raise ValueError('迁移字段记录缺少资产或时钟')
            observed, available = field_clocks(row)
            if row['instrument'] not in ids or available is None or max(observed, available) > through:
                raise ValueError('字段修订超出已发表范围')
        kwargs = {'snapshot_ref': snapshot_id, 'units': att['units'], 'records': att['records'],
                  'source_declaration': 'Published attachment ' + original}
        typed = (ResearchFieldAttachment(**kwargs, market=att['market']) if legacy else PanelFieldAttachment(**kwargs))
        refs[original] = typed.fingerprint
        rebuilt.append(typed)
    contract = None
    if data['contract'] is not None:
        original = PanelContract.from_dict(data['contract'])
        if through > panel_time(original.observed_at[-1]):
            raise ValueError('迁移数据截止时间超出冻结面板范围')
        if original.snapshot_ref != data['original_snapshot'] or sorted(original.instrument_ids) != ids:
            raise ValueError('面板合同与迁移原快照或资产不一致')
        options = {k: v for k, v in original.to_dict().items() if k not in {'fingerprint', 'schema_version'}}
        for key in PANEL_RULE_FIELDS:
            options[key] = refs.get(options[key], options[key])
        contract = PanelContract(**{**options, 'snapshot_ref': snapshot_id})
    panel = _checked_panel(snapshot, rebuilt, contract)
    return (snapshot, tuple(rebuilt), panel if with_panel else contract)

"""Causal, audited projection of immutable snapshots and field attachments."""
from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass

import pandas as pd

from finahinking.p8_2.contracts import DatasetSnapshot

from .panel_contracts import FIELD_DEFINITIONS, PanelContract, PanelFieldAttachment, panel_time
from .research_fields import ResearchFieldAttachment, _json

_RULE_REFS = {'calendar_ref': ('is_session',), 'universe_ref': ('in_universe',),
              'adjustment_ref': ('split_factor',), 'trade_rule_ref': ('trade_allowed', 'lot_size', 'settlement_days')}
_NATIVE = {'open', 'high', 'low', 'close', 'volume', 'amount', 'suspended'}


@dataclass(frozen=True)
class ResearchPanel:
    _frame: pd.DataFrame
    _audit: dict
    contract: PanelContract
    fingerprint: str

    @property
    def frame(self):
        return self._frame.copy(deep=True)

    @property
    def audit(self):
        return copy.deepcopy(self._audit)

    @property
    def status(self):
        return self._audit['status']

    def to_dict(self):
        rows = self.frame.reset_index()
        rows['observed_at'] = rows['observed_at'].map(lambda t: t.isoformat())
        rows = rows.astype(object).where(pd.notna(rows), None)
        return {'schema_version': 'research-panel.v1', 'fingerprint': self.fingerprint,
                'contract': self.contract.to_dict(), 'audit': self.audit, 'records': rows.to_dict('records')}


def build_research_panel(snapshot, field_attachments, contract: PanelContract):
    if not isinstance(snapshot, DatasetSnapshot) or not isinstance(contract, PanelContract):
        raise TypeError('需要类型化快照和面板合同')
    if snapshot.fingerprint != contract.snapshot_ref or not set(contract.instrument_ids) <= set(snapshot.instruments):
        raise ValueError('面板与快照身份不一致')
    if not isinstance(field_attachments, (tuple, list)) or len(field_attachments) > 32:
        raise ValueError('字段附件数量超限')
    attachments, streams, seen, units = {}, {}, set(), dict(contract.field_units)
    for att in field_attachments:
        if not isinstance(att, (PanelFieldAttachment, ResearchFieldAttachment)) or att.snapshot_ref != snapshot.fingerprint:
            raise ValueError('字段附件未绑定当前快照')
        if att.fingerprint in attachments:
            raise ValueError('附件重复')
        attachments[att.fingerprint] = att
        for field, unit in att.units.items():
            if field not in FIELD_DEFINITIONS:
                continue  # legacy fields retain their own semantics; never silently execute as panel fields
            if field in units and units[field] != unit:
                raise ValueError('字段单位不一致')
            units[field] = unit
        for row in att.records:
            if row['instrument'] not in snapshot.instruments:
                raise ValueError('附件资产不属于快照')
            for field in set(row) & set(FIELD_DEFINITIONS):
                if field in _NATIVE:
                    raise ValueError('附件不能覆盖原始量价快照')
                available = row.get('available_at')
                # Legacy metadata cannot make a later announcement/revision available early.
                clocks = [panel_time(row[k]) for k in ('available_at', 'announcement_at', 'revision_at') if row.get(k)]
                available = max(clocks) if available is not None else None
                observed = panel_time(row['timestamp'])
                if row.get('effective_from'):
                    observed = max(observed, panel_time(row['effective_from']))
                identity = (row['instrument'], observed, available, field)
                if identity in seen:
                    raise ValueError('跨附件字段修订冲突')
                seen.add(identity)
                streams.setdefault((row['instrument'], field), []).append(
                    (observed, available, row[field], att.fingerprint, panel_time(row['effective_until']) if row.get('effective_until') else None))
    if len(seen) > 100000:
        raise ValueError('面板字段记录总量超限')
    globals_ = []
    for key, fields in _RULE_REFS.items():
        ref = getattr(contract, key)
        if ref not in attachments or not set(fields) <= set(attachments[ref].units):
            globals_.append(f'{key}：缺少可核验的规则附件')
    if contract.price_basis != 'raw' or str(snapshot.provenance.get('adjustment', 'UNKNOWN')).upper() != 'RAW':
        globals_.append('adjustment_ref：快照未声明原始价格，不能推断复权')
    declared_units = dict(snapshot.provenance.get('units', {}))
    if 'price_currency' in declared_units:
        declared_units.update({name: declared_units['price_currency'] for name in ('open','high','low','close','amount')})
    if 'volume_unit' in declared_units:
        declared_units['volume'] = declared_units['volume_unit']
    if any(name in units and unit == 'UNKNOWN' for name, unit in declared_units.items()):
        globals_.append('currency：快照单位未知，请重新声明并获取快照')
    if any(name in units and unit != 'UNKNOWN' and units[name] != unit for name, unit in declared_units.items()):
        raise ValueError('面板单位与快照声明不一致')
    native = {}
    for obs in snapshot.observations:
        native.setdefault(obs.instrument, []).append(obs)
    index = pd.MultiIndex.from_product([pd.to_datetime(contract.observed_at, utc=True), contract.instrument_ids],
                                      names=['observed_at', 'instrument'])
    columns = sorted(set(units) | {'is_session', 'in_universe', 'trade_allowed', 'lot_size', 'settlement_days', 'split_factor', 'suspended'})
    if len(index) * len(columns) > 50000 or len(contract.observed_at) * (len(snapshot.observations) + len(seen)) > 5_000_000:
        raise ValueError('面板重建预算超限，请缩小资产或日期范围')
    rows, selections, reasons, row_reasons = [], [], list(globals_), []
    for time, asset in index:
        cutoff = min(time, snapshot.as_of)
        row = {name: None for name in columns}
        issues = list(globals_)
        zone, currency = contract.timezones.get(asset), contract.currencies.get(asset)
        if not zone:
            issues.append('timezone：缺少资产时区')
        if currency not in {'CNY','USD','HKD','EUR','JPY','GBP'}:
            issues.append('currency：缺少资产币种')
        candidates = [o for o in native.get(asset, ()) if zone and o.timestamp <= cutoff and o.available_at is not None
                      and o.available_at <= cutoff and o.timestamp.tz_convert(zone).date() == time.tz_convert(zone).date()]
        obs = max(candidates, key=lambda o: o.timestamp) if candidates else None
        for name in columns:
            if name in _NATIVE:
                if obs is not None:
                    unit = units.get(name)
                    if unit in {'CNY','USD','HKD','EUR','JPY','GBP'} and unit != currency:
                        issues.append(f'currency：{name} 币种不一致')
                    else:
                        row[name] = getattr(obs, name)
                continue
            candidates = [(t, a, v, ref, expiry) for t, a, v, ref, expiry in streams.get((asset, name), ())
                          if a is not None and t <= cutoff and a <= cutoff
                          and (FIELD_DEFINITIONS[name][3] == 'asof' or t == time)]
            if candidates:
                chosen = max(candidates, key=lambda x: (x[0], x[1]))
                row[name] = chosen[2] if chosen[4] is None or time < chosen[4] else None
                selections.append({'observed_at': time.isoformat(), 'instrument': asset, 'field': name,
                    'source_observed_at': chosen[0].isoformat(), 'available_at': chosen[1].isoformat(), 'attachment_ref': chosen[3]})
        for ref_key, fields in _RULE_REFS.items():
            # Explicit references, not a different attachment supplying coincidentally named fields.
            for name in fields:
                if row[name] is None:
                    issues.append(f'{ref_key}：{name} 在此时点未知')
                chosen_refs = [s['attachment_ref'] for s in selections[-len(columns):]
                               if s['observed_at'] == time.isoformat() and s['instrument'] == asset and s['field'] == name]
                if chosen_refs and chosen_refs[-1] != getattr(contract, ref_key):
                    issues.append(f'{ref_key}：规则来源与合同不一致')
        for name, unit in units.items():
            if unit.split('_')[0] in {'CNY','USD','HKD','EUR','JPY','GBP'} and unit.split('_')[0] != currency:
                row[name] = None
                issues.append(f'currency：{name} 币种不一致')
        for name in contract.field_units:
            if row.get(name) is None:
                issues.append(f'missing_field：{name} 在此时点不可用')
        if row['suspended'] is None:
            issues.append('trade_rule_ref：停牌状态未知')
        row['execution_eligible'] = not issues and row['is_session'] is True and row['in_universe'] is True and row['trade_allowed'] is True and row['suspended'] is False
        rows.append(row)
        reasons.extend(issues)
        if issues:
            row_reasons.append({'observed_at': time.isoformat(), 'instrument': asset, 'reasons': sorted(set(issues))})
    frame = pd.DataFrame(rows, index=index)
    for name in columns:
        if name in FIELD_DEFINITIONS and FIELD_DEFINITIONS[name][1] == 'number':
            frame[name] = pd.to_numeric(frame[name], errors='raise').astype(float)
    audit = {'status': 'NEEDS_DATA' if reasons else 'READY', 'reasons': sorted(set(reasons)),
             'row_reasons': row_reasons, 'units': units, 'coverage': {f: int(frame[f].notna().sum()) for f in columns},
             'selection_count': len(selections), 'selections': selections, 'price_basis': 'raw',
             'attachment_refs': sorted(attachments), 'rule': 'observed_at <= decision and available_at <= decision; no backfill'}
    identity = {'contract': contract.fingerprint, 'attachments': sorted(attachments), 'semantics': 'research-panel.v1'}
    return ResearchPanel(frame, audit, contract, hashlib.sha256(_json(identity).encode()).hexdigest())

"""Versioned, provider-neutral point-in-time field and panel contracts."""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

from .research_fields import _json, _label

CURRENCIES = frozenset({'CNY', 'USD', 'HKD', 'EUR', 'JPY', 'GBP'})
# kind, legal units, alignment. State values carry forward only after availability;
# exact values never carry across sessions. No backward fill or adjustment occurs.
_PACKS = {
    'prices': {'open': ('number', CURRENCIES, 'exact'), 'high': ('number', CURRENCIES, 'exact'),
               'low': ('number', CURRENCIES, 'exact'), 'close': ('number', CURRENCIES, 'exact'),
               'volume': ('number', {'shares'}, 'exact'), 'amount': ('number', CURRENCIES, 'exact'),
               'suspended': ('boolean', {'boolean'}, 'exact'),
               'turnover_rate': ('number', {'ratio_free_float','percent_free_float','ratio_total_shares','percent_total_shares'}, 'exact')},
    'calendar_universe': {'is_session': ('boolean', {'boolean'}, 'exact'),
                          'in_universe': ('boolean', {'boolean'}, 'asof'),
                          'trade_allowed': ('boolean', {'boolean'}, 'asof'),
                          'lot_size': ('number', {'shares'}, 'asof'),
                          'settlement_days': ('number', {'sessions'}, 'asof'),
                          'limit_up_price': ('number', CURRENCIES, 'exact'), 'limit_down_price': ('number', CURRENCIES, 'exact'),
                          'limit_rule_version': ('text', {'rule_version'}, 'asof')},
    'corporate_actions': {'split_factor': ('number', {'ratio'}, 'asof'),
                          'cash_dividend': ('number', {f'{c}_per_share' for c in CURRENCIES}, 'exact')},
    'financials': {'net_profit': ('number', CURRENCIES, 'asof'), 'total_assets': ('number', CURRENCIES, 'asof'),
                   'book_equity': ('number', CURRENCIES, 'asof'), 'profit_yoy': ('number', {'ratio', 'percent'}, 'asof'),
                   'earnings_per_share': ('number', {f'{c}_per_share' for c in CURRENCIES}, 'asof')},
    'industry_style': {'industry': ('text', {'historical_classification'}, 'asof'),
                       'market_cap': ('number', CURRENCIES, 'exact'), 'style_beta': ('number', {'ratio'}, 'asof')},
    'institutional': {'institutional_net_buy': ('number', CURRENCIES, 'exact'),
                      'institutional_ownership': ('number', {'ratio'}, 'asof'),
                      'seat_identity_confidence': ('text', {'declared_confidence'}, 'asof')},
    'events_news': {'news_count': ('number', {'count'}, 'exact'), 'sentiment_score': ('number', {'score'}, 'exact'),
                    'event_type': ('text', {'event_category'}, 'exact')},
    'etf': {'nav': ('number', {f'{c}_per_share' for c in CURRENCIES}, 'asof'),
             'holdings_weight': ('number', {'ratio'}, 'asof'),
             'holdings_scope': ('text', {'declared_basket'}, 'asof'),
             'tracking_error': ('number', {'ratio'}, 'asof')},
    'macro_policy': {'policy_rate': ('number', {'ratio', 'percent'}, 'asof'),
                      'macro_value': ('number', {'index_points'}, 'asof'),
                      'policy_regime': ('text', {'policy_category'}, 'asof')},
}
FIELD_PACKS = MappingProxyType({k: tuple(v) for k, v in _PACKS.items()})
FIELD_DEFINITIONS = MappingProxyType({name: (pack, kind, frozenset(units), alignment)
    for pack, fields in _PACKS.items() for name, (kind, units, alignment) in fields.items()})
MAX_PANEL_ROWS = 10000
MAX_PANEL_BYTES = 1_000_000


def panel_time(value):
    if not isinstance(value, (str, pd.Timestamp)):
        raise TypeError('时间必须为带时区的 ISO 时间')
    try:
        stamp = pd.Timestamp(value)
        if pd.isna(stamp) or stamp.tzinfo is None:
            raise ValueError('missing timezone')
        return stamp.tz_convert('UTC')
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('时间必须为有效的带时区时间') from exc


def reference(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        raise ValueError('需要 SHA-256 内容引用')
    return value


def instrument(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}', value):
        raise ValueError('资产标识无效')
    return value


def units_checked(units):
    if not isinstance(units, Mapping) or not units or len(units) > 64:
        raise ValueError('请提供有限的字段单位合同')
    for name, unit in units.items():
        if name not in FIELD_DEFINITIONS or unit not in FIELD_DEFINITIONS[name][2]:
            raise ValueError('字段单位或类别未定义')
    return dict(units)


class JsonContract:
    def _store(self, payload):
        encoded = _json(payload)
        if len(encoded.encode()) > MAX_PANEL_BYTES:
            raise ValueError('面板合同超过大小限制')
        object.__setattr__(self, '_json', encoded)

    @property
    def fingerprint(self):
        return hashlib.sha256(self._json.encode()).hexdigest()

    def to_dict(self):
        return {**json.loads(self._json), 'fingerprint': self.fingerprint}

    def __getattr__(self, name):
        data = json.loads(self._json)
        if name in data:
            return data[name]
        raise AttributeError(name)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise TypeError('合同必须为对象')
        values = dict(data)
        claimed, version = values.pop('fingerprint', None), values.pop('schema_version', None)
        if version != cls.VERSION:
            raise ValueError('合同版本无效')
        try:
            result = cls(**values)
        except TypeError as exc:
            raise ValueError('合同字段无效') from exc
        if result.fingerprint != claimed:
            raise ValueError('合同完整性校验失败')
        return result


@dataclass(frozen=True, slots=True, init=False, repr=False)
class PanelFieldAttachment(JsonContract):
    _json: str
    VERSION = 'panel-fields.v1'

    def __init__(self, snapshot_ref, units, records, source_declaration):
        units = units_checked(units)
        if not isinstance(records, (list, tuple)) or len(records) > MAX_PANEL_ROWS:
            raise ValueError('面板记录数量超限')
        normalized, seen = [], set()
        for row in records:
            if not isinstance(row, Mapping) or not {'instrument', 'timestamp', 'available_at'} <= set(row):
                raise ValueError('字段记录需要资产、观察时间和可用时间')
            if set(row) - {'instrument', 'timestamp', 'available_at'} - set(units):
                raise ValueError('记录包含未声明字段')
            item = {'instrument': instrument(row['instrument']), 'timestamp': panel_time(row['timestamp']).isoformat(),
                    'available_at': None if row['available_at'] is None else panel_time(row['available_at']).isoformat()}
            for name in set(row) & set(units):
                val, kind = row[name], FIELD_DEFINITIONS[name][1]
                if val is not None:
                    if kind == 'number':
                        try:
                            finite = math.isfinite(val)
                        except (TypeError, ValueError, OverflowError):
                            finite = False
                        if isinstance(val, bool) or not isinstance(val, (int, float)) or not finite:
                            raise ValueError('数值必须有限且单位明确')
                        if name in {'split_factor', 'lot_size'} and val <= 0:
                            raise ValueError('拆分比例和最小交易单位必须为正数')
                        if name in {'settlement_days', 'news_count'} and (val < 0 or val != int(val)):
                            raise ValueError('计数必须为非负整数')
                        if name == 'holdings_weight' and not 0 <= val <= 1:
                            raise ValueError('持仓汇总权重需要在 0 到 1 之间')
                        val = float(val)
                    elif kind == 'boolean' and not isinstance(val, bool):
                        raise ValueError('规则必须使用显式布尔值')
                    elif kind == 'text':
                        val = _label(val)
                key = (item['instrument'], item['timestamp'], item['available_at'], name)
                if key in seen:
                    raise ValueError('字段修订身份重复')
                seen.add(key)
                item[name] = val
            normalized.append(item)
        normalized.sort(key=lambda r: (r['instrument'], r['timestamp'], r['available_at'] or '', _json(r)))
        self._store({'schema_version': self.VERSION, 'snapshot_ref': reference(snapshot_ref), 'units': units,
                     'records': normalized, 'source_declaration': _label(source_declaration, maximum=500)})


@dataclass(frozen=True, slots=True, init=False, repr=False)
class PanelContract(JsonContract):
    _json: str
    VERSION = 'research-panel-contract.v1'

    def __init__(self, snapshot_ref, instrument_ids, observed_at, field_units, scope_id,
                 currencies, timezones, calendar_ref=None, universe_ref=None, adjustment_ref=None,
                 trade_rule_ref=None, price_basis='raw'):
        if not isinstance(instrument_ids, (list, tuple)) or not 1 <= len(instrument_ids) <= 100:
            raise ValueError('面板需要 1 到 100 个资产')
        ids = sorted(instrument(a) for a in instrument_ids)
        if len(set(ids)) != len(ids):
            raise ValueError('资产重复')
        if not isinstance(observed_at, (list, tuple)) or not observed_at or len(observed_at) * len(ids) > MAX_PANEL_ROWS:
            raise ValueError('面板行数超限')
        times = sorted(panel_time(t).isoformat() for t in observed_at)
        if len(set(times)) != len(times):
            raise ValueError('观察时间重复')
        if not isinstance(currencies, Mapping) or not isinstance(timezones, Mapping) or set(currencies) - set(ids) or set(timezones) - set(ids):
            raise ValueError('币种和时区必须绑定资产')
        if any(v not in CURRENCIES | {'UNKNOWN'} for v in currencies.values()):
            raise ValueError('币种无效')
        for zone in timezones.values():
            try:
                ZoneInfo(zone)
            except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
                raise ValueError('时区无效') from exc
        if price_basis not in {'raw', 'unknown'}:
            raise ValueError('此版本只接受原始价格，不接受未来复权序列')
        refs = {'calendar_ref': calendar_ref, 'universe_ref': universe_ref, 'adjustment_ref': adjustment_ref, 'trade_rule_ref': trade_rule_ref}
        self._store({'schema_version': self.VERSION, 'snapshot_ref': reference(snapshot_ref), 'instrument_ids': ids,
            'observed_at': times, 'field_units': units_checked(field_units), 'scope_id': _label(scope_id),
            'currencies': dict(currencies), 'timezones': dict(timezones), 'price_basis': price_basis,
            **{key: None if val is None else reference(val) for key, val in refs.items()}})

"""Immutable, versioned strategy and risk definitions from the packaged asset catalog."""

from __future__ import annotations

import copy
import json
import math
import re
from collections.abc import Mapping

from finahinking.data.panel_contracts import FIELD_DEFINITIONS

from .asset_catalog import ResearchAssetCatalog
from .contracts import stable_digest

TEMPLATE_IDS = (
    'equal-weight', 'single-factor-quantile', 'short-term-reversal', 'trend-breakout',
    'low-volatility-low-turnover', 'value-quality', 'multifactor-neutral', 'etf-risk-budget',
    'etf-relative-strength', 'regime-allocation', 'event-study', 'pairs-mean-reversion',
)
RISK_IDS = (
    'data-pit', 'financial-abnormality', 'tradeability', 'liquidity', 'concentration',
    'industry', 'collinearity', 'volatility', 'tail-loss', 'stop-loss', 'drawdown', 'stress',
)
_ID = re.compile(r'[a-z][a-z0-9_-]{0,63}')
_MODES = {'PAPER_LONG_ONLY', 'THEORETICAL', 'STATISTICS'}
_UNIVERSES = {'all', 'equity', 'etf'}


def _fingerprint(record):
    return stable_digest({key: value for key, value in record.items() if key != 'fingerprint'})


def _parameter_value(name, descriptor, value):
    kind = descriptor['type']
    if kind == 'enum':
        valid = type(value) is str and value in descriptor['choices']
    else:
        valid = (type(value) is int or kind == 'number' and type(value) is float)
        valid = valid and descriptor['minimum'] <= value <= descriptor['maximum']
    if not valid:
        raise ValueError(f'策略参数 {name} 超出声明范围')
    return value


def _validate_parameter(name, descriptor):
    if not _ID.fullmatch(name) or not isinstance(descriptor, dict):
        raise ValueError('策略参数定义无效')
    kind = descriptor.get('type')
    if kind == 'enum':
        choices = descriptor.get('choices')
        if set(descriptor) != {'type', 'choices', 'default'} or not isinstance(choices, list) or not 1 <= len(choices) <= 16 or any(
            not isinstance(item, str) or not _ID.fullmatch(item) for item in choices
        ) or len(set(choices)) != len(choices):
            raise ValueError('策略枚举定义无效')
    elif kind in {'integer', 'number'}:
        if set(descriptor) != {'type', 'minimum', 'maximum', 'default'}:
            raise ValueError('策略数值边界定义无效')
        for bound in ('minimum', 'maximum'):
            value = descriptor[bound]
            if type(value) not in (int, float) or abs(value) > 1_000_000 or not math.isfinite(value):
                raise ValueError('策略数值边界必须有限')
        if descriptor['minimum'] > descriptor['maximum']:
            raise ValueError('策略最小值高于最大值')
    else:
        raise ValueError('策略参数类型无效')
    _parameter_value(name, descriptor, descriptor['default'])


def bind_parameters(template, parameters):
    """Freeze defaults and reject undeclared, ambiguous or out-of-range inputs."""
    if template.get('fingerprint') != _fingerprint(template):
        raise ValueError('策略模板指纹不一致')
    bounds = template['parameter_bounds']
    if not isinstance(parameters, Mapping) or set(parameters) - set(bounds):
        raise ValueError('策略参数必须在模板中声明')
    return {
        name: _parameter_value(name, descriptor, parameters.get(name, descriptor['default']))
        for name, descriptor in bounds.items()
    }


def _checked_records(catalog, resource, schema, key, identifier, identities):
    data = catalog.read_text(resource, max_bytes=128_000)
    payload = json.loads(data)
    if not isinstance(payload, dict) or set(payload) != {'schema_version', key} or payload['schema_version'] != schema:
        raise ValueError('策略资源版本无效')
    records = payload[key]
    if not isinstance(records, list) or len(records) != len(identities):
        raise ValueError('策略资源记录数量不匹配')
    result = {}
    for record in records:
        if not isinstance(record, dict):
            raise TypeError('策略资源记录无效')
        record_id = record.get(identifier)
        if type(record_id) is not str or record_id not in identities or record_id in result or record.get('version') != '1.0.0':
            raise ValueError('策略资源身份无效或重复')
        result[record_id] = record
    return result


def _validate_sources(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 8:
        raise ValueError('需要固定参考来源')
    keys = {'source_id', 'repository', 'commit', 'path', 'sha256', 'license',
            'license_path', 'capture_date', 'use', 'topic'}
    for row in rows:
        if (not isinstance(row, dict) or set(row) != keys
                or any(type(value) is not str or not value.strip() for value in row.values())
                or not row['repository'].startswith('https://github.com/')
                or not re.fullmatch('[a-f0-9]{40}', row['commit'])
                or not re.fullmatch('[a-f0-9]{64}', row['sha256'])
                or row['use'] != 'REFERENCE_ONLY'):
            raise ValueError('参考来源身份不完整')


class StrategyCatalog:
    def __init__(self, catalog=None):
        catalog = ResearchAssetCatalog.packaged() if catalog is None else catalog
        self._rules = _checked_records(catalog, 'strategy-risk-rules', 'strategy-risk-rules.v1', 'rules', 'rule_id', RISK_IDS)
        self._templates = _checked_records(catalog, 'strategy-templates', 'strategy-templates.v1', 'templates', 'template_id', TEMPLATE_IDS)
        for rule in self._rules.values():
            if set(rule) != {'rule_id', 'version', 'title', 'metric', 'unit', 'operator', 'limit', 'action', 'description', 'examples', 'source_refs'}:
                raise ValueError('风险规则字段无效')
            if rule['action'] not in {'GATE', 'ADVISORY'} or not _ID.fullmatch(rule['metric']):
                raise ValueError('风险规则操作无效')
            if rule['operator'] == 'is_true':
                valid = rule['unit'] == 'boolean' and rule['limit'] is True
            elif rule['operator'] == 'at_most':
                valid = type(rule['limit']) in (int, float) and 0 <= rule['limit'] <= 1 and rule['unit'] != 'boolean'
            else:
                valid = False
            if not valid or not isinstance(rule['examples'], dict) or set(rule['examples']) != {'pass', 'triggered', 'needs_data'}:
                raise ValueError('风险规则阈值或示例无效')
            _validate_sources(rule['source_refs'])
            rule['fingerprint'] = _fingerprint(rule)
        for template in self._templates.values():
            if set(template) - {'parameter_fields'} != {'template_id', 'version', 'title', 'universe', 'required_fields', 'selection_rule',
                                 'weight_rule', 'rebalance_rule', 'execution_mode', 'execution_rule', 'benchmark',
                                 'risk_rules', 'failure_scenario', 'parameter_bounds', 'source_refs'}:
                raise ValueError('策略模板字段无效')
            _validate_sources(template['source_refs'])
            universe = template['universe']
            if universe not in _UNIVERSES or template['execution_mode'] not in _MODES:
                raise ValueError('策略模板资产范围或执行模式无效')
            if template['benchmark'] != {'kind': 'equal-weight-buy-hold', 'universe': universe, 'cost_basis': 'frictionless'}:
                raise ValueError('策略基准定义无效')
            fields = template['required_fields']
            if not isinstance(fields, list) or len(set(fields)) != len(fields) or not set(fields) <= set(FIELD_DEFINITIONS):
                raise ValueError('策略模板字段依赖无效')
            if template['risk_rules'] != list(RISK_IDS):
                raise ValueError('策略模板风险规则引用不完整')
            bounds = template['parameter_bounds']
            if not isinstance(bounds, dict) or not 1 <= len(bounds) <= 24:
                raise ValueError('策略参数数量无效')
            for name, descriptor in bounds.items():
                _validate_parameter(name, descriptor)
            conditional = template.get('parameter_fields', {})
            if not isinstance(conditional, dict) or not set(conditional) <= set(bounds):
                raise ValueError('策略条件字段声明无效')
            for name, alternatives in conditional.items():
                if (bounds[name]['type'] != 'enum' or not isinstance(alternatives, dict)
                        or not set(alternatives) <= set(bounds[name]['choices'])
                        or any(not isinstance(fields, list) or not set(fields) <= set(FIELD_DEFINITIONS)
                               for fields in alternatives.values())):
                    raise ValueError('策略参数的条件字段无效')
            template['risk_rule_fingerprints'] = {rule_id: self._rules[rule_id]['fingerprint'] for rule_id in RISK_IDS}
            template['fingerprint'] = _fingerprint(template)

    @property
    def fingerprint(self):
        return stable_digest({'templates': self._templates, 'rules': self._rules})

    def templates(self):
        return tuple(self.template(template_id) for template_id in TEMPLATE_IDS)

    def template(self, template_id):
        if not isinstance(template_id, str) or template_id not in self._templates:
            raise ValueError('策略模板不存在')
        return copy.deepcopy(self._templates[template_id])

    def risk_rules(self):
        return tuple(copy.deepcopy(self._rules[rule_id]) for rule_id in RISK_IDS)

"""Causal, bounded panel operators. This interpreter never evaluates Python code."""
from __future__ import annotations

import ast
import hashlib
import math
from dataclasses import dataclass
from types import MappingProxyType

import numpy as np
import pandas as pd

from finahinking.data.panel_contracts import MAX_PANEL_ROWS, panel_time
from finahinking.data.research_fields import _json
from finahinking.data.research_panel import ResearchPanel

SEMANTICS_VERSION = 'bounded-panel-dsl.v1'
TS_NAMES = ('lag', 'return', 'diff', 'sum', 'mean', 'std', 'min', 'max', 'rank', 'corr', 'cov',
            'slope', 'linear_decay', 'argmin', 'argmax', 'zscore')
CS_NAMES = ('rank', 'zscore', 'winsorize', 'demean', 'neutralize', 'group_rank', 'group_zscore', 'group_demean')
_NAMES = tuple('ts_' + name for name in TS_NAMES) + tuple(name if name.startswith('group_') else 'cs_' + name for name in CS_NAMES)
OPERATOR_CONTRACTS = MappingProxyType({name: MappingProxyType({
    'scope': 'ts' if name.startswith('ts_') else 'group' if name.startswith('group_') else 'cs',
    'ddof': 0, 'window': 'trailing inclusive; positive lag; at most 252 declared sessions' if name.startswith('ts_') else 'same observed_at only',
    'min_periods': 'explicit argument; lag/return/diff require both endpoints',
    'ties': 'average percentile rank; argmin/argmax choose oldest tied position',
    'missing': 'never filled; exclude nulls from estimation; current missing rank/zscore stays null',
    'unit': 'ratio for return/rank/zscore/corr; sessions for argmin/argmax; product for cov; input/session for slope; input otherwise',
}) for name in _NAMES})


@dataclass(frozen=True)
class PanelExpression:
    expression: str
    fields: tuple[str, ...]
    operators: tuple[str, ...]
    fingerprint: str
    cost: int


def _integer(node, maximum=252):
    if not isinstance(node, ast.Constant) or isinstance(node.value, bool) or not isinstance(node.value, int) or not 1 <= node.value <= maximum:
        raise ValueError('窗口和最小样本数必须为范围内的正整数')
    return node.value


def _quantile(node):
    if not isinstance(node, ast.Constant) or isinstance(node.value, bool) or not isinstance(node.value, (int, float)) or not math.isfinite(node.value) or not 0 <= node.value <= 1:
        raise ValueError('分位数必须在 0 到 1 之间')
    return float(node.value)


def parse_panel_expression(expression, fields, *, row_count=1):
    if not isinstance(expression, str) or not expression.strip() or len(expression.encode()) > 4096:
        raise ValueError('面板表达式为空或过长')
    if not isinstance(row_count, int) or isinstance(row_count, bool) or not 1 <= row_count <= MAX_PANEL_ROWS:
        raise ValueError('面板行数超限')
    try:
        tree = ast.parse(expression.strip(), mode='eval')
    except (SyntaxError, RecursionError) as exc:
        raise ValueError('面板表达式语法无效') from exc
    if sum(1 for _ in ast.walk(tree)) > 128:
        raise ValueError('面板表达式节点超限')
    used, ops, cost = set(), set(), 0

    def visit(node, depth=0):
        nonlocal cost
        if depth > 12:
            raise ValueError('面板表达式嵌套超限')
        if isinstance(node, ast.Name):
            if node.id not in fields:
                raise ValueError('面板缺少表达式字段')
            used.add(node.id)
            return node.id
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id not in OPERATOR_CONTRACTS or node.keywords:
            raise ValueError('仅支持显式 ts/cs/group 算子')
        name, args = node.func.id, node.args
        ops.add(name)
        if name in {'ts_lag', 'ts_return', 'ts_diff'}:
            arity, nseries = 2, 1
        elif name in {'ts_corr', 'ts_cov'}:
            arity, nseries = 4, 2
        elif name.startswith('ts_'):
            arity, nseries = 3, 1
        elif name == 'cs_winsorize':
            arity, nseries = 4, 1
        elif name == 'cs_neutralize' or name.startswith('group_'):
            arity, nseries = 3, 2
        else:
            arity, nseries = 2, 1
        if len(args) != arity:
            raise ValueError('面板算子参数数量无效，需要显式 min_periods')
        canonical = [visit(arg, depth + 1) for arg in args[:nseries]]
        if name.startswith('ts_'):
            window = _integer(args[nseries])
            nums = [window]
            if arity - nseries == 2:
                minimum = _integer(args[-1])
                if minimum > window:
                    raise ValueError('min_periods 不得大于窗口')
                nums.append(minimum)
            cost += row_count * window
        else:
            nums = []
            if name == 'cs_winsorize':
                low, high = _quantile(args[1]), _quantile(args[2])
                if low >= high:
                    raise ValueError('缩尾分位数顺序无效')
                nums.extend([low, high])
            nums.append(_integer(args[-1], 100))
            cost += row_count * 10
        if cost > 5_000_000:
            raise ValueError('面板算子计算预算超限')
        return f'{name}({",".join(canonical + [str(n) for n in nums])})'

    canonical = visit(tree.body)
    digest = hashlib.sha256(_json({'expression': canonical, 'semantics_version': SEMANTICS_VERSION}).encode()).hexdigest()
    return PanelExpression(canonical, tuple(sorted(used)), tuple(sorted(ops)), digest, cost)


def _validate_panel(panel):
    if not isinstance(panel, ResearchPanel):
        raise TypeError('需要已验证的 ResearchPanel')
    frame = panel.frame.sort_index()
    if not isinstance(frame.index, pd.MultiIndex) or frame.index.names != ['observed_at', 'instrument'] or not frame.index.is_unique or not 1 <= len(frame) <= MAX_PANEL_ROWS:
        raise ValueError('面板索引无效')
    return frame


def evaluate_panel(expression, panel):
    frame = _validate_panel(panel)
    parsed = parse_panel_expression(expression, set(frame), row_count=len(frame))
    diagnostics = set()
    active = frame['is_session'].eq(True) & frame['in_universe'].eq(True)
    unit_map = panel.audit['units']

    def calc(node):
        if isinstance(node, ast.Name):
            series = frame[node.id].where(active)
            return series, unit_map.get(node.id, 'boolean')
        name = node.func.id
        nseries = 2 if name in {'ts_corr', 'ts_cov', 'cs_neutralize'} or name.startswith('group_') else 1
        sources = [calc(arg) for arg in node.args[:nseries]]
        x, unit = sources[0]
        if not pd.api.types.is_numeric_dtype(x) or pd.api.types.is_bool_dtype(x):
            raise ValueError('计算输入必须为有单位的数值字段')
        x = x.astype(float)
        params = [arg.value for arg in node.args[nseries:]]
        out = pd.Series(np.nan, index=frame.index, dtype=float)
        if name.startswith('ts_'):
            op, window = name[3:], params[0]
            minimum = params[1] if len(params) > 1 else window
            for indices in frame.groupby(level='instrument', sort=True).groups.values():
                index = indices[frame.loc[indices, 'is_session'].eq(True).to_numpy()]
                v = x.loc[index]
                rolling = v.rolling(window, min_periods=minimum)
                if op == 'lag':
                    val = v.shift(window)
                elif op == 'return':
                    val = v / v.shift(window).replace(0, np.nan) - 1
                elif op == 'diff':
                    val = v.diff(window)
                elif op in {'sum', 'mean', 'min', 'max'}:
                    val = getattr(rolling, op)()
                elif op == 'std':
                    val = rolling.std(ddof=0)
                elif op == 'rank':
                    val = rolling.rank(method='average', pct=True)
                elif op == 'zscore':
                    std = rolling.std(ddof=0)
                    if std.eq(0).any():
                        diagnostics.add('DEGENERATE_VARIANCE')
                    val = (v - rolling.mean()) / std.replace(0, np.nan)
                elif op in {'corr', 'cov'}:
                    y = sources[1][0].loc[index]
                    if not pd.api.types.is_numeric_dtype(y) or pd.api.types.is_bool_dtype(y):
                        raise ValueError('协方差和相关需要数值输入')
                    val = rolling.corr(y, ddof=0) if op == 'corr' else rolling.cov(y, ddof=0)
                else:
                    def custom(a):
                        finite = np.isfinite(a)
                        positions = np.arange(len(a))[finite]
                        vals = a[finite]
                        if op == 'linear_decay':
                            weights = positions + 1
                            return float(np.dot(vals, weights) / weights.sum())
                        if op in {'argmin', 'argmax'}:
                            return float(positions[np.argmin(vals) if op == 'argmin' else np.argmax(vals)])
                        if len(vals) < 2:
                            return np.nan
                        centered = positions - positions.mean()
                        return float(np.dot(centered, vals - vals.mean()) / np.dot(centered, centered))
                    val = rolling.apply(custom, raw=True)
                out.loc[index] = val
            if op in {'return', 'rank', 'corr', 'zscore'}:
                unit = 'ratio'
            elif op in {'argmin', 'argmax'}:
                unit = 'sessions'
            elif op == 'slope':
                unit += '/session'
            elif op == 'cov':
                unit += '*' + sources[1][1]
        else:
            groups = [frame.index.get_level_values('observed_at')]
            if name.startswith('group_'):
                groups.append(sources[1][0])
            op = name.removeprefix('cs_').removeprefix('group_')
            minimum = params[-1]
            for indices in x.groupby(groups[0] if len(groups) == 1 else groups, dropna=True, sort=True).groups.values():
                v = x.loc[indices].dropna()
                if name.startswith('group_'):
                    v = v[sources[1][0].loc[v.index].notna()]
                if op == 'neutralize':
                    y = sources[1][0].loc[v.index]
                    if not pd.api.types.is_numeric_dtype(y) or pd.api.types.is_bool_dtype(y):
                        raise ValueError('中性化需要数值暴露')
                    valid = y.notna()
                    v, y = v.loc[valid], y.loc[valid]
                    if len(v) < max(3, minimum):
                        diagnostics.add('INSUFFICIENT_CROSS_SECTION')
                        continue
                    design = np.column_stack([np.ones(len(y)), y.to_numpy(float)])
                    if np.linalg.matrix_rank(design) < 2:
                        diagnostics.add('DEGENERATE_EXPOSURE')
                        continue
                    out.loc[v.index] = v - design @ np.linalg.lstsq(design, v.to_numpy(), rcond=None)[0]
                    continue
                if len(v) < minimum:
                    diagnostics.add('INSUFFICIENT_CROSS_SECTION')
                    continue
                if op == 'rank':
                    val = v.rank(method='average', pct=True)
                elif op == 'demean':
                    val = v - v.mean()
                elif op == 'zscore':
                    std = v.std(ddof=0)
                    if std == 0:
                        diagnostics.add('DEGENERATE_VARIANCE')
                        continue
                    val = (v-v.mean()) / std
                elif op == 'winsorize':
                    val = v.clip(v.quantile(params[0], interpolation='linear'), v.quantile(params[1], interpolation='linear'))
                out.loc[v.index] = val
            if op in {'rank', 'zscore'}:
                unit = 'ratio'
        return out.where(active).replace([np.inf, -np.inf], np.nan), unit

    result, unit = calc(ast.parse(parsed.expression, mode='eval').body)
    if not pd.api.types.is_numeric_dtype(result) or pd.api.types.is_bool_dtype(result):
        raise ValueError('面板表达式必须产生数值序列')
    result = result.astype(float).rename('value')
    result.attrs = {'semantics_version': SEMANTICS_VERSION, 'expression': parsed.expression,
                    'expression_fingerprint': parsed.fingerprint, 'panel_fingerprint': panel.fingerprint,
                    'unit': unit, 'diagnostics': sorted(diagnostics), 'status': 'NEEDS_DATA' if diagnostics or not result.notna().any() else panel.status,
                    'cost': parsed.cost}
    return result


def _training_data(panel, field, start, end):
    frame = _validate_panel(panel)
    if field not in panel.contract.field_units or panel.contract.field_units[field] == 'boolean' or not pd.api.types.is_numeric_dtype(frame[field]) or pd.api.types.is_bool_dtype(frame[field]):
        raise ValueError('拟合字段需要明确数值单位')
    start, end = panel_time(start), panel_time(end)
    times = frame.index.get_level_values(0)
    if start > end or end > times.max() or start < times.min():
        raise ValueError('训练边界超出面板区间')
    data = frame.loc[(times >= start) & (times <= end) & frame.is_session.eq(True) & frame.in_universe.eq(True), field]
    payload = [[t.isoformat(), a, None if pd.isna(v) else float(v)] for (t, a), v in data.items()]
    identity = {'rows': payload, 'unit': panel.contract.field_units[field], 'scope_id': panel.contract.scope_id,
                'instrument_ids': panel.contract.instrument_ids}
    return data.dropna(), hashlib.sha256(_json(identity).encode()).hexdigest()


def fit_panel_standardizer(panel, field, *, fit_start, fit_end):
    data, digest = _training_data(panel, field, fit_start, fit_end)
    std = float(data.std(ddof=0)) if len(data) else 0.0
    ready = len(data) >= 2 and std > 0 and math.isfinite(std)
    result = {'transform_version': 'train-standardizer.v1', 'field': field, 'fit_start': panel_time(fit_start).isoformat(),
              'fit_end': panel_time(fit_end).isoformat(), 'training_digest': digest,
              'mean': float(data.mean()) if ready else None, 'std': std if ready else None,
              'status': 'READY' if ready else 'NEEDS_DATA', 'reason': None if ready else '训练样本不足或常量列'}
    return {**result, 'fingerprint': hashlib.sha256(_json(result).encode()).hexdigest()}


def transform_panel_standardizer(panel, fitted):
    if not isinstance(fitted, dict) or not {'field','fit_start','fit_end'} <= set(fitted):
        raise ValueError('需要完整的已拟合版本')
    expected = fit_panel_standardizer(panel, fitted['field'], fit_start=fitted['fit_start'], fit_end=fitted['fit_end'])
    if fitted != expected:
        raise ValueError('拟合参数或训练数据发生变化，不能复用旧版本')
    frame = _validate_panel(panel)
    result = pd.Series(np.nan, index=frame.index, name='value', dtype=float)
    if fitted['status'] == 'READY':
        mask = (frame.index.get_level_values(0) > panel_time(fitted['fit_end'])) & frame.is_session.eq(True) & frame.in_universe.eq(True)
        result.loc[mask] = (frame.loc[mask, fitted['field']]-fitted['mean']) / fitted['std']
    result.attrs = {'fitted': dict(fitted), 'unit': 'ratio', 'status': fitted['status']}
    return result

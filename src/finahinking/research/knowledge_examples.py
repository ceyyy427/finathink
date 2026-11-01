"""Fixed teaching operations over small inputs; never eval/import upstream code."""
from __future__ import annotations

import math
import statistics
from decimal import Decimal


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1e12:
        raise ValueError('示例需要有限且有界的数值。')
    return value


def _numbers(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 256:
        raise ValueError('示例数组需要1到256项。')
    return [_number(v) for v in value]


def _names(value):
    if not isinstance(value, list) or len(value) > 256 or any(not isinstance(v, str) or not 1 <= len(v) <= 128 for v in value) or len(set(value)) != len(value):
        raise ValueError('示例标识应为不重复的有界文本。')
    return set(value)


def _divide(a, b):
    if b == 0:
        raise ValueError('示例分母不能为零。')
    return a / b


def _ranks(values):
    ordered = sorted(values)
    return [(ordered.index(v) + 1 + len(ordered) - ordered[::-1].index(v)) / 2 for v in values]


def evaluate_example(example):
    if not isinstance(example, dict) or not isinstance(example.get('inputs'), dict):
        raise TypeError('示例合同无效。')
    op, raw = example.get('operation'), example['inputs']
    fields = {
        'visible_count': 'available cutoff', 'latest_time': 'times', 'asof_value': 'available values cutoff',
        'coverage': 'observed expected_count', 'scale': 'value scale', 'paired_count': 'left right',
        'price_return': 'start end', 'compound': 'returns', 'log_return': 'start end',
        'difference': 'left right', 'annualized_growth': 'growth periods_per_year observations',
        'total_return': 'start end distribution', 'mean': 'values', 'sample_variance': 'values',
        'sample_sd': 'values', 'sample_covariance': 'x y', 'correlation': 'x y',
        'known_sigma_interval': 'mean sigma n z', 'ema_step': 'previous price alpha',
        'rsi': 'average_up average_down', 'true_range': 'high low previous_close',
        'weighted_mean': 'values weights', 'ratio': 'numerator denominator',
        'scaled_difference': 'left right scale', 'rank_correlation': 'x y', 'mean_sd_ratio': 'values',
        'mean_difference': 'left right', 'membership_turnover': 'previous current', 'equal_weights': 'count',
        'inverse_volatility': 'volatilities', 'portfolio_variance': 'weights covariance',
        'sharpe_from_moments': 'mean_excess sample_sd periods_per_year', 'max_drawdown': 'nav',
        'downside_deviation': 'returns target', 'nearest_rank_var': 'returns confidence',
        'fractional_es': 'returns confidence', 'concentration': 'weights', 'weighted_sum': 'values weights',
    }
    if op not in fields or set(raw) != set(fields[op].split()):
        raise ValueError('示例运算或输入字段不在允许清单。')
    if op in {'paired_count', 'membership_turnover'}:
        a, b = (_names(raw[k]) for k in fields[op].split())
        return len(a & b) if op == 'paired_count' else _divide(len(b - a), len(b))
    values = {k: ([_numbers(row) for row in v] if k == 'covariance' else _numbers(v)) if isinstance(v, list) else _number(v) for k, v in raw.items()}
    try:
        result = _calculate(op, values)
        for number in result if isinstance(result, list) else [result]:
            if not math.isfinite(number):
                raise ValueError('示例结果不是有限数值。')
        return result
    except (ArithmeticError, statistics.StatisticsError, IndexError, TypeError) as exc:
        raise ValueError('示例不满足样本、分母或维度条件。') from exc


def _calculate(op, d):
    if op == 'visible_count':
        return sum(t <= d['cutoff'] for t in d['available'])
    if op == 'latest_time':
        return max(d['times'])
    if op == 'asof_value':
        if len(d['available']) != len(d['values']) or len(set(d['available'])) != len(d['available']):
            raise ValueError('版本时间应唯一且与数值一一对应。')
        candidates = [(t, v) for t, v in zip(d['available'], d['values']) if t <= d['cutoff']]
        if not candidates:
            raise ValueError('截止时点没有可见版本。')
        return max(candidates)[1]
    if op == 'coverage':
        if not 0 <= d['observed'] <= d['expected_count'] or d['expected_count'] <= 0:
            raise ValueError('覆盖率计数无效。')
        return d['observed'] / d['expected_count']
    if op == 'scale':
        return d['value'] * d['scale']
    if op in {'price_return', 'log_return', 'total_return'}:
        if d['start'] <= 0 or (op == 'log_return' and d['end'] <= 0):
            raise ValueError('价格不满足定义条件。')
        if op == 'log_return':
            return math.log(d['end'] / d['start'])
        return (d['end'] + d.get('distribution', 0)) / d['start'] - 1
    if op == 'compound':
        if any(v < -1 for v in d['returns']):
            raise ValueError('简单收益不能低于−100%。')
        return math.prod(1 + v for v in d['returns']) - 1
    if op == 'difference':
        return d['left'] - d['right']
    if op == 'annualized_growth':
        if min(d.values()) <= 0 or d['periods_per_year'] / d['observations'] > 366:
            raise ValueError('增长因子和频率必须正且有界。')
        return d['growth'] ** (d['periods_per_year'] / d['observations']) - 1
    if op in {'mean', 'sample_variance', 'sample_sd', 'mean_sd_ratio'}:
        x = d['values']
        if op == 'mean':
            return statistics.mean(x)
        if op == 'sample_variance':
            return statistics.variance(x)
        sd = statistics.stdev(x)
        return sd if op == 'sample_sd' else _divide(statistics.mean(x), sd)
    if op in {'correlation', 'rank_correlation', 'sample_covariance'}:
        x, y = d['x'], d['y']
        if op == 'rank_correlation':
            x, y = _ranks(x), _ranks(y)
        return statistics.covariance(x, y) if op == 'sample_covariance' else statistics.correlation(x, y)
    if op == 'known_sigma_interval':
        if d['n'] <= 0 or d['n'] != int(d['n']) or d['sigma'] < 0 or d['z'] <= 0:
            raise ValueError('区间样本量、尺度或分位数无效。')
        margin = d['z'] * d['sigma'] / math.sqrt(d['n'])
        return [d['mean'] - margin, d['mean'] + margin]
    if op == 'ema_step':
        if not 0 < d['alpha'] <= 1:
            raise ValueError('EMA平滑系数需在(0,1]。')
        return d['alpha'] * d['price'] + (1 - d['alpha']) * d['previous']
    if op == 'rsi':
        if min(d.values()) < 0:
            raise ValueError('RSI幅度不能为负。')
        return _divide(100 * d['average_up'], d['average_up'] + d['average_down'])
    if op == 'true_range':
        if d['high'] < d['low']:
            raise ValueError('最高价不能低于最低价。')
        return max(d['high'] - d['low'], abs(d['high'] - d['previous_close']), abs(d['low'] - d['previous_close']))
    if op in {'weighted_mean', 'weighted_sum'}:
        x, w = d['values'], d['weights']
        if len(x) != len(w) or any(v < 0 for v in w) or sum(w) <= 0:
            raise ValueError('权重应非负且与数值配对。')
        total = sum(a * b for a, b in zip(x, w))
        return total if op == 'weighted_sum' else total / sum(w)
    if op == 'ratio':
        return _divide(d['numerator'], d['denominator'])
    if op == 'scaled_difference':
        return _divide(d['left'] - d['right'], d['scale'])
    if op == 'mean_difference':
        return statistics.mean(d['left']) - statistics.mean(d['right'])
    if op == 'equal_weights':
        if not 1 <= d['count'] <= 256 or d['count'] != int(d['count']):
            raise ValueError('资产数量应为1到256的整数。')
        return [1 / d['count']] * int(d['count'])
    if op == 'inverse_volatility':
        if min(d['volatilities']) <= 0:
            raise ValueError('波动必须为正。')
        inverse = [1 / v for v in d['volatilities']]
        return [v / sum(inverse) for v in inverse]
    if op == 'portfolio_variance':
        w, matrix = d['weights'], d['covariance']
        if len(matrix) != len(w) or any(len(row) != len(w) for row in matrix):
            raise ValueError('协方差矩阵维度不匹配。')
        # Existing NumPy dependency verifies the full PSD condition, not just a positive portfolio result.
        import numpy as np
        array = np.array(matrix)
        if not np.allclose(array, array.T, rtol=0, atol=1e-12) or np.linalg.eigvalsh(array).min() < -1e-12:
            raise ValueError('协方差矩阵须对称且半正定。')
        return float(np.array(w) @ array @ np.array(w))
    if op == 'sharpe_from_moments':
        if d['sample_sd'] <= 0 or d['periods_per_year'] <= 0:
            raise ValueError('波动和年期数须为正。')
        return d['mean_excess'] / d['sample_sd'] * math.sqrt(d['periods_per_year'])
    if op == 'max_drawdown':
        if min(d['nav']) <= 0:
            raise ValueError('净值应为正。')
        peak, result = d['nav'][0], 0
        for nav in d['nav']:
            peak = max(peak, nav)
            result = max(result, 1 - nav / peak)
        return result
    if op == 'downside_deviation':
        return math.sqrt(statistics.mean(min(v - d['target'], 0) ** 2 for v in d['returns']))
    if op in {'nearest_rank_var', 'fractional_es'}:
        c = d['confidence']
        if not 0 < c < 1:
            raise ValueError('置信水平必须在0和1之间。')
        losses = sorted(-v for v in d['returns'])
        if op == 'nearest_rank_var':
            return losses[math.ceil(c * len(losses)) - 1]
        losses.reverse()
        mass = (Decimal(1) - Decimal(str(c))) * len(losses)
        whole, fraction = int(mass), float(mass - int(mass))
        return max(0, (sum(losses[:whole]) + (fraction * losses[whole] if fraction else 0)) / float(mass))
    if op == 'concentration':
        w = d['weights']
        if min(w) < 0 or not math.isclose(sum(w), 1, abs_tol=1e-10):
            raise ValueError('集中度使用归一化非负权重。')
        return sum(v * v for v in w)
    raise ValueError('示例运算不在允许清单。')

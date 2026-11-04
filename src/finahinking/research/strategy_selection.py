"""Deterministic target selection from the panel prefix available at a decision."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd


class StrategyDataUnavailable(ValueError):
    """A declared input or comparable window is unavailable."""


def asset_kind(value):
    if value in ('EQUITY', 'STOCK'):
        return 'equity'
    if value == 'ETF':
        return 'etf'
    return None


def _current(frame, names):
    latest = frame.xs(frame.index.get_level_values('observed_at')[-1], level='observed_at')
    latest = latest.reindex(names)
    fields = ['is_session', 'in_universe']
    if not set(fields) <= set(latest.columns) or latest[fields].isna().any().any():
        raise StrategyDataUnavailable('交易日或股票池成员信息缺失')
    return latest.loc[latest['is_session'].eq(True) & latest['in_universe'].eq(True)]


def _numbers(latest, field, *, positive=False, nonnegative=False):
    if field not in latest:
        raise StrategyDataUnavailable(f'{field} 字段不可用')
    values = latest[field]
    if not pd.api.types.is_numeric_dtype(values) or pd.api.types.is_bool_dtype(values) or not np.isfinite(values).all():
        raise StrategyDataUnavailable(f'{field} 字段缺少有效数值')
    if positive and values.le(0).any() or nonnegative and values.lt(0).any():
        raise StrategyDataUnavailable(f'{field} 字段的数值域无效')
    return values.astype(float)


def adjusted_price_matrix(frame, names, periods):
    """Use the declared cumulative split ratio to compare raw prices causally."""
    fields = {'close', 'split_factor'}
    if not fields <= set(frame.columns):
        raise StrategyDataUnavailable('需要价格和累计拆分比例')
    dates = frame.index.get_level_values('observed_at').unique()[-periods:]
    latest = frame.loc[pd.IndexSlice[dates, :], :]
    close = latest['close'].unstack('instrument').reindex(columns=names)
    split = latest['split_factor'].unstack('instrument').reindex(columns=names)
    close = close.apply(pd.to_numeric, errors='coerce')
    split = split.apply(pd.to_numeric, errors='coerce')
    with np.errstate(over='ignore', invalid='ignore'):
        values = close.where(close.gt(0)) * split.where(split.gt(0))
    return values.where(np.isfinite(values))


def _prices(frame, latest, window):
    values = adjusted_price_matrix(frame, latest.index, window + 1)
    if len(values) < window + 1 or values.isna().any().any():
        raise StrategyDataUnavailable('同窗拆分中性价格缺少完整观察')
    return values


def _momentum(frame, latest, window):
    prices = _prices(frame, latest, window)
    returns = prices.iloc[-1] / prices.iloc[0] - 1
    if not np.isfinite(returns).all():
        raise StrategyDataUnavailable('拆分中性收益超出有限数值范围')
    return returns


def _volatility(frame, latest, window):
    vol = _prices(frame, latest, window).pct_change(fill_method=None).iloc[1:].std(ddof=1)
    if not np.isfinite(vol).all():
        raise StrategyDataUnavailable('收益波动缺少可比样本')
    return vol


def _fundamentals(latest):
    value = _numbers(latest, 'book_equity') / _numbers(latest, 'market_cap', positive=True)
    quality = _numbers(latest, 'net_profit') / _numbers(latest, 'total_assets', positive=True)
    if not np.isfinite(value).all() or not np.isfinite(quality).all():
        raise StrategyDataUnavailable('财务比率超出有限数值范围')
    return value, quality


def _ranked(scores, *, descending=True):
    # Stable asset identity breaks exact ties in the bounded-count selectors.
    return list(scores.sort_index().sort_values(ascending=not descending, kind='stable').index)


def _equal(names):
    return {name: 1.0 for name in names}


def _allocate(raw, params):
    gross = 1 - params['cash_buffer']
    total = sum(raw.values())
    if not total:
        return {}
    return {name: float(min(params['max_weight'], gross * weight / total)) for name, weight in raw.items()}


def _result(status, weights=None, *, reason, diagnostics=None):
    return {'status': status, 'target_weights': {} if weights is None else weights,
            'reason': reason, 'diagnostics': {} if diagnostics is None else diagnostics}


def _neutral(frame, latest, params):
    if len(latest) < 4:
        raise StrategyDataUnavailable('多因子中性需要至少四个可比股票')
    value, quality = _fundamentals(latest)
    momentum = _momentum(frame, latest, params['window'])
    beta = _numbers(latest, 'style_beta')
    scores = (value.rank(pct=True) + quality.rank(pct=True) + momentum.rank(pct=True)) / 3
    design = np.column_stack([np.ones(len(beta)), beta])
    coeff, _, rank, _ = np.linalg.lstsq(design, scores, rcond=None)
    residuals = scores - design @ coeff
    largest = float(residuals.abs().max())
    if rank < 2 or largest <= 1e-10:
        raise StrategyDataUnavailable('风格暴露或合成信号不足以估计中性残差')
    scale = min((1 - params['cash_buffer']) / residuals.abs().sum(), params['max_weight'] / largest)
    weights = {name: float(value * scale) for name, value in residuals.items()}
    return _result('RESEARCH_ONLY', reason='风格中性残差的理论多空研究', diagnostics={
        'theoretical_weights': weights,
        'net_exposure': sum(weights.values()),
        'style_beta_exposure': float(sum(beta[name] * weight for name, weight in weights.items())),
        'execution_status': 'BORROW_NOT_SUPPORTED',
    })


def _pairs(frame, latest, params):
    if len(latest) < 2:
        raise StrategyDataUnavailable('配对研究至少需要两个股票')
    pair = list(latest.index[:2])
    prices = _prices(frame, latest.loc[pair], params['window'])
    log_prices = np.log(prices)
    train = log_prices.iloc[:-1]
    design = np.column_stack([np.ones(len(train)), train[pair[1]]])
    coeff, _, rank, _ = np.linalg.lstsq(design, train[pair[0]], rcond=None)
    residual = train[pair[0]] - design @ coeff
    scale = float(residual.std(ddof=1))
    if rank < 2 or scale <= 1e-10:
        raise StrategyDataUnavailable('前窗价格回归或残差方差退化')
    current = log_prices.iloc[-1]
    hedge = float(coeff[1])
    zscore = float((current[pair[0]] - coeff[0] - hedge * current[pair[1]] - residual.mean()) / scale)
    weights = {}
    if hedge > 0 and abs(zscore) >= params['entry_z']:
        direction = -1 if zscore > 0 else 1
        position = min((1 - params['cash_buffer']) / (1 + hedge), params['max_weight'] / max(1, hedge))
        weights = {pair[0]: direction * position, pair[1]: -direction * position * hedge}
    return _result('RESEARCH_ONLY', reason='以前窗拟合、当前残差生成理论配对研究', diagnostics={
        'pair': pair, 'hedge_ratio': hedge, 'zscore': zscore, 'fit_observations': len(train),
        'theoretical_weights': weights, 'execution_status': 'BORROW_NOT_SUPPORTED',
    })


def _events(frame, names, params):
    if 'event_type' not in frame or not {'is_session', 'in_universe'} <= set(frame.columns):
        raise StrategyDataUnavailable('事件分类或历史资产池字段不可用')
    prices = adjusted_price_matrix(frame, names, params['event_window'])
    history = frame.loc[pd.IndexSlice[prices.index, :], :]
    events = history['event_type'].unstack('instrument').reindex(columns=names)
    active = history['is_session'].eq(True) & history['in_universe'].eq(True)
    active = active.unstack('instrument').reindex(columns=names)
    horizon = params['event_horizon']
    observations = {}
    immature = missing_marks = 0
    for name in names:
        for index, (category, eligible) in enumerate(zip(events[name], active[name], strict=True)):
            if eligible != True or not isinstance(category, str) or not category.strip() or category == 'NONE':
                continue
            if index + horizon >= len(prices):
                immature += 1
                continue
            start, end = prices[name].iloc[[index, index + horizon]]
            value = end / start - 1
            if not np.isfinite(value):
                missing_marks += 1
                continue
            observations.setdefault(category, []).append(float(value))
    if not observations:
        raise StrategyDataUnavailable('缺少已成熟且价格可比较的事件观察')
    return _result('RESEARCH_ONLY', reason='事件原始价格收益统计', diagnostics={
        'horizon_observations': horizon, 'matured_events': sum(map(len, observations.values())),
        'pending_events': immature, 'missing_marks': missing_marks,
        'event_type_known': int((events.notna() & active.eq(True)).sum().sum()),
        'groups': {category: {'count': len(values), 'mean_price_return': sum(value / len(values) for value in values)}
                   for category, values in sorted(observations.items())},
        'execution_status': 'EVENT_STATISTICS_ONLY',
    })


def _long_targets(template_id, frame, latest, params, kinds, previous):
    if template_id == 'equal-weight':
        _numbers(latest, 'close', positive=True)
        return _equal(latest.index)
    if template_id == 'single-factor-quantile':
        if params['factor'] == 'momentum':
            scores = _momentum(frame, latest, params['window'])
        else:
            if any(kinds[name] != 'equity' for name in latest.index):
                raise StrategyDataUnavailable('财务分层要求股票口径，需使用仅股票的研究面板')
            numerator, denominator = {
                'book_to_market': ('book_equity', 'market_cap'),
                'return_on_assets': ('net_profit', 'total_assets'),
            }[params['factor']]
            scores = _numbers(latest, numerator) / _numbers(latest, denominator, positive=True)
            if not np.isfinite(scores).all():
                raise StrategyDataUnavailable('财务分层值超出有限数值范围')
        if len(scores) < 2 or scores.nunique() < 2:
            raise StrategyDataUnavailable('单因子缺少可分层的截面差异')
        return _equal(scores[scores.ge(scores.quantile(params['quantile']))].index)
    if template_id == 'short-term-reversal':
        scores = _momentum(frame, latest, params['window'])
        return _equal(_ranked(scores, descending=False)[:params['selection_count']])
    if template_id == 'trend-breakout':
        prices = _prices(frame, latest, params['window'])
        signal = prices.iloc[-1].gt(prices.iloc[:-1].max())
        return _equal(signal[signal].index)
    if template_id == 'low-volatility-low-turnover':
        ranked = _ranked(_volatility(frame, latest, params['window']), descending=False)
        count = params['selection_count']
        retained = [name for name in ranked[:count + params['rank_buffer']] if previous.get(name, 0) > 0]
        return _equal(list(dict.fromkeys(retained + ranked))[:count])
    if template_id == 'value-quality':
        value, quality = _fundamentals(latest)
        scores = value.rank(pct=True) + quality.rank(pct=True)
        return _equal(_ranked(scores)[:params['selection_count']])
    if template_id == 'etf-risk-budget':
        _numbers(latest, 'nav', positive=True)
        error = _numbers(latest, 'tracking_error', nonnegative=True)
        vol = _volatility(frame, latest, params['window'])
        admitted = vol[error.le(params['tracking_error_limit'])]
        if admitted.le(1e-10).any():
            raise StrategyDataUnavailable('基金价格波动不足以估计风险预算')
        return {name: 1 / float(value) for name, value in admitted.items()}
    if template_id == 'etf-relative-strength':
        _numbers(latest, 'nav', positive=True)
        scores = _momentum(frame, latest, params['window'])
        return _equal(_ranked(scores[scores.gt(0)])[:params['selection_count']])
    if template_id == 'regime-allocation':
        _numbers(latest, 'close', positive=True)
        regimes = latest.get('policy_regime', pd.Series(dtype=object))
        allocation = {'RISK_ON': .67, 'NEUTRAL': .5, 'RISK_OFF': .25}
        if len(regimes) != len(latest) or regimes.nunique(dropna=False) != 1 or regimes.iloc[0] not in allocation:
            raise StrategyDataUnavailable('需要一致的点时市场状态')
        groups = {kind: [name for name in latest.index if kinds[name] == kind] for kind in ('equity', 'etf')}
        if not all(groups.values()):
            raise StrategyDataUnavailable('状态调仓同时需要股票和 ETF')
        equity_budget = allocation[regimes.iloc[0]]
        return {name: budget / len(groups[kind])
                for kind, budget in (('equity', equity_budget), ('etf', 1 - equity_budget))
                for name in groups[kind]}
    raise ValueError('没有匹配的策略选择算子')


def select_strategy(template, panel_prefix, parameters, asset_classes, previous_weights=None):
    """Return a target or research result for the last declared observation."""
    index = panel_prefix.index
    if (not isinstance(index, pd.MultiIndex) or index.names != ['observed_at', 'instrument']
            or not index.is_unique or not index.is_monotonic_increasing or not 1 <= len(index) <= 10_000):
        raise ValueError('策略面板需要有界、唯一且排序的观察点和资产索引')
    times = index.get_level_values('observed_at')
    if not isinstance(times, pd.DatetimeIndex) or times.tz is None or times.hasnans:
        raise ValueError('策略观察点需要明确时区')
    if not isinstance(asset_classes, Mapping):
        raise TypeError('需要冻结的资产类别映射')
    kinds = {name: asset_kind(asset_classes.get(name)) for name in index.get_level_values('instrument').unique()}
    if None in kinds.values():
        return _result('NEEDS_DATA', reason='资产类别缺失')
    names = sorted(name for name, kind in kinds.items() if template['universe'] in ('all', kind))
    template_id = template['template_id']
    try:
        required_fields = set(template['required_fields'])
        for name, alternatives in template.get('parameter_fields', {}).items():
            required_fields.update(alternatives.get(parameters[name], ()))
        missing = required_fields - set(panel_prefix.columns)
        if missing:
            raise StrategyDataUnavailable('缺少策略字段 ' + ', '.join(sorted(missing)))
        if not names:
            raise StrategyDataUnavailable('声明资产范围内没有可比资产')
        if template_id == 'event-study':
            return _events(panel_prefix, names, parameters)
        latest = _current(panel_prefix, names)
        if latest.empty:
            return _result('READY', reason='当期资产池为空，保留现金') if template['execution_mode'] == 'PAPER_LONG_ONLY' else _result('NEEDS_DATA', reason='当期资产池为空')
        if template_id == 'multifactor-neutral':
            return _neutral(panel_prefix, latest, parameters)
        if template_id == 'pairs-mean-reversion':
            return _pairs(panel_prefix, latest, parameters)
        raw = _long_targets(template_id, panel_prefix, latest, parameters, kinds, previous_weights or {})
        weights = _allocate(raw, parameters)
    except np.linalg.LinAlgError:
        return _result('NEEDS_DATA', reason='已知样本无法估计回归')
    except StrategyDataUnavailable as exc:
        return _result('NEEDS_DATA', reason=str(exc))
    return _result('READY', weights, reason='生成纸面目标权重' if weights else '无符合选择条件的资产，保留现金',
                   diagnostics={'eligible_assets': list(latest.index), 'cash_target': 1 - sum(weights.values())})

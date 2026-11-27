"""Versioned risk rule evaluation against evidence known at the decision."""

from __future__ import annotations

import math
from itertools import combinations
from numbers import Real
from statistics import stdev

import numpy as np

from .risk_runtime import empirical_cvar
from .strategy_selection import StrategyDataUnavailable, adjusted_price_matrix, asset_kind


def _number(value):
    if not isinstance(value, Real) or isinstance(value, (bool, np.bool_)):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    return value if math.isfinite(value) else None


def evaluate_risk_rules(rules, inputs):
    """Unknown evidence is explicit. The rule's action decides whether it gates an order."""
    results = []
    for rule in rules:
        metric = rule['metric']
        value = inputs.get(metric)
        operator = rule['operator']
        if operator == 'is_true':
            value = bool(value) if isinstance(value, (bool, np.bool_)) else None
            passed = value is True
        elif operator == 'at_most':
            value = _number(value)
            passed = value is not None and value <= rule['limit'] + 1e-12
        else:
            raise ValueError('风险算子未声明')
        if value is None:
            status, reason = 'NEEDS_DATA', f'{rule["title"]}的可用证据不足'
        elif passed:
            status, reason = 'PASS', f'{rule["title"]}符合登记阈值'
        else:
            status, reason = 'TRIGGERED', f'{rule["title"]}触发登记阈值'
        results.append({
            'rule_id': rule['rule_id'], 'version': rule['version'], 'fingerprint': rule['fingerprint'],
            'metric': metric, 'unit': rule['unit'], 'value': value, 'limit': rule['limit'],
            'status': status, 'action': rule['action'], 'reason': reason,
        })
    return results


def _industry(latest, targets):
    groups = {}
    for asset, weight in targets.items():
        name = latest.loc[asset].get('industry')
        if not isinstance(name, str) or not name.strip() or name.strip().upper() == 'UNKNOWN':
            return None
        groups[name] = groups.get(name, 0.0) + weight
    return max(groups.values(), default=0.0)


def _financial(latest, targets, asset_classes):
    values = []
    for asset in targets:
        if asset_kind(asset_classes.get(asset)) != 'equity':
            continue
        row = latest.loc[asset]
        profit, assets, book = map(_number, (row.get('net_profit'), row.get('total_assets'), row.get('book_equity')))
        if profit is None or assets is None or assets <= 0 or book is None:
            return None
        values.append(max(abs(profit / assets), max(0.0, -book / assets)))
    return _number(max(values)) if values else None


def _tradeable(latest, changes):
    required = {'is_session': True, 'in_universe': True, 'trade_allowed': True, 'suspended': False}
    passed = True
    for asset in changes:
        for field, expected in required.items():
            value = latest.loc[asset].get(field)
            if not isinstance(value, (bool, np.bool_)):
                return None
            passed = passed and bool(value) is expected
    return passed


def _liquidity(latest, changes, equity):
    if not changes:
        return 0.0
    if equity is None or equity <= 0:
        return None
    fractions = []
    for asset, delta in changes.items():
        amount = _number(latest.loc[asset].get('amount'))
        if amount is None or amount <= 0:
            return None
        fractions.append(abs(delta) * equity / amount)
    return _number(max(fractions))


def _max_abs_correlation(rows):
    """Maximum absolute Pearson correlation on the complete shared window."""
    unit_columns = []
    for column in zip(*rows, strict=True):
        if min(column) == max(column):
            return None
        # Power-of-two scaling preserves the input precision, including tiny
        # differences in near-constant series. Center before taking the norm.
        exponent = math.frexp(max(map(abs, column)))[1]
        scaled = [math.ldexp(value, -exponent) for value in column]
        offsets = [value - scaled[0] for value in scaled]
        mean_offset = math.fsum(offsets) / len(offsets)
        centered = [value - mean_offset for value in offsets]
        norm = math.sqrt(math.fsum(value * value for value in centered))
        unit_columns.append([value / norm for value in centered])

    # Explicit binary64 products avoid platform-specific fused reductions in
    # native correlation kernels. These values enter published strategy hashes.
    correlations = (abs(math.fsum(x * y for x, y in zip(left, right, strict=True)))
                    for left, right in combinations(unit_columns, 2))
    return min(1.0, max(correlations))


def _history_risks(frame, targets):
    if not targets:
        return None, None, None, False
    try:
        assets = sorted(targets)
        prices = adjusted_price_matrix(frame, assets, 61)
    except StrategyDataUnavailable:
        return None, None, None, False
    if prices.isna().any().any():
        return None, None, None, False
    returns = prices.pct_change(fill_method=None).iloc[1:]
    if not np.isfinite(returns).all().all():
        return None, None, None, False
    if len(prices) < 7:
        return None, None, None, True
    rows = returns.to_numpy().tolist()
    weights = [targets[asset] for asset in assets]
    portfolio_returns = [math.fsum(value * weight for value, weight in zip(row, weights, strict=True))
                         for row in rows]
    volatility = _number(stdev(portfolio_returns))
    tail = _number(empirical_cvar(portfolio_returns))
    corr = _max_abs_correlation(rows) if len(assets) >= 2 else None
    return volatility, tail, corr, False


def _drawdowns(equity_curve, current):
    if current is None or current <= 0:
        return None, None
    previous = [_number(value) for value in equity_curve]
    if any(value is None or value <= 0 for value in previous):
        return None, None
    if not previous:
        return None, None
    drawdown = max(0.0, 1 - current / max(previous))
    logic_loss = max(0.0, 1 - current / previous[-1])
    return drawdown, logic_loss


def strategy_risk_inputs(panel_prefix, target_weights, equity_curve, equity, asset_classes, *, pit_valid,
                         current_weights=None, include_warmup=False):
    """Derive metrics from the known prefix and earlier paper equity marks."""
    targets = {asset: weight for asset, weight in target_weights.items() if weight != 0}
    current_weights = {} if current_weights is None else current_weights
    if any(_number(weight) is None or weight < 0 or weight > 1
           for weight in (*targets.values(), *current_weights.values())) or sum(targets.values()) > 1 + 1e-12:
        raise ValueError('纸面权重需要有限的多头分配')
    names = sorted(set(targets) | set(current_weights))
    last_date = panel_prefix.index.get_level_values('observed_at')[-1]
    latest = panel_prefix.xs(last_date, level='observed_at').reindex(names)
    changes = {asset: targets.get(asset, 0.0) - current_weights.get(asset, 0.0) for asset in names}
    changes = {asset: delta for asset, delta in changes.items() if abs(delta) > 1e-12}
    marked_equity = _number(equity)
    volatility, tail, collinearity, lookback_warmup = _history_risks(panel_prefix, targets)
    drawdown, logic_loss = _drawdowns(equity_curve, marked_equity)
    return {
        **({'price_history_warmup': True} if include_warmup and lookback_warmup else {}),
        'pit_valid': pit_valid,
        'financial_anomaly': _financial(latest, targets, asset_classes),
        'tradeable': _tradeable(latest, changes),
        'liquidity_participation': _liquidity(latest, changes, marked_equity),
        'concentration': max(targets.values(), default=0.0),
        'industry_concentration': _industry(latest, targets),
        'collinearity': collinearity,
        'volatility': volatility,
        'tail_loss': tail,
        'logic_loss': logic_loss,
        'drawdown': drawdown,
        'stress_loss': .15 * sum(targets.values()),
    }

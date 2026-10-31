"""Bounded historical evidence on declared units and exactly aligned intervals."""
import math
import re
from itertools import pairwise

import numpy as np
import pandas as pd

from .contracts import stable_digest
from .risk_runtime import RiskManager, empirical_cvar


def _uncertainty(returns, intervals):
    count = len(returns)
    result = {'status': 'INSUFFICIENT_SAMPLE', 'sample_size': count, 'interval': None,
              'level': .95, 'replicates': 200, 'seed': 19, 'block_length': math.ceil(math.sqrt(count)),
              'method': 'moving-block-bootstrap-mean.v1', 'mean': float(np.mean(returns)) if count else None}
    if count < 20:
        return result
    if len({end - start for start, end in intervals}) != 1 or any(a[1] != b[0] for a, b in pairwise(intervals)):
        result['status'] = 'DISCONTINUOUS_SAMPLE'
        return result
    block = result['block_length']
    rng = np.random.default_rng(19)
    means = []
    for _ in range(200):
        starts = rng.integers(0, count - block + 1, size=math.ceil(count / block))
        samples = np.concatenate([returns[start:start + block] for start in starts])[:count]
        means.append(float(np.mean(samples)))
    result.update(status='ESTIMATED', interval=[float(item) for item in np.quantile(means, [.025, .975])])
    return result


def compute_quant_evidence(snapshot, as_of, publication_before=None):
    from .snapshot_tools import SnapshotResearchTools

    boundary = SnapshotResearchTools._boundary(as_of)
    cutoff = pd.Timestamp(publication_before) if publication_before is not None else None
    if cutoff is not None and (pd.isna(cutoff) or cutoff.tzinfo is None):
        raise ValueError('publication cutoff must be timezone-aware')
    result = {'schema_version': 'quant-evidence.v1', 'status': 'BLOCKED', 'paper_only': True,
        'data_mode': snapshot.mode, 'dataset_fingerprint': snapshot.fingerprint,
        'as_of': as_of, 'publication_before': cutoff.isoformat() if cutoff is not None else None,
        'blocking_reasons': [], 'coverage': {}, 'weights': {}, 'exposure': None,
        'variance': None, 'tail_risk': None, 'stress': None, 'sensitivity': None,
        'uncertainty': {'status': 'NOT_COMPUTED', 'interval': None},
        'source_claims_status': 'NOT_VALIDATED',
        'limitations': ['等权固定研究权重用于历史证据，不是决策持仓；收益未扣成本。',
          '样本协方差与区块重采样仅描述已观察区间；95%区间不是未来盈利概率。',
          '动量暴露是权重乘已知两期收益，不是市场或风格 beta。',
          '压力网格只证明已测情景；稳定点不是未测试的连续安全区间。',
          '单位和资产类别来自用户声明；合成输入只验证计算，未验证来源研究主张。']}
    def finish():
        result['fingerprint'] = stable_digest(result)
        return result
    if len(snapshot.observations) > 20000 or len(snapshot.instruments) > 20:
        result['blocking_reasons'] = ['RESOURCE_LIMIT']
        return finish()
    rows = [row for row in snapshot.observations if cutoff is None or row.timestamp < cutoff]
    assets = sorted({row.instrument for row in rows})
    groups = {asset: [row for row in rows if row.instrument == asset] for asset in assets}
    units = snapshot.provenance.get('units', {})
    classes = snapshot.provenance.get('asset_classes', {})
    if not isinstance(units, dict) or not isinstance(units.get('price'), str) or not re.fullmatch('[A-Z]{3}', units['price']) or units.get('cash') != units['price'] or units.get('volume') != 'shares':
        result['blocking_reasons'].append('UNITS_REQUIRED')
    if not isinstance(classes, dict) or any(classes.get(asset) not in {'EQUITY', 'ETF'} for asset in assets):
        result['blocking_reasons'].append('ASSET_CLASS_REQUIRED')
    if len(assets) < 2:
        result['blocking_reasons'].append('MULTI_ASSET_REQUIRED')
    if not rows or any(not row.available_at_time(boundary) for row in rows):
        result['blocking_reasons'].append('PIT_UNAVAILABLE')
    if any(left.available_at is None or left.available_at > right.timestamp for group in groups.values() for left, right in pairwise(group)):
        result['blocking_reasons'].append('HISTORICAL_AVAILABILITY')
    if cutoff is not None and any(row.available_at is None or row.available_at >= cutoff for row in rows):
        result['blocking_reasons'].append('PIT_UNAVAILABLE')
    if any(len({row.timestamp.date() for row in group}) != len(group) for group in groups.values()):
        result['blocking_reasons'].append('LOW_FREQUENCY_REQUIRED')
    result['coverage'] = {'asset_count': len(assets), 'observations': len(rows),
                          'per_asset': {asset: len(group) for asset, group in groups.items()},
                          'common_intervals': 0}
    if result['blocking_reasons']:
        return finish()
    per_asset = {asset: {(left.timestamp, right.timestamp): right.close / left.close - 1
                        for left, right in pairwise(group)} for asset, group in groups.items()}
    intervals = sorted(set.intersection(*(set(values) for values in per_asset.values())))
    result['coverage'].update(common_intervals=len(intervals),
        union_intervals=len(set.union(*(set(values) for values in per_asset.values()))),
        interval_digest=stable_digest([[str(a), str(b)] for a, b in intervals]))
    if len(intervals) < 2:
        result['blocking_reasons'].append('COMMON_SAMPLE_REQUIRED')
        return finish()
    matrix = np.array([[per_asset[asset][interval] for asset in assets] for interval in intervals])
    if not np.isfinite(matrix).all() or np.max(np.abs(matrix)) > 1e100:
        result["blocking_reasons"].append("NONFINITE_RETURN")
        return finish()
    endpoint = intervals[-1][1]
    if any(row.available_at > endpoint for group in groups.values() for row in group if row.timestamp <= endpoint):
        result["blocking_reasons"].append("EXPOSURE_AVAILABILITY")
        return finish()
    weights = np.full(len(assets), 1 / len(assets))
    returns = matrix @ weights
    covariance = np.cov(matrix, rowvar=False, ddof=1)
    contribution = weights * (covariance @ weights)
    variance = float(weights @ covariance @ weights)
    result['weights'] = dict(zip(assets, weights.tolist()))
    result['variance'] = {'total': variance, 'contributions': dict(zip(assets, contribution.tolist())),
        'shares': dict(zip(assets, (contribution / variance).tolist())) if variance > 0 else None,
        'residual': float(variance - sum(contribution)), 'method': 'sample-covariance-euler.v1',
        'formula': 'component_i = w_i * (Sigma @ w)_i; total = w^T Sigma w', 'ddof': 1}
    result['tail_risk'] = {'cvar': empirical_cvar(returns.tolist(), .95), 'confidence': .95,
                           'sample_size': len(returns), 'basis': 'OBSERVATION'}
    # Exposure uses the last common endpoint, not each asset's potentially later endpoint.
    endpoint = intervals[-1][1]
    observations, industries, momentum = [], {}, {}
    for asset, weight in zip(assets, weights):
        prefix = [row for row in groups[asset] if row.timestamp <= endpoint]
        last = prefix[-1]
        industry = last.industry or 'UNKNOWN'
        value = last.close / prefix[-3].close - 1 if len(prefix) >= 3 else None
        momentum[asset] = None if value is None else float(weight * value)
        industries[industry] = industries.get(industry, 0.0) + float(weight)
        observations.append({'instrument': asset, 'timestamp': last.timestamp.isoformat(),
            'available_at': last.available_at.isoformat(), 'industry': industry,
            'factors': {} if value is None else {'momentum_2obs': value}})
    result['exposure'] = {'as_of': endpoint.isoformat(), 'industry': industries,
        'momentum_contributions': momentum,
        'momentum_total': sum(momentum.values()) if all(value is not None for value in momentum.values()) else None,
        'asset_classes': {asset: classes[asset] for asset in assets}, 'units': dict(units)}
    scenarios = {f'market_{int(abs(shock)*100)}': {'return_shock': shock, 'max_loss': .15} for shock in (-.1, -.2, -.3)}
    scenarios.update({f'industry_{key}': {'industry_shocks': {key: -.2}, 'max_loss': .15} for key in industries})
    if all(value is not None for value in momentum.values()):
        scenarios['momentum_reversal'] = {'factor_shocks': {'momentum_2obs': -1}, 'max_loss': .15}
    snap = {'pit_status': 'AVAILABLE', 'as_of': boundary.isoformat(), 'observations': observations}
    result['stress'] = RiskManager().stress(snap, {'weights': result['weights']}, scenarios).to_dict()
    points = [{'shock': shock, 'loss': result['stress']['scenarios'][f'market_{int(abs(shock)*100)}']['loss'],
               'within_limit': result['stress']['scenarios'][f'market_{int(abs(shock)*100)}']['passed']} for shock in (-.1, -.2, -.3)]
    result['sensitivity'] = {'loss_limit': .15, 'points': points,
                            'stable_tested_shocks': [point['shock'] for point in points if point['within_limit']]}
    result['uncertainty'] = _uncertainty(returns, intervals)
    result['status'] = 'READY'
    return finish()

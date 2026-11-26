"""Explain six frozen conditions; never manufacture identities for old reports."""
import math

from .contracts import stable_digest

_METHOD = {'factor_id', 'strategy_version', 'engine_version', 'allocation_method', 'bar_frequency',
           'metric_basis', 'annualization', 'delay_observations'}
_PARAMETERS = {'momentum_window', 'threshold', 'target_weight', 'starting_cash'}
_COST = {'fee_bps', 'slippage_bps'}
_LABELS = {'data': '数据与样本', 'method': '方法与口径', 'parameters': '参数',
           'costs': '费用与滑点', 'risk': '风险政策', 'code': '代码与依赖'}


def validation_comparison_summary(plan):
    """Persist the Studio conditions independently of the auxiliary research baseline."""
    source, options = plan['source'], plan['options']
    windows = [{k: v for k, v in window.items() if v is not None} for window in plan['windows']]
    return {
        'run_id': plan['run_id'], 'template_id': plan['template_id'], 'plan_fingerprint': plan['plan_fingerprint'],
        'conditions': {
            'data': {'ledger': plan['original_ledger'], 'original_report': plan['original_report'],
                     'panel': source['panel_ref'], 'windows': windows},
            'method': {k: plan[k] for k in ('studio_version', 'method', 'objective', 'selection_rule',
                                             'stability_rule', 'account_initialization', 'warmup')},
            'parameters': {'parameter': plan['parameter'], 'grid': plan['parameter_values'],
                           **{k: options[k] for k in ('train_size', 'test_size', 'stability_tolerance')}},
            'costs': {'execution_scenarios': plan['scenarios']},
            'risk': {'policy': source['policy_digest'], 'portfolio': plan['portfolio']},
        },
    }


def _conditions(source):
    reproduction = source.get('reproduction') or {}
    if 'strategy_validation' in source:
        marker = source['strategy_validation'] or {}
        terms = marker.get('conditions') or {}
        return {
            'data': {'source_run': marker.get('run_id'), 'snapshot': source.get('dataset_fingerprint'),
                     'as_of': source.get('as_of'), **terms.get('data', {})},
            'method': {'product': 'STRATEGY_VALIDATION', 'template_id': marker.get('template_id'),
                       **terms.get('method', {})},
            'parameters': {'plan_fingerprint': marker.get('plan_fingerprint'), **terms.get('parameters', {})},
            'costs': terms.get('costs'), 'risk': terms.get('risk'), 'code': reproduction.get('identity'),
        }
    policy = reproduction.get('policy') or {}
    quant = source.get('quant_analytics') or {}
    conditions = quant.get('comparison_conditions') or {}
    study = source.get('factor_study') or {}
    charter = study.get('charter') or {}
    discovery = (source.get('discovery') or {}).get('blueprint') or {}
    strategy = (source.get('strategy_study') or {}).get('binding') or {}
    def subset(keys):
        return {key: policy.get(key) for key in sorted(keys)}
    return {
        'data': {'snapshot': source.get('dataset_fingerprint'), 'as_of': source.get('as_of'),
                 **{key: conditions.get(key) for key in ('instrument', 'sample_period', 'scoring_index')},
                 'publication_scope': 'FULL' if reproduction and reproduction.get('publication_before') is None
                                      else reproduction.get('publication_before'),
                 'attachment': charter.get('attachment_ref', 'NONE')},
        'method': {**subset(_METHOD), 'split': conditions.get('split'),
                   'factor_study': {key: charter.get(key, 'NONE') for key in ('version', 'recipe_id', 'recipe_version', 'factor_spec', 'method_binding')} if study else 'NONE',
                   'study_phase': study.get('phase', 'NONE'),
                   'discovery': {key: discovery.get(key) for key in ('target_digest', 'method_manifest_digest', 'execution_ref')} if discovery else 'NONE',
                   'strategy': {key: strategy.get(key) for key in ('template_digest', 'risk_digest')} if strategy else 'NONE'},
        'parameters': {**subset(_PARAMETERS), 'study': {key: charter[key] for key in ('windows', 'label_horizon', 'embargo', 'n_splits', 'min_abs_ic', 'max_neighbor_delta') if key in charter}},
        'costs': {**subset(_COST), 'study': charter.get('cost', 'NONE')},
        'risk': {key: value for key, value in policy.items() if key not in _METHOD | _PARAMETERS | _COST} or None,
        'code': reproduction.get('identity'),
    }


def _unknown(value):
    if value is None:
        return True
    if isinstance(value, dict):
        return any(_unknown(child) for child in value.values())
    if isinstance(value, list):
        return any(_unknown(child) for child in value)
    return False


def compare_research(left, right):
    a, b = _conditions(left), _conditions(right)
    left_studio, right_studio = 'strategy_validation' in left, 'strategy_validation' in right
    compact = left_studio and right_studio and any(
        not (side.get('strategy_validation') or {}).get('conditions') for side in (left, right))
    identifiers = {'data': ('source_run', 'snapshot', 'as_of'), 'method': ('template_id',),
                   'parameters': ('plan_fingerprint',)}
    rows = []
    for key, label in _LABELS.items():
        unknown = _unknown(a[key]) or _unknown(b[key])
        status = 'UNKNOWN' if unknown else 'SAME' if stable_digest(a[key]) == stable_digest(b[key]) else 'CHANGED'
        if compact and key in identifiers:
            # A shorter archived summary proves a change only where both reports carry that identity.
            known_pairs = [(a[key].get(name), b[key].get(name)) for name in identifiers[key]]
            status = 'CHANGED' if any(x is not None and y is not None and x != y for x, y in known_pairs) else 'UNKNOWN'
        if key == 'method' and left_studio != right_studio:
            status = 'CHANGED'
        rows.append({'id': key, 'label': label, 'status': status, 'left': a[key], 'right': b[key],
                     'explanation': {'UNKNOWN': '原报告缺少明确条件，暂不能证明可比。',
                                     'SAME': '已冻结的条件一致。',
                                     'CHANGED': '条件发生变化，请分别阅读结果。'}[status]})
    status = 'INCOMPATIBLE' if any(row['status'] == 'CHANGED' for row in rows) else 'UNKNOWN' if any(row['status'] == 'UNKNOWN' for row in rows) else 'COMPATIBLE'
    metrics = {}
    if status == 'COMPATIBLE' and not (left_studio or right_studio):
        first = left.get('quant_analytics', {}).get('metrics', {})
        for key, value in right.get('quant_analytics', {}).get('metrics', {}).items():
            old = first.get(key)
            if type(value) in (int, float) and type(old) in (int, float) and math.isfinite(value) and math.isfinite(old):
                metrics[key] = value - old
    return {'schema_version': 'research-differences.v1', 'status': status, 'categories': rows,
            'metric_differences': metrics, 'paper_only': True,
            'basis': ('比较策略验证的原账本、时间窗和冻结方案。逐窗收益与风险归因见完整验证报告。'
                      if left_studio or right_studio else '同条件基线指标的右侧减左侧；差异描述不构成策略排名。')}

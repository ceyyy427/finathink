"""Content-addressed workflow archives for published evidence and independent replay."""
import base64
import hashlib
import html
import json
import re

from . import portable_factors, portable_learning, portable_strategy
from .contracts import stable_digest
from .portable_comparison import compare_product
from .portable_history import freeze_report, validate_history
from .portable_inputs import PortableUnavailable
from .reports import _ABSOLUTE_PATH, _SECRET_TEXT, _sanitize_public
from .research_identity import execution_identity
from .snapshot_tools import local_research_policy

VERSION = 'research-package.v2'
MAX_BYTES = 32 * 1024 * 1024
GROUPS = {
    'research': (),
    'factor_study': ('factor_study',),
    'strategy_study': ('strategy_ledger', 'strategy_benchmark', 'strategy_decisions', 'strategy_result'),
    'strategy_validation': ('strategy_validation_plan', 'strategy_walkforward', 'strategy_portfolio'),
    'research_pair': ('learning_approval', 'learning_baseline', 'learning_candidate', 'learning_comparison'),
    'strategy_pair': ('learning_approval', 'learning_baseline', 'learning_candidate', 'learning_comparison'),
}
_PAIRS = {'research_pair', 'strategy_pair'}
_FIELDS = {'schema_version', 'run_id', 'kind', 'archive', 'archive_digest', 'package_id',
           'product_coverage', 'missing_dependencies'}
_URL = re.compile(r'(?:https?|ftp|file|wss?)://[^\s<>"\']+', re.IGNORECASE)
_PUBLIC_CODE = re.compile(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_./#-]+\Z')
_REPORT_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\.html\Z')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':'))


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _public(value):
    # Published usage uses numeric token counts. Never treat free-form strings as counts.
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {'input_tokens', 'output_tokens', 'total_tokens', 'prompt_tokens', 'completion_tokens'} and type(item) is int and item >= 0:
                continue
            _sanitize_public({key: None})
            _public(item)
    elif isinstance(value, list):
        for item in value:
            _public(item)
    else:
        if isinstance(value, str):
            if any(not _PUBLIC_CODE.fullmatch(url) for url in _URL.findall(value)):
                raise ValueError('导出内容包含非公开来源地址。')
            value = _URL.sub('[公开代码引用]', value)
        _sanitize_public(value)


def _adapter(kind):
    return portable_factors if kind == 'factor_study' else portable_strategy


def _check_report(item):
    if (not isinstance(item, dict) or set(item) != {'name', 'media_type', 'sha256', 'content_base64', 'publication'}
            or not _REPORT_NAME.fullmatch(item['name']) or item['media_type'] != 'text/html'):
        raise ValueError('历史报告记录无效。')
    raw = base64.b64decode(item['content_base64'], validate=True)
    if _sha(raw) != item['sha256']:
        raise ValueError('历史报告字节损坏。')
    # CSS namespaces are identifiers in published report styles, not user data sources.
    document = html.unescape(raw.decode('utf-8'))
    for url in _URL.findall(document):
        if not (_PUBLIC_CODE.fullmatch(url) or url in {'http://www.w3.org/2000/svg', 'http://www.w3.org/1999/xlink'}):
            raise ValueError('历史报告包含非公开来源地址。')
    remaining = _URL.sub('', document)
    if _SECRET_TEXT.search(remaining) or _ABSOLUTE_PATH.search(remaining):
        raise ValueError('历史报告内含私有配置或本地路径。')


def _artifact_runs(kind, run_id, inputs):
    ids = [run_id]
    if kind == 'research_pair':
        ids.extend(child['run_id'] for child in inputs['children'].values())
    elif kind == 'factor_study' and inputs:
        ids.append(inputs['receipt']['validation_run'])
    elif kind == 'strategy_validation' and inputs:
        ids.append(inputs['source']['publication']['run_id'])
    return list(dict.fromkeys(ids))


def _model_evidence(analytics):
    record = analytics.get('model_research')
    if not record:
        return None
    return {key: record[key] for key in ('roles', 'synthesis', 'calls', 'limitations', 'execution_mode') if key in record}


def _coverage(kind, body):
    expected = body['expected']
    core = body['core']
    coverage = {group: 'INCLUDED' if group in expected else 'BLOCKED' for group in GROUPS[kind]}
    if core is not None:
        coverage.update({group: 'INCLUDED' if core['snapshot'] is not None else 'BLOCKED' for group in core['expected']})
    if body['model'] is not None:
        coverage['model_text'] = 'ARCHIVED'
    if body['discovery'] is not None:
        coverage['discovery'] = 'ARCHIVED'
    if body['history']:
        coverage['historical_reports'] = 'ARCHIVED'
    return coverage


def freeze(manager, run_id, pair_id):
    service = manager.service
    if pair_id is not None:
        frozen = portable_learning.freeze_pair(service, pair_id)
        inputs, expected = frozen['inputs'], frozen['expected']
        kind, source_id = inputs['kind'], inputs['run_id']
        _, analytics = manager.source(source_id)
        identity = inputs['source']['runtime'] if kind == 'strategy_pair' else inputs['binding']['execution_identity']
        core, pending, reference = None, [], pair_id
    else:
        source_id = reference = run_id
        _, analytics = manager.source(run_id)
        kind = next((key for key in ('strategy_validation', 'strategy_study', 'factor_study') if analytics.get(key)), 'research')
        inputs, expected, pending = None, {}, []
        try:
            core = manager.export_legacy(run_id, True, published_only=True)
        except (FileNotFoundError, PortableUnavailable) as exc:
            pending.append('NORMALIZED_DATA' if isinstance(exc, FileNotFoundError) else str(exc))
            core = manager.export_legacy(run_id, False)
        identity = core['identity']
        if GROUPS[kind]:
            try:
                frozen = _adapter(kind).freeze_run(service, run_id, analytics, kind)
                inputs, expected = frozen['inputs'], frozen['expected']
            except PortableUnavailable as exc:
                pending.append(str(exc))
            except (FileNotFoundError, ValueError) as exc:
                cause = exc
                while cause and not isinstance(cause, FileNotFoundError):
                    cause = cause.__cause__
                if cause is None:
                    raise
                pending.append('WORKFLOW_SOURCE_MATERIAL_MISSING')
    history = [freeze_report(service, key) for key in _artifact_runs(kind, source_id, inputs)]
    body = {'identity': identity, 'published_policy': analytics['reproduction']['policy'] if core else None, 'core': core, 'inputs': inputs, 'expected': expected, 'history': history,
            'model': _model_evidence(analytics), 'discovery': analytics.get('discovery'), 'missing': pending}
    archive = _json(body)
    package = {'schema_version': VERSION, 'run_id': reference, 'kind': kind, 'archive': archive,
               'archive_digest': _sha(archive.encode()), 'product_coverage': _coverage(kind, body),
               'missing_dependencies': pending}
    package['package_id'] = _sha(_json(package).encode())
    validate(package)
    return package


def _check_identity(value):
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'source_digest', 'dependencies', 'engine_version', 'strategy_version'}
            or value['schema_version'] != 'research-execution.v1' or not re.fullmatch('[0-9a-f]{64}', value['source_digest'])
            or not isinstance(value['dependencies'], dict) or set(value['dependencies']) != {'python', 'numpy', 'pandas'}
            or any(not isinstance(v, str) or len(v) > 100 for v in (*value['dependencies'].values(), value['engine_version'], value['strategy_version']))):
        raise ValueError('冻结的执行版本清单无效。')


def _validate(package):
    from .reproduction import validate_package

    if (set(package) != _FIELDS or package['schema_version'] != VERSION or package['kind'] not in GROUPS
            or not isinstance(package['archive'], str) or len(package['archive']) > MAX_BYTES):
        raise ValueError('工作流包版本或字段不受支持。')
    encoded = _json({k: v for k, v in package.items() if k != 'package_id'}).encode()
    if (len(encoded) + 80 > MAX_BYTES or _sha(encoded) != package['package_id']
            or _sha(package['archive'].encode()) != package['archive_digest']):
        raise ValueError('工作流包大小或内容摘要无效。')
    body = json.loads(package['archive'])
    if set(body) != {'identity', 'published_policy', 'core', 'inputs', 'expected', 'history', 'model', 'discovery', 'missing'} or _json(body) != package['archive']:
        raise ValueError('归档内容不是受支持的规范 JSON。')
    kind, inputs, expected = package['kind'], body['inputs'], body['expected']
    _check_identity(body['identity'])
    _public({k: v for k, v in body.items() if k != 'history'})
    if not isinstance(body['missing'], list) or any(not re.fullmatch('[A-Z][A-Z0-9_]{0,79}', reason) for reason in body['missing']):
        raise ValueError('缺依赖声明无效。')
    if kind in _PAIRS:
        if body['core'] is not None or body['published_policy'] is not None or not inputs or inputs['kind'] != kind or inputs['pair_id'] != package['run_id']:
            raise ValueError('学习对照包身份不一致。')
        portable_learning.validate_pair(inputs)
        identity = inputs['source']['runtime'] if kind == 'strategy_pair' else inputs['binding']['execution_identity']
        source_id = inputs['run_id']
    else:
        core = validate_package(body['core'], legacy_only=True)
        identity, source_id = core['identity'], core['run_id']
        if source_id != package['run_id'] or body['published_policy'] != core['policy']:
            raise ValueError('归档与普通研究冻结输入不一致。')
        if core['snapshot'] is not None:
            from pandas import Timestamp

            from .snapshot_tools import SnapshotResearchTools
            boundary = SnapshotResearchTools._boundary(core['as_of'])
            cutoff = core['publication_before']
            for row in core['snapshot']['observations']:
                observed, available = Timestamp(row['timestamp']), Timestamp(row['available_at'])
                if (available.tzinfo is None or observed.tzinfo is None or available > boundary or observed > boundary
                        or cutoff is not None and available >= Timestamp(cutoff)):
                    raise ValueError('研究包价格晚于发表边界。')
        specialist = set(core['product_coverage']) & {'strategy_validation', 'strategy_study', 'factor_study'}
        if specialist != ({kind} if kind != 'research' else set()):
            raise ValueError('研究类型与原发表产物不符。')
        if bool(body['model'] is not None) != ('model_text' in core['product_coverage']) or bool(body['discovery'] is not None) != ('discovery' in core['product_coverage']):
            raise ValueError('历史证据与研究清单不符。')
        if inputs is not None:
            if kind == 'research':
                raise ValueError('普通研究包附加了未支持的输入。')
            _adapter(kind).validate_run(inputs, kind)
            if kind == 'factor_study':
                receipt = inputs['receipt']
                if (receipt['test_run'] != source_id or receipt['snapshot_ref'] != core['original_dataset']
                        or receipt['as_of'] != core['as_of'] or core['instrument'] not in inputs['data']['instruments']
                        or receipt['policy_fingerprint'] != stable_digest(body['published_policy'])):
                    raise ValueError('因子测试与原研究未使用同一发表记录和数据。')
            else:
                source = inputs['source']
                publication = inputs['publication'] if kind == 'strategy_validation' else source['publication']
                if (publication['run_id'] != source_id or source['data']['original_snapshot'] != core['original_dataset']
                        or source['runtime'] != identity or source['policy'] != core['policy']):
                    raise ValueError('策略及验证材料与原研究发表记录不一致。')
    if identity != body['identity'] or set(expected) != (set(GROUPS[kind]) if inputs is not None else set()):
        raise ValueError('产物或执行身份与冻结材料不一致。')
    lacks_core = body['core'] is not None and body['core']['snapshot'] is None
    misses_baseline = bool(set(body['missing']) & {'NORMALIZED_DATA', 'BASELINE_SAMPLE_SIZE'})
    misses_workflow = bool(set(body['missing']) - {'NORMALIZED_DATA', 'BASELINE_SAMPLE_SIZE'})
    if lacks_core != misses_baseline or misses_workflow != (bool(GROUPS[kind]) and inputs is None):
        raise ValueError('缺依赖原因与冻结产物不一致。')
    ids = _artifact_runs(kind, source_id, inputs)
    if not isinstance(body['history'], list) or len(body['history']) != len(ids):
        raise ValueError('历史报告清单不完整。')
    for artifact in body['history']:
        _check_report(artifact)
    validate_history(body['history'], kind, source_id, inputs)
    if sorted(a['name'] for a in body['history']) != sorted(i + '.html' for i in ids):
        raise ValueError('历史报告身份无效。')
    if package['product_coverage'] != _coverage(kind, body) or package['missing_dependencies'] != body['missing']:
        raise ValueError('复现范围与冻结材料不一致。')
    return body


def validate(package):
    try:
        return _validate(package)
    except (KeyError, IndexError, TypeError, AttributeError, OverflowError, RecursionError) as exc:
        raise ValueError('工作流包的冻结材料结构无效。') from exc


def replay(service, package):
    from .reproduction import _policy_supported, compute_baseline

    body = json.loads(package['archive'])
    kind, inputs = package['kind'], body['inputs']
    current = execution_identity(local_research_policy())
    reasons = list(package['missing_dependencies'])
    expected_identity = body['identity']
    mismatches = {key: {'expected': value, 'actual': current[key]} for key, value in expected_identity.items()
                  if current[key] != value}
    codes = {'source_digest': 'CODE_MISMATCH', 'dependencies': 'DEPENDENCY_MISMATCH',
             'strategy_version': 'STRATEGY_VERSION_MISMATCH', 'engine_version': 'ENGINE_VERSION_MISMATCH'}
    reasons.extend(codes[key] for key in mismatches if key in codes)
    rows = {key: {'status': 'ARCHIVED' if status == 'ARCHIVED' else 'BLOCKED'}
            for key, status in package['product_coverage'].items()}
    def compare(expected, compute):
        try:
            actual = json.loads(_json(compute()))
            for key, recorded in expected.items():
                rows[key] = compare_product(recorded, actual[key])
        except PortableUnavailable as exc:
            reasons.append(str(exc))
        except (ValueError, TypeError, KeyError, ArithmeticError):
            reasons.append('FROZEN_INPUT_UNSUPPORTED')
    if not mismatches:
        core = body['core']
        if core is not None and core['snapshot'] is not None:
            if _policy_supported(core['policy']):
                compare(core['expected'], lambda: compute_baseline(core))
            else:
                reasons.append('POLICY_UNSUPPORTED')
        if inputs is not None:
            if kind in _PAIRS:
                compare(body['expected'], lambda: portable_learning.replay_pair(service, inputs))
            else:
                compare(body['expected'], lambda: _adapter(kind).replay_run(service, inputs, kind))
    states = {row['status'] for row in rows.values()}
    status = ('DIFFERENT' if 'DIFFERENT' in states else 'BLOCKED' if 'REPRODUCED' not in states else
              'PARTIAL' if 'BLOCKED' in states else 'REPRODUCED')
    return {'package_id': package['package_id'], 'status': status, 'products': rows,
            'missing_dependencies': list(dict.fromkeys(reasons)), 'runtime_identity': current,
            'version_differences': mismatches, 'model_called': False, 'paper_only': True,
            'integrity': 'CONTENT_ONLY'}


def history(package):
    body = json.loads(package['archive'])
    inputs = body['inputs'] or {}
    evidence = {key: value for key, value in [('model_text', body['model']), ('discovery', body['discovery'])]
                if value is not None}
    kind = package['kind']
    if kind == 'factor_study' and inputs.get('review'):
        evidence['factor_review'] = inputs['review']
    elif kind == 'strategy_pair':
        evidence['learning_approval'] = inputs['decision']
    elif kind == 'research_pair':
        evidence['learning_approval'] = inputs['review']
    return {'artifacts': body['history'], 'evidence': evidence}

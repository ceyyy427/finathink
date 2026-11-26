"""Scoped, inert JSON research packages and deterministic local recomputation."""
from __future__ import annotations

import hashlib
import json
import math
import re

from finahinking.p8_2.contracts import DatasetSnapshot, MarketObservation

from .contracts import stable_digest
from .reports import _sanitize_public
from .research_identity import execution_identity
from .snapshot_tools import SnapshotResearchTools, local_research_policy

MAX_PACKAGE_BYTES = 8 * 1024 * 1024
_FIELDS = {'schema_version', 'run_id', 'original_dataset', 'as_of', 'instrument', 'publication_before',
           'identity', 'policy', 'snapshot', 'snapshot_digest', 'expected', 'product_coverage',
           'missing_dependencies', 'package_id'}
_WORKFLOW_DEPENDENCIES = {
    "factor_study": "FACTOR_WORKFLOW_DEPENDENCIES",
    "strategy_study": "STRATEGY_WORKFLOW_DEPENDENCIES",
    "strategy_validation": "STRATEGY_VALIDATION_WORKFLOW_DEPENDENCIES",
    "model_text": "MODEL_MATERIALS_AND_AUTHORIZATION",
    "discovery": "DISCOVERY_BLUEPRINT_DEPENDENCIES",
}
_ROW = {'instrument', 'timestamp', 'available_at', 'open', 'high', 'low', 'close', 'volume', 'amount', 'industry', 'suspended'}
_IDENTITY_KEYS = {'run_id', 'fingerprint', 'dataset_fingerprint', 'engine_dataset_fingerprint',
                  'policy_fingerprint', 'snapshot_id', 'evidence_refs', 'baseline_fingerprints',
                  'snapshot_digest', 'portfolio_digest', 'factor_digest', 'exposure_digest', 'stress_digest',
                  'risk_digest', 'proposal_digest'}


def _canonical_numbers(value):
    # JSON has one number type: browser roundtrips turn 1.0 into 1.
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _canonical_numbers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_numbers(item) for item in value]
    return value


def _digest(value):
    return stable_digest(_canonical_numbers(value))


def _encode(value, limit=MAX_PACKAGE_BYTES):
    try:
        encoded = json.dumps(_canonical_numbers(value), sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError('研究包必须是有限、非递归的 JSON 数据。') from exc
    if len(encoded.encode()) > limit:
        raise ValueError(f'研究包超过 {limit // (1024 * 1024)} MiB，请缩小研究样本。')
    return encoded


def _ref(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        raise ValueError('研究包标识无效。')
    return value


def product_values(value):
    """Drop provenance identities only; preserve all other computed values."""
    if isinstance(value, dict):
        return {key: product_values(item) for key, item in value.items() if key not in _IDENTITY_KEYS}
    if isinstance(value, (list, tuple)):
        return [product_values(item) for item in value]
    return value


def products(analytics):
    baseline = {key: analytics[key] for key in ('metrics', 'backtest', 'risk_portfolio') if key in analytics}
    result = {'baseline': product_values(baseline)}
    if analytics.get('quant_evidence'):
        result['quant_evidence'] = product_values(analytics['quant_evidence'])
    return result


def _portable_snapshot(snapshot, cutoff, *, panel_scope=None, as_of=None):
    import pandas as pd
    if panel_scope is not None:
        rows = SnapshotResearchTools.panel_observations(snapshot, panel_scope, cutoff)
    else:
        rows = [row for row in snapshot.observations if cutoff is None or row.timestamp < pd.Timestamp(cutoff)]
    if as_of is not None:
        boundary = SnapshotResearchTools._boundary(as_of)
        rows = [row for row in rows if row.available_at_time(boundary)
                and (cutoff is None or row.available_at < pd.Timestamp(cutoff))]
    if not 4 <= len(rows) <= 5000:
        if as_of is not None:
            from .portable_inputs import PortableUnavailable
            raise PortableUnavailable('BASELINE_SAMPLE_SIZE')
        raise ValueError('迁移复算需要 4 至 5000 条已发表观测。')
    provenance = snapshot.provenance
    classes = provenance.get('asset_classes', {})
    if panel_scope is not None:
        classes = {asset: classes[asset] for asset in panel_scope["instrument_ids"] if asset in classes}
    result = {'mode': snapshot.mode, 'units': provenance.get('units', {}),
              'asset_classes': classes,
              'observations': [{key: value for key, value in row.to_dict().items() if key in _ROW} for row in rows]}
    _sanitize_public(result)
    result['fingerprint'] = _digest(result)
    return result


def _snapshot(value):
    if not isinstance(value, dict) or set(value) != {'mode', 'units', 'asset_classes', 'observations', 'fingerprint'}:
        raise ValueError('研究包数据结构无效。')
    if _digest({key: item for key, item in value.items() if key != 'fingerprint'}) != value['fingerprint']:
        raise ValueError('研究包数据摘要不匹配。')
    rows = value['observations']
    if not isinstance(rows, list) or not 4 <= len(rows) <= 5000 or any(not isinstance(row, dict) or set(row) - _ROW for row in rows):
        raise ValueError('研究包观测超出范围。')
    if not isinstance(value['units'], dict) or set(value['units']) - {'price', 'cash', 'volume', 'price_currency', 'volume_unit'} or not isinstance(value['asset_classes'], dict):
        raise ValueError('研究包单位声明无效。')
    try:
        result = DatasetSnapshot('portable-research', tuple(MarketObservation(**row, source='portable', provider='portable') for row in rows),
            mode=value['mode'], provenance={'units': value['units'], 'asset_classes': value['asset_classes']})
    except OverflowError as exc:
        raise ValueError('研究包数值超出可计算范围。') from exc
    if len(result.instruments) > 20 or set(value['asset_classes']) - set(result.instruments):
        raise ValueError('研究包资产声明超出范围。')
    return result


def _policy_supported(policy):
    base = local_research_policy()
    mutable = {'version', 'max_cvar', 'signal_budget_scale', 'learning_policy_ref'}
    if not isinstance(policy, dict) or not set(base) <= set(policy) or set(policy) - (set(base) | mutable):
        return False
    if any(policy[key] != value or isinstance(policy[key], bool) != isinstance(value, bool) for key, value in base.items() if key not in mutable):
        return False
    if not isinstance(policy['version'], str) or not re.fullmatch(r'local-paper\.v3(?:\.learned-[0-9]{1,9})?', policy['version']):
        return False
    for key, low, high in [('max_cvar', .01, .05), ('signal_budget_scale', .1, 1)]:
        value = policy.get(key, 1)
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            return False
    return 'learning_policy_ref' not in policy or bool(re.fullmatch('[a-f0-9]{64}', str(policy['learning_policy_ref'])))


def validate_package(package, *, legacy_only=False):
    if not legacy_only and isinstance(package, dict) and package.get('schema_version') == 'research-package.v2':
        from .portable_package import validate
        validate(package)
        return package
    encoded = _encode(package)
    package = json.loads(encoded)
    if not isinstance(package, dict) or set(package) != _FIELDS or package['schema_version'] != 'research-package.v1':
        raise ValueError('研究包版本或字段不受支持。')
    _sanitize_public(package)
    if _ref(package['package_id']) != _digest({key: item for key, item in package.items() if key != 'package_id'}):
        raise ValueError('研究包内容被修改，摘要校验失败。')
    _ref(package['original_dataset'])
    if package['snapshot_digest'] is not None:
        _ref(package['snapshot_digest'])
    if not isinstance(package['run_id'], str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', package['run_id']):
        raise ValueError('原研究标识无效。')
    SnapshotResearchTools._boundary(package['as_of'])
    if not isinstance(package['expected'], dict) or set(package['expected']) - {'baseline', 'quant_evidence'} or 'baseline' not in package['expected']:
        raise ValueError('确定性产物清单无效。')
    coverage = package['product_coverage']
    if not isinstance(coverage, dict) or set(coverage) - {"baseline", "quant_evidence", *_WORKFLOW_DEPENDENCIES} or any(coverage.get(key) != 'INCLUDED' for key in package['expected']):
        raise ValueError('复现覆盖清单无效。')
    if any(status != ('INCLUDED' if key in {'baseline', 'quant_evidence'} else 'REQUIRES_ORIGINAL_WORKFLOW') for key, status in coverage.items()) or {key for key, status in coverage.items() if status == 'INCLUDED'} != set(package['expected']):
        raise ValueError('复现覆盖清单须注明已携带的计算产物以及依赖原工作流的产物。')
    expected_missing = ([] if package['snapshot'] is not None else ['NORMALIZED_DATA'])
    expected_missing.extend(code for key, code in _WORKFLOW_DEPENDENCIES.items() if key in coverage)
    if package['missing_dependencies'] != expected_missing:
        raise ValueError('缺依赖清单与复现覆盖不一致。')
    if package['snapshot'] is not None:
        snapshot = _snapshot(package['snapshot'])
        if package['snapshot']['fingerprint'] != package['snapshot_digest'] or package['instrument'] not in snapshot.instruments:
            raise ValueError('数据与研究对象绑定不一致。')
        if package['publication_before'] is not None:
            import pandas as pd
            cutoff = pd.Timestamp(package['publication_before'])
            if pd.isna(cutoff) or cutoff.tzinfo is None or any(row.timestamp >= cutoff for row in snapshot.observations):
                raise ValueError('迁移数据超出原发表边界。')
    identity = package['identity']
    if not isinstance(identity, dict) or set(identity) != {'schema_version', 'source_digest', 'dependencies', 'engine_version', 'strategy_version'} or identity['schema_version'] != 'research-execution.v1':
        raise ValueError('执行身份无效。')
    _ref(identity['source_digest'])
    if not isinstance(identity['dependencies'], dict) or set(identity['dependencies']) != {'python', 'numpy', 'pandas'} or any(not isinstance(v, str) or len(v) > 64 for v in identity['dependencies'].values()):
        raise ValueError('依赖版本清单无效。')
    return package


class ReproductionService:
    def __init__(self, service):
        self.service = service
        with service.queue._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS research_packages (scope_id TEXT NOT NULL, package_id TEXT NOT NULL, payload TEXT NOT NULL, receipt TEXT, receipt_digest TEXT, PRIMARY KEY(scope_id,package_id))')

    def source(self, run_id):
        entry = self.service.runs.get_run(run_id)
        if entry['report_status'] != 'AVAILABLE':
            raise ValueError('原报告缺失或校验失败，请先恢复报告。')
        relative = '4_quant/analytics.json'
        manifest = entry['manifest']
        expected = manifest['files'].get(relative)
        if not expected:
            raise ValueError('该报告没有可复算的量化产物。')
        path = entry['bundle'] / relative
        if path.is_symlink():
            raise ValueError('量化产物不能为符号链接。')
        with path.open('rb') as handle:
            raw = handle.read(MAX_PACKAGE_BYTES + 1)
        if len(raw) > MAX_PACKAGE_BYTES or hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('量化产物校验失败。')
        return entry, json.loads(raw)

    def export(self, run_id=None, include_data=False, *, data_scope=None, pair_id=None):
        if data_scope is None and pair_id is None and run_id is not None:
            return self.export_legacy(run_id, include_data)
        if data_scope != 'workflow' or include_data is not True or (run_id is None) == (pair_id is None):
            raise ValueError('请选择一项研究或学习对照并明确同意导出已发表的工作流数据。')
        from .portable_package import freeze
        return freeze(self, run_id, pair_id)

    def export_legacy(self, run_id, include_data=False, *, published_only=False):
        if type(include_data) is not bool:
            raise ValueError('请明确选择是否携带标准化数据。')
        entry, analytics = self.source(run_id)
        frozen = analytics.get('reproduction')
        if not frozen or frozen != entry['manifest']['source_snapshot'].get('reproduction'):
            raise ValueError('旧报告缺少冻结的执行身份，请从原输入创建新研究。')
        compact = None
        if include_data:
            snapshot = self.service.snapshots.load(analytics['dataset_fingerprint'])
            compact = _portable_snapshot(snapshot, frozen['publication_before'], panel_scope=frozen.get("panel_scope"),
                                         as_of=analytics["as_of"] if published_only else None)
        expected = products(analytics)
        coverage = {key: 'INCLUDED' for key in expected}
        missing = [] if include_data else ['NORMALIZED_DATA']
        for key, code in _WORKFLOW_DEPENDENCIES.items():
            if analytics.get('model_research' if key == 'model_text' else key):
                coverage[key] = 'REQUIRES_ORIGINAL_WORKFLOW'
                missing.append(code)
        package = {'schema_version': 'research-package.v1', 'run_id': run_id,
            'original_dataset': analytics['dataset_fingerprint'], 'as_of': analytics['as_of'], 'instrument': analytics['instrument'],
            'publication_before': frozen['publication_before'], 'identity': frozen['identity'], 'policy': frozen['policy'],
            'snapshot': compact, 'snapshot_digest': compact['fingerprint'] if compact is not None else None,
            'expected': expected, 'product_coverage': coverage, 'missing_dependencies': missing}
        package['package_id'] = _digest(package)
        return validate_package(package)

    def restore(self, package):
        package = validate_package(package)
        with self.service.queue._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            found = db.execute('SELECT 1 FROM research_packages WHERE scope_id=? AND package_id=?', (self.service.scope_id, package['package_id'])).fetchone()
            if not found:
                if db.execute('SELECT count(*) FROM research_packages WHERE scope_id=?', (self.service.scope_id,)).fetchone()[0] >= 20:
                    raise ValueError('当前工作区已有 20 个迁移包，请在独立工作区继续。')
                db.execute('INSERT INTO research_packages(scope_id,package_id,payload) VALUES(?,?,?)', (self.service.scope_id, package['package_id'], _storage_json(package)))
        return self.get(package['package_id'])

    def _load(self, reference):
        with self.service.queue._connect() as db:
            row = db.execute('SELECT * FROM research_packages WHERE scope_id=? AND package_id=?', (self.service.scope_id, _ref(reference))).fetchone()
        if row is None:
            raise KeyError('研究包不在当前工作区。')
        package = validate_package(json.loads(row['payload']))
        if package['package_id'] != reference:
            raise ValueError('本地研究包身份校验失败。')
        receipt = json.loads(row['receipt']) if row['receipt'] else None
        if receipt is not None and (stable_digest(receipt) != row['receipt_digest'] or receipt.get('package_id') != reference):
            raise ValueError('本地复算记录校验失败。')
        return package, receipt

    def get(self, reference):
        package, receipt = self._load(reference)
        summary = {key: package[key] for key in ('package_id', 'run_id', 'schema_version', 'product_coverage', 'missing_dependencies')}
        workflow = package['schema_version'] == 'research-package.v2'
        return {**summary, 'receipt': receipt, 'data_included': workflow or package['snapshot'] is not None,
                'history_count': len(json.loads(package['archive'])['history']) if workflow else 0}

    def history(self, reference):
        package, _ = self._load(reference)
        from .portable_package import VERSION, history
        return history(package) if package['schema_version'] == VERSION else {'artifacts': [], 'evidence': {}}

    def list(self):
        with self.service.queue._connect() as db:
            rows = db.execute('SELECT package_id FROM research_packages WHERE scope_id=? ORDER BY rowid DESC LIMIT 20', (self.service.scope_id,)).fetchall()
        return [self.get(row['package_id']) for row in rows]

    def recompute(self, reference):
        package, saved = self._load(reference)
        if package['schema_version'] == 'research-package.v2':
            from .portable_package import replay
            return self._save_receipt(reference, replay(self.service, package))
        current = execution_identity(local_research_policy())
        if saved and saved.get('runtime_identity') == current:
            return saved
        missing = list(package['missing_dependencies'])
        if package['identity']['source_digest'] != current['source_digest']:
            missing.append('CODE_MISMATCH')
        if package['identity']['dependencies'] != current['dependencies']:
            missing.append('DEPENDENCY_MISMATCH')
        if not _policy_supported(package['policy']):
            missing.append('POLICY_UNSUPPORTED')
        receipt = {'package_id': reference, 'status': 'BLOCKED', 'products': {key: 'BLOCKED' if status == 'INCLUDED' else status for key, status in package['product_coverage'].items()}, 'missing_dependencies': missing,
                   'runtime_identity': current, 'numeric_products_only': True, 'integrity': 'CONTENT_ONLY',
                   'model_called': False, 'paper_only': True}
        blockers = set(missing) - set(_WORKFLOW_DEPENDENCIES.values())
        if not blockers:
            try:
                actual = compute_baseline(package)
                receipt['products'] = {key: 'MATCHED' if _digest(actual.get(key)) == _digest(value) else 'DIFFERENT' for key, value in package['expected'].items()}
                receipt['products'].update({key: value for key, value in package['product_coverage'].items() if value != 'INCLUDED'})
                receipt['computed_digests'] = {key: _digest(actual.get(key)) for key in package['expected']}
                receipt['status'] = 'DIFFERENT' if 'DIFFERENT' in receipt['products'].values() else 'PARTIAL' if missing else 'MATCHED'
            except (ValueError, TypeError, KeyError, ArithmeticError):
                receipt['missing_dependencies'].append('INPUT_UNSUPPORTED')
        return self._save_receipt(reference, receipt)

    def _save_receipt(self, reference, receipt):
        encoded = json.dumps(receipt, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        with self.service.queue._connect() as db:
            db.execute('UPDATE research_packages SET receipt=?,receipt_digest=? WHERE scope_id=? AND package_id=?',
                       (encoded, stable_digest(receipt), self.service.scope_id, reference))
        return receipt


def _storage_json(package):
    from .portable_package import MAX_BYTES, VERSION
    return _encode(package, MAX_BYTES if package['schema_version'] == VERSION else MAX_PACKAGE_BYTES)


def compute_baseline(package):
    snapshot = _snapshot(package['snapshot'])
    tools = SnapshotResearchTools(snapshot, policy=package['policy'], publication_before=package['publication_before'])
    tools.run_id = 'portable-recompute'
    arguments = {'dataset_fingerprint': snapshot.fingerprint, 'instrument': package['instrument'],
                 'as_of': package['as_of'], 'factor_ids': [package['policy']['factor_id']]}
    tools._backtest(arguments)
    tools._risk(arguments)
    return products(tools.analysis)

"""Cross-workspace public service contracts, drift, archived model evidence and privacy."""
import base64
import hashlib
import json

import pytest

from finahinking.research.application_service import ResearchApplicationService
from finahinking.research.portable_comparison import compare_product
from finahinking.research.portable_package import _json, _sha
from finahinking.research.reproduction import ReproductionService, _digest
from tests.research.grounded_worker_peer import install_peer
from tests.research.test_application_service import wait_for
from tests.research.test_grounded_application import configured
from tests.research.test_grounded_spawn import submit
from tests.research.test_reproduction import original

__all__ = ['configured', 'original']


def reseal(package, body):
    material = _json(body)
    updated = {**package, 'archive': material, 'archive_digest': _sha(material.encode())}
    updated.pop('package_id')
    return {**updated, 'package_id': _sha(_json(updated).encode())}


def reseal_core(core):
    core['package_id'] = _digest({k: v for k, v in core.items() if k != 'package_id'})


def test_bounds_and_numeric_comparison():
    expected = {'count': 10000000000, 'value': [4.0, False], 'zero': None}
    within = {**expected, 'value': [4.0 + 1e-10, False]}
    assert compare_product(expected, within)['status'] == 'REPRODUCED'
    changed = {'count': 10000000001, 'value': [4.0 + 0.1, 0]}
    row = compare_product(expected, changed)
    assert row['status'] == 'DIFFERENT' and row['difference_count'] == row['fields_checked'] == 4
    assert [item['path'] for item in row['differences']] == ['/count', '/value/0', '/value/1', '/zero']


def test_published_package_drifts_versions_and_byte_integrity(original, tmp_path):
    service, run_id = original
    source = ReproductionService(service)
    package = source.export(run_id, include_data=True, data_scope='workflow')
    target = ResearchApplicationService(tmp_path/'clean', allow_model_calls=False)
    try:
        dest = ReproductionService(target)
        with pytest.raises(ValueError):
            source.export(run_id, data_scope='workflow')
        ref = dest.restore(json.loads(json.dumps(package)))['package_id']
        assert dest.recompute(ref)['status'] == 'REPRODUCED'
        html = base64.b64decode(dest.history(ref)['artifacts'][0]['content_base64'])
        assert html == (service.runs.get_run(run_id)['bundle'] / 'complete_report.html').read_bytes()
        assert target.list_jobs()['total'] == 0
        body = json.loads(package['archive'])
        core = body['core']
        core['expected']['baseline']['metrics']['total_return'] = 17.125
        reseal_core(core)
        diff = dest.recompute(dest.restore(reseal(package, body))['package_id'])
        assert diff['status'] == 'DIFFERENT' and diff['products']['quant_evidence']['status'] == 'REPRODUCED'
        metric, = diff['products']['baseline']['differences']
        assert metric['path'] == '/metrics/total_return' and metric['expected'] == 17.125
        body['identity']['dependencies']['numpy'] = 'other'
        core['identity'] = body['identity']
        reseal_core(core)
        blocked = dest.recompute(dest.restore(reseal(package, body))['package_id'])
        assert blocked['status'] == 'BLOCKED' and blocked['missing_dependencies'] == ['DEPENDENCY_MISMATCH']
        assert blocked['version_differences']['dependencies']['expected']['numpy'] == 'other'
        body['history'][0]['sha256'] = '0'*64
        with pytest.raises(ValueError):
            dest.restore(reseal(package, body))
        for private in ('/private/local/file', 'https://data.invalid/private', 'api_key=private-value'):
            raw = ('<html>' + private + '</html>').encode()
            body['history'][0].update(content_base64=base64.b64encode(raw).decode(), sha256=hashlib.sha256(raw).hexdigest())
            with pytest.raises(ValueError):
                dest.restore(reseal(package, body))
        assert len(dest.list()) == 3
    finally:
        target.close()


def test_external_model_calls_are_archived_without_authorizing_target(configured, tmp_path):
    origin, _ = configured
    install_peer(origin)
    job = submit(origin)
    origin.start()
    finished = wait_for(origin, job['job_id'])
    assert finished['status'] == 'completed'
    package = ReproductionService(origin).export(finished['run_id'], include_data=True, data_scope='workflow')
    target = ResearchApplicationService(tmp_path/'offline', allow_model_calls=False)
    try:
        dest = ReproductionService(target)
        ref = dest.restore(package)['package_id']
        receipt = dest.recompute(ref)
        assert receipt['status'] == 'REPRODUCED' and receipt['model_called'] is False
        assert receipt['products']['model_text'] == {'status': 'ARCHIVED'}
        model = dest.history(ref)['evidence']['model_text']
        assert model['calls'] == finished['model_research']['calls'] and len(model['calls']) == 3
        assert model['synthesis'] == finished['model_research']['synthesis']
        assert 'models.acme.com' not in package['archive'] and target.list_jobs()['total'] == 0
    finally:
        target.close()


def test_missing_source_snapshot_preserves_history_but_blocks_computation(original, tmp_path):
    service, run_id = original
    source = ReproductionService(service)
    snapshot_ref = source.source(run_id)[1]['dataset_fingerprint']
    service.snapshots._path(snapshot_ref).unlink()
    package = source.export(run_id, include_data=True, data_scope='workflow')
    target = ResearchApplicationService(tmp_path/'clean', allow_model_calls=False)
    try:
        dest = ReproductionService(target)
        restored = dest.restore(package)
        receipt = dest.recompute(restored['package_id'])
        assert restored['history_count'] == 1 and receipt['status'] == 'BLOCKED'
        assert receipt['missing_dependencies'] == ['NORMALIZED_DATA']
        assert receipt['products']['baseline'] == {'status': 'BLOCKED'}
        assert len(dest.history(restored['package_id'])['artifacts']) == 1
    finally:
        target.close()

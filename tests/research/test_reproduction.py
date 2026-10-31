"""Move an actual spawned run to an empty root and recompute without providers."""
import copy
import importlib.util
import json

import pytest

from finahinking.research.application_service import ResearchApplicationService
from finahinking.research.contracts import stable_digest
from tests.research.test_application_service import payload, wait_for
from tests.research.test_quant_evidence import sample


def reproduction(service):
    assert importlib.util.find_spec('finahinking.research.reproduction'), 'portable research service missing'
    from finahinking.research.reproduction import ReproductionService
    return ReproductionService(service)


@pytest.fixture
def original(tmp_path):
    service = ResearchApplicationService(tmp_path / 'original', allow_model_calls=False)
    service.start()
    job = wait_for(service, service.submit(payload(service, snapshot=sample()))['job_id'])
    assert job['status'] == 'completed'
    try:
        yield service, job['run_id']
    finally:
        service.close()


def test_export_restore_recompute_new_root_and_restart(original, tmp_path):
    service, run_id = original
    exporter = reproduction(service)
    metadata = exporter.export(run_id)
    assert metadata['snapshot'] is None
    assert 'NORMALIZED_DATA' in metadata['missing_dependencies']
    package = exporter.export(run_id, include_data=True)
    assert package['original_dataset'] != package['snapshot']['fingerprint']
    assert package['product_coverage']['baseline'] == 'INCLUDED'
    encoded = json.dumps(package)
    assert 'provider' not in encoded and 'credential' not in encoded and '/Users/' not in encoded
    fresh = ResearchApplicationService(tmp_path / 'fresh', allow_model_calls=False)
    try:
        restored = reproduction(fresh).restore(package)
        assert reproduction(fresh).restore(package)['package_id'] == restored['package_id']
        result = reproduction(fresh).recompute(restored['package_id'])
        assert result['status'] == 'MATCHED', result
        assert result['products'] == {'baseline': 'MATCHED', 'quant_evidence': 'MATCHED'}
        assert fresh.list_jobs()['total'] == 0
        assert reproduction(fresh).get(restored['package_id'])['receipt'] == result
    finally:
        fresh.close()
    reopened = ResearchApplicationService(tmp_path / 'fresh', allow_model_calls=False)
    try:
        assert reproduction(reopened).recompute(package['package_id']) == result
    finally:
        reopened.close()


def test_metadata_missing_code_or_dependency_never_silently_recomputes(original, tmp_path):
    service, run_id = original
    target = ResearchApplicationService(tmp_path / 'target', allow_model_calls=False)
    try:
        importer = reproduction(target)
        meta = reproduction(service).export(run_id)
        saved = importer.restore(meta)
        assert importer.recompute(saved['package_id'])['status'] == 'BLOCKED'
        for field, code in [('source_digest', 'CODE_MISMATCH'), ('dependencies', 'DEPENDENCY_MISMATCH')]:
            package = reproduction(service).export(run_id, include_data=True)
            package['identity'][field] = '0'*64 if field == 'source_digest' else {**package['identity']['dependencies'], 'numpy': '0.0.0'}
            package['package_id'] = stable_digest({k: v for k, v in package.items() if k != 'package_id'})
            importer.restore(package)
            receipt = importer.recompute(package['package_id'])
            assert receipt['status'] == 'BLOCKED' and code in receipt['missing_dependencies']
    finally:
        target.close()


def test_tampering_unsafe_fields_and_cross_scope_are_rejected(original, tmp_path):
    service, run_id = original
    exporter = reproduction(service)
    package = exporter.export(run_id, include_data=True)
    changed = copy.deepcopy(package)
    changed['expected']['baseline']['metrics']['total_return'] = 9
    with pytest.raises(ValueError):
        exporter.restore(changed)
    for key, value in [('endpoint', 'https://example.test'), ('file_path', '/tmp/anything')]:
        changed = {**package, key: value}
        changed['package_id'] = stable_digest(sorted((k, v) for k, v in changed.items() if k != 'package_id'))
        with pytest.raises(ValueError):
            exporter.restore(changed)
    exporter.restore(package)
    other = ResearchApplicationService(service.root, scope_id='other', allow_model_calls=False)
    try:
        with pytest.raises(KeyError):
            reproduction(other).get(package['package_id'])
        with pytest.raises(KeyError):
            reproduction(other).export(run_id, include_data=True)
    finally:
        other.close()
    with pytest.raises(ValueError):
        exporter.export(run_id, include_data='true')
    with pytest.raises(ValueError):
        exporter.get('../anything')


def test_validation_export_never_leaks_heldout_rows(original):
    from finahinking.p8_2.data_sources import FixtureMarketDataSource
    from tests.research.test_factor_study_application import values, wait_study
    service, _ = original
    ref = service.snapshots.save(FixtureMarketDataSource(points=240).snapshot('AAA'))
    study = service.create_factor_study(values(ref))
    done = wait_study(service, study['study_id'], 'validation')
    package = reproduction(service).export(done['validation_job']['run_id'], include_data=True)
    cutoff = package['publication_before']
    assert cutoff
    assert all(row['timestamp'] < cutoff for row in package['snapshot']['observations'])
    assert 'FACTOR_WORKFLOW_DEPENDENCIES' in package['missing_dependencies']
    saved = reproduction(service).restore(package)
    result = reproduction(service).recompute(saved['package_id'])
    assert result['status'] == 'PARTIAL' and result['products']['baseline'] == 'MATCHED'
    assert result['products']['factor_study'] == 'REQUIRES_ORIGINAL_WORKFLOW'


def test_oversize_nested_data_unsupported_policy_and_stored_receipt_tamper(original):
    service, run_id = original
    manager = reproduction(service)
    package = manager.export(run_id, include_data=True)
    for field, value in [('surprise', True), ('snapshot', {'observations': 'x'*(8*1024*1024)})]:
        with pytest.raises(ValueError):
            manager.restore({**package, field: value})
    changed = copy.deepcopy(package)
    changed['policy']['risk_lookback'] = 1000000000
    changed['package_id'] = stable_digest({k: v for k, v in changed.items() if k != 'package_id'})
    manager.restore(changed)
    assert 'POLICY_UNSUPPORTED' in manager.recompute(changed['package_id'])['missing_dependencies']
    saved = manager.restore(package)
    manager.recompute(saved['package_id'])
    with service.queue._connect() as db:
        db.execute('UPDATE research_packages SET receipt=? WHERE scope_id=?', ('{}', service.scope_id))
    with pytest.raises(ValueError):
        manager.get(saved['package_id'])

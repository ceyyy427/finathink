"""Receipt-bound pagination through the actual local HTTP and persistence paths."""
import json

import pytest

from finahinking.research import reproduction
from finahinking.research.application_service import ResearchApplicationService
from finahinking.research.contracts import stable_digest
from finahinking.research.portable_difference_pages import PAGE_SIZE
from finahinking.research.reproduction import ReproductionService
from tests.research.test_phase18_api import app_http, post
from tests.research.test_phase19_api import BASE, completed
from tests.research.test_portable_package import reseal, reseal_core

__all__ = ['app_http']


def test_all_differences_survive_reload_are_scoped_and_are_replaced_atomically(app_http, monkeypatch):
    app, http, _ = app_http
    service = app.research_service
    manager = ReproductionService(service)
    run_id = completed(app, 'paged-differences')
    package = manager.export(run_id, include_data=True, data_scope='workflow')
    body = json.loads(package['archive'])
    total = 2 * PAGE_SIZE + 19
    body['core']['expected']['baseline']['metrics'].update({f'changed_{i:03}': i for i in range(total)})
    reseal_core(body['core'])
    package = reseal(package, body)
    reference = manager.restore(package)['package_id']
    route = f'{BASE}/{reference}/differences?product=baseline&page='
    assert http(route + '0')[0] == 422
    assert http(route + '-1')[0] == 422

    receipt = manager.recompute(reference)
    pinned = '&receipt_id=' + receipt['receipt_id']
    baseline = receipt['products']['baseline']
    assert baseline['full_differences_available'] and baseline['difference_count'] == total
    assert len(baseline['differences']) == 24
    # A pre-pagination receipt remains readable and requires an explicit rerun for its missing pages.
    old = json.loads(json.dumps(receipt))
    del old['receipt_id']
    for row in old['products'].values():
        row.pop('full_differences_available', None)
        row.pop('difference_pages_digest', None)
    with service.queue._connect() as db:
        db.execute('UPDATE research_packages SET receipt=?,receipt_digest=? WHERE scope_id=? AND package_id=?',
                   (json.dumps(old), stable_digest(old), service.scope_id, reference))
    assert http(f'{BASE}/{reference}')[1]['receipt'] == old
    assert http(route + '0')[0] == 422
    assert manager.recompute(reference) == receipt

    other = ResearchApplicationService(service.root, scope_id='different-workspace', allow_model_calls=False)
    try:
        with pytest.raises(KeyError):
            ReproductionService(other).differences(reference, 'baseline', 0, receipt['receipt_id'])
        ReproductionService(other).restore(package)
        with pytest.raises(ValueError):
            ReproductionService(other).differences(reference, 'baseline', 0, receipt['receipt_id'])
    finally:
        other.close()

    def unexpected(*args):
        raise AssertionError('Reading a saved difference page requested a calculation')

    with monkeypatch.context() as read_only:
        read_only.setattr(reproduction, 'compute_baseline', unexpected)
        pages = [http(route + str(page) + pinned) for page in range(3)]
        assert all(status == 200 and result['page'] == n and result['page_size'] == PAGE_SIZE
                   and result['receipt_id'] == receipt['receipt_id'] and result['package_id'] == reference
                   and result['total'] == total and result['product'] == 'baseline'
                   for n, (status, result) in enumerate(pages))
        assert [result['has_more'] for _, result in pages] == [True, True, False]
        flattened = [entry for _, page in pages for entry in page['differences']]
        assert flattened[:24] == baseline['differences']
        assert flattened == [{'path': f'/metrics/changed_{i:03}', 'expected': i, 'actual': {'present': False}}
                             for i in range(total)]
        assert http(route + '3' + pinned)[0] == 422
        assert http(route + '0&receipt_id=' + '0'*64)[0] == 422
        assert http(route + '0&page=1')[0] == 422
        assert http(route.replace('baseline', 'unknown') + '0')[0] == 422

    # Corrupted payload and index digests are independently checked against the durable receipt.
    with service.queue._connect() as db:
        db.execute("UPDATE research_difference_pages SET payload='[]' WHERE scope_id=? AND page=2", (service.scope_id,))
    assert http(route + '2' + pinned)[0] == 422
    with service.queue._connect() as db:
        db.execute("UPDATE research_difference_pages SET digest=? WHERE scope_id=? AND page=2", ('f'*64, service.scope_id))
    assert http(route + '0' + pinned)[0] == 422
    assert manager.recompute(reference) == receipt

    # A simulated error while publishing the replacement retains the exact old receipt and pages.
    monkeypatch.setattr(reproduction, 'compute_baseline', lambda core: core['expected'])
    with service.queue._connect() as db:
        db.execute("""CREATE TRIGGER reject_receipt BEFORE UPDATE OF receipt ON research_packages
            BEGIN SELECT RAISE(ABORT, 'simulated publication failure'); END""")
    assert post(app, http, f'{BASE}/{reference}/recompute', {})[0] == 503
    assert http(route + '2' + pinned) == pages[2]
    with service.queue._connect() as db:
        db.execute('DROP TRIGGER reject_receipt')
    assert service.list_jobs()['total'] == 1
    status, replacement = post(app, http, f'{BASE}/{reference}/recompute', {})
    assert status == 200 and replacement['status'] == 'REPRODUCED' and replacement['receipt_id'] != receipt['receipt_id']
    assert http(route + '0' + pinned)[0] == 422
    status, empty = http(route + '0&receipt_id=' + replacement['receipt_id'])
    assert status == 200 and empty['total'] == 0 and empty['differences'] == [] and not empty['has_more']
    assert http(route + '1')[0] == 422
    with service.queue._connect() as db:
        assert not db.execute('SELECT 1 FROM research_difference_pages WHERE scope_id=?', (service.scope_id,)).fetchone()

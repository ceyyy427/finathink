"""Real HTTP, immutable reload, and existing spawn boundary (no model/provider)."""
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from finahinking.data.research_fields import ResearchFieldStore
from finahinking.data.snapshot_store import SnapshotStore
from finahinking.local_app import LocalAppConfig, LocalApplication, create_server
from finahinking.research.contracts import stable_digest
from finahinking.research.panel_research import evaluate_panel_request
from tests.data.test_research_panel import fixture
from tests.research.test_spawn_worker import _collect, _invocation, _registry


def panel_stage(invocation):
    """Test runner uses production projection/evaluation, not a mocked worker."""
    import os
    from pathlib import Path
    root = Path(os.environ['FINATHINK_PANEL_TEST_ROOT'])
    request = json.loads((root / 'panel-input.json').read_text())
    result = evaluate_panel_request(SnapshotStore(root / 'snapshots'), request)
    (root / 'panel-output.json').write_text(json.dumps(result))
    return {'status': 'completed', 'result_ref': 'panel-output', 'artifact_digest': result['fingerprint']}


def request_for(store):
    snap, fields, contract, _ = fixture()
    store.save(snap)
    ResearchFieldStore(store).save(fields)
    return {'contract': contract.to_dict(), 'attachment_refs': [fields.fingerprint],
            'expressions':['ts_rank(close,3,3)','cs_rank(close,3)']}, fields


def test_real_http_panel_prepare_evaluate_and_errors(tmp_path):
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path/'app.sqlite3'), offline=True))
    payload, fields = request_for(app.research_service.snapshots)
    server = create_server(app, host='127.0.0.1', port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    def post(path, data, csrf=True):
        req = Request(base + path, data=json.dumps({**data, **({'_csrf':app.csrf_token} if csrf else {})}).encode(),
                      headers={'Content-Type':'application/json'}, method='POST')
        with urlopen(req, timeout=10) as response:
            return json.load(response)
    try:
        with pytest.raises(HTTPError) as error:
            post('/api/research/panels/evaluate', payload, csrf=False)
        assert error.value.code == 403
        stored = post('/api/research/panels/fields', {'attachment':fields.to_dict()})
        assert stored['attachment_ref'] == fields.fingerprint
        response = post('/api/research/panels/evaluate', payload)
        assert response == evaluate_panel_request(app.research_service.snapshots, payload)
        assert response['results'][0]['records'][-3]['value'] == 1
        assert response['results'][1]['records'][-3]['value'] == 2/3
        assert response['status'] == 'NEEDS_DATA' and not response['execution_permission']
        with pytest.raises(HTTPError) as error:
            post('/api/research/panels/evaluate', {**payload,'expressions':['eval(close)']})
        assert error.value.code in {400,422}
        assert app.research_service.list_jobs()['total'] == 0
        assert response['fingerprint'] == stable_digest({k:v for k,v in response.items() if k!='fingerprint'})
    finally:
        server.shutdown(); server.server_close(); thread.join(3); app.close()


def test_existing_spawn_rebuilds_panel_and_keeps_result_identity(tmp_path, monkeypatch):
    from finahinking.research.worker_entrypoint import SpawnWorkerHandle
    payload, _ = request_for(SnapshotStore(tmp_path/'snapshots'))
    (tmp_path/'panel-input.json').write_text(json.dumps(payload))
    monkeypatch.setenv('FINATHINK_PANEL_TEST_ROOT', str(tmp_path))
    invocation = _invocation('panel', timeout=20, max_result_bytes=65536)
    messages = _collect(SpawnWorkerHandle(invocation, registry=_registry('panel', panel_stage)))
    assert messages[-1].kind == 'RESULT', messages[-1]
    output = json.loads((tmp_path/'panel-output.json').read_text())
    assert output == evaluate_panel_request(SnapshotStore(tmp_path/'snapshots'), payload)


def test_review_result_readiness_includes_degenerate_operator(tmp_path):
    from finahinking.data.panel_contracts import PanelContract
    store = SnapshotStore(tmp_path/'snapshots')
    payload, _ = request_for(store)
    values = payload['contract'].copy(); values.pop('fingerprint'); values.pop('schema_version')
    values['field_units'] = {'close':'CNY', 'volume':'shares'}
    payload['contract'] = PanelContract(**values).to_dict()
    payload['expressions'] = ['cs_neutralize(close,volume,3)']
    result = evaluate_panel_request(store, payload)
    assert result['status'] == 'NEEDS_DATA'
    assert result['panel_status'] == 'READY'
    assert result['results'][0]['metadata']['diagnostics'] == ['DEGENERATE_EXPOSURE']
    assert all(row['value'] is None for row in result['results'][0]['records'])

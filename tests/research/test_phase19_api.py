"""Actual HTTP: explicit writes, scope checks and no hidden research calls."""
from tests.research.test_application_service import payload, wait_for
from tests.research.test_phase18_api import app_http, post
from tests.research.test_quant_evidence import sample

__all__ = ['app_http']
BASE = '/api/research/reproduction'


def completed(app, key='phase19'):
    service = app.research_service
    service.start()
    job = wait_for(service, service.submit(payload(service, key=key, snapshot=sample()))['job_id'])
    assert job['status'] == 'completed'
    return job['run_id']


def test_readonly_empty_and_explicit_export_restore_recompute(app_http):
    app, http, wire = app_http
    assert http(BASE) == (200, {'packages': []})
    assert http('/research/compare')[0] == 200
    assert http(BASE + '/export', {})[0] == 403
    assert http(BASE + '/export')[0] == 405
    run_id = completed(app)
    status, metadata = post(app, http, BASE+'/export', {'run_id': run_id})
    assert status == 200 and metadata['snapshot'] is None
    status, package = post(app, http, BASE+'/export', {'run_id': run_id, 'include_data': True})
    assert status == 200 and package['snapshot']
    status, saved = post(app, http, BASE+'/restore', {'package': package})
    assert status == 201
    status, receipt = post(app, http, BASE+'/'+saved['package_id']+'/recompute', {})
    assert status == 200 and receipt['status'] == 'MATCHED'
    assert http(BASE+'/'+saved['package_id'])[1]['receipt'] == receipt
    assert len(http(BASE)[1]['packages']) == 1
    assert app.research_service.list_jobs()['total'] == 1 and not wire.calls


def test_compare_has_six_categories_evidence_and_unknown_run_recovery(app_http):
    app, http, _ = app_http
    first, second = completed(app, 'one'), completed(app, 'two')
    status, value = http(BASE+'/compare?left='+first+'&right='+second)
    assert status == 200 and value['comparison']['status'] == 'COMPATIBLE'
    assert len(value['comparison']['categories']) == 6
    assert value['left']['quant_evidence']['coverage']['asset_count'] == 2
    assert value['right']['quant_evidence']['variance']['contributions']['ETF'] < 0
    assert http(BASE+'/compare?left='+first+'&right=absent')[0] == 404
    assert http(BASE+'/compare?left='+first)[0] == 422
    assert post(app, http, BASE+'/restore', {'package': {'schema_version': 'unsafe'}})[0] == 422
    assert post(app, http, BASE+'/export', {'run_id': first, 'include_data': 'true'})[0] == 422


def test_multiasset_capture_route_records_units_and_rejects_malformed_input(app_http):
    from finahinking.data.user_api_contracts import DataConnectionConfig
    from tests.data.test_user_api import FakeTransport, response
    app, http, _ = app_http
    records = [row.to_dict() for row in sample().observations]
    names = ('instrument', 'timestamp', 'available_at', 'open', 'high', 'low', 'close', 'volume', 'industry', 'suspended')
    app._data_connections.save(DataConnectionConfig(connection_id='multi', display_name='多资产', base_url='https://93.184.216.34/api', field_mapping={key:key for key in names}, credential_ref=None, auth_mode='no_auth', records_path='data'))
    transport = FakeTransport([response({'data':records}, url='https://93.184.216.34/api')])
    app._data_transport = transport
    values = {'connection_id':'multi', 'instrument':['AAA', 'ETF'], 'as_of':'2026-10-01', 'price_currency':'USD', 'volume_unit':'units', 'asset_classes':{'AAA':'EQUITY','ETF':'ETF'}}
    assert post(app,http,'/api/research/snapshots/capture',values)[0] == 422
    assert not transport.calls
    status, result = post(app,http,'/api/research/snapshots/capture',{**values, 'instrument':'AAA, ETF'})
    assert status == 201
    snapshot = app.research_service.snapshots.load(result['snapshot_ref'])
    from finahinking.research.quant_evidence import compute_quant_evidence
    assert compute_quant_evidence(snapshot,'2026-10-01')['status'] == 'READY'
    assert len(transport.calls) == 1


def test_oversized_imported_number_is_a_structured_validation_error(app_http):
    from finahinking.research.reproduction import ReproductionService, _digest
    app, http, _ = app_http
    package = ReproductionService(app.research_service).export(completed(app, 'oversize-number'), include_data=True)
    package['snapshot']['observations'][0]['volume'] = 10**400
    package['snapshot']['fingerprint'] = _digest({k:v for k,v in package['snapshot'].items() if k != 'fingerprint'})
    package['snapshot_digest'] = package['snapshot']['fingerprint']
    package['package_id'] = _digest({k:v for k,v in package.items() if k != 'package_id'})
    status, result = post(app,http,BASE+'/restore',{'package':package})
    assert status == 422 and '校验' in result['error']
    assert http(BASE) == (200, {'packages':[]})

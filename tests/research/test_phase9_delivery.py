"""Chinese first-use and shareable diagnostics reuse the existing facts."""

import json
import os
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from finahinking.local_app import LocalAppConfig, LocalApplication, create_server
from finahinking.p8_2.data_sources import FixtureMarketDataSource


def test_chinese_shell_and_first_use_do_not_create_data_or_jobs(tmp_path):
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "private-account.sqlite")))
    try:
        status, _, page = app.route("GET", "/research/jobs")
        assert status == 200 and '<html lang="zh-CN">' in page
        assert 'aria-label="主导航"' in page and '跳到主要内容' in page
        assert 'data-run-onboarding' in page and 'data-run-template' in page
        assert 'data-run-readiness' in page and 'data-run-budget' in page
        assert 'data-run-history-next' in page and 'data-run-history-prev' in page
        assert not app.research_service.snapshots.list_snapshots()
        assert app.research_service.list_jobs()["total"] == 0
        assert all(not value.isascii() for value in app._page_names().values())
    finally:
        app.close()


def test_diagnostic_bundle_contains_facts_not_private_filenames_or_fake_connections(tmp_path):
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "sensitive-customer-name.sqlite")))
    try:
        payload = app.diagnostics_bundle()
        raw = json.dumps(payload, ensure_ascii=False)
        assert payload["bundle_version"] == 2
        assert payload["research"]["total_jobs"] == 0
        assert payload["research"]["latest_report_status"] == "NO_REPORT"
        assert payload["diagnostics"]["source_connectivity"] == "NOT_TESTED"
        assert 'sensitive-customer-name' not in raw and str(tmp_path) not in raw
        assert 'no API key configured' not in raw
        assert '研究任务' in app.route("GET", "/diagnostics")[2]
    finally:
        app.close()


def test_unexpected_server_failure_is_chinese_and_does_not_echo_private_details(tmp_path, monkeypatch):
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / 'app.sqlite')))
    server = create_server(app, host='127.0.0.1', port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def fail(*args, **kwargs):
        raise RuntimeError('读取 /Users/private/customer-file 失败：private-credential')

    monkeypatch.setattr(app, 'route', fail)
    try:
        try:
            urlopen(f'http://127.0.0.1:{server.server_address[1]}/api/diagnostics', timeout=5)
        except HTTPError as error:
            assert error.code == 503
            raw = error.read().decode()
            assert '本地服务暂时无法完成请求' in raw
            assert 'Users' not in raw and 'private-credential' not in raw
        else:
            raise AssertionError('expected fail-closed local service response')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


@pytest.mark.skipif(os.name == 'nt', reason='POSIX permission evidence only')
def test_new_application_state_has_private_directory_and_database_permissions(tmp_path):
    db = tmp_path / 'private-state' / 'app.sqlite3'
    app = LocalApplication(LocalAppConfig(db_path=str(db)))
    try:
        assert db.parent.stat().st_mode & 0o777 == 0o700
        assert db.stat().st_mode & 0o777 == 0o600
    finally:
        app.close()


@pytest.mark.parametrize('damage', ['malformed-json', 'invalid-digest'])
def test_diagnostic_export_survives_broken_research_metadata_without_fake_counts(tmp_path, damage):
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / 'private-filename.sqlite')))
    try:
        service = app.research_service
        ref = service.snapshots.save(FixtureMarketDataSource(points=20).snapshot('AAA'))
        job = service.submit({'idempotency_key': 'bad-metadata', 'snapshot_ref': ref, 'instrument': 'AAA', 'as_of': '2026-10-01', 'question': '不应出现在诊断的私人研究', 'model_config_ref': 'offline'})
        task_ref = service.queue.get(job['job_id']).task_ref
        with service.queue._connect() as connection:
            if damage == 'malformed-json':
                connection.execute('UPDATE request_snapshots SET payload=? WHERE task_ref=?', ('{', task_ref))
            else:
                connection.execute('UPDATE request_snapshots SET request_digest=? WHERE task_ref=?', ('0' * 64, task_ref))
        assert app.route('GET', '/diagnostics')[0] == 200
        assert app.route('GET', '/api/diagnostics')[0] == 200
        status, _, bundle = app.route('GET', '/api/diagnostics/bundle')
        assert status == 200
        assert bundle['research']['status'] == 'UNAVAILABLE'
        assert bundle['research']['total_jobs'] is None
        assert bundle['research']['latest_report_status'] == 'UNKNOWN'
        raw = json.dumps(bundle, ensure_ascii=False)
        assert '不应出现在诊断' not in raw and 'private-filename' not in raw
        assert str(tmp_path) not in raw and ref not in raw
    finally:
        app.close()

"""Chinese first-use and shareable diagnostics reuse the existing facts."""

import json
import os
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from finahinking.local_app import LocalAppConfig, LocalApplication, create_server


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

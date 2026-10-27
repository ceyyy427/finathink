"""Run from a clean installed venv, not from the source package directory."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen

import finahinking
from finahinking.local_app import LocalAppConfig, LocalApplication, create_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-root', required=True, type=Path)
    args = parser.parse_args()
    assert Path(finahinking.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), 'must import the installed wheel in the clean venv'
    root = args.state_root.resolve()
    root.mkdir(mode=0o700)
    db = root / 'original' / 'app.sqlite3'
    app = LocalApplication(LocalAppConfig(db_path=str(db), offline=True))
    server = create_server(app, host='127.0.0.1', port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_address[1]}'

    def get(path):
        with urlopen(base + path, timeout=10) as response:
            return response.read()

    def post(path, data):
        request = Request(base + path, json.dumps({**data, '_csrf': app.csrf_token}).encode(), {'Content-Type': 'application/json'}, method='POST')
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read())

    try:
        assert '第一次使用' in get('/research/jobs').decode()
        from finahinking.research.asset_catalog import ResearchAssetCatalog

        catalog = ResearchAssetCatalog.packaged()
        assets = json.loads(get('/api/research/assets?kind=methods&limit=1'))
        assert assets['schema_version'] == 'asset-workspace.v1'
        assert assets['catalog_total'] == len(catalog.skills()) and len(assets['items']) == 1
        assert assets['items'][0]['loaded'] is False
        assert '资料展示不代表已加载执行' in get('/library/methods?asset=' + assets['items'][0]['asset_id']).decode()
        factor = json.loads(get('/api/research/factors/finathink-turnover-freefloat-mean'))
        assert len(factor['track']) == 9 and all(row['status'] == 'NOT_RUN' for row in factor['track'])
        assert '来源声明与本地证据' in get('/research/factors/finathink-turnover-freefloat-mean').decode()
        assert not json.loads(get('/api/research/snapshots'))['snapshots']
        snapshot = post('/api/research/snapshots/sample', {'instrument': 'AAA'})
        job = post('/api/research/jobs', {'idempotency_key': 'installed-first-use', 'snapshot_ref': snapshot['snapshot_ref'], 'instrument': 'AAA', 'as_of': snapshot['as_of'][:10], 'question': '检验所选数据的动量、费用与历史回撤', 'model_config_ref': 'offline'})
        deadline = time.monotonic() + 30
        while job['status'] not in {'completed', 'failed', 'cancelled'} and time.monotonic() < deadline:
            time.sleep(0.05)
            job = json.loads(get('/api/research/jobs/' + job['job_id']))
        assert job['status'] == 'completed', job['failure_kind']
        assert job['report_status'] == 'AVAILABLE'
        report = get(job['report_url'])
        assert snapshot['snapshot_ref'].encode() in report and b'SAMPLE' in report
        assert b'#f5f8fd' in report and b'Content-Security-Policy' in report
        assert job['elapsed_seconds'] >= 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()
    archive = root / 'private.zip'
    maintenance = Path(sys.executable).parent / ('finathink-state.exe' if sys.platform == 'win32' else 'finathink-state')
    subprocess.run([str(maintenance), 'backup', '--db', str(db), '--output', str(archive)], check=True)
    subprocess.run([str(maintenance), 'restore', '--archive', str(archive), '--to', str(root / 'restored')], check=True)
    restored = root / 'restored' / 'app.sqlite3'
    # Opening the upgraded/restored installation twice must preserve history.
    for _ in range(2):
        reopened = LocalApplication(LocalAppConfig(db_path=str(restored), offline=True))
        try:
            value = reopened.research_service.get_job(job['job_id'])
            assert value['snapshot_ref'] == snapshot['snapshot_ref'] and value['attempts'] == 1
            assert value['report_url'] == job['report_url'] and value['report_status'] == 'AVAILABLE'
            restored_report = reopened.route('GET', value['report_url'])[2]
            assert (restored_report.encode() if isinstance(restored_report, str) else restored_report) == report
        finally:
            reopened.close()
    print('全新 wheel 安装 → 资料与因子证据 → 真实 HTTP/独立 spawn 任务 → 蓝白报告 → 私密备份恢复 → 重复迁移检查：通过。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

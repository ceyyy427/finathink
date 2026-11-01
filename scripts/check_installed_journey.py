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


def learning_journey(app, get, post, template_ref):
    from dataclasses import replace
    from datetime import UTC, datetime

    import pandas as pd

    template = app.research_service.snapshots.load(template_ref)
    rows = []
    for i in range(12):
        stamp = pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(days=i)
        price = 100+i
        rows.append(replace(template.observations[0], timestamp=stamp, available_at=stamp, open=price, close=price, high=price+1, low=price-1, volume=100000, industry="technology"))
    source = replace(template, observations=tuple(rows), as_of=rows[-1].timestamp)
    following = []
    for i, price in enumerate((100,98,96,94,92,90), 1):
        stamp = rows[-1].timestamp+pd.Timedelta(days=i)
        following.append(replace(rows[-1], timestamp=stamp, available_at=stamp, open=price, close=price, high=price+1, low=price-1))
    evidence = replace(source, observations=(*rows,*following), as_of=following[-1].timestamp)
    source_ref, evidence_ref = app.research_service.snapshots.save(source), app.research_service.snapshots.save(evidence)
    def wait(job):
        deadline=time.monotonic()+40
        while job['status'] not in {'completed','failed','cancelled'} and time.monotonic()<deadline:
            time.sleep(.05)
            job=json.loads(get('/api/research/jobs/'+job['job_id']))
        assert job['status']=='completed' and job['report_status']=='AVAILABLE'
        return job
    job = wait(post('/api/research/jobs', {'idempotency_key':'installed-learning', 'snapshot_ref':source_ref, 'question':'Observe paper momentum and fixed costs', 'instrument':'AAA', 'as_of':'2026-01-12'}))
    base='/api/research/learning'
    review=post(base, {'run_id':job['run_id'], 'evidence_snapshot_ref':evidence_ref, 'as_of':'2026-01-18'})
    assessment=post(base+'/assessments', {'anchor_review_id':review['review_id'], 'start':'2026-01-01', 'as_of':datetime.now(UTC).date().isoformat()})
    assert assessment['status']=='OBSERVE' and assessment['sample_count']==1
    approved=post(base+'/'+review['review_id']+'/decision', {'decision':'APPROVED', 'allow_exploratory':True, 'rationale':'Explicit installed exploratory experiment', 'expected_preview_fingerprint':review['preview_fingerprint']})
    pair=post(base+'/pairs', {'review_id':review['review_id'], 'snapshot_ref':source_ref, 'as_of':approved['available_as_of']})
    for child in pair['children'].values():
        wait(json.loads(get('/api/research/jobs/'+child['job_id'])))
    assert json.loads(get(base+'/pairs/'+pair['pair_id']))['status']=='AVAILABLE'
    assert all(row['report_status']=='NOT_CHECKED' for row in json.loads(get('/api/research/jobs/summaries'))['jobs'])
    assert json.loads(get('/api/research/maintenance'))['resources']['worker_rss_bytes'] is None
    return assessment['assessment_id'], pair['pair_id']


def panel_journey(app, post):
    from finahinking.data.panel_contracts import PanelContract, PanelFieldAttachment
    from finahinking.p8_2.contracts import DatasetSnapshot, MarketObservation

    times = [f"2026-01-0{d}T16:00:00Z" for d in (2,3,4)]
    observations = tuple(MarketObservation(a,t,v,v,v,v,100,available_at=t,suspended=False)
                         for a, values in (("AAA",(1,2,3)),("BBB",(5,6,7))) for t,v in zip(times,values))
    snap = DatasetSnapshot("installed-panel", observations, provenance={"adjustment":"RAW"})
    app.research_service.snapshots.save(snap)
    units = {"is_session":"boolean", "in_universe":"boolean", "trade_allowed":"boolean",
             "lot_size":"shares", "settlement_days":"sessions", "split_factor":"ratio"}
    records = [{"instrument":a,"timestamp":t,"available_at":t,"is_session":True,"in_universe":True,
                "trade_allowed":True,"lot_size":1,"settlement_days":0,"split_factor":1}
               for a in snap.instruments for t in times]
    fields = PanelFieldAttachment(snap.fingerprint, units, records, "Installed synthetic panel")
    ref = post("/api/research/panels/fields", {"attachment":fields.to_dict()})["attachment_ref"]
    contract = PanelContract(snap.fingerprint, snap.instruments, times, {"close":"CNY"}, "installed-panel",
                            {a:"CNY" for a in snap.instruments}, {a:"UTC" for a in snap.instruments},
                            ref, ref, ref, ref)
    inputs = {"contract":contract.to_dict(), "attachment_refs":[ref], "expressions":["ts_return(close,1)"]}
    result = post("/api/research/panels/evaluate", inputs)
    assert result["status"] == "READY" and result["execution_permission"] is False
    assert result["results"][0]["records"][-2]["value"] == .5
    assert result == post("/api/research/panels/evaluate", inputs)


def library_journey(app, get, post):
    import math

    import pandas as pd

    from finahinking.data.panel_contracts import PanelContract, PanelFieldAttachment
    from finahinking.p8_2.contracts import DatasetSnapshot, MarketObservation

    catalog = json.loads(get('/api/research/factor-library'))
    assert catalog['inventory']['executable_formula_count'] >= 80
    assert '因子定义库' in get('/research/factor-library').decode()
    times = pd.date_range('2025-01-01', periods=120, tz='UTC')
    observations = []
    for number, asset in enumerate(('LIBA', 'LIBB', 'LIBC')):
        for i, stamp in enumerate(times):
            close = 30 + number * 7 + i * .03 + math.sin(i / (3 + number))
            observations.append(MarketObservation(asset, stamp, close-.1, close+.2, close-.2, close,
                10000, available_at=stamp, suspended=False))
    snapshot = DatasetSnapshot('installed-library-synthetic', tuple(observations), provenance={'adjustment':'RAW'})
    app.research_service.snapshots.save(snapshot)
    units = {'is_session':'boolean','in_universe':'boolean','trade_allowed':'boolean',
        'lot_size':'shares','settlement_days':'sessions','split_factor':'ratio'}
    records = [{'instrument':a,'timestamp':t.isoformat(),'available_at':t.isoformat(),
        'is_session':True,'in_universe':True,'trade_allowed':True,'lot_size':1,'settlement_days':0,'split_factor':1}
        for a in snapshot.instruments for t in times]
    fields = PanelFieldAttachment(snapshot.fingerprint, units, records, 'Installed library synthetic input')
    ref = post('/api/research/panels/fields', {'attachment':fields.to_dict()})['attachment_ref']
    contract = PanelContract(snapshot.fingerprint, snapshot.instruments, [t.isoformat() for t in times],
        {'open':'CNY','high':'CNY','low':'CNY','close':'CNY','volume':'shares'}, 'installed-library',
        {a:'CNY' for a in snapshot.instruments},{a:'UTC' for a in snapshot.instruments},ref,ref,ref,ref)
    study = post('/api/research/factor-library/studies', {'factor_id':'lib-trend-roc','factor_version':'1.0.0',
        'idempotency_key':'installed-library','contract':contract.to_dict(),'attachment_refs':[ref],
        'question':'验证已安装版本的因子定义与后台计算'})
    deadline = time.monotonic()+45
    while study['state'] in {'DECLARED','VALIDATING'} and time.monotonic()<deadline:
        time.sleep(.05)
        study = json.loads(get('/api/research/experiments/'+study['study_id']))
    assert study['state']=='VALIDATED', study
    assert study['validation']['library_validation']['backtest']['metric_basis']=='ACTUAL_NET_LEDGER'
    assert study['validation_job']['report_status']=='AVAILABLE'
    assert '因子库定义与量化证据' in get(study['validation_job']['report_url']).decode()
    return study['study_id']


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
        first_use = get('/research/jobs').decode()
        assert '从数据到第一份研究报告' in first_use and 'data-first-research-steps' in first_use
        assert 'data-interface-workspace' in get('/diagnostics').decode()
        assert not json.loads(get('/api/diagnostics/interfaces'))['records']
        cases = json.loads(get('/api/research/quality/cases'))['cases']
        assert {case['case_id'] for case in cases} == {'complete', 'sparse', 'conflict'}
        assert not json.loads(get('/api/research/quality/jobs'))['jobs']
        diagnostic = post('/api/diagnostics/interfaces/local', {})
        assert diagnostic['status'] == 'READY' and len(diagnostic['cases']) == 34
        assert diagnostic['external_account_verified'] is False and len(diagnostic['quality_demo']) == 3
        assert json.loads(get('/api/diagnostics/interfaces/' + diagnostic['receipt_id'])) == diagnostic
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
        inputs = {'snapshot_ref': snapshot['snapshot_ref'], 'instrument': 'AAA', 'as_of': snapshot['as_of'][:10], 'question': '检验所选数据的动量、费用与历史回撤', 'model_config_ref': 'offline'}
        readiness = post('/api/research/readiness', inputs)
        assert readiness['can_submit'] and readiness['status'] == 'NEEDS_REVIEW'
        assert json.loads(get('/api/research/jobs'))['total'] == 0
        job = post('/api/research/jobs', {**inputs, 'idempotency_key': 'installed-first-use'})
        deadline = time.monotonic() + 30
        while job['status'] not in {'completed', 'failed', 'cancelled'} and time.monotonic() < deadline:
            time.sleep(0.05)
            job = json.loads(get('/api/research/jobs/' + job['job_id']))
        assert job['status'] == 'completed', job['failure_kind']
        assert job['report_status'] == 'AVAILABLE'
        assert job['explanation']['outcome'] == 'AVAILABLE' and job['explanation']['evidence']
        report = get(job['report_url'])
        assert snapshot['snapshot_ref'].encode() in report and b'SAMPLE' in report
        assert b'#f5f8fd' in report and b'Content-Security-Policy' in report
        assert b'data-research-explanation' in report
        assert job['elapsed_seconds'] >= 0
        from finahinking.research.application_service import ResearchApplicationService
        from finahinking.research.reproduction import ReproductionService
        package = ReproductionService(app.research_service).export(job['run_id'], include_data=True)
        portable = ResearchApplicationService(root / 'portable', allow_model_calls=False)
        try:
            replay = ReproductionService(portable)
            restored = replay.restore(package)
            assert replay.recompute(restored['package_id'])['status'] == 'MATCHED'
            assert portable.list_jobs()['total'] == 0
        finally:
            portable.close()
        assessment_id, pair_id = learning_journey(app, get, post, snapshot['snapshot_ref'])
        panel_journey(app, post)
        library_study_id = library_journey(app, get, post)
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
            assert reopened.route('GET', '/api/research/learning/assessments/'+assessment_id)[2]['sample_count'] == 1
            assert reopened.route('GET', '/api/research/learning/pairs/'+pair_id)[2]['status'] == 'AVAILABLE'
            restored_diagnostic = reopened.route('GET', '/api/diagnostics/interfaces/' + diagnostic['receipt_id'])
            assert restored_diagnostic[0] == 200 and restored_diagnostic[2] == diagnostic
            restored_library = reopened.research_service.factor_study(library_study_id)
            assert restored_library['state'] == 'VALIDATED'
            assert restored_library['validation_job']['report_status'] == 'AVAILABLE'
            value = reopened.research_service.get_job(job['job_id'])
            assert value['snapshot_ref'] == snapshot['snapshot_ref'] and value['attempts'] == 1
            assert value['report_url'] == job['report_url'] and value['report_status'] == 'AVAILABLE'
            restored_report = reopened.route('GET', value['report_url'])[2]
            assert (restored_report.encode() if isinstance(restored_report, str) else restored_report) == report
        finally:
            reopened.close()
    print('全新 wheel 安装 → 来源卡与因子库真实研究 → 真实 HTTP/独立 spawn 任务 → 蓝白报告 → 样本评估/成对复跑 → 私密备份恢复 → 重复迁移检查：通过。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

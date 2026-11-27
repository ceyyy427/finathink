"""Run from a clean installed venv, not from the source package directory."""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zipfile import ZIP_DEFLATED, ZipFile

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


def panel_journey(app, get, post):
    from finahinking.data.panel_contracts import PanelContract, PanelFieldAttachment
    from finahinking.p8_2.contracts import DatasetSnapshot, MarketObservation

    times = [f"2026-01-0{d}T16:00:00Z" for d in (2,3,4)]
    observations = tuple(MarketObservation(a,t,v,v,v,v,100,available_at=t,suspended=False)
                         for a, values in (("AAA",(1,2,3)),("BBB",(5,6,7))) for t,v in zip(times,values))
    snap = DatasetSnapshot("installed-panel", observations, provenance={
        "adjustment": "RAW", "units": {"price_currency": "CNY", "volume_unit": "shares"},
    })
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
    assert "保存面板" in get("/research/panels").decode()
    draft = {
        "name": "已安装面板", "snapshot_ref": snap.fingerprint, "instrument_ids": list(snap.instruments),
        "start": "2026-01-02", "end": "2026-01-04", "timezone": "UTC", "attachment_refs": [],
        "rules": dict.fromkeys(("calendar_ref", "universe_ref", "adjustment_ref", "trade_rule_ref"), ref),
    }
    preview = post("/api/research/panels/preview", draft)
    assert preview["status"] == "READY" and preview["row_count"] == 6
    saved = post("/api/research/panels/save", {**draft, "preview_fingerprint": preview["preview_fingerprint"]})
    assert json.loads(get("/api/research/panels/saved"))["panels"] == [saved]
    return saved


def strategy_journey(get, post):
    catalog = json.loads(get('/api/research/strategies'))
    assert len(catalog['templates']) == len(catalog['risk_rules']) == 12
    sample = post('/api/research/strategies/sample', {})
    job = post('/api/research/strategies', {'idempotency_key': 'installed-strategy',
        'template_id': 'equal-weight', 'contract': sample['contract'],
        'attachment_refs': sample['attachment_refs']})
    deadline = time.monotonic() + 60
    while job['status'] not in {'completed', 'failed', 'cancelled'} and time.monotonic() < deadline:
        time.sleep(.1)
        job = json.loads(get('/api/research/jobs/' + job['job_id']))
    assert job['status'] == 'completed' and job['report_status'] == 'AVAILABLE', job
    result = job['strategy_study']['result']
    assert result['status'] == 'COMPLETE' and result['ledger']['fills']
    assert '策略研究与统一纸面账本' in get(job['report_url']).decode()
    studio = '/api/research/strategy-validation'
    assert '策略验证室' in get('/research/strategy-validation').decode()
    assert b'.sv-page' in get('/assets/strategy-validation.css')
    plan = post(studio + '/preview', {'run_id': job['run_id']})
    queued = post(studio + '/run', {'run_id': job['run_id'], 'plan_fingerprint': plan['plan_fingerprint']})
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        evidence = json.loads(get(studio + '?study=' + queued['study_id']))
        if evidence['status'] in {'completed', 'failed', 'cancelled'}:
            break
        time.sleep(.25)
    assert evidence['status'] == 'completed', evidence
    assert len(evidence['walkforward']['windows']) == 3
    assert evidence['portfolio']['ledger_digest'] == plan['original_ledger']
    assert plan['plan_fingerprint'] in get(evidence['report_url']).decode()
    learning = '/api/research/learning/strategy'
    receipt = post(learning + '/observations', {'run_id': job['run_id'], 'as_of': job['as_of'][:10]})
    audit = post(learning + '/audits', {'observation_ref': receipt['observation_ref']})
    assert audit['status'] == 'READY' and audit['scope']['asset_scope'] == 'MIXED'
    proposal = post(learning + '/audits/' + audit['audit_id'] + '/propose', {})
    change = learning + '/proposals/' + proposal['proposal_id']
    approved = post(change + '/decision', {'decision': 'APPROVED',
                    'rationale': '检查合成账本的可比窗口并验证独立配置。',
                    'expected_preview_fingerprint': proposal['preview_fingerprint']})
    pair = post(change + '/pair', {})
    assert pair['sides']['candidate']['policy_ref'] == approved['policy']['policy_ref']
    assert pair['fills_changed'] and '政策版本对照' in get('/research/atlas/runs?pair=' + pair['pair_id']).decode()

    decision = next(row for row in reversed(result['decisions']) if row['status'] == 'SCHEDULED')
    entry = post('/api/research/pools', {'pool_ref': 'installed-pool', 'job_id': job['job_id'],
        'decision_origin': 'QUANT_BASELINE', 'stage': 'DECISION',
        'instrument': next(iter(decision['target_weights']))})
    cohort = post('/api/research/pools/freeze', {'pool_ref': entry['pool_ref'], 'decision_at': entry['decision_at']})
    observed = post('/api/research/pools/observe', {'cohort_ref': cohort['cohort_ref'],
        'snapshot_ref': sample['snapshot_ref']})
    assert len(observed['horizons']) == 4
    validation_job = json.loads(get('/api/research/jobs/' + queued['study_id']))
    return job, entry['entry_ref'], validation_job['run_id'], pair, approved['admission']


def discovery_journey(get, post):
    sample = post("/api/research/strategies/sample", {})
    values = {name: sample[name] for name in ("contract", "attachment_refs")}
    values["question"] = "等权组合的集中风险如何？"
    preview = post("/api/research/discovery/preview", values)
    assert preview["status"] == "READY" and preview["target_id"] == "equal-weight"
    job = post("/api/research/discovery", {**values, "authorized": True,
        "blueprint_fingerprint": preview["fingerprint"], "idempotency_key": "installed-discovery"})["job"]
    deadline = time.monotonic() + 60
    while job["status"] not in {"completed", "failed", "cancelled"} and time.monotonic() < deadline:
        time.sleep(.1)
        job = json.loads(get("/api/research/jobs/" + job["job_id"]))
    assert job["report_status"] == "AVAILABLE", job
    proof = job["discovery"]
    assert proof["blueprint"]["fingerprint"] == preview["fingerprint"] and len(proof["roles"]) == 5
    document = get(job["report_url"])
    assert "研究计划与分析师证据".encode() in document
    return job["job_id"], document


def suite_journey(app, get, post):
    from finahinking.research.factor_validation_sample import SAMPLE_SPEC
    from finahinking.research.research_evidence import PackagedCounterexamples

    assert len(PackagedCounterexamples().records) >= 20
    assert '因子检验与评分卡' in get('/research/factor-suites').decode()
    spec = {**SAMPLE_SPEC, 'assets':8, 'observations':80}
    study = post('/api/research/factor-suites', {'idempotency_key':'installed-suite',
        'family_key':'installed-suite', 'factor_ids':['lib-trend-roc'], 'sample_spec':spec,
        'question':'核对已安装版本的因子评分与独立报告'})
    deadline = time.monotonic()+60
    while study['state'] in {'DECLARED','VALIDATING'} and time.monotonic()<deadline:
        time.sleep(.1)
        study = json.loads(get('/api/research/factor-suites?study='+study['study_id']))
    assert study['state']=='VALIDATED', study
    trial = study['validation']['suite_validation']['trials'][0]
    assert trial['status'] in {'PASS','FALSIFIED'} and trial['data_mode']=='SAMPLE'
    assert trial['financial_falsification'] is False
    assert '因子检验与稳健性评分卡' in get(study['validation_job']['report_url']).decode()
    assert '评分、有效样本与计算范围' in get('/research/factor-suites?study='+study['study_id']).decode()
    return study['study_id']


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
    path = '/api/research/experiments/' + study['study_id']
    candidate = next(row for row in study['validation']['experiments'] if row['failure_reason'] is None)
    post(path + '/freeze', {'candidate_id': candidate['experiment_id']})
    tested = post(path + '/test', {'idempotency_key': 'installed-frozen-library-test'})
    deadline = time.monotonic() + 60
    while tested['state'] in {'FROZEN', 'TEST_RESERVED', 'TESTING'} and time.monotonic() < deadline:
        time.sleep(.1)
        tested = json.loads(get(path))
    assert tested['state'] == 'TEST_EVALUATED', tested['state']
    return study['study_id'], tested['test_job']['run_id']


@contextmanager
def http_workspace(db):
    app = LocalApplication(LocalAppConfig(db_path=str(db), offline=True))
    server = create_server(app, host='127.0.0.1', port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_address[1]}'

    def get(path):
        with urlopen(base + path, timeout=30) as response:
            return response.read()

    def post(path, data):
        encoded = json.dumps({**data, '_csrf': app.csrf_token}, ensure_ascii=False, separators=(',', ':')).encode()
        request = Request(base + path, encoded, {'Content-Type': 'application/json'}, method='POST')
        with urlopen(request, timeout=120) as response:
            return json.loads(response.read())

    try:
        yield app, get, post
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


EXAMPLE_NAMES = {
    'research': '普通研究',
    'factor_study': '因子库冻结测试',
    'strategy_study': '策略账本与基准',
    'strategy_validation': '策略滚动验证',
    'research_pair': '普通学习成对复跑',
    'strategy_pair': '策略学习成对复跑',
}


def write_examples(output, examples, root):
    assert set(examples) == set(EXAMPLE_NAMES)
    files = {}
    identity = examples['research'][1]['runtime_identity']
    for kind, (package, receipt) in examples.items():
        assert receipt['runtime_identity'] == identity and receipt['package_id'] == package['package_id']
        assert receipt['status'] == 'REPRODUCED' and not receipt['missing_dependencies']
        for directory, value in (('packages', package), ('receipts', receipt)):
            files[f'{directory}/{kind}.json'] = json.dumps(value, ensure_ascii=False, allow_nan=False,
                                                           separators=(',', ':')).encode('utf-8')
    deps = identity['dependencies']
    listing = '\n'.join(f'- `{kind}.json`　{label}' for kind, label in EXAMPLE_NAMES.items())
    files['README.md'] = f"""# Finathink 工作流复现示例

这些示例由 Finathink {version('finathink')} 的已安装 wheel 在全新本机工作区生成。普通研究和学习使用内置 AAA 样本或脚本生成的价格，因子测试使用三个虚构资产的价格和交易规则，策略使用内置样本面板。所有研究均为离线纸面计算。

{listing}

## 使用方法

1. 解压后在本目录运行 `shasum -a 256 -c SHA256SUMS`（Linux 可运行 `sha256sum -c SHA256SUMS`）。
2. 打开「研究对比」，从 `packages/` 选择一个 JSON，点击「恢复研究包」，然后点击「复算已恢复产物」。
3. 在 `receipts/` 找到同名文件，与界面中的包标识、各项复算状态、字段数和摘要核对。这里的回执来自第二个全新工作区对这些导出包执行的实际复算。

## 生成与复算环境

Python {deps['python']}，NumPy {deps['numpy']}，pandas {deps['pandas']}。运行环境差异会显示在回执中。源文件摘要为 `{identity['source_digest']}`。
""".encode()
    sums = ''.join(f'{sha256(value).hexdigest()}  {name}\n' for name, value in sorted(files.items()))
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as temporary:
        staged = Path(temporary) / 'examples.zip'
        with ZipFile(staged, 'w', compression=ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
            archive.writestr('SHA256SUMS', sums)
        verify_examples(staged, root / 'examples-import', same_host=True)
        staged.replace(output)


def verify_examples(archive_path, root, *, same_host=False):
    """Restore the exact published files through the installed HTTP application."""
    from finahinking.research.strategy_learning import StrategyLearningService

    names = {'README.md'} | {f'{directory}/{kind}.json' for kind in EXAMPLE_NAMES
                             for directory in ('packages', 'receipts')}
    with ZipFile(archive_path) as archive, http_workspace(root / 'app.sqlite3') as (target, get, post):
        assert sorted(archive.namelist()) == sorted(names | {'SHA256SUMS'})
        sums = []
        for row in archive.read('SHA256SUMS').decode('ascii').splitlines():
            digest, name = row.split('  ', 1)
            assert name in names and sha256(archive.read(name)).hexdigest() == digest, name
            sums.append(name)
        assert sorted(sums) == sorted(names)
        base = '/api/research/reproduction'
        assert json.loads(get(base))['packages'] == []
        policies = target.research_service.learning.policies()
        for kind in EXAMPLE_NAMES:
            package = json.loads(archive.read(f'packages/{kind}.json'))
            published = json.loads(archive.read(f'receipts/{kind}.json'))
            restored = post(base + '/restore', {'package': package})
            assert package['kind'] == kind and restored['package_id'] == published['package_id'] == package['package_id']
            path = base + '/' + restored['package_id']
            receipt = post(path + '/recompute', {})
            assert receipt['status'] == published['status'] == 'REPRODUCED', (kind, receipt)
            assert not receipt['missing_dependencies'] and not receipt['version_differences']
            assert receipt['runtime_identity'] == published['runtime_identity']
            assert receipt['paper_only'] is True and receipt['model_called'] is False
            assert receipt['products'].keys() == published['products'].keys()
            if same_host:
                assert receipt == published, kind
            fields = 0
            for key, reference in published['products'].items():
                product = receipt['products'][key]
                if key == 'historical_reports':
                    assert product['status'] == reference['status'] == 'ARCHIVED'
                    continue
                assert product['status'] == reference['status'] == 'REPRODUCED', (kind, key, product)
                assert product['difference_count'] == reference['difference_count'] == 0
                assert product['fields_checked'] == reference['fields_checked'] > 0
                assert product['expected_digest'] == reference['expected_digest']
                fields += product['fields_checked']
            reports = json.loads(get(path + '/history'))['artifacts']
            assert reports and fields, kind
            for report in reports:
                contents = base64.b64decode(report['content_base64'], validate=True)
                assert sha256(contents).hexdigest() == report['sha256'], (kind, report['name'])
            print(f'{kind}: {fields} fields, {len(reports)} archived reports, 0 differences', flush=True)
        assert len(json.loads(get(base))['packages']) == len(EXAMPLE_NAMES)
        assert target.research_service.list_jobs()['total'] == 0
        assert target.research_service.learning.policies() == policies
        assert StrategyLearningService(target.research_service).store.list('policy') == []


def portable_journey(root, source_get, source_post, cases, *, collect_examples=False):
    from finahinking.research.strategy_learning import StrategyLearningService

    base = '/api/research/reproduction'
    examples = {}
    with http_workspace(root / 'portable-workflows' / 'app.sqlite3') as (target, get, post):
        assert json.loads(get(base))['packages'] == []
        for identifier, required in cases:
            try:
                package = source_post(base + '/export', {**identifier, 'include_data': True, 'data_scope': 'workflow'})
            except HTTPError as error:
                error.add_note(f'Export of {identifier} for {required} returned {error.code}.')
                raise
            saved = post(base + '/restore', {'package': package})
            path = base + '/' + saved['package_id']
            receipt = post(path + '/recompute', {})
            for key in required:
                assert receipt['products'][key]['status'] == 'REPRODUCED', (key, receipt)
            assert json.loads(get(path))['receipt'] == receipt
            history = json.loads(get(path + '/history'))['artifacts']
            assert history and ('run_id' not in identifier or identifier['run_id'] + '.html' in
                                {artifact['name'] for artifact in history}), identifier
            for artifact in history:
                run_id = artifact['publication']['run_id']
                assert artifact['name'] == run_id + '.html'
                original = source_get('/research/' + run_id + '/report/complete')
                assert artifact['sha256'] == sha256(original).hexdigest()
                assert base64.b64decode(artifact['content_base64'], validate=True) == original
            if collect_examples:
                assert package['kind'] not in examples
                examples[package['kind']] = (package, receipt)
        assert target.research_service.list_jobs()['total'] == 0
        assert len(target.research_service.learning.policies()) == 1
        assert StrategyLearningService(target.research_service).store.list('policy') == []
    return examples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-root', required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--examples-output', type=Path, help='write importable examples generated from this fresh fixture workspace')
    mode.add_argument('--verify-examples', type=Path, help='restore and replay every example in a published ZIP')
    args = parser.parse_args()
    assert Path(finahinking.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), 'must import the installed wheel in the clean venv'
    root = args.state_root.resolve()
    root.mkdir(mode=0o700)
    if args.verify_examples:
        verify_examples(args.verify_examples, root)
        return 0
    db = root / 'original' / 'app.sqlite3'
    with http_workspace(db) as (app, get, post):
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
        assert b'#f5f1e9' in report and b'Content-Security-Policy' in report
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
        saved_panel = panel_journey(app, get, post)
        library_study_id, factor_run = library_journey(app, get, post)
        suite_study_id = suite_journey(app, get, post)
        strategy_job, pool_entry_ref, studio_run, strategy_pair, admission = strategy_journey(get, post)
        strategy_document = get(strategy_job['report_url'])
        learned = ('learning_approval', 'learning_baseline', 'learning_candidate', 'learning_comparison')
        examples = portable_journey(root, get, post, [
            ({'run_id': job['run_id']}, ('baseline', 'quant_evidence')),
            ({'run_id': factor_run}, ('factor_study',)),
            ({'run_id': strategy_job['run_id']}, ('strategy_ledger', 'strategy_benchmark', 'strategy_decisions')),
            ({'run_id': studio_run}, ('strategy_validation_plan', 'strategy_walkforward', 'strategy_portfolio')),
            ({'pair_id': pair_id}, learned), ({'pair_id': strategy_pair['pair_id']}, learned),
        ], collect_examples=args.examples_output is not None)
        workbench = json.loads(get("/api/research/quant-workspace"))
        assert sum(workbench["inventory"].values()) >= 200 and len(workbench["journeys"]) == 12
        assert "量化研究工作台".encode() in get("/research/workbench?journey=portfolio")
        discovery_job_id, discovery_document = discovery_journey(get, post)
    archive = root / 'private.zip'
    maintenance = Path(sys.executable).parent / ('finathink-state.exe' if sys.platform == 'win32' else 'finathink-state')
    subprocess.run([str(maintenance), 'backup', '--db', str(db), '--output', str(archive)], check=True)
    subprocess.run([str(maintenance), 'restore', '--archive', str(archive), '--to', str(root / 'restored')], check=True)
    restored = root / 'restored' / 'app.sqlite3'
    cache = restored.parent / ".knowledge-cache" / "public.sqlite3"
    assert not cache.exists()  # derived index is rebuilt from installed canonical content

    # Opening the upgraded/restored installation twice must preserve history.
    for _ in range(2):
        reopened = LocalApplication(LocalAppConfig(db_path=str(restored), offline=True))
        try:
            assert reopened.route('GET', '/api/research/learning/assessments/'+assessment_id)[2]['sample_count'] == 1
            assert reopened.route('GET', '/api/research/learning/pairs/'+pair_id)[2]['status'] == 'AVAILABLE'
            assert reopened.route('GET', '/api/research/panels/saved')[2]["panels"] == [saved_panel]
            _, _, rebuilt = reopened.route("GET", "/api/research/quant-workspace")
            assert cache.is_file() and rebuilt["fingerprint"] == workbench["fingerprint"]
            restored_diagnostic = reopened.route('GET', '/api/diagnostics/interfaces/' + diagnostic['receipt_id'])
            assert restored_diagnostic[0] == 200 and restored_diagnostic[2] == diagnostic
            discovery = reopened.research_service.get_job(discovery_job_id)
            assert discovery["report_status"] == "AVAILABLE" and discovery["discovery"]["product_ref"]
            assert reopened.route("GET", discovery["report_url"])[2].encode() == discovery_document
            restored_learning = '/api/research/learning/strategy'
            assert reopened.route('GET', restored_learning + '/pairs/' + strategy_pair['pair_id'])[2] == strategy_pair
            decision = reopened.route('GET', restored_learning + '/proposals/' + strategy_pair['proposal_id'])[2]
            assert decision['admission'] == admission
            assert reopened.route('GET', strategy_job['report_url'])[2].encode() == strategy_document
            strategy = reopened.research_service.get_job(strategy_job['job_id'])
            assert strategy['report_status'] == 'AVAILABLE' and strategy['strategy_study']['result']['ledger']['fills']
            _, _, pool = reopened.route('GET', '/api/research/pools?pool_ref=installed-pool')
            assert pool['entries'][0]['entry_ref'] == pool_entry_ref
            restored_suite = reopened.research_service.factor_study(suite_study_id)
            assert restored_suite['state'] == 'VALIDATED'
            assert restored_suite['validation']['suite_validation']['trials'][0]['data_mode'] == 'SAMPLE'
            assert restored_suite['validation_job']['report_status'] == 'AVAILABLE'
            restored_library = reopened.research_service.factor_study(library_study_id)
            assert restored_library['state'] == 'TEST_EVALUATED' and restored_library['test_job']['run_id'] == factor_run
            assert restored_library['validation_job']['report_status'] == 'AVAILABLE'
            value = reopened.research_service.get_job(job['job_id'])
            assert value['snapshot_ref'] == snapshot['snapshot_ref'] and value['attempts'] == 1
            assert value['report_url'] == job['report_url'] and value['report_status'] == 'AVAILABLE'
            restored_report = reopened.route('GET', value['report_url'])[2]
            assert (restored_report.encode() if isinstance(restored_report, str) else restored_report) == report
        finally:
            reopened.close()
    if args.examples_output is not None:
        write_examples(args.examples_output, examples, root)
    print('全新 wheel 安装 → 来源卡与因子库真实研究 → 真实 HTTP/独立 spawn 任务 → 离线研究报告 → 策略结算审核与成对复跑 → 工作流跨工作区复算与原报告字节校验 → 样本评估 → 私密备份恢复 → 重复迁移检查：通过。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

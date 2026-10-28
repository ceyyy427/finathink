"""First-use delivery: real local browser/worker, controlled user API, bounded UI."""

import json
import threading
import time
from contextlib import contextmanager
from dataclasses import replace

from finahinking.data.connection_settings import InMemoryDataCredentialStore
from finahinking.data.user_api import TransportResponse
from finahinking.local_app import LocalAppConfig, LocalApplication, create_server
from finahinking.maintenance import backup_state, restore_state
from finahinking.p8_2.data_sources import FixtureMarketDataSource
from tests.p7_5.test_phase4_browser import browser, observed_page

__all__ = ['browser', 'observed_page']


@contextmanager
def serve(app):
    server = create_server(app, host='127.0.0.1', port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_address[1]}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


def wait_report(page):
    page.wait_for_selector('[data-job-status="completed"]', timeout=30_000)
    return page.locator('[data-run-report]').get_attribute('href')


def test_first_use_sample_report_offline_file_backup_restore_and_corruption(tmp_path, observed_page):
    page, artifacts, diagnostics = observed_page
    diagnostics['metrics']['browser_version'] = page.context.browser.version
    diagnostics['metrics']['layouts'] = []
    db = tmp_path / 'source' / 'local.sqlite'
    app = LocalApplication(LocalAppConfig(db_path=str(db), offline=True))
    with serve(app) as base:
        page.emulate_media(reduced_motion='reduce')
        page.goto(base + '/research/jobs')
        page.wait_for_selector('[data-workspace-state="EMPTY"]')
        assert '尚无快照' in page.locator('[data-run-data-state]').inner_text()
        assert not app.research_service.snapshots.list_snapshots()
        assert page.get_by_role('navigation', name='主导航').is_visible()
        for width, height in ((1440, 900), (1920, 1080), (375, 812)):
            page.set_viewport_size({'width': width, 'height': height})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
            diagnostics['metrics']['layouts'].append(page.evaluate("() => ({width: innerWidth, height: innerHeight, scrollWidth: document.documentElement.scrollWidth, guideFontPx: parseFloat(getComputedStyle(document.querySelector('[data-run-onboarding]')).fontSize)})"))
            assert diagnostics['metrics']['layouts'][-1]['guideFontPx'] >= 16
            page.screenshot(path=str(artifacts / f'first-use-empty-{width}.png'), full_page=True)
            page.screenshot(path=str(artifacts / f'first-use-viewport-{width}.png'))
        (artifacts / 'first-use-accessibility.txt').write_text(page.locator('main').aria_snapshot(), encoding='utf-8')
        page.get_by_text('使用明确标记的演示数据', exact=True).click()
        button = page.get_by_role('button', name='生成 SAMPLE 快照')
        page.keyboard.press('Tab')
        button.focus()
        assert button.evaluate("el => el.matches(':focus-visible')")
        page.keyboard.press('Enter')
        page.wait_for_function("() => document.querySelector('[data-run-submit] [name=snapshot_ref]').value.length === 64")
        form = page.locator('[data-run-submit]')
        form.locator('[data-run-template]').select_option('baseline')
        assert '历史回撤' in form.locator('[name="question"]').input_value()
        assert form.locator('[name="model_config_ref"]').input_value() == 'offline'
        form.locator('button[type="submit"]').click()
        href = wait_report(page)
        assert '耗时' in page.locator('[data-run-budget]').inner_text()
        assert '120 秒' in page.locator('[data-run-budget]').inner_text()
        page.screenshot(path=str(artifacts / 'first-use-complete-375.png'), full_page=True)
        job_id = app.research_service.list_jobs()['jobs'][0]['job_id']
        reference = app.research_service.get_job(job_id)['snapshot_ref']
        report = page.request.get(base + href)
        assert report.ok and reference in report.text()
        offline = artifacts / 'first-report.html'
        offline.write_bytes(report.body())
        page.goto(offline.absolute().as_uri())
        assert 'SAMPLE' in page.locator('body').inner_text()
        page.goto('about:blank')
    archive = tmp_path / 'private-state.zip'
    backup_state(db, archive)
    restored = restore_state(archive, tmp_path / 'restored')
    app = LocalApplication(LocalAppConfig(db_path=str(restored), offline=True))
    with serve(app) as base:
        page.goto(base + f'/research/jobs?job_id={job_id}')
        assert wait_report(page) == href
        assert app.research_service.get_job(job_id)['snapshot_ref'] == reference
        assert app.research_service.get_job(job_id)['attempts'] == 1
        assert page.request.get(base + href).ok
        manifest = next((restored.parent / 'artifacts' / 'reports').rglob('manifest.json'))
        manifest.write_text('{}')
        page.reload()
        page.locator('[data-run-failure]').filter(has_text='需要处理').wait_for()
        assert page.locator('[data-run-report]').is_hidden()
        assert 'PUBLICATION_INVALID' in page.locator('[data-run-failure]').inner_text()
        page.screenshot(path=str(artifacts / 'corrupt-report-recovery.png'), full_page=True)
        page.goto(base + '/diagnostics')
        with page.expect_download() as download:
            page.get_by_role('link', name='下载脱敏诊断').click()
        exported = artifacts / 'shareable-diagnostics.json'
        download.value.save_as(exported)
        raw = exported.read_text()
        assert str(tmp_path) not in raw and 'local.sqlite' not in raw
        assert reference not in raw
        page.goto('about:blank')
    assert not diagnostics['errors']


def test_user_configures_own_api_then_explicitly_captures_and_researches(tmp_path, observed_page, monkeypatch):
    import finahinking.data.user_api as api

    page, artifacts, diagnostics = observed_page
    monkeypatch.setattr(api, '_default_resolver', lambda *_: ('93.184.216.34',))
    source = FixtureMarketDataSource(points=20).snapshot('AAA')
    records = [{key: getattr(item, key).isoformat() if key in {'timestamp', 'available_at'} else getattr(item, key) for key in ('instrument', 'timestamp', 'available_at', 'open', 'high', 'low', 'close', 'volume')} for item in source.observations]

    class Transport:
        def __init__(self):
            self.calls = []

        def request(self, url, *, headers, timeout, allow_redirects=False):
            self.calls.append(dict(headers))
            return TransportResponse(200, {'content-type': 'application/json'}, json.dumps({'data': records}).encode(), url)

    transport = Transport()
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / 'local.sqlite'), offline=True), data_transport=transport, data_credential_store=InMemoryDataCredentialStore())
    with serve(app) as base:
        page.goto(base + '/settings/data-connections')
        form = page.locator('form[action="/api/data/connections"]')
        form.locator('[name="connection_id"]').fill('trial-prices')
        form.locator('[name="display_name"]').fill('我的日频接口')
        form.locator('[name="base_url"]').fill('https://api.trial.invalid/prices')
        form.locator('[name="auth_mode"]').select_option('bearer')
        form.locator('[name="api_key"]').fill('trial-private-credential')
        form.locator('[name="records_path"]').fill('data')
        form.locator('[name="field_mapping"]').fill(json.dumps({key: key for key in records[0]}))
        with page.expect_response('**/api/data/connections') as saved:
            form.locator('button[type="submit"]').click()
        assert saved.value.status == 201, (saved.value.status, saved.value.text(), saved.value.request.all_headers().get('origin'))
        assert page.request.post(base + '/api/data/connections', data={'_csrf': app.csrf_token}, headers={'Origin': 'null'}).status == 403
        page.wait_for_url('**/api/data/connections')
        assert not transport.calls
        assert 'trial-private-credential' not in page.content()
        page.goto(base + '/research/jobs')
        page.wait_for_function("() => document.querySelector('[data-snapshot-capture] [name=connection_id]').options.length === 2")
        assert '已有接口配置' in page.locator('[data-run-data-state]').inner_text()
        capture = page.locator('[data-snapshot-capture]')
        capture.locator('[name="connection_id"]').select_option('trial-prices')
        for name, value in (('start', '2026-01-01'), ('end', '2026-10-01'), ('as_of', '2026-10-01')):
            capture.locator(f'[name="{name}"]').fill(value)
        capture.locator('button[type="submit"]').click()
        page.wait_for_function("() => document.querySelector('[data-run-submit] [name=snapshot_ref]').value.length === 64")
        assert transport.calls and transport.calls[0]['Authorization'] == 'Bearer trial-private-credential'
        page.locator('[data-run-submit] button[type="submit"]').click()
        href = wait_report(page)
        job = app.research_service.list_jobs()['jobs'][0]
        assert job['data_mode'] != 'SAMPLE'
        assert 'trial-private-credential' not in page.request.get(base + href).text()
        page.screenshot(path=str(artifacts / 'user-api-complete.png'), full_page=True)
    assert not diagnostics['errors']


def test_large_task_history_is_paginated_under_cpu_throttle(tmp_path, observed_page):
    page, artifacts, diagnostics = observed_page
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / 'local.sqlite'), offline=True))
    with serve(app) as base:
        app.research_service.close()
        ref = app.research_service.snapshots.save(FixtureMarketDataSource(points=20).snapshot('AAA'))
        for index in range(25):
            app.research_service.submit({'idempotency_key': f'list-{index}', 'snapshot_ref': ref, 'instrument': 'AAA', 'as_of': '2026-10-01', 'question': '检查有界历史列表', 'model_config_ref': 'offline'})
        session = page.context.new_cdp_session(page)
        session.send('Emulation.setCPUThrottlingRate', {'rate': 4})
        page.set_viewport_size({'width': 375, 'height': 812})
        start = time.monotonic()
        page.goto(base + '/research/jobs')
        page.wait_for_selector('[data-workspace-state="READY"]', timeout=20_000)
        assert page.locator('[data-run-history] li').count() == 20
        first = page.locator('[data-run-history]').inner_text()
        page.locator('[data-run-history-next]').focus()
        page.keyboard.press('Enter')
        page.wait_for_function("() => document.querySelector('[data-run-history-page]').textContent.includes('21–25')")
        assert page.locator('[data-run-history] li').count() == 5
        assert page.locator('[data-run-history]').inner_text() != first
        assert page.locator('[data-run-history-next]').is_disabled()
        page.locator('[data-run-history-prev]').click()
        page.wait_for_function("() => document.querySelector('[data-run-history-page]').textContent.includes('1–20')")
        assert page.locator('[data-run-history] li').count() == 20
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
        diagnostics['metrics']['cpu_throttled_history_journey_ms'] = round((time.monotonic() - start) * 1000, 2)
        diagnostics['metrics']['cpu_throttle_rate'] = 4
        diagnostics['metrics']['rendered_history_limit'] = 20
        assert diagnostics['metrics']['cpu_throttled_history_journey_ms'] < 20_000
        page.screenshot(path=str(artifacts / 'paged-history-throttle-375.png'), full_page=True)
        session.detach()
        # End the queued-job poller while its HTTP server is still available.
        page.goto('about:blank')
    assert not diagnostics['errors']


def test_history_paging_preserves_multi_instrument_submission_intent(tmp_path, observed_page):
    page, _, diagnostics = observed_page
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / 'app.sqlite'), offline=True))
    a = FixtureMarketDataSource(points=20).snapshot('AAA')
    b = FixtureMarketDataSource(points=20).snapshot('BBB')
    snapshot = replace(a, observations=(*a.observations, *b.observations))
    snapshot_reads = []
    page.on('request', lambda request: snapshot_reads.append(request.url) if request.url.endswith('/api/research/snapshots') else None)
    with serve(app) as base:
        app.research_service.close()
        ref = app.research_service.snapshots.save(snapshot)
        for index in range(21):
            app.research_service.submit({'idempotency_key': f'intent-{index}', 'snapshot_ref': ref, 'instrument': 'AAA', 'as_of': '2026-10-01', 'question': '检查有界历史列表', 'model_config_ref': 'offline'})
        page.goto(base + '/research/jobs')
        page.wait_for_selector('[data-workspace-state="READY"]')
        form = page.locator('[data-run-submit]')
        form.locator('[name="snapshot_ref"]').select_option(ref)
        form.locator('[name="instrument"]').fill('BBB')
        form.locator('[name="question"]').fill('保留我选择的 BBB 和研究问题')
        form.locator('[name="as_of"]').fill('2026-10-01')
        page.locator('[data-run-history-next]').click()
        page.wait_for_function("() => document.querySelector('[data-run-history-page]').textContent.includes('21–21')")
        assert form.locator('[name="instrument"]').input_value() == 'BBB'
        assert len(snapshot_reads) == 1
        page.locator('[data-run-history-prev]').click()
        page.wait_for_function("() => document.querySelector('[data-run-history-page]').textContent.includes('1–20')")
        form.locator('[name="instrument"]').fill('UNFINISHED-DRAFT')
        page.locator('[data-run-history-next]').click()
        page.wait_for_function("() => document.querySelector('[data-run-history-page]').textContent.includes('21–21')")
        assert form.locator('[name="instrument"]').input_value() == 'UNFINISHED-DRAFT'
        assert len(snapshot_reads) == 1
        form.locator('[name="instrument"]').fill('BBB')
        page.locator('[data-run-refresh]').click()
        page.wait_for_selector('[data-workspace-state="READY"]')
        assert form.locator('[name="instrument"]').input_value() == 'BBB'
        assert form.locator('[name="question"]').input_value() == '保留我选择的 BBB 和研究问题'
        form.locator('button[type="submit"]').click()
        page.wait_for_function("() => document.querySelector('[data-run-identity]').textContent.includes('BBB')")
        job = next(item for item in app.research_service.list_jobs()['jobs'] if item['question'] == '保留我选择的 BBB 和研究问题')
        assert job['instrument'] == 'BBB' and job['snapshot_ref'] == ref
    assert not diagnostics['errors']

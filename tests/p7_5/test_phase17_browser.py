"""First research, stale-input checks and evidence via real HTTP and spawn."""
import json
import re
from dataclasses import replace

import pytest
from playwright.sync_api import expect

from finahinking.p8_2.data_sources import FixtureMarketDataSource
from tests.data.test_user_api import FakeTransport, response
from tests.p7_5.test_phase4_browser import browser, observed_page
from tests.p7_5.test_phase5_browser import http_app
from tests.research.test_application_service import payload

__all__ = ["browser", "http_app", "observed_page"]


def select_input(page, base, ref):
    page.goto(base + "/research/jobs")
    page.locator("[data-run-submit] [name=snapshot_ref] option").nth(1).wait_for(state="attached")
    form = page.locator('[data-run-submit]')
    form.locator('[name=snapshot_ref]').select_option(ref)
    form.locator('[name=as_of]').fill("2026-10-01")
    return form


def test_own_connection_capture_check_spawn_and_read_report(http_app, observed_page):
    app, base = http_app
    page, artifacts, _ = observed_page
    records = [{**item.to_dict(), "suspended": False, "industry": "Industrials"} for item in FixtureMarketDataSource(points=20).snapshot("AAA").observations]
    transport = FakeTransport([response({"data": records}, url="https://93.184.216.34/api")])
    app._data_transport = transport
    page.goto(base + "/settings/data-connections")
    fields = {"connection_id": "my-data", "display_name": "我的研究数据", "base_url": "https://93.184.216.34/api", "records_path": "data", "field_mapping": json.dumps({name: name for name in ("instrument", "timestamp", "available_at", "open", "high", "low", "close", "volume", "suspended", "industry")})}
    for name, value in fields.items():
        page.locator(f'[name={name}]').fill(value)
    page.locator('form button[type=submit]').click()
    page.wait_for_url('**/api/data/connections')
    assert transport.calls == []
    page.goto(base + "/research/jobs")
    page.locator("[data-snapshot-capture] [name=connection_id] option").nth(1).wait_for(state="attached")
    form = page.locator('[data-snapshot-capture]')
    form.locator('[name=connection_id]').select_option('my-data')
    for name, value in {"start": "2026-01-01", "end": "2026-10-01", "as_of": "2026-10-01"}.items():
        form.locator(f'[name={name}]').fill(value)
    form.locator('[name=price_currency]').select_option('USD')
    form.locator('[name=volume_unit]').select_option('shares')
    form.locator('button[type=submit]').click()
    expect(page.locator("[data-run-submit] [name=snapshot_ref]")).to_have_value(re.compile(r"^[a-f0-9]{64}$"))
    page.locator('[data-readiness-check]').click()
    page.wait_for_selector('[data-readiness-state="NEEDS_REVIEW"]')
    assert len(transport.calls) == 1 and app.research_service.list_jobs()["total"] == 0
    assert 'CAPTURED' in page.locator('[data-readiness-summary]').inner_text()
    assert page.locator('[data-readiness-passed] li').count() > 5
    assert not page.locator('[data-readiness-passed]').is_visible()
    page.screenshot(path=str(artifacts / 'ready.png'), full_page=True, animations='disabled')
    page.locator('[data-run-submit] button[type=submit]').click()
    page.wait_for_selector('[data-job-status="completed"]', timeout=30000)
    assert page.locator('[data-run-explanation]').get_by_role('heading', name='结论').is_visible()
    expect(page.locator('[data-run-history]')).to_contain_text('运行完成')
    assert 'SAMPLE' not in page.locator('[data-run-explanation]').inner_text()
    assert not page.locator('[data-run-metrics]').is_visible()
    links = page.locator('[data-run-explanation] a').evaluate_all('(nodes) => nodes.map(node => node.getAttribute("href"))')
    for link in links:
        report = page.request.get(base + link.split('#')[0])
        assert report.ok and f'id="{link.split("#")[1]}"' in report.text()
    page.screenshot(path=str(artifacts / 'completed.png'), full_page=True, animations='disabled')
    offline = page.context.browser.new_context(java_script_enabled=False)
    try:
        report_page = offline.new_page()
        report_page.goto(base + page.locator('[data-run-report]').get_attribute('href'))
        assert report_page.locator('[data-research-explanation]').is_visible()
        assert report_page.locator('[data-research-explanation]').get_by_text('下一步', exact=True).is_visible()
        report_page.screenshot(path=str(artifacts / 'offline-summary.png'), full_page=True)
    finally:
        offline.close()
    page.reload()
    page.wait_for_selector('[data-job-status="completed"]')
    assert app.research_service.list_jobs()["total"] == 1 and len(transport.calls) == 1


def test_blocked_check_repairs_in_place_and_never_queues(http_app, observed_page):
    app, base = http_app
    page, artifacts, _ = observed_page
    ref = app.research_service.snapshots.save(FixtureMarketDataSource(points=3).snapshot('AAA'))
    form = select_input(page, base, ref)
    form.locator('button[type=submit]').click()
    page.wait_for_selector('[data-readiness-state="BLOCKED"]')
    assert app.research_service.list_jobs()["total"] == 0
    assert '至少需要 4' in page.locator('[data-readiness-results]').inner_text()
    assert page.locator('[data-readiness-summary]').evaluate('el => el === document.activeElement')
    page.locator('[data-readiness-results] a[href="/research/jobs#prepare-data"]').first.click()
    assert page.locator('[name=question]').input_value()
    assert page.evaluate('document.activeElement.id') == 'prepare-data'
    page.screenshot(path=str(artifacts / 'blocked.png'), full_page=True, animations='disabled')


def test_late_check_cannot_submit_edited_inputs(http_app, observed_page):
    app, base = http_app
    page, _, _ = observed_page
    ref = app.research_service.snapshots.save(FixtureMarketDataSource(points=20).snapshot('AAA'))
    form = select_input(page, base, ref)
    held = []
    page.route('**/api/research/readiness', lambda route: held.append(route))
    form.locator('button[type=submit]').click()
    page.wait_for_selector('[data-readiness-state="CHECKING"]')
    form.locator('[name=question]').fill('改写后的研究问题')
    assert held
    held.pop().continue_()
    page.wait_for_selector('[data-readiness-state="STALE"]')
    assert app.research_service.list_jobs()["total"] == 0
    page.unroute('**/api/research/readiness')
    page.locator('[data-readiness-check]').click()
    page.wait_for_selector('[data-readiness-state="NEEDS_REVIEW"]')
    assert app.research_service.list_jobs()["total"] == 0


def test_restore_preserves_base_input_and_clears_paid_authorization(http_app, observed_page):
    app, base = http_app
    page, _, _ = observed_page
    app.research_service.close()
    body = payload(app.research_service, budget={"max_wall_seconds": 35})
    queued = app.research_service.submit(body)
    page.goto(base + '/research/jobs?job_id=' + queued['job_id'])
    page.wait_for_selector('[data-job-status="queued"]')
    page.locator('[data-run-cancel]').click()
    page.wait_for_selector('[data-job-status="cancelled"]')
    page.locator('[name=allow_model_calls]').check()
    page.locator('[data-run-restore]').click()
    form = page.locator('[data-run-submit]')
    assert form.locator('[name=snapshot_ref]').input_value() == body['snapshot_ref']
    assert form.locator('[name=question]').input_value() == body['question']
    assert form.locator('[name=max_wall_seconds]').input_value() == '35'
    assert form.locator('[name=allow_model_calls]').is_checked() is False
    assert app.research_service.list_jobs()["total"] == 1
    assert '取消' in page.locator('[data-run-explanation]').inner_text()


@pytest.mark.parametrize('width,height', [(1440, 900), (1920, 1080), (375, 812)])
def test_guidance_layout_keyboard_and_reduced_motion(http_app, observed_page, width, height):
    app, base = http_app
    page, artifacts, _ = observed_page
    page.set_viewport_size({"width": width, "height": height})
    page.emulate_media(reduced_motion='reduce')
    snapshot = FixtureMarketDataSource(points=4).snapshot('AAA')
    ref = app.research_service.snapshots.save(replace(snapshot, observations=tuple(replace(row, available_at=None) for row in snapshot.observations)))
    select_input(page, base, ref)
    page.locator('[data-readiness-check]').focus()
    page.keyboard.press('Enter')
    page.wait_for_selector('[data-readiness-state="BLOCKED"]')
    assert page.locator('[data-first-research-steps] li').count() == 4
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
    page.screenshot(path=str(artifacts / f'guidance-{width}.png'), full_page=True, animations='disabled')
    assert app.research_service.list_jobs()["total"] == 0


def test_failed_check_keeps_inputs_and_recovers_without_creating_jobs(http_app, observed_page):
    app, base = http_app
    page, _, diagnostics = observed_page
    ref = app.research_service.snapshots.save(FixtureMarketDataSource(points=20).snapshot('AAA'))
    form = select_input(page, base, ref)
    form.locator('[name=question]').fill('保留这个研究问题')
    page.route('**/api/research/readiness', lambda route: route.fulfill(status=503, content_type='application/json', body='{"error":"unavailable"}'))
    form.locator('button[type=submit]').click()
    page.wait_for_selector('[data-readiness-state="ERROR"]')
    assert form.locator('[name=question]').input_value() == '保留这个研究问题'
    assert app.research_service.list_jobs()["total"] == 0
    page.unroute('**/api/research/readiness')
    diagnostics['errors'].clear()  # Deliberate HTTP 503, no production error ignored.
    page.locator('[data-readiness-check]').click()
    page.wait_for_selector('[data-readiness-state="NEEDS_REVIEW"]')
    assert app.research_service.list_jobs()["total"] == 0


def test_stage_explanation_preserves_expansion_and_focus_during_polling(http_app, observed_page):
    app, base = http_app
    page, _, _ = observed_page
    app.research_service.close()
    queued = app.research_service.submit(payload(app.research_service))
    page.goto(base + '/research/jobs?job_id=' + queued['job_id'])
    page.wait_for_selector('[data-job-status="queued"]')
    summary = page.get_by_text('为什么这样运行：阶段输入与结果', exact=True)
    summary.focus()
    page.keyboard.press('Enter')
    with page.expect_response(lambda response: response.url.endswith('/api/research/jobs/' + queued['job_id'])):
        pass
    assert summary.evaluate('el => el === document.activeElement')
    assert page.locator('[data-run-explanation] details').get_attribute('open') is not None
    app.research_service.cancel(queued['job_id'])
    page.wait_for_selector('[data-job-status="cancelled"]')
    assert summary.evaluate('el => el === document.activeElement')
    assert page.locator('[data-run-explanation] details').get_attribute('open') is not None

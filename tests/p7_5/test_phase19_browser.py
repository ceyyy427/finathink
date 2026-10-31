"""Comparison, package recovery and keyboard use through the actual browser."""
import json

import pytest
from playwright.sync_api import expect

from tests.p7_5.test_phase4_browser import browser, observed_page
from tests.research.test_phase18_api import app_http
from tests.research.test_phase19_api import completed

__all__ = ['app_http', 'browser', 'observed_page']


def enter(app_http, page):
    app, http, _ = app_http
    base = http.__closure__[0].cell_contents
    page.goto(base+'/research/compare')
    page.locator('[data-reproduction-ready="true"]').wait_for()
    return app, base


def test_compare_export_import_recompute_and_reload(app_http, observed_page):
    page, artifacts, _ = observed_page
    app = app_http[0]
    first, second = completed(app, 'first'), completed(app, 'second')
    _, _base = enter(app_http, page)
    posts = []
    page.on('request', lambda req: posts.append(req.url) if req.method == 'POST' else None)
    page.locator('[data-repro-left]').select_option(first)
    page.locator('[data-repro-right]').select_option(second)
    button = page.get_by_role('button', name='比较研究条件', exact=True)
    button.focus()
    assert button.evaluate('el => getComputedStyle(el).outlineStyle') == 'solid'
    page.keyboard.press('Enter')
    expect(page.locator('[data-repro-comparison]')).to_contain_text('条件一致')
    expect(page.locator('[data-repro-comparison]')).to_contain_text('风险政策')
    expect(page.locator('[data-repro-evidence]')).to_contain_text('方差贡献')
    assert not posts
    page.locator('[data-repro-data]').check()
    with page.expect_download() as downloaded:
        page.get_by_role('button', name='导出左侧研究包').click()
    package = json.loads(downloaded.value.path().read_text())
    assert package['snapshot']
    page.locator('[data-repro-file]').set_input_files({'name': 'research.json', 'mimeType': 'application/json', 'buffer': json.dumps(package).encode()})
    page.get_by_role('button', name='恢复研究包', exact=True).click()
    page.get_by_role('button', name='复算已恢复产物', exact=True).click()
    expect(page.locator('[data-repro-receipt]')).to_contain_text('已覆盖产物一致')
    page.screenshot(path=str(artifacts/'comparison-restored.png'), full_page=True)
    count = len(posts)
    page.reload()
    page.locator('[data-reproduction-ready="true"]').wait_for()
    expect(page.locator('[data-repro-receipt]')).to_contain_text('已覆盖产物一致')
    assert len(posts) == count and app.research_service.list_jobs()['total'] == 2


@pytest.mark.parametrize('viewport', [(1440,900), (1920,1080), (375,812)])
def test_empty_responsive_reduced_motion(app_http, observed_page, viewport):
    page, artifacts, _ = observed_page
    page.set_viewport_size({'width': viewport[0], 'height': viewport[1]})
    page.emulate_media(reduced_motion='reduce')
    enter(app_http, page)
    expect(page.locator('[data-repro-comparison]')).to_contain_text('先完成研究')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    assert page.get_by_role('button', name='比较研究条件', exact=True).is_disabled()
    page.screenshot(path=str(artifacts/'empty-responsive.png'), full_page=True)


def test_invalid_file_shows_chinese_error_without_creating_job(app_http, observed_page):
    page, _, _ = observed_page
    app, _ = enter(app_http, page)
    page.locator('[data-repro-file]').set_input_files({'name': 'bad.json', 'mimeType': 'application/json', 'buffer': b'not json'})
    page.get_by_role('button', name='恢复研究包', exact=True).click()
    expect(page.locator('[data-repro-error]')).to_contain_text('JSON')
    assert app.research_service.list_jobs()['total'] == 0


def test_late_comparison_cannot_replace_changed_selection(app_http, observed_page):
    page, _, _ = observed_page
    app = app_http[0]
    first, second = completed(app, 'late-one'), completed(app, 'late-two')
    enter(app_http, page)
    page.locator('[data-repro-left]').select_option(first)
    page.locator('[data-repro-right]').select_option(second)
    pending = []
    page.route('**/api/research/reproduction/compare?*', lambda route: pending.append(route))
    page.get_by_role('button', name='比较研究条件', exact=True).click()
    expect(page.locator('[data-repro-comparison]')).to_contain_text('正在读取')
    page.locator('[data-repro-right]').select_option(first)
    assert pending
    pending[0].fulfill(response=page.request.get(pending[0].request.url))
    expect(page.get_by_role('button', name='比较研究条件', exact=True)).to_be_enabled()
    expect(page.locator('[data-repro-comparison]')).to_contain_text('选择已变化')
    assert page.locator('[data-repro-evidence]').inner_text() == ''


def test_filled_mobile_evidence_is_readable_and_no_script_keeps_inventory(app_http, observed_page, browser):
    page, artifacts, _ = observed_page
    app = app_http[0]
    first = completed(app, 'mobile')
    page.set_viewport_size({'width':375,'height':812})
    _, base = enter(app_http, page)
    page.locator('[data-repro-left]').select_option(first)
    page.locator('[data-repro-right]').select_option(first)
    page.get_by_role('button',name='比较研究条件',exact=True).click()
    expect(page.locator('[data-repro-comparison]')).to_contain_text('条件一致')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    page.screenshot(path=str(artifacts/'filled-mobile.png'), full_page=True)
    context = browser.new_context(java_script_enabled=False)
    try:
        native = context.new_page()
        native.goto(base+'/research/compare')
        expect(native.get_by_role('table',name='Research run comparison')).to_contain_text(first)
        expect(native.get_by_text('已有研究和量化条件可在本页下方阅读。交互比较、导出与恢复需要启用 JavaScript。', exact=True)).to_be_visible()
    finally:
        context.close()

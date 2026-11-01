"""Installed browser bundle reads Chinese public knowledge over real HTTP."""
import pytest

from tests.p7_5.test_phase4_browser import browser, http_app, observed_page

__all__ = ['browser', 'http_app', 'observed_page']


@pytest.mark.parametrize('width,height', [(1440, 900), (1920, 1080), (375, 812)])
def test_knowledge_layout_keyboard_detail_and_preparation(http_app, observed_page, width, height):
    app, base = http_app
    page, artifacts, diagnostics = observed_page
    requests, external = [], []
    page.on('request', lambda r: requests.append(r.url) if '/api/knowledge/search' in r.url else None)
    page.on('request', lambda r: external.append(r.url) if not r.url.startswith(base) else None)
    before = app.research_service.list_jobs()['total']
    page.set_viewport_size({'width': width, 'height': height})
    page.emulate_media(reduced_motion='reduce')
    page.goto(base + '/knowledge', wait_until='networkidle')
    assert page.get_by_role('heading', name='量化知识库', exact=True).count() == 1
    assert requests == []  # SSR is the first result; mounting does not fetch it again.
    assert page.locator('[data-knowledge-results] .knowledge-tile').count() == 16
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
    page.keyboard.press('Tab')
    assert page.evaluate("document.activeElement.matches(':focus-visible') && getComputedStyle(document.activeElement).outlineStyle !== 'none'")
    assert page.locator('.knowledge-tile').first.evaluate("el => getComputedStyle(el).transitionDuration") == '0s'
    page.screenshot(path=str(artifacts / f'knowledge-{width}.png'), full_page=True)
    field = page.locator('[data-knowledge-search] [name=q]')
    field.fill('回撤')
    field.press('Enter')
    page.wait_for_selector('[data-knowledge-state=ready]')
    assert len(requests) == 1
    page.get_by_role('link', name='回撤、峰值与修复', exact=True).click()
    assert page.get_by_role('heading', name='公式与口径').count() == 1
    assert page.get_by_role('heading', name='容易误解的地方').count() == 1
    page.get_by_text('回撤、峰值与修复 · 手算核对', exact=True).click()
    assert '25%' in page.locator('.knowledge-detail').inner_text()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
    page.screenshot(path=str(artifacts / f'knowledge-detail-{width}.png'), full_page=True)
    page.get_by_role('link', name='检查研究所需数据').click()
    assert 'NEEDS_DATA' in page.locator('[data-knowledge-preparation]').inner_text()
    assert page.locator('[data-knowledge-preparation] a[href*="github.com"]').count() > 0
    page.screenshot(path=str(artifacts / f'knowledge-preparation-{width}.png'), full_page=True)
    assert app.research_service.list_jobs()['total'] == before and not external
    diagnostics['metrics']['initial_search_requests'] = 0


def test_search_empty_error_retry_filters_paths_and_legacy(http_app, observed_page):
    app, base = http_app
    page, _, _ = observed_page
    page.goto(base + '/knowledge', wait_until='networkidle')
    field = page.locator('[data-knowledge-search] [name=q]')
    field.fill('火星陨石玄武岩')
    field.press('Enter')
    page.wait_for_selector('[data-knowledge-state=empty]')
    assert page.locator('[data-knowledge-empty]').is_visible()

    def malformed(route):
        route.fulfill(status=200, content_type='application/json', body='{"schema_version":"wrong"}')

    page.route('**/api/knowledge/search?*', malformed)
    field.fill('Rank IC')
    field.press('Enter')
    page.wait_for_selector('[data-knowledge-state=error]')
    assert field.input_value() == 'Rank IC'
    assert page.locator('[data-knowledge-results]').is_hidden()
    page.unroute('**/api/knowledge/search?*', malformed)
    page.get_by_role('button', name='重新检索').click()
    page.wait_for_selector('[data-knowledge-state=ready]')
    assert page.get_by_role('link', name='Rank IC与并列排名').count() == 1
    page.locator('[name=domain]').select_option('fundamentals')
    field.fill('')
    field.press('Enter')
    page.wait_for_selector('[data-knowledge-state=ready]')
    assert page.locator('[data-knowledge-results] .knowledge-tile').count() == 10
    page.get_by_role('link', name='从数据到可用信息', exact=True).click()
    assert page.locator('.knowledge-detail ol li').count() == 6
    page.goto(base + '/knowledge/ols', wait_until='networkidle')
    assert page.locator('[data-finathink-knowledge]').count() == 1
    assert page.locator('math').count() > 0
    assert app.research_service.list_jobs()['total'] == 0


@pytest.mark.parametrize('observed_page', [{'java_script_enabled': False}], indirect=True)
def test_no_javascript_search_and_versioned_preparation(http_app, observed_page):
    app, base = http_app
    page, _, _ = observed_page
    page.goto(base + '/knowledge')
    page.locator('[name=q]').fill('PIT')
    page.get_by_role('button', name='检索知识').click()
    page.get_by_role('link', name='时点可用性（PIT）', exact=True).click()
    page.get_by_role('link', name='检查研究所需数据').click()
    page.get_by_role('link', name='带入研究问题').click()
    assert 'knowledge=point-in-time' in page.url
    assert page.locator('[data-knowledge-preparation]').count() == 1
    assert 'factor-mining-workflow' in page.locator('[data-knowledge-preparation]').inner_text()
    assert '知识:point-in-time@1.0.0' in page.locator('[data-run-workspace] [name=question]').input_value()
    assert app.research_service.list_jobs()['total'] == 0

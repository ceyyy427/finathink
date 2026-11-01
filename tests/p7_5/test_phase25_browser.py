"""Explicit scorecard registration, accessible layouts and published real-worker data."""
import pytest

from tests.p7_5.test_phase4_browser import browser, http_app, observed_page

__all__ = ['browser', 'http_app', 'observed_page']


@pytest.mark.parametrize('width,height', [(1440,900),(1920,1080),(375,812)])
def test_suite_layout_keyboard_empty_and_invalid_selection(http_app, observed_page, width, height):
    app, base = http_app
    page, artifacts, _ = observed_page
    page.set_viewport_size({'width':width,'height':height})
    page.emulate_media(reduced_motion='reduce')
    page.goto(base+'/research/factor-suites',wait_until='networkidle')
    assert page.get_by_role('heading',name='因子检验与评分卡',exact=True).count()==1
    assert page.locator('[name=factor]').count()==80
    assert page.locator('[data-suite-counterexample]').count()>=20
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
    page.keyboard.press('Tab')
    assert page.evaluate("document.activeElement.matches(':focus-visible')")
    page.get_by_role('button',name='登记候选并提交后台检验').click()
    assert '请选1至24' in page.locator('[data-suite-status]').inner_text()
    for box in page.locator('[name=factor]').all()[:25]:box.check()
    page.get_by_role('button',name='登记候选并提交后台检验').click()
    assert '请选1至24' in page.locator('[data-suite-status]').inner_text()
    assert app.research_service.list_jobs()['total']==0
    page.screenshot(path=str(artifacts/f'factor-suite-{width}.png'),full_page=True)


def test_suite_browser_registers_real_worker_and_reads_scorecard(http_app, observed_page):
    app,base=http_app
    page,artifacts,_=observed_page
    page.goto(base+'/research/factor-suites',wait_until='networkidle')
    page.locator('[name=factor][value="lib-trend-roc"]').check()
    page.get_by_role('button',name='登记候选并提交后台检验').click()
    page.wait_for_url('**/research/factor-suites?study=*',timeout=60000)
    # Refresh is explicit; reading the page cannot create another study.
    for _ in range(100):
        if page.locator('[data-suite-record][data-state="VALIDATED"]').count():break
        if page.locator('[data-suite-record][data-state="VALIDATION_FAILED"]').count():pytest.fail(page.locator('[data-suite-record]').inner_text())
        page.wait_for_timeout(500)
        page.get_by_role('button',name='刷新研究状态').click()
        page.wait_for_load_state('networkidle')
    assert page.locator('[data-suite-record][data-state="VALIDATED"]').count()==1
    assert page.locator('[data-suite-trial]').count()==1
    page.locator('[data-suite-trial] summary').click()
    assert 'mean_ic' in page.locator('[data-suite-trial]').inner_text()
    assert page.get_by_role('rowheader',name='截面秩IC',exact=True).is_visible()
    assert app.research_service.list_jobs()['total']==1
    assert page.get_by_role('link',name='打开离线研究报告').get_attribute('href').startswith('/research/')
    page.screenshot(path=str(artifacts/'factor-suite-result.png'),full_page=True)


def test_disabled_javascript_never_sends_private_research_in_query(http_app,browser):
    app,base=http_app
    context=browser.new_context(java_script_enabled=False)
    try:
        page=context.new_page()
        page.goto(base+'/research/factor-suites',wait_until='networkidle')
        page.locator('[name=question]').fill('private-research-question')
        page.locator('[name=factor]').first.check()
        with page.expect_request(lambda r:r.method=='POST') as requested:
            page.get_by_role('button',name='登记候选并提交后台检验').click()
        assert requested.value.url==base+'/api/research/factor-suites'
        assert 'private-research-question' not in page.url
        assert app.research_service.list_jobs()['total']==0
    finally:context.close()

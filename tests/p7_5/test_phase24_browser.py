"""Library navigation, data preparation and true queued submission in the shipped UI."""
import json

import pytest

from tests.p7_5.test_phase4_browser import browser, http_app, observed_page
from tests.research.test_factor_library_journey import prepare

__all__ = ['browser','http_app','observed_page']


@pytest.mark.parametrize('width,height',[(1440,900),(1920,1080),(375,812)])
def test_library_layout_keyboard_empty_detail_and_input_errors(http_app,observed_page,width,height):
    app,base=http_app;page,artifacts,_=observed_page
    page.set_viewport_size({'width':width,'height':height});page.emulate_media(reduced_motion='reduce')
    page.goto(base+'/research/factor-library',wait_until='networkidle')
    assert page.get_by_role('heading',name='因子定义库',exact=True).count()==1
    assert page.locator('.factor-library-card').count()>=80
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
    page.keyboard.press('Tab')
    assert page.evaluate("document.activeElement.matches(':focus-visible') && getComputedStyle(document.activeElement).outlineStyle !== 'none'")
    page.screenshot(path=str(artifacts/f'factor-library-{width}.png'),full_page=True)
    page.locator('[name=q]').fill('不存在的公式名字')
    page.get_by_role('button',name='筛选',exact=True).click()
    assert page.locator('.empty-state').count()==1
    page.get_by_text('查看全部来源卡与审阅状态',exact=True).click()
    reference=page.locator('[data-source-card][data-adaptation="REFERENCE_ONLY"]').first
    reference.locator('summary').click()
    assert '仅作来源参考' in reference.locator('[data-source-status]').inner_text()
    assert '原式：' in reference.locator('[data-source-status]').inner_text()
    assert '证据：' in reference.locator('[data-source-status]').inner_text()
    assert reference.locator('[data-source-formula]').is_visible()
    assert reference.locator('[data-source-link]').get_attribute('href').startswith('https://github.com/')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
    page.goto(base+'/research/factor-library?factor=lib-trend-roc',wait_until='networkidle')
    assert page.get_by_role('heading',name='固定定义与适用范围',exact=True).count()==1
    source=page.locator('[data-source-card]').first
    source.locator('summary').click()
    assert 'return close / close.shift(n) - 1' in source.locator('[data-source-formula]').inner_text()
    assert page.get_by_role('heading',name='独立数值样例',exact=True).is_visible()
    assert '观察点' in page.locator('[data-definition-policy="window_semantics"]').inner_text()
    assert page.locator('[data-definition-policy]').count()==6
    assert page.locator('[data-numeric-inputs] tbody tr').count()>=1
    assert '14/11-1' in page.locator('[data-numeric-calculation]').inner_text()
    assert '0.272727' in page.locator('[data-numeric-expected]').inner_text()
    assert page.locator('[data-definition-notes] a[href="/knowledge/items/momentum"]').count()==1
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
    page.locator('[name=contract]').fill('{bad-json')
    page.get_by_role('button',name='固定定义与输入，提交后台验证',exact=True).click()
    assert 'JSON 格式有误' in page.locator('[data-library-status]').inner_text()
    assert page.locator('[name=contract]').input_value()=='{bad-json'
    page.screenshot(path=str(artifacts/f'factor-library-detail-{width}.png'),full_page=True)
    assert app.research_service.list_jobs()['total']==0


def test_library_card_submit_to_existing_worker_and_history(http_app,observed_page):
    app,base=http_app;page,_,_=observed_page
    payload=prepare(app.research_service,'lib-trend-roc')
    page.goto(base+'/research/factor-library?factor=lib-trend-roc',wait_until='networkidle')
    page.locator('[name=contract]').fill(json.dumps(payload['contract']))
    page.locator('[name=attachment_refs]').fill(json.dumps(payload['attachment_refs']))
    page.get_by_role('button',name='固定定义与输入，提交后台验证',exact=True).click()
    page.get_by_role('link',name='进入因子实验历史查看任务与报告',exact=True).wait_for()
    assert app.research_service.list_jobs()['total']==1
    page.get_by_role('link',name='进入因子实验历史查看任务与报告',exact=True).click()
    page.locator('[data-study-history] button').first.click()
    page.locator('[data-experiment-workspace][data-study-state="VALIDATED"]').wait_for(timeout=40000)
    assert '因子库' in page.locator('[data-study-method-binding]').inner_text()
    assert page.locator('[data-study-report]').is_visible()

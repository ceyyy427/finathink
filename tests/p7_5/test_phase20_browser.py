"""Actual learning approval, pair replay and maintenance in the native workspace."""
from datetime import UTC, datetime

import pytest
from playwright.sync_api import expect

from tests.p7_5.test_phase4_browser import browser, observed_page
from tests.research.test_application_service import payload, wait_for
from tests.research.test_learning_assessments import cohort_reviews
from tests.research.test_phase8_learning_flow import inputs, settle
from tests.research.test_phase18_api import app_http

__all__ = ['app_http', 'browser', 'observed_page']


def enter(app_http, page, path='/research/learning#cohort-evidence'):
    base = app_http[1].__closure__[0].cell_contents
    page.goto(base+path)
    selector = '[data-maintenance-ready="true"]' if 'maintenance' in path else '[data-cohort-ready="true"]'
    page.locator(selector).wait_for()
    return base


def assess(page, review):
    page.locator('[data-cohort-anchor]').select_option(review['review_id'])
    form = page.locator('[data-cohort-form]')
    form.locator('[name="start"]').fill('2026-01-01')
    form.locator('[name="as_of"]').fill(datetime.now(UTC).date().isoformat())
    page.get_by_role('button', name='评估全部候选', exact=True).click()


def test_actual_ready_cohort_approval_paired_replay_and_reload(app_http, observed_page, monkeypatch):
    page, artifacts, diagnostics = observed_page
    app = app_http[0]
    reviews, source_ref = cohort_reviews(app.research_service)
    enter(app_http, page)
    assess(page, reviews[0])
    expect(page.locator('[data-cohort-result]')).to_contain_text('20 / 20 个合格窗口')
    page.get_by_role('button', name='生成建议并打开审核', exact=True).click()
    expect(page.locator('[data-learning-outcome-heading]')).to_have_text('分离窗口的样本证据')
    page.locator('[data-learning-review-form] [name="rationale"]').fill('核对20个窗口、排除记录与区间假设')
    page.locator('[data-learning-ack]').check()
    page.get_by_role('button', name='批准为新配置', exact=True).click()
    expect(page.locator('[data-learning-admission]')).to_contain_text('已批准')
    pair_form = page.locator('[data-pair-form]')
    expect(pair_form.locator('[name="review_id"] option')).to_have_count(2)
    approved = next(row for row in app.research_service.learning.list_reviews() if row.get('source_kind') == 'LEARNING_COHORT')
    pair_form.locator('[name="review_id"]').select_option(approved['review_id'])
    pair_form.locator('[name="snapshot_ref"]').select_option(source_ref)
    original_submit = app.research_service.submit
    def shutdown_after_enqueue(body, **kwargs):
        original_submit(body, **kwargs)
        raise OSError('simulated shutdown before child binding')
    with monkeypatch.context() as patch:
        patch.setattr(app.research_service, 'submit', shutdown_after_enqueue)
        page.get_by_role('button', name='创建两项离线复跑', exact=True).click()
        expect(page.locator('[data-cohort-status]')).to_contain_text('需要处理')
    expected='Failed to load resource: the server responded with a status of 503 (Service Unavailable)'
    assert diagnostics['errors'] == [expected]
    diagnostics['expected_enqueue_failure'] = diagnostics['errors'].copy()
    diagnostics['errors'].clear()
    page.get_by_role('button',name='重新读取样本与版本',exact=True).click()
    expect(page.locator('[data-pair-result]')).to_contain_text('尚有任务未排队')
    page.get_by_role('button',name='继续未排队的任务',exact=True).click()
    expect(page.locator('[data-pair-result]')).not_to_contain_text('尚有任务未排队')
    expect(page.locator('[data-pair-saved] option')).to_have_count(2)
    assert app.research_service.list_jobs()['total'] == 22
    from finahinking.research.learning_replays import LearningReplayService
    pair = LearningReplayService(app.research_service).list()[0]
    for child in pair['children'].values():
        wait_for(app.research_service, child['job_id'])
    page.get_by_role('button', name='刷新复跑状态', exact=True).click()
    expect(page.locator('[data-pair-result]')).to_contain_text('成对结果已校验')
    expect(page.locator('[data-pair-result]')).to_contain_text('回溯比较')
    page.screenshot(path=str(artifacts/'cohort-approved-pair.png'), full_page=True)
    posts=[]
    page.on('request',lambda req: posts.append(req.url) if req.method=='POST' else None)
    page.reload()
    page.locator('[data-cohort-ready="true"]').wait_for()
    expect(page.locator('[data-pair-result]')).to_contain_text('成对结果已校验')
    assert posts == []


@pytest.mark.parametrize('viewport',[(1440,900),(1920,1080),(375,812)])
@pytest.mark.parametrize('path',['/research/learning#cohort-evidence','/research/maintenance'])
def test_empty_responsive_keyboard_reduced_motion(app_http, observed_page, viewport, path):
    page, artifacts, _ = observed_page
    page.set_viewport_size({'width':viewport[0],'height':viewport[1]})
    page.emulate_media(reduced_motion='reduce')
    enter(app_http,page,path)
    button=page.get_by_role('button',name='重新读取维护信息' if 'maintenance' in path else '重新读取样本与版本',exact=True)
    button.focus()
    assert button.evaluate('el=>getComputedStyle(el).outlineStyle') == 'solid'
    page.keyboard.press('Enter')
    expect(button).to_be_enabled()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    assert app_http[0].research_service.list_jobs()['total']==0 and not app_http[2].calls
    page.screenshot(path=str(artifacts/'responsive.png'),full_page=True)


def test_insufficient_samples_and_late_response_cannot_overwrite_new_selection(app_http, observed_page):
    page, _, _=observed_page
    app=app_http[0]
    source, _, evidence=inputs(app.research_service)
    review=settle(app.research_service,source,evidence)
    enter(app_http,page)
    assess(page,review)
    expect(page.locator('[data-cohort-result]')).to_contain_text('1 / 20 个合格窗口')
    expect(page.get_by_role('button',name='生成建议并打开审核',exact=True)).to_be_disabled()
    held=[]
    page.route('**/api/research/learning/assessments',lambda route: held.append(route) if route.request.method=='POST' else route.continue_())
    page.locator('[data-cohort-form] [name="start"]').fill('2026-01-02')
    page.get_by_role('button',name='评估全部候选',exact=True).click()
    page.wait_for_timeout(100)
    assert held
    response=held[0].fetch()
    page.locator('[data-cohort-form] [name="start"]').fill('2026-01-03')
    held[0].fulfill(response=response)
    expect(page.get_by_role('button',name='评估全部候选',exact=True)).to_be_enabled()
    expect(page.locator('[data-cohort-result]')).to_contain_text('选择已变化')
    assert page.locator('[data-cohort-form] [name="start"]').input_value()=='2026-01-03'


def test_maintenance_pagination_and_chinese_network_error(app_http, observed_page):
    page, artifacts, diagnostics=observed_page
    app=app_http[0]
    for i in range(22):
        app.research_service.submit(payload(app.research_service,key=f'history-{i}'))
    enter(app_http,page,'/research/maintenance')
    expect(page.locator('[data-maintenance-jobs] li')).to_have_count(20)
    page.get_by_role('button',name='读取下一页',exact=True).click()
    expect(page.locator('[data-maintenance-jobs] li')).to_have_count(2)
    page.get_by_role('button',name='返回第一页',exact=True).click()
    expect(page.locator('[data-maintenance-jobs] li')).to_have_count(20)
    page.set_viewport_size({'width':375,'height':812})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    page.screenshot(path=str(artifacts/'filled-maintenance-mobile.png'),full_page=True)
    page.route('**/api/research/maintenance',lambda route:route.fulfill(status=503,content_type='application/json',body='{"error":"本地记录暂不可用，请重新读取。"}'))
    page.get_by_role('button',name='重新读取维护信息',exact=True).click()
    expect(page.locator('[data-maintenance-status]')).to_contain_text('需要处理')
    expect(page.get_by_role('button',name='重新读取维护信息',exact=True)).to_be_enabled()
    expected='Failed to load resource: the server responded with a status of 503 (Service Unavailable)'
    assert diagnostics['errors'] == [expected]
    diagnostics['expected_http_failure'] = diagnostics['errors'].copy()
    diagnostics['errors'].clear()


@pytest.mark.parametrize('target', ['assessment', 'pair'])
def test_saved_selection_during_other_panel_load_cannot_act_on_previous_record(app_http, observed_page, target):
    page, _, _ = observed_page
    a, b = ['assessment-'+char*64 for char in 'ab']
    p, q = ['pair-'+char*64 for char in 'cd']
    def assessment(identity, count):
        return {'assessment_id': identity, 'paper_only': True, 'status': 'RECOMMENDED' if count == 20 else 'OBSERVE', 'sample_count': count, 'minimum_samples': 20, 'selected_review_ids': [f'r{i}' for i in range(count)], 'exclusions': [], 'counterexamples': [], 'limits': [], 'statistics': {'mean': -.1, 'median': -.1, 'losses': count, 'non_losses': 0, 'zero': 0, 'loss_interval_95': [.8, 1]}, 'scope': {'instrument': 'AAA', 'data_modes': {}}, 'start': '2026-01-01', 'as_of': '2026-10-31', 'anchor_review_id': 'r'}
    first, second = assessment(a, 20), assessment(b, 1)
    def pair(identity):
        return {'pair_id': identity, 'status': 'INCOMPLETE', 'paper_only': True, 'children': {}, 'metrics': [], 'binding': {}, 'limits': []}
    values = {'research/learning': {'reviews': []}, 'research/snapshots': {'snapshots': []}, 'research/learning/assessments': {'assessments': [first, second]}, 'research/learning/assessments/'+a: first, 'research/learning/assessments/'+b: second, 'research/learning/pairs': {'pairs': [pair(p), pair(q)]}, 'research/learning/pairs/'+p: pair(p), 'research/learning/pairs/'+q: pair(q)}
    held, posts, hold = [], [], []
    busy_path = 'research/learning/pairs/'+p if target == 'assessment' else 'research/learning/assessments/'+b
    def route(request):
        path = request.request.url.split('/api/')[1]
        if request.request.method == 'POST':
            posts.append(path)
            request.fulfill(json=pair(q) if path.endswith('/resume') else {'review_id': 'result'})
        elif hold and path == busy_path:
            held.append(request)
        elif path in values:
            request.fulfill(json=values[path])
        else:
            request.continue_()
    page.route('**/api/**', route)
    enter(app_http, page)
    hold.append(True)
    if target == 'assessment':
        page.locator('[data-pair-refresh]').click()
    else:
        page.locator('[data-cohort-saved]').select_option(b)
    page.wait_for_timeout(100)
    assert held
    selector = page.locator('[data-cohort-saved]' if target == 'assessment' else '[data-pair-saved]')
    result = page.locator('[data-cohort-result]' if target == 'assessment' else '[data-pair-result]')
    button = page.locator('[data-cohort-propose]' if target == 'assessment' else '[data-pair-resume]')
    selector.select_option(b if target == 'assessment' else q)
    expect(result).to_contain_text('选择已变化')
    expect(button).to_be_disabled()
    hold.clear()
    held[0].fulfill(json=values[busy_path])
    expect(result).to_contain_text('1 / 20 个合格窗口' if target == 'assessment' else q)
    if target == 'assessment':
        expect(button).to_be_disabled()
        selector.select_option(a)
        expect(button).to_be_enabled()
        # Programmatic selection changes are also checked at mutation time.
        selector.evaluate('(el,value)=>{el.value=value;}', b)
    else:
        expect(button).to_be_enabled()
        button.click()
        expect(button).to_be_enabled()
        assert posts == ['research/learning/pairs/'+q+'/resume']
        posts.clear()
        selector.evaluate('(el,value)=>{el.value=value;}', p)
    button.click()
    expect(button).to_be_disabled()
    assert posts == []

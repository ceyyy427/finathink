"""Interface workspace, actual loopback peer and controlled spawn acceptance."""
import json

import pytest
from playwright.sync_api import expect

from tests.p7_5.test_phase4_browser import browser, observed_page
from tests.research.test_phase18_api import app_http

__all__=['app_http', 'browser', 'observed_page']


def enter(page,app_http):
    app,http,_=app_http
    # The HTTP test helper closes over the owned server; expose only its local URL.
    base=http.__closure__[0].cell_contents
    page.goto(base+'/diagnostics')
    page.locator('[data-interface-workspace][data-interface-ready="true"]').wait_for(timeout=5000)
    return app,base


def test_local_suite_keyboard_export_reload_and_inert_reads(app_http,observed_page):
    page,artifacts,_=observed_page
    app,_base=enter(page,app_http)
    posts=[];page.on('request',lambda req:posts.append(req.url) if req.method=='POST' else None)
    button=page.locator('[data-interface-local]');button.focus();page.keyboard.press('Enter')
    page.wait_for_selector('[data-interface-result][data-result-status="READY"]')
    expect(page.locator('[data-interface-result]')).to_contain_text('34')
    expect(page.locator('[data-interface-result]')).to_contain_text('合成评分示例')
    link=page.locator('[data-interface-export]')
    with page.expect_download() as download: link.click()
    saved=download.value.path();record=json.loads(saved.read_text())
    assert record['external_account_verified'] is False and len(record['quality_demo'])==3
    page.screenshot(path=str(artifacts/'local-ready.png'),full_page=True)
    page.reload();page.wait_for_selector('[data-interface-history] a')
    page.locator('[data-interface-history] a').first.click()
    page.wait_for_selector('[data-interface-result][data-result-status="READY"]')
    assert len(posts)==1 and app.research_service.list_jobs()['total']==0
    assert '/api/diagnostics/interfaces/' in link.get_attribute('href')


def test_explicit_interface_probe_keeps_intent_and_failure_evidence(app_http,observed_page):
    page,artifacts,_=observed_page
    _,_=enter(page,app_http)
    page.locator('[data-interface-probe-details] summary').click()
    form=page.locator('[data-interface-probe]')
    form.locator('[name=target_kind]').select_option('model')
    form.locator('[name=config_ref] option[value="desk"]').wait_for(state='attached')
    form.locator('[name=config_ref]').select_option('desk')
    form.locator('[name=authorize]').check()
    form.locator('button[type=submit]').click()
    page.wait_for_selector('[data-interface-result][data-result-status="UNAUTHORIZED"]')
    expect(page.locator('[data-interface-result]')).to_contain_text('认证失败')
    assert len(app_http[2].calls)==1
    form.locator('button[type=submit]').click()
    assert len(app_http[2].calls)==1
    assert form.locator('[name=config_ref]').input_value()=='desk'
    page.screenshot(path=str(artifacts/'probe-auth.png'),full_page=True)


def test_quality_submit_report_assessment_compare_and_reload(app_http,observed_page):
    page,artifacts,_=observed_page
    app,base=enter(page,app_http)
    form=page.locator('[data-quality-submit]')
    form.locator('[name=model_config_ref]').select_option('desk')
    form.locator('[name=allow_model_calls]').check()
    form.evaluate('form => { form.requestSubmit(); form.requestSubmit(); }')
    page.wait_for_selector('[data-quality-result][data-quality-state="EVALUATED"]',timeout=30000)
    assert app.research_service.list_jobs()['total']==1
    expect(page.locator('[data-quality-result]')).to_contain_text('6 / 6')
    links=page.locator('[data-quality-result] a[data-quality-evidence]')
    assert links.count()==2
    for href in links.evaluate_all('(nodes)=>nodes.map(n=>n.getAttribute("href"))'):
        response=page.request.get(base+href.split('#')[0]); assert response.ok and 'id="'+href.split('#')[1]+'"' in response.text()
    held=[]
    page.route('**/api/research/quality/jobs/*',lambda route:held.append(route))
    page.locator('[data-quality-new]').click()
    assert not form.locator('[name=allow_model_calls]').is_checked()
    form.locator('[name=allow_model_calls]').check();form.locator('button[type=submit]').click()
    expect(page.locator('[data-quality-history] input[type=checkbox]')).to_have_count(2)
    expect(page.locator('[data-quality-result]')).to_have_attribute('data-quality-state','READING')
    for route in held: route.continue_()
    page.unroute('**/api/research/quality/jobs/*')
    page.wait_for_selector('[data-quality-result][data-quality-state="EVALUATED"]',timeout=30000)
    for checkbox in page.locator('[data-quality-history] input[type=checkbox]').all(): checkbox.check()
    page.locator('[data-quality-compare]').click()
    expect(page.locator('[data-quality-comparison]')).to_contain_text('冻结条件一致')
    expect(page.locator('[data-quality-comparison]')).to_contain_text('技术:user-model')
    page.screenshot(path=str(artifacts/'quality-comparison.png'),full_page=True)
    page.reload();page.wait_for_selector('[data-quality-result][data-quality-state="EVALUATED"]')
    assert app.research_service.list_jobs()['total']==2


@pytest.mark.parametrize('width,height',[(1440,900),(1920,1080),(375,812)])
def test_responsive_reduced_motion_and_visible_focus(app_http,observed_page,width,height):
    page,artifacts,_=observed_page
    page.set_viewport_size({'width':width,'height':height});page.emulate_media(reduced_motion='reduce')
    enter(page,app_http)
    page.locator('[data-interface-local]').focus()
    assert page.evaluate('getComputedStyle(document.activeElement).outlineStyle')!='none'
    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
    page.screenshot(path=str(artifacts/f'diagnostics-{width}.png'),full_page=True,animations='disabled')


def test_no_script_keeps_local_health_and_existing_receipts_readable(app_http,browser):
    app,http,_=app_http
    _,record=http('/api/diagnostics/interfaces/local',{'_csrf':app.csrf_token})
    base=http.__closure__[0].cell_contents
    context=browser.new_context(java_script_enabled=False)
    try:
        page=context.new_page();page.goto(base+'/diagnostics')
        page.get_by_text('本地健康状态与恢复',exact=True).click()
        assert page.get_by_role('heading',name='本地状态',exact=True).is_visible()
        assert page.locator('noscript').is_visible()
        assert page.locator('a[href="/api/diagnostics/interfaces/'+record['receipt_id']+'"]').count()>=1
    finally: context.close()


def test_lost_probe_response_keeps_same_key_across_reload(app_http,observed_page):
    page,_,_=observed_page
    enter(page,app_http)
    keys=[]
    def response_lost(route):
        keys.append(route.request.post_data_json['request_key'])
        response=route.fetch()
        route.fulfill(status=200,content_type='application/json',body='{broken') if len(keys)==1 else route.fulfill(response=response)
    page.route('**/api/diagnostics/interfaces/probe',response_lost)
    def submit_probe():
        page.locator('[data-interface-probe-details] summary').click()
        form=page.locator('[data-interface-probe]')
        form.locator('[name=target_kind]').select_option('model')
        form.locator('[name=config_ref]').select_option('desk')
        form.locator('[name=authorize]').check()
        form.locator('button[type=submit]').click()
    submit_probe()
    expect(page.locator('[data-interface-status]')).to_contain_text('数据格式异常')
    assert len(app_http[2].calls)==1
    page.reload();page.locator('[data-interface-workspace][data-interface-ready="true"]').wait_for()
    submit_probe()
    page.wait_for_selector('[data-interface-result][data-result-status="UNAUTHORIZED"]')
    assert len(keys)==2 and keys[0]==keys[1] and len(app_http[2].calls)==1


def test_blocked_session_storage_disables_paid_actions_but_keeps_local_checks(app_http,observed_page):
    page,_,_=observed_page
    page.add_init_script("Object.defineProperty(window,'sessionStorage',{get(){throw new Error('storage disabled');}})")
    enter(page,app_http)
    expect(page.locator('[data-interface-probe] button[type=submit]')).to_be_disabled()
    expect(page.locator('[data-quality-submit] button[type=submit]')).to_be_disabled()
    expect(page.locator('[data-interface-status]')).to_contain_text('会话存储')
    page.locator('[data-interface-local]').click()
    page.wait_for_selector('[data-interface-result][data-result-status="READY"]')
    assert not app_http[2].calls


def test_new_manager_disagreement_preserves_open_binding_and_keyboard_focus(app_http,observed_page):
    from tests.research.test_application_service import wait_for

    page,_,_=observed_page
    app,_=enter(page,app_http)
    held=[];first=True
    def progress(route):
        nonlocal first
        if first:
            first=False
            wait_for(app.research_service,route.request.url.rsplit('/',1)[-1])
            response=route.fetch();value=response.json()
            value['status']='PARTIAL';value['job_status']='running';value['metrics']['disagreements']=[]
            route.fulfill(json=value)
        else:
            held.append(route)
            page.locator('[data-quality-result]').evaluate("el=>el.dataset.testPollHeld='true'")
    page.route('**/api/research/quality/jobs/*',progress)
    form=page.locator('[data-quality-submit]');form.locator('[name=model_config_ref]').select_option('desk')
    form.locator('[name=allow_model_calls]').check();form.locator('button[type=submit]').click()
    page.wait_for_selector('[data-quality-result][data-quality-state="PARTIAL"]')
    summary=page.locator('[data-quality-result]').get_by_text('查看冻结条件、角色模型与观察',exact=True)
    with page.expect_request('**/api/research/quality/jobs/*'): summary.click()
    page.wait_for_selector('[data-quality-result][data-test-poll-held="true"]')
    assert held
    held[0].fulfill(response=held[0].fetch())
    page.unroute('**/api/research/quality/jobs/*')
    page.wait_for_selector('[data-quality-result][data-quality-state="EVALUATED"]')
    assert summary.evaluate('el=>el.parentElement.open')
    assert summary.evaluate('el=>el===document.activeElement')


def test_selecting_history_supersedes_an_ambiguous_paid_submission(app_http,observed_page):
    from tests.research.test_application_service import wait_for

    app,_,_=app_http
    old=app.quality_evaluation.submit({'case_id':'complete','idempotency_key':'history-before-submit','model_config_ref':'desk','allow_model_calls':True})
    wait_for(app.research_service,old['job_id'])
    page,_,_=observed_page;enter(page,app_http)
    held=[]
    def response_lost(route):
        if route.request.method!='POST':
            route.continue_()
            return
        held.append((route,route.fetch()))
        page.locator('[data-interface-workspace]').evaluate("el=>el.dataset.testSubmissionHeld='true'")
    page.route('**/api/research/quality/jobs',response_lost)
    form=page.locator('[data-quality-submit]');form.locator('[name=model_config_ref]').select_option('desk')
    form.locator('[name=allow_model_calls]').check();form.locator('button[type=submit]').click()
    page.wait_for_selector('[data-test-submission-held="true"]')
    page.locator('[data-quality-history] a').first.click()
    page.wait_for_selector('[data-quality-result][data-quality-state="EVALUATED"]')
    held[0][0].fulfill(response=held[0][1]);page.unroute('**/api/research/quality/jobs')
    expect(form).to_have_attribute('aria-busy','false')
    expect(page.locator('[data-quality-result]')).to_have_attribute('data-quality-job',old['job_id'])
    assert app.research_service.list_jobs()['total']==2


def test_late_history_read_cannot_replace_a_newer_probe_result(app_http,observed_page):
    app,_http,_=app_http
    old=app.interface_diagnostics.run_local()
    page,_,_=observed_page;enter(page,app_http)
    held=[]
    page.route('**/api/diagnostics/interfaces/'+old['receipt_id'],lambda route:held.append(route))
    page.locator('[data-interface-history] a').first.click()
    page.locator('[data-interface-probe-details] summary').click()
    form=page.locator('[data-interface-probe]');form.locator('[name=target_kind]').select_option('model')
    form.locator('[name=config_ref]').select_option('desk');form.locator('[name=authorize]').check()
    form.locator('button[type=submit]').click()
    page.wait_for_selector('[data-interface-result][data-result-status="UNAUTHORIZED"]')
    assert held
    with page.expect_response('**/api/diagnostics/interfaces/'+old['receipt_id']) as response:
        held[0].fulfill(response=held[0].fetch())
    response.value.finished()
    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    expect(page.locator('[data-interface-result]')).to_have_attribute('data-result-status','UNAUTHORIZED')
    page.unroute('**/api/diagnostics/interfaces/'+old['receipt_id'])

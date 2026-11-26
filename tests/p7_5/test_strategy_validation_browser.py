"""Two native submissions take a frozen source through the actual validation worker."""

import pytest

from tests.p7_5.test_phase4_browser import browser, http_app, observed_page
from tests.research.test_strategy_worker import completed

__all__ = ["browser", "http_app", "observed_page"]

BASE = "/research/strategy-validation"


def _fits(page, width):
    page.set_viewport_size({"width": width, "height": 812 if width < 500 else (1080 if width == 1920 else 900)})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


@pytest.mark.parametrize("http_app", [False], indirect=True)
@pytest.mark.parametrize("observed_page", [{"java_script_enabled": True}, {"java_script_enabled": False}],
                         indirect=True, ids=["interactive", "no-js"])
def test_native_example_freeze_run_and_read_evidence(http_app, observed_page):
    from playwright.sync_api import expect

    app, base = http_app
    page, artifacts, _ = observed_page
    page.goto(base + BASE)
    workspace = page.locator("[data-strategy-validation]")
    expect(workspace.get_by_role("heading", name="策略验证室", exact=True)).to_be_visible()
    sample = workspace.get_by_role("button", name="生成纸面示例账本")
    expect(sample).to_be_visible()
    _fits(page, 375)
    page.screenshot(path=str(artifacts / "studio-empty-375.png"))
    sample.focus()
    sample.press("Enter")

    refresh = workspace.locator('a[href*="source_job="]').first
    source = workspace.locator('select[name=run_id]')
    expect(refresh.or_(source)).to_be_visible()
    job = app.research_service.list_jobs()["jobs"][0]
    published = completed(app.research_service, job["job_id"])
    if refresh.is_visible():
        refresh.click()
    expect(source).to_have_value(published["run_id"], timeout=30000)
    workspace.get_by_label("训练期数", exact=True).fill("32")
    workspace.get_by_label("测试期数", exact=True).fill("16")
    workspace.get_by_role("button", name="预览验证方案").click()
    expect(workspace.get_by_role("heading", name="冻结方案")).to_be_visible()
    expect(workspace.locator('[name=plan_fingerprint]')).to_have_count(1)
    details = workspace.get_by_text("读取各窗口起止日期", exact=True)
    details.focus()
    details.press("Enter")
    expect(workspace.get_by_role("table", name="窗口日期（含首尾）")).to_be_visible()
    for width in (1440, 1920):
        _fits(page, width)
        page.screenshot(path=str(artifacts / f"studio-plan-{width}.png"))
    workspace.get_by_role("button", name="运行冻结方案").click()

    study = workspace.locator(".sv-receipt a[href*='study=']").first
    expect(study).to_be_visible()
    job = app.research_service.list_jobs()["jobs"][0]
    completed(app.research_service, job["job_id"])
    # Enhanced browsers follow the canonical GET receipt automatically. The
    # native link provides the equivalent path for keyboard/no-script sessions.
    if page.locator(".app-shell[data-shell-enhanced=true]").count():
        expect(workspace.get_by_role("heading", name="样本外结果")).to_be_visible(timeout=35000)
    else:
        study.click()
    expect(workspace.get_by_role("heading", name="样本外结果")).to_be_visible()
    expect(workspace.locator(".sv-window")).to_have_count(3)
    expect(workspace.get_by_role("heading", name="原账本组合诊断")).to_be_visible()
    first = workspace.locator(".sv-window").first
    curve = first.locator("[data-sv-curve]").first
    expect(curve).to_be_visible()
    control = curve.locator("[type=range]")
    if control.is_visible():
        control.focus()
        control.press("Home")
        current = curve.locator("output").inner_text()
        control.press("ArrowRight")
        expect(curve.locator("output")).not_to_have_text(current)
        expect(control).to_have_value("1")

    curve.locator(".sv-chart-data summary").click()
    expect(curve.get_by_role("table")).to_be_visible()
    for width in (375, 1440, 1920):
        _fits(page, width)
        first.scroll_into_view_if_needed()
        page.screenshot(path=str(artifacts / f"studio-result-{width}.png"))
    with page.expect_download() as downloaded:
        workspace.get_by_role("link", name="下载离线报告").click()
    report = downloaded.value.path().read_text(encoding="utf-8")
    assert published["run_id"] in report and "<script" not in report
    workspace.get_by_role("link", name="打开验证报告").click()
    expect(page.get_by_role("heading", name="策略验证报告")).to_be_visible()
    expect(page.locator(".sv-offline .sv-window")).to_have_count(3)
    expect(page.locator(".sv-offline form")).to_have_count(0)
    content = page.locator(".sv-offline").text_content()
    for label in ("SAMPLE", "来源报告指纹", "来源账本指纹", "面板指纹", "执行政策引用",
                  "执行政策指纹", "快照指纹", "冻结执行情景", "成交参与上限", "滑点 / bp"):
        assert label in content
    assert published["run_id"] in content
    _fits(page, 375)


@pytest.mark.parametrize("http_app", [False], indirect=True)
def test_pending_receipt_polls_lightweight_status_before_navigating(http_app, observed_page):
    """Repeated pending statuses and one interrupted response must preserve the document."""
    from playwright.sync_api import expect

    from finahinking.research.strategy_validation_page import render_validation_page

    app, base = http_app
    page, _, _ = observed_page
    receipt_url = base + BASE + "?study=progress-fixture"
    progress_url = base + "/api" + BASE + "/progress?study=progress-fixture"
    documents, checks = [], []
    values = {"study_id": "progress-fixture", "run_id": "source-fixture", "status": "queued"}

    def document(route):
        documents.append(route.request.url)
        if len(documents) == 1:
            fragment = render_validation_page(sources=[], selected_run="", plan=None, result=values,
                                              history=[], csrf_token="fixture")
        else:
            fragment = "<h1>已读取终态</h1>"
        route.fulfill(content_type="text/html", body=app.render_shell(BASE, "策略验证室", fragment))

    def status(route):
        checks.append((route.request.method, len(documents)))
        if len(checks) == 1:
            route.fulfill(content_type="application/json", body="{")
        else:
            route.fulfill(json={"status": {2: "queued", 3: "running"}.get(len(checks), "completed")})

    page.route(receipt_url, document)
    page.route(progress_url, status)
    page.goto(receipt_url)
    expect(page.locator(".sv-receipt")).to_contain_text("已排队")
    expect(page.locator(".sv-receipt [data-sv-phase]")).to_have_text("正在验证", timeout=24000)
    expect(page.get_by_role("heading", name="已读取终态")).to_be_visible(timeout=12000)
    assert checks == [("GET", 1)] * 4
    assert documents == [receipt_url, receipt_url]

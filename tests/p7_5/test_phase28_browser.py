"""Workspace navigation, keyboard, stale reads, and offline report behavior."""
import pytest

from tests.p7_5.test_phase4_browser import browser, http_app, observed_page
from tests.research.test_discovery_journeys import call
from tests.research.test_strategy_worker import completed

__all__ = ["browser", "http_app", "observed_page"]


@pytest.mark.parametrize("http_app", [False], indirect=True)
def test_three_viewports_keyboard_motion_and_statuses(http_app, observed_page):
    app, base = http_app
    page, artifacts, _ = observed_page
    page.add_init_script("""(() => {
      const original = window.fetch.bind(window);
      window.fetch = (url, options) => {
        if (String(url).includes('simulate-error')) return Promise.reject(new Error('controlled failure'));
        return original(url, options);
      };
    })()""")
    page.goto(base + "/research/workbench", wait_until="networkidle")
    search = page.get_by_role("textbox", name="关键词")
    for width, height in ((1440, 900), (1920, 1080), (375, 812)):
        page.set_viewport_size({"width": width, "height": height})
        page.emulate_media(reduced_motion="reduce")
        assert page.get_by_role("heading", name="量化研究工作台", exact=True).is_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2")
        search.focus()
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.matches(':focus-visible')")
        page.screenshot(path=str(artifacts / f"workbench-{width}.png"), full_page=True)
    search.fill("simulate-error")
    search.press("Enter")
    page.get_by_text("可再次检索", exact=False).wait_for()
    search.fill("无匹配词q9k1")
    search.press("Enter")
    page.get_by_text("暂无匹配对象", exact=False).wait_for()
    search.fill("净资产收益率")
    search.press("Enter")
    page.locator("[data-qw-results] article").first.wait_for()
    assert app.research_service.list_jobs()["total"] == 0


@pytest.mark.parametrize("http_app", [False], indirect=True)
def test_changed_selection_rejects_late_response_and_old_approval(http_app, observed_page):
    app, base = http_app
    page, _, _ = observed_page
    page.add_init_script("""(() => {
      const original = window.fetch.bind(window);
      window.fetch = (url, opts) => {
        if (!String(url).includes('q=first-pending')) return original(url, opts);
        const {signal, ...rest} = opts || {};
        return new Promise(resolve => { window.releaseOldCatalogue = () => resolve(original(url, rest)); });
      };
    })()""")
    page.goto(base + "/research/workbench?journey=portfolio", wait_until="networkidle")
    query = page.get_by_role("textbox", name="关键词")
    query.fill("first-pending")
    assert page.locator("[data-qw-results] article").count() == 0
    query.press("Enter")
    page.get_by_text("正在读取研究目录…").wait_for()
    query.fill("等权")
    query.press("Enter")
    page.locator("[data-qw-results]").get_by_text("等权", exact=False).first.wait_for()
    with page.expect_response(lambda response: "q=first-pending" in response.url) as old_response:
        page.evaluate("window.releaseOldCatalogue()")
    assert old_response.value.finished() is None
    page.locator("[data-qw-results]").get_by_text("等权", exact=False).first.wait_for()
    query.fill("")
    page.locator("[name=mode]").select_option("sample")
    page.get_by_role("button", name="预览研究计划").click()
    page.get_by_text("可以提交", exact=True).wait_for()
    assert page.get_by_role("button", name="确认并提交研究").is_enabled()
    page.get_by_label("研究问题").fill("财报事件研究遇到公告滞后会怎样？")
    assert page.get_by_role("button", name="确认并提交研究").is_disabled()
    assert app.research_service.list_jobs()["total"] == 0


@pytest.mark.parametrize("http_app", [False], indirect=True)
def test_offline_html_preserves_sources_and_has_zero_external_requests(http_app, observed_page):
    app, base = http_app
    page, artifacts, _ = observed_page
    page.goto(base + "/research/workbench?journey=portfolio", wait_until="networkidle")
    page.locator("[name=mode]").select_option("sample")
    page.get_by_role("button", name="预览研究计划").click()
    page.get_by_text("可以提交", exact=True).wait_for()
    with page.expect_response(lambda r: r.url.endswith("/api/research/discovery")) as sent:
        page.get_by_role("button", name="确认并提交研究").click()
    job = completed(app.research_service, sent.value.json()["job"]["job_id"])
    document = call(app, base, job["report_url"])
    target = artifacts / "offline-research.html"
    target.write_bytes(document)
    requests = []
    page.on("request", lambda r: requests.append(r.url) if r.url.startswith(("http:", "https:")) else None)
    page.context.set_offline(True)
    page.goto(target.as_uri(), wait_until="load")
    assert page.get_by_role("heading", name="研究计划与分析师证据").is_visible()
    page.get_by_text("technical · READY", exact=True).focus()
    page.keyboard.press("Enter")
    assert page.get_by_text("technical · READY", exact=True).locator("..").get_attribute("open") is not None
    assert job["discovery"]["blueprint"]["fingerprint"] in page.locator("body").inner_text()
    assert not requests

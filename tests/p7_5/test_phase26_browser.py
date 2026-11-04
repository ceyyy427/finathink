"""Strategy catalogue and real browser submission."""

import pytest

from tests.p7_5.test_phase4_browser import browser, http_app, observed_page

__all__ = ["browser", "http_app", "observed_page"]


@pytest.mark.parametrize("size", [(1440, 900), (1920, 1080), (375, 812)])
def test_strategy_page_layout(http_app, observed_page, size):
    app, base = http_app
    page, artifacts, _ = observed_page
    width, height = size
    page.set_viewport_size({"width": width, "height": height})
    page.emulate_media(reduced_motion="reduce")
    page.goto(base + "/research/strategies", wait_until="networkidle")
    assert page.get_by_role("heading", name="策略与纸面账本", exact=True).is_visible()
    assert page.locator("[name=template_id] option").count() == 12
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.matches(':focus-visible')")
    assert app.research_service.list_jobs()["total"] == 0
    page.screenshot(path=str(artifacts / f"strategy-{width}.png"), full_page=True)


def test_strategy_submit_from_browser(http_app, observed_page):
    app, base = http_app
    page, _, _ = observed_page
    page.goto(base + "/research/strategies", wait_until="networkidle")
    with page.expect_response(lambda r: r.url == base + "/api/research/strategies" and r.request.method == "POST") as submitted:
        page.get_by_role("button", name="提交纸面策略研究").click()
    assert submitted.value.status == 202
    assert page.get_by_role("link", name="打开后台任务").is_visible()
    from tests.research.test_strategy_worker import completed

    job = completed(app.research_service, submitted.value.json()["job_id"])
    page.goto(base + job["report_url"], wait_until="networkidle")
    assert page.get_by_role("heading", name="策略研究与统一纸面账本").is_visible()
    explanation = page.locator("[data-research-explanation]")
    assert "本轮策略纸面账本记录" in explanation.inner_text()
    assert explanation.get_by_role("link", name="策略结果与统一纸面账本").get_attribute("href") == "#strategy-study"

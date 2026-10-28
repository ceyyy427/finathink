"""Shared motion must preserve readable, usable research on real HTTP pages."""

import pytest

from tests.p7_5.test_phase4_browser import browser, http_app, observed_page

__all__ = ["browser", "http_app", "observed_page"]


@pytest.mark.parametrize("width,height", [(1440, 900), (1920, 1080), (2560, 1440), (1024, 900), (390, 844), (375, 812)])
def test_workbench_stays_bounded_centered_and_aligned(http_app, observed_page, width, height):
    _, base = http_app
    page, artifacts, diagnostics = observed_page
    page.set_viewport_size({"width": width, "height": height})
    for route in ("/", "/library/methods", "/research/factors/finathink-turnover-freefloat-mean"):
        page.goto(base + route, wait_until="networkidle")
        boxes = page.evaluate("""() => {
            const area = document.querySelector('.workspace-layout');
            const box = area.getBoundingClientRect();
            const frame = document.querySelector('.app-frame').getBoundingClientRect();
            const style = getComputedStyle(area);
            const top = document.querySelector('.topbar-context').getBoundingClientRect();
            const actions = document.querySelector('.topbar-actions').getBoundingClientRect();
            return {width: box.width, left: box.left - frame.left, right: frame.right - box.right,
                contentLeft: box.left + parseFloat(style.paddingLeft), topLeft: top.left,
                contentRight: box.right - parseFloat(style.paddingRight), actionsRight: actions.right,
                overflow: document.documentElement.scrollWidth > innerWidth + 1};
        }""")
        assert boxes["width"] <= 1481, (route, boxes)
        assert abs(boxes["left"] - boxes["right"]) <= 1, (route, boxes)
        assert abs(boxes["contentLeft"] - boxes["topLeft"]) <= 1, (route, boxes)
        assert abs(boxes["contentRight"] - boxes["actionsRight"]) <= 1, (route, boxes)
        assert not boxes["overflow"], route
        if route == "/":
            page.locator("#overview-question").fill("比较不同窗口、费用与样本的变化；" * 30)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            page.screenshot(path=str(artifacts / f"overview-{width}.png"))
        elif width in (1920, 390):
            name = "methods" if route == "/library/methods" else "factor"
            page.screenshot(path=str(artifacts / f"{name}-{width}.png"))
        diagnostics["metrics"].setdefault("layout", []).append({"route": route, "viewport": width, **boxes})


def test_arrival_and_search_feedback_do_not_delay_input_or_replay(http_app, observed_page):
    app, base = http_app
    page, artifacts, _ = observed_page
    page.emulate_media(reduced_motion="no-preference")
    page.add_init_script("""window.arrivals = [];
        document.addEventListener('animationstart', event => {
            if (event.target.matches('.workspace-layout')) window.arrivals.push(event.animationName);
        });""")
    page.goto(base, wait_until="networkidle")
    assert len(page.evaluate("window.arrivals")) == 1
    assert page.locator(".workspace-layout").evaluate("el => getComputedStyle(el).opacity") == "1"
    page.locator("#overview-question").fill("输入时页面不能重播或提交。")
    opener = page.locator("[data-shell-search-open]")
    opener.click()
    dialog = page.get_by_role("dialog", name="搜索功能入口")
    feedback = dialog.evaluate("""el => ({
        name: getComputedStyle(el).animationName,
        duration: parseFloat(getComputedStyle(el).animationDuration)
    })""")
    assert feedback["name"] != "none"
    assert 0 < feedback["duration"] <= .3
    page.locator("[data-shell-search-input]").fill("研究")
    assert "找到" in page.locator("[data-shell-search-status]").inner_text()
    page.screenshot(path=str(artifacts / "search-open-1440.png"), animations="disabled")
    page.keyboard.press("Escape")
    assert not dialog.is_visible()
    assert opener.evaluate("el => el === document.activeElement")
    for _ in range(3):
        page.keyboard.press("Control+k")
        page.keyboard.press("Escape")
    assert not dialog.is_visible()
    assert opener.evaluate("el => el === document.activeElement")
    assert page.locator("#overview-question").input_value() == "输入时页面不能重播或提交。"
    assert len(page.evaluate("window.arrivals")) == 1
    assert app.research_service.list_jobs()["total"] == 0
    page.screenshot(path=str(artifacts / "keyboard-focus-restored.png"))


def test_live_reduced_motion_removes_spatial_effects_preserving_feedback(http_app, observed_page):
    app, base = http_app
    page, artifacts, _ = observed_page
    page.goto(base, wait_until="networkidle")
    opener = page.locator("[data-shell-search-open]")
    opener.click()
    dialog = page.get_by_role("dialog", name="搜索功能入口")
    page.emulate_media(reduced_motion="reduce")
    assert dialog.evaluate("el => getComputedStyle(el).animationName") == "none"
    assert dialog.evaluate("el => getComputedStyle(el).transform") == "none"
    assert page.locator("[data-shell-search-input]").evaluate("el => el === document.activeElement")
    page.locator("[data-shell-search-input]").fill("不存在的功能")
    assert "没有匹配" in page.locator("[data-shell-search-status]").inner_text()
    page.keyboard.press("Escape")
    assert opener.evaluate("el => el === document.activeElement")
    feedback = opener.evaluate("el => ({duration:getComputedStyle(el).transitionDuration, properties:getComputedStyle(el).transitionProperty})")
    assert any(0 < float(item.strip().removesuffix("s")) <= .1 for item in feedback["duration"].split(","))
    assert "transform" not in feedback["properties"] and "all" not in feedback["properties"]
    page.set_viewport_size({"width": 390, "height": 844})
    menu = page.get_by_role("button", name="展开导航", exact=True)
    menu.click()
    nav = page.locator("#shell-navigation")
    assert nav.is_visible() and not nav.evaluate("el => el.inert")
    assert nav.evaluate("el => getComputedStyle(el).transitionDuration") == "0s"
    page.keyboard.press("Escape")
    assert nav.evaluate("el => el.inert")
    assert menu.evaluate("el => el === document.activeElement")
    assert app.research_service.list_jobs()["total"] == 0
    page.screenshot(path=str(artifacts / "reduced-motion-mobile.png"))


@pytest.mark.parametrize("observed_page", [{"java_script_enabled": False}], indirect=True)
def test_motion_keeps_no_script_content_and_print_readable(http_app, observed_page):
    app, base = http_app
    page, artifacts, _ = observed_page
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(base + "/library/methods", wait_until="networkidle")
    assert page.get_by_role("main").is_visible()
    assert page.get_by_role("navigation", name="主导航").is_visible()
    summary = page.locator("[data-asset-workspace] details summary").first
    summary.click()
    assert summary.locator("..").get_attribute("open") is not None
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.screenshot(path=str(artifacts / "no-script-390.png"), animations="disabled")
    page.emulate_media(media="print")
    assert page.locator(".workspace-layout").evaluate("el => getComputedStyle(el).animationName") == "none"
    assert app.research_service.list_jobs()["total"] == 0
    page.screenshot(path=str(artifacts / "no-script-print.png"), full_page=True, animations="disabled")

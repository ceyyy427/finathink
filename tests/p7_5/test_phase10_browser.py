"""Blue-white shell on real HTTP, without replacing the research workflow."""

from __future__ import annotations

import re

import pytest

from tests.p7_5.test_phase4_browser import browser, http_app, observed_page

__all__ = ["browser", "http_app", "observed_page"]


@pytest.mark.parametrize("width,height", [(1440, 900), (1920, 1080)])
def test_overview_heading_spans_columns_and_context_aligns_with_composer(
    http_app, observed_page, width, height
):
    _, base = http_app
    page, _, _ = observed_page
    page.set_viewport_size({"width": width, "height": height})
    page.goto(base, wait_until="networkidle")
    heading = page.locator(".overview-header").bounding_box()
    intro = page.locator(".research-intro").bounding_box()
    context = page.locator(".inspector").bounding_box()
    assert abs(intro["y"] - context["y"]) <= 1
    assert heading["y"] + heading["height"] <= intro["y"]
    assert heading["x"] + heading["width"] >= context["x"] + context["width"] - 1
    assert page.get_by_role("main").get_by_role("heading", level=1).count() == 1


@pytest.mark.parametrize("width", [1024, 390])
def test_search_opener_keeps_accessible_name_when_caption_is_hidden(
    http_app, observed_page, width
):
    _, base = http_app
    page, _, _ = observed_page
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(base, wait_until="networkidle")
    opener = page.get_by_role("button", name="搜索功能入口", exact=True)
    assert opener.is_visible()
    opener.click()
    assert page.get_by_role("dialog", name="搜索功能入口").is_visible()


def test_reference_theme_preserves_real_counts_and_readable_brand(http_app, observed_page):
    _, base = http_app
    page, artifacts, diagnostics = observed_page
    page.goto(base, wait_until="networkidle")
    assert page.locator('[data-overview-metric="sample_events"]').inner_text() == "1"
    assert page.locator('[data-overview-metric="saved_items"]').inner_text() == "1"
    assert page.locator('[data-overview-metric="learning_items"]').inner_text() == "1"
    assert page.locator('[data-overview-metric="research_runs"]').inner_text() == "1"
    style = page.evaluate("""() => ({
        background: getComputedStyle(document.documentElement).backgroundColor,
        sidebar: getComputedStyle(document.querySelector('.sidebar')).backgroundColor,
        primary: getComputedStyle(document.querySelector('.composer-footer button')).backgroundColor,
        brandFont: getComputedStyle(document.querySelector('.brand-name')).fontFamily,
        bodyFont: getComputedStyle(document.body).fontFamily,
        topbarHeight: document.querySelector('.topbar').getBoundingClientRect().height
    })""")
    diagnostics["metrics"]["theme"] = style
    assert style["background"] == "rgb(245, 248, 253)"
    assert style["sidebar"] == "rgb(255, 255, 255)"
    assert style["primary"] == "rgb(37, 99, 235)"
    assert style["brandFont"] == style["bodyFont"]
    assert style["topbarHeight"] == 76
    page.screenshot(path=str(artifacts / "reference-theme-desktop.png"), full_page=True)


def test_question_moves_to_existing_form_without_creating_a_job(http_app, observed_page):
    app, base = http_app
    page, _, _ = observed_page
    alerts = []
    page.on("dialog", lambda dialog: (alerts.append(dialog.message), dialog.dismiss()))
    page.goto(base, wait_until="networkidle")
    question = '比较 A&B 的窗口；</textarea><script>alert("private")</script>'
    page.locator("#overview-question").fill(question)
    page.get_by_role("button", name="建立研究", exact=True).click()
    page.wait_for_url("**/research/jobs?*")
    page.wait_for_load_state("networkidle")
    assert page.locator('[data-run-submit] [name="question"]').input_value() == question
    assert page.locator('[data-run-submit] [name="snapshot_ref"]').input_value() == ""
    assert page.locator('[data-run-submit] [name="model_config_ref"]').input_value() == "offline"
    assert not app.research_service.snapshots.list_snapshots()
    assert app.research_service.list_jobs()["total"] == 0
    assert alerts == []


def test_search_is_local_and_escape_restores_keyboard_focus(http_app, observed_page):
    app, base = http_app
    page, _, _ = observed_page
    requests = []
    page.goto(base, wait_until="networkidle")
    page.on("request", lambda request: requests.append(request.url))
    opener = page.locator("[data-shell-search-open]")
    opener.focus()
    page.keyboard.press("Control+k")
    assert page.get_by_role("dialog", name="搜索功能入口").is_visible()
    assert page.locator("[data-shell-search-input]").evaluate("el => el === document.activeElement")
    page.locator("[data-shell-search-input]").fill("不存在的研究入口")
    assert page.locator("[data-shell-search-results] a").count() == 0
    assert "没有匹配" in page.locator("[data-shell-search-status]").inner_text()
    page.locator("[data-shell-search-input]").fill("数据接口")
    links = page.locator("[data-shell-search-results] a")
    assert links.count() == 1
    assert links.first.get_attribute("href") == "/settings/data-connections"
    page.keyboard.press("Escape")
    assert not page.get_by_role("dialog", name="搜索功能入口").is_visible()
    assert opener.evaluate("el => el === document.activeElement")
    assert requests == []
    assert app.research_service.list_jobs()["total"] == 0


def test_mobile_navigation_traps_then_restores_focus_and_survives_resize(http_app, observed_page):
    _, base = http_app
    page, _, _ = observed_page
    page.set_viewport_size({"width": 375, "height": 812})
    page.goto(base, wait_until="networkidle")
    nav = page.locator("#shell-navigation")
    opener = page.get_by_role("button", name="展开导航", exact=True)
    assert nav.get_attribute("aria-hidden") == "true"
    assert nav.evaluate("el => el.inert")
    opener.focus()
    page.keyboard.press("Tab")
    assert not page.evaluate("document.querySelector('#shell-navigation').contains(document.activeElement)")
    opener.click()
    assert opener.get_attribute("aria-expanded") == "true"
    assert page.locator("[data-shell-frame]").evaluate("el => el.inert")
    close = nav.get_by_role("button", name="关闭导航", exact=True)
    assert close.evaluate("el => el === document.activeElement")
    page.keyboard.press("Shift+Tab")
    assert nav.evaluate("el => el.contains(document.activeElement)")
    page.keyboard.press("Escape")
    assert opener.get_attribute("aria-expanded") == "false"
    assert opener.evaluate("el => el === document.activeElement")
    opener.click()
    close.click()
    assert opener.evaluate("el => el === document.activeElement")
    opener.click()
    page.set_viewport_size({"width": 1440, "height": 900})
    page.wait_for_function("() => !document.querySelector('[data-shell-frame]').inert && !document.querySelector('#shell-navigation').inert")
    assert not nav.evaluate("el => el.inert")
    assert not page.locator("[data-shell-frame]").evaluate("el => el.inert")
    assert page.evaluate("getComputedStyle(document.body).overflow") != "hidden"
    page.set_viewport_size({"width": 375, "height": 812})
    page.wait_for_function("() => document.querySelector('#shell-navigation').inert")
    assert nav.evaluate("el => el.inert")
    assert not page.evaluate("!!document.activeElement.closest('[inert]')")


def test_mobile_forms_and_navigation_have_readable_touch_targets(http_app, observed_page):
    _, base = http_app
    page, _, diagnostics = observed_page
    page.set_viewport_size({"width": 375, "height": 812})
    page.emulate_media(reduced_motion="reduce")
    page.goto(base, wait_until="networkidle")
    dimensions = page.evaluate("""() => {
        const rect = selector => {
            const el = document.querySelector(selector), box = el.getBoundingClientRect();
            return {width: box.width, height: box.height, fontSize: parseFloat(getComputedStyle(el).fontSize)};
        };
        return {question: rect('#overview-question'), menu: rect('[data-shell-menu]'),
            create: rect('.topbar-actions .button'), transition: getComputedStyle(document.querySelector('.sidebar')).transitionDuration};
    }""")
    diagnostics["metrics"]["mobile"] = dimensions
    assert dimensions["question"]["fontSize"] >= 16
    assert dimensions["menu"]["width"] >= 44 and dimensions["menu"]["height"] >= 44
    assert dimensions["create"]["height"] >= 44
    assert dimensions["transition"] == "0s"
    page.get_by_role("button", name="展开导航", exact=True).click()
    assert page.get_by_role("navigation", name="主导航").get_by_role("link", name="研究任务", exact=True).bounding_box()["height"] >= 44


def test_context_action_keeps_the_reference_mobile_button_affordance(http_app, observed_page):
    _, base = http_app
    page, _, _ = observed_page
    page.set_viewport_size({"width": 375, "height": 812})
    page.goto(base, wait_until="networkidle")
    action = page.get_by_role("link", name="打开研究工作台", exact=True)
    style = action.evaluate("el => ({border: getComputedStyle(el).borderTopStyle, width: getComputedStyle(el).borderTopWidth, height: el.getBoundingClientRect().height})")
    assert style["border"] == "solid" and style["width"] == "1px"
    assert style["height"] >= 44


def test_footer_follows_context_in_mobile_reading_order(http_app, observed_page):
    _, base = http_app
    page, _, _ = observed_page
    page.set_viewport_size({"width": 375, "height": 812})
    page.goto(base, wait_until="networkidle")
    footer = page.locator(".footer-note").last.bounding_box()
    context = page.locator("#inspector").bounding_box()
    assert footer["y"] >= context["y"] + context["height"]


@pytest.mark.parametrize("width,height", [(1440, 900), (1920, 1080), (1024, 768), (375, 812), (390, 844)])
def test_existing_workspaces_keep_light_shell_and_fit_viewports(http_app, observed_page, width, height):
    _, base = http_app
    page, artifacts, diagnostics = observed_page
    page.set_viewport_size({"width": width, "height": height})
    for route in ("/", "/research/jobs", "/research/experiments", "/research/compare", "/research/learning", "/settings/data-connections"):
        page.goto(base + route, wait_until="networkidle")
        assert page.locator("main#main").is_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.locator("[data-finathink-shell]").get_attribute("data-shell-enhanced") == "true"
        if width > 800:
            assert page.get_by_role("navigation", name="主导航").is_visible()
        else:
            assert page.get_by_role("button", name="展开导航", exact=True).is_visible()
        name = route.strip("/").replace("/", "-") or "overview"
        page.screenshot(path=str(artifacts / f"{name}-{width}.png"), full_page=True)
    diagnostics["metrics"]["viewport"] = {"width": width, "height": height}


@pytest.mark.parametrize("observed_page", [{"java_script_enabled": False}], indirect=True)
def test_no_js_keeps_navigation_and_question_get_form(http_app, observed_page):
    app, base = http_app
    page, _, _ = observed_page
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(base, wait_until="networkidle")
    assert page.get_by_role("navigation", name="主导航").is_visible()
    assert page.get_by_role("table", name="每日活动数据").is_visible()
    page.locator("#overview-question").fill("离线核对窗口、费用和历史回撤")
    page.get_by_role("button", name="建立研究", exact=True).click()
    page.wait_for_url(re.compile(r"/research/jobs\?"))
    assert page.locator('[data-run-submit] [name="question"]').input_value() == "离线核对窗口、费用和历史回撤"
    assert app.research_service.list_jobs()["total"] == 0

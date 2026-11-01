"""Source intake is discoverable, keyboard accessible and read-only over real HTTP."""

import pytest

from tests.p7_5.test_phase4_browser import browser, http_app, observed_page

__all__ = ["browser", "http_app", "observed_page"]


@pytest.mark.parametrize("width,height", [(1440, 900), (1920, 1080), (375, 812)])
def test_sources_layout_focus_and_offline_read_only(http_app, observed_page, width, height):
    app, base = http_app
    page, artifacts, diagnostics = observed_page
    external = []
    page.on("request", lambda request: external.append(request.url)
            if not request.url.startswith(base) else None)
    page.set_viewport_size({"width": width, "height": height})
    page.emulate_media(reduced_motion="reduce")
    page.goto(base + "/library/sources", wait_until="networkidle")
    assert page.get_by_role("heading", name="开源来源", exact=True).count() == 1
    assert page.locator(".asset-entry").count() == 7
    assert page.get_by_role("link", name="带入研究问题", exact=True).count() == 0
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.matches(':focus-visible') && getComputedStyle(document.activeElement).outlineStyle !== 'none'")
    assert page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
    page.screenshot(path=str(artifacts / f"sources-{width}.png"), full_page=True)
    page.get_by_role("link", name="alphalens-reloaded", exact=True).click()
    assert "Apache-2.0" in page.locator(".asset-document").inner_text()
    assert "财务效果" in page.locator(".asset-document").inner_text()
    assert "alphalens-performance-reference" in page.locator(".asset-document").inner_text()
    page.screenshot(path=str(artifacts / f"source-detail-{width}.png"), full_page=True)
    assert page.request.post(base + "/api/research/assets?kind=sources", data={}).status == 405
    assert app.research_service.list_jobs()["total"] == 0 and not external
    diagnostics["metrics"]["source_records"] = 7


def test_source_search_empty_error_retry_preserves_read_only_contract(http_app, observed_page):
    app, base = http_app
    page, _, diagnostics = observed_page
    page.goto(base + "/library/sources", wait_until="networkidle")
    field = page.locator('[data-asset-form] [name="q"]')
    submit = page.locator('[data-asset-form] button[type="submit"]')
    field.fill("没有这项来源")
    submit.click()
    page.wait_for_selector('[data-asset-state="empty"]')
    field.fill("Alphalens")

    def invalid_response(route):
        route.fulfill(status=200, content_type="application/json", body='{"schema_version":"wrong"}')

    page.route("**/api/research/assets?*", invalid_response)
    submit.click()
    page.wait_for_selector('[data-asset-state="error"]')
    assert field.input_value() == "Alphalens"
    page.unroute("**/api/research/assets?*", invalid_response)
    page.locator("[data-asset-retry]").click()
    page.wait_for_selector('[data-asset-state="ready"]')
    assert page.locator(".asset-entry").count() == 1
    assert page.get_by_role("link", name="带入研究问题", exact=True).count() == 0
    assert app.research_service.list_jobs()["total"] == 0
    diagnostics["metrics"]["recovered_search"] = True


@pytest.mark.parametrize("observed_page", [{"java_script_enabled": False}], indirect=True)
def test_source_detail_without_javascript(http_app, observed_page):
    _, base = http_app
    page, _, _ = observed_page
    page.goto(base + "/library/methods")
    page.get_by_role("link", name="开源来源", exact=True).click()
    page.get_by_role("link", name="QUANTAXIS", exact=True).click()
    assert page.locator(".asset-document").is_visible()
    assert "45项" in page.locator(".asset-document").inner_text()

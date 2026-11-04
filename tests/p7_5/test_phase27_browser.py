"""Read one real discovery report on desktop and mobile."""
import pytest

from tests.p7_5.test_phase4_browser import browser, http_app, observed_page
from tests.research.test_discovery_journeys import call
from tests.research.test_strategy_worker import completed

__all__ = ["browser", "http_app", "observed_page"]


@pytest.mark.parametrize("http_app", [False], indirect=True)
def test_discovery_report_at_three_viewports(http_app, observed_page):
    app, base = http_app
    page, artifacts, _ = observed_page
    sample = call(app, base, "/api/research/strategies/sample", {})
    values = {key: sample[key] for key in ("contract", "attachment_refs")}
    values["question"] = "等权组合策略能降低集中风险吗？"
    preview = call(app, base, "/api/research/discovery/preview", values)
    submitted = call(app, base, "/api/research/discovery", {**values, "authorized": True,
        "blueprint_fingerprint": preview["fingerprint"], "idempotency_key": "browser-discovery"})
    job = completed(app.research_service, submitted["job"]["job_id"])
    page.goto(base + job["report_url"], wait_until="networkidle")
    section = page.locator("section").filter(has=page.get_by_role("heading", name="研究计划与分析师证据", exact=True))
    assert section.is_visible()
    disclosure = section.get_by_text("technical · READY", exact=True)
    for width, height in ((1440, 900), (1920, 1080), (375, 812)):
        page.set_viewport_size({"width": width, "height": height})
        page.emulate_media(reduced_motion="reduce")
        disclosure.focus()
        page.keyboard.press("Enter")
        assert disclosure.locator("..").get_attribute("open") is not None
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2")
        page.screenshot(path=str(artifacts / f"discovery-{width}.png"), full_page=True)
        page.keyboard.press("Enter")

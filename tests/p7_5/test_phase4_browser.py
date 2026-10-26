"""Real-browser release gates for the Phase 4 dashboard fixes."""

from __future__ import annotations

import importlib.util
import json
import os
import threading
from datetime import date
from pathlib import Path

import pytest

from finahinking.local_app import LocalAppConfig, LocalApplication, create_server
from finahinking.p7 import MasteryEvidence, PersonalNode
from finahinking.research.contracts import (
    AgentReport,
    ResearchRunResult,
    ResearchRunState,
    ResearchState,
)
from finahinking.research.reports import ReportBundleWriter


@pytest.fixture(scope="module")
def browser():
    required = os.environ.get("FINATHINK_REQUIRE_BROWSER") == "1"
    if importlib.util.find_spec("playwright") is None:
        if required:
            pytest.fail("Browser release gate requires Playwright; install the dev dependencies.")
        pytest.skip("Playwright is not installed")
    from playwright.sync_api import Error, sync_playwright

    with sync_playwright() as playwright:
        try:
            instance = playwright.chromium.launch(headless=True)
        except Error as exc:
            if required:
                pytest.fail(f"Required Chromium could not launch: {exc}")
            pytest.skip("Chromium unavailable; run python3 -m playwright install chromium")
        try:
            yield instance
        finally:
            instance.close()


@pytest.fixture
def http_app(request):
    app = LocalApplication(LocalAppConfig(db_path=":memory:", offline=True))
    if getattr(request, "param", True):
        session = app.config.session_id
        for index, (kind, when) in enumerate((("quant", "2026-10-02"), ("quant", "2026-10-02"), ("strategy", "2026-10-03"))):
            app.repository.save_history_entry(session, history_id=f"history-{index}", source_kind=kind, source_id=f"source-{index}", source_fingerprint="a" * 64, event_type="completed", title="Local research", occurred_at=when)
        app.repository.save_node(session, PersonalNode("phase4-concept", "concept", "Local concept", {}))
        app.repository.save_mastery_evidence(session, MasteryEvidence("phase4-evidence", "phase4-concept", "quiz_response", "knowledge:local:q1", "correct", "2026-10-02T12:00:00Z"))
        state = ResearchRunState(run_id="phase4-run", current_state=ResearchState.ANALYSTS_READY, as_of=date(2026, 10, 1), analyst_reports=(AgentReport("technical", "READY", evidence_refs=("artifact:local",)),))
        result = ResearchRunResult(state=state, events=())
        manifest = ReportBundleWriter().write(result, app.artifact_root / "reports")
        app.register_research_run(result, manifest)
    server = create_server(app, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield app, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


@pytest.fixture
def observed_page(browser, request):
    artifact_root = Path(os.environ.get("FINATHINK_BROWSER_ARTIFACT_DIR", ".superpowers/sdd/2026-10-26-finathink-product-completion-roadmap/browser-artifacts"))
    artifacts = artifact_root / request.node.name.replace("/", "-")
    artifacts.mkdir(parents=True, exist_ok=True)
    diagnostics = {"errors": [], "metrics": {}}
    context = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1, **getattr(request, "param", {}))
    page = context.new_page()
    page.on("pageerror", lambda error: diagnostics["errors"].append(str(error)))
    page.on("console", lambda message: diagnostics["errors"].append(message.text) if message.type == "error" else None)
    try:
        yield page, artifacts, diagnostics
    finally:
        try:
            page.screenshot(path=str(artifacts / "final.png"), full_page=True)
            (artifacts / "page.html").write_text(page.content(), encoding="utf-8")
        finally:
            (artifacts / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
            context.close()
    assert not diagnostics["errors"], diagnostics["errors"]


def test_overview_loads_once_and_keeps_daily_keyboard_data_after_chart_resize(http_app, observed_page):
    _, base = http_app
    page, artifacts, diagnostics = observed_page
    requests = []
    page.on("request", lambda request: requests.append(request.url) if request.url.endswith("/api/overview") else None)
    page.goto(base, wait_until="networkidle")
    assert len(requests) == 1
    assert page.locator('[data-overview-day]').count() == 3
    assert page.get_by_role("table", name="Daily activity data").is_visible()
    day = page.locator('[data-overview-day][data-date="2026-10-02"]')
    day.focus()
    assert "2026-10-02" in page.locator('[data-overview-tooltip]').inner_text()
    assert "3 recorded" in page.locator('[data-overview-tooltip]').inner_text()
    assert day.evaluate("el => getComputedStyle(el).outlineStyle") == "solid"
    page.keyboard.press("Tab")
    assert page.locator(":focus").get_attribute("data-date") == "2026-10-03"
    assert page.locator('[data-overview-topics] .overview-topic-cell').first.evaluate("el => getComputedStyle(el).display") == "grid"
    for width, height in ((1920, 1080), (375, 812), (1440, 900)):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_function("""() => {
            const target = document.querySelector('[data-overview-canvas]');
            const root = document.querySelector('[data-finathink-overview]');
            return root._finathinkOverviewChart && Math.abs(root._finathinkOverviewChart.getWidth() - target.clientWidth) < 2;
        }""")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.locator('[data-overview-day]').count() == 3
        page.screenshot(path=str(artifacts / f"resize-{width}.png"), full_page=True)
    page.emulate_media(reduced_motion="reduce")
    page.wait_for_function("() => document.querySelector('[data-finathink-overview]')._finathinkOverviewChart.getOption().animation === false")
    diagnostics["metrics"] = page.evaluate("""() => {
        const nav = performance.getEntriesByType('navigation')[0];
        return {domContentLoadedMs: nav.domContentLoadedEventEnd, resources: performance.getEntriesByType('resource').length};
    }""")
    assert diagnostics["metrics"]["domContentLoadedMs"] < 5000
    assert diagnostics["metrics"]["resources"] < 40
    assert len(requests) == 1
    assert page.evaluate("""() => {
        const chart = document.querySelector('[data-finathink-overview]')._finathinkOverviewChart;
        dispatchEvent(new PageTransitionEvent('pagehide'));
        return chart.isDisposed();
    }""")


@pytest.mark.parametrize("width,height", [(375, 812), (768, 1024), (1024, 768), (1440, 900), (1920, 1080)])
def test_overview_and_comparison_keep_shell_and_fit_viewports(http_app, observed_page, width, height):
    _, base = http_app
    page, artifacts, _ = observed_page
    page.set_viewport_size({"width": width, "height": height})
    for path, name in (("/", "overview"), ("/research/compare", "comparison")):
        page.goto(base + path, wait_until="networkidle")
        assert page.get_by_role("navigation", name="Primary").is_visible()
        assert page.locator("main#main").is_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        if path != "/":
            assert page.locator('a[aria-current="page"]').get_attribute("href") == path
            assert page.get_by_role("table", name="Research run comparison").is_visible()
        page.screenshot(path=str(artifacts / f"{name}.png"), full_page=True)


@pytest.mark.parametrize("http_app", [False], indirect=True)
def test_empty_loading_and_error_states_keep_readable_fallback(http_app, observed_page):
    _, base = http_app
    page, artifacts, _ = observed_page
    pending = []
    page.route("**/api/overview", lambda route: pending.append(route))
    page.goto(base, wait_until="domcontentloaded")
    page.locator('[data-overview-loading]').wait_for(state="visible")
    assert page.locator('[data-finathink-overview]').get_attribute("aria-busy") == "true"
    assert page.get_by_text("No saved research activity yet").is_visible()
    page.screenshot(path=str(artifacts / "loading.png"), full_page=True)
    page.wait_for_function("() => document.querySelector('[data-finathink-overview]').dataset.overviewStatus === 'LOADING'")
    assert len(pending) == 1
    pending[0].fulfill(status=200, content_type="application/json", body="{}")
    page.locator('[data-overview-error]').wait_for(state="visible")
    assert "retry" in page.locator('[data-overview-error]').inner_text()
    assert page.locator('[data-finathink-overview]').get_attribute("aria-busy") == "false"
    assert page.locator('[data-overview-loading]').is_hidden()
    page.screenshot(path=str(artifacts / "error.png"), full_page=True)
    page.unroute("**/api/overview")
    page.reload(wait_until="networkidle")
    assert page.locator('[data-overview-error]').is_hidden()
    assert page.locator('[data-finathink-overview]').get_attribute("data-overview-status") == "READY"
    assert page.get_by_text("No topics are recorded yet").is_visible()
    assert page.locator('[data-overview-day]').count() == 0


@pytest.mark.parametrize("observed_page", [{"java_script_enabled": False}], indirect=True)
def test_no_javascript_overview_and_comparison_are_complete(http_app, observed_page):
    _, base = http_app
    page, artifacts, _ = observed_page
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(base, wait_until="networkidle")
    assert page.get_by_role("table", name="Daily activity data").is_visible()
    assert page.locator('[data-overview-loading]').is_hidden()
    assert page.locator('[data-finathink-overview]').get_attribute("aria-busy") == "false"
    page.screenshot(path=str(artifacts / "overview-no-js.png"), full_page=True)
    page.goto(base + "/research/compare", wait_until="networkidle")
    assert page.locator("main#main").is_visible()
    assert page.get_by_role("navigation", name="Primary").is_visible()
    assert page.get_by_role("table", name="Research run comparison").is_visible()


def test_empty_payload_replaces_server_daily_rows(http_app, observed_page):
    app, base = http_app
    page, _, _ = observed_page
    payload = app.overview()
    payload["activity_series"] = []
    payload["activity"] = []
    page.route("**/api/overview", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(payload)))
    page.goto(base, wait_until="networkidle")
    assert page.locator('[data-overview-day]').count() == 0
    assert page.locator('[data-overview-canvas]').is_hidden()
    assert page.get_by_role("table", name="Daily activity data").get_by_text("No dated activity recorded", exact=False).is_visible()

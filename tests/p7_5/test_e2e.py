from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from finahinking.local_app import LocalAppConfig, LocalApplication, create_server
from finahinking.research.contracts import (
    AgentReport,
    ResearchRunResult,
    ResearchRunState,
    ResearchState,
    RunEvent,
)
from finahinking.research.reports import ReportBundleWriter


def _request(base: str, path: str, *, method: str = "GET", body: dict | None = None) -> tuple[int, dict | str]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(base + path, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    # CI and developer shells may export an HTTP proxy.  A loopback smoke test
    # must never leave the machine or depend on proxy policy.
    opener = build_opener(ProxyHandler({}))
    try:
        response = opener.open(request, timeout=3)
    except HTTPError as error:
        response = error
    with response:
        raw = response.read().decode("utf-8")
        content_type = response.headers.get("Content-Type", "")
        status = response.status
    return status, json.loads(raw) if "json" in content_type else raw


class _DocumentProbe(HTMLParser):
    """Small deterministic DOM probe for no-JS and accessibility assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str]]] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, {key: value or "" for key, value in attrs}))

    def handle_data(self, data: str) -> None:
        self.text.append(data)

    def has(self, tag: str, **wanted: str) -> bool:
        return any(name == tag and all(attrs.get(key) == value for key, value in wanted.items()) for name, attrs in self.tags)

    def values(self, tag: str, attribute: str) -> list[str]:
        return [attrs[attribute] for name, attrs in self.tags if name == tag and attrs.get(attribute)]

    @property
    def visible_text(self) -> str:
        return " ".join(" ".join(self.text).split())


def _registered_runtime_app() -> LocalApplication:
    """Create one bounded report so HTTP tests exercise a real run journey."""

    app = LocalApplication(LocalAppConfig(db_path=":memory:", offline=True))
    state = ResearchRunState(
        run_id="browser-e2e-run",
        current_state=ResearchState.ANALYSTS_RUNNING,
        as_of=date(2026, 10, 1),
        state_history=(ResearchState.RECEIVED, ResearchState.DATA_CHECKED, ResearchState.ANALYSTS_RUNNING),
        analyst_reports=(AgentReport("technical", "READY", evidence_refs=("artifact:technical",), limitations=("offline fixture",)),),
    )
    result = ResearchRunResult(
        state=state,
        events=(RunEvent("event-1", state.run_id, state.current_state, "worker", datetime(2026, 10, 1, tzinfo=UTC), "digest-1", metadata={"tool_summary": {"name": "factor_scan", "status": "COMPLETE"}, "retry_count": 1}),),
    )
    manifest = ReportBundleWriter().write(result, app.artifact_root / "reports")
    app.register_research_run(result, manifest)
    return app


def _register_failed_runtime_app() -> LocalApplication:
    """Register a real server-owned failed run for browser failure coverage."""

    app = LocalApplication(LocalAppConfig(db_path=":memory:", offline=True))
    state = ResearchRunState(
        run_id="browser-failed-run",
        current_state=ResearchState.FAILED,
        as_of=date(2026, 10, 1),
        state_history=(ResearchState.RECEIVED, ResearchState.FAILED),
        analyst_reports=(),
    )
    result = ResearchRunResult(state=state, events=())
    manifest = ReportBundleWriter().write(result, app.artifact_root / "reports")
    app.register_research_run(result, manifest)
    return app


def _write_artifact_manifest(artifact_dir: Path) -> list[str]:
    """Write a stable manifest for evidence files, excluding the manifest itself."""

    manifest_path = artifact_dir / "SHA256SUMS.tsv"
    lines: list[str] = []
    for item in sorted(
        (candidate for candidate in artifact_dir.iterdir() if candidate.is_file() and candidate != manifest_path),
        key=lambda candidate: candidate.relative_to(artifact_dir).as_posix(),
    ):
        relative_name = item.relative_to(artifact_dir).as_posix()
        content = item.read_bytes()
        lines.append(f"{relative_name}\t{len(content)}\t{hashlib.sha256(content).hexdigest()}")
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


def _assert_artifact_manifest(artifact_dir: Path, lines: list[str]) -> None:
    """Verify every listed evidence hash and size after writing the manifest."""

    for line in lines:
        relative_name, size, digest = line.split("\t")
        item = artifact_dir / relative_name
        content = item.read_bytes()
        assert item != artifact_dir / "SHA256SUMS.tsv"
        assert int(size) == len(content)
        assert digest == hashlib.sha256(content).hexdigest()


@pytest.mark.skipif(__import__("importlib.util").util.find_spec("playwright") is None and os.environ.get("FINATHINK_REQUIRE_BROWSER") != "1", reason="playwright dev dependency is not installed")
def test_browser_acceptance_real_engine_keyboard_responsive_failures_and_evidence(monkeypatch) -> None:
    """Exercise the served product in Chromium; HTMLParser tests cannot prove these behaviors."""

    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    artifact_dir = Path(os.environ.get("FINATHINK_BROWSER_ARTIFACT_DIR", ".superpowers/sdd/2026-10-26-finathink-product-completion-roadmap/browser-artifacts")) / "runtime-journeys"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    secret = "browser-secret-must-never-render"
    monkeypatch.setenv("FINAHINK_USER_API_KEY", secret)
    app = _register_failed_runtime_app()
    server = create_server(app, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    browser_errors: list[str] = []
    try:
        with sync_playwright() as playwright:
            # Let Playwright resolve its own bundled browser so this works on
            # every developer/CI machine. Missing browser binaries are an
            # explicit environment skip, rather than a path-specific failure.
            if not Path(playwright.chromium.executable_path).is_file():
                if os.environ.get("FINATHINK_REQUIRE_BROWSER") == "1":
                    pytest.fail("Required Chromium binary is unavailable; run python -m playwright install chromium")
                pytest.skip("Playwright bundled Chromium is unavailable; run `python -m playwright install chromium`")
            try:
                browser = playwright.chromium.launch(headless=True)
            except PlaywrightError as error:
                if os.environ.get("FINATHINK_REQUIRE_BROWSER") == "1":
                    pytest.fail(f"Required Chromium could not launch: {error}")
                pytest.skip(
                    f"Playwright bundled Chromium could not launch ({error.__class__.__name__}); "
                    "run `python -m playwright install chromium`"
                )
            no_js = browser.new_context(java_script_enabled=False, viewport={"width": 390, "height": 844})
            page = no_js.new_page()
            page.goto(f"{base}/research/browser-failed-run", wait_until="networkidle")
            assert page.locator("main#main").is_visible()
            assert page.get_by_text("FAILED", exact=True).count() >= 1
            assert page.get_by_text("READ-ONLY", exact=False).count() >= 1
            assert secret not in page.content()
            page.screenshot(path=str(artifact_dir / "no-js-failed-mobile.png"), full_page=True)
            (artifact_dir / "no-js-failed-mobile.html").write_text(page.content(), encoding="utf-8")
            no_js.close()

            context = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
            page = context.new_page()
            page.on("pageerror", lambda error: browser_errors.append(f"pageerror:{error}"))
            page.on("console", lambda msg: browser_errors.append(f"console:{msg.text}") if msg.type == "error" else None)
            page.goto(f"{base}/", wait_until="networkidle")
            assert page.locator("[data-finathink-overview]").is_visible()
            assert page.get_by_text("Overview", exact=True).count() >= 1
            assert page.get_by_text("PAPER-ONLY", exact=True).count() >= 1
            assert page.locator("body").evaluate("el => el.scrollWidth <= window.innerWidth")
            assert page.locator("[data-finathink-overview]").get_attribute("aria-busy") == "false"
            assert page.locator("[data-overview-tooltip]").get_attribute("aria-live") == "polite"
            navigation_timing = page.evaluate("""() => {
                const entry = performance.getEntriesByType('navigation')[0];
                return { domContentLoaded: entry ? entry.domContentLoadedEventEnd : 0, resourceCount: performance.getEntriesByType('resource').length };
            }""")
            assert navigation_timing["domContentLoaded"] < 5000
            assert navigation_timing["resourceCount"] < 40
            page.locator("[data-overview-chart]").focus()
            assert page.locator(":focus").get_attribute("data-overview-chart") == ""
            page.screenshot(path=str(artifact_dir / "overview-desktop.png"), full_page=True)
            (artifact_dir / "overview-desktop.html").write_text(page.content(), encoding="utf-8")

            wide = browser.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
            wide_page = wide.new_page()
            wide_page.goto(f"{base}/", wait_until="networkidle")
            assert wide_page.locator("body").evaluate("el => el.scrollWidth <= window.innerWidth")
            wide_page.screenshot(path=str(artifact_dir / "overview-wide-1920.png"), full_page=True)
            wide.close()

            for route in ("/research", "/knowledge", "/quant"):
                page.goto(f"{base}{route}", wait_until="networkidle")
                assert page.locator("main#main").is_visible()
                assert page.locator("body").evaluate("el => el.scrollWidth <= window.innerWidth")

            reduced = browser.new_context(viewport={"width": 375, "height": 812}, reduced_motion="reduce")
            reduced_page = reduced.new_page()
            reduced_page.goto(f"{base}/", wait_until="networkidle")
            assert reduced_page.locator("body").evaluate("el => el.scrollWidth <= window.innerWidth")
            assert reduced_page.locator("[data-finathink-overview]").get_attribute("data-animation") == "reduced"
            reduced_page.screenshot(path=str(artifact_dir / "overview-mobile-reduced-motion.png"), full_page=True)
            reduced.close()
            page.goto(f"{base}/settings/data-connections", wait_until="networkidle")
            assert page.locator('input[name="api_key"]').get_attribute("type") == "password"
            assert secret not in page.content()
            # Tab traversal reaches the skip link, then the primary navigation and main content.
            page.keyboard.press("Tab")
            assert page.locator(":focus").get_attribute("href") == "#main"
            for _ in range(8):
                page.keyboard.press("Tab")
            assert page.locator(":focus").count() == 1
            assert page.locator("body").evaluate("el => el.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(artifact_dir / "settings-desktop.png"), full_page=True)
            (artifact_dir / "settings-desktop.html").write_text(page.content(), encoding="utf-8")

            # Mobile layout must fit without horizontal overflow and retain readable controls.
            mobile = browser.new_page(viewport={"width": 390, "height": 844})
            mobile.goto(f"{base}/settings/data-connections", wait_until="networkidle")
            assert mobile.locator("body").evaluate("el => el.scrollWidth <= window.innerWidth")
            assert mobile.locator('input[name="api_key"]').is_visible()
            mobile.screenshot(path=str(artifact_dir / "settings-mobile.png"), full_page=True)

            # A failed stream fetch must become an explicit visible UI error, not a silent success.
            page.goto(f"{base}/research/browser-failed-run", wait_until="networkidle")
            page.route("**/api/research/runs/browser-failed-run/stream", lambda route: route.fulfill(status=200, content_type="application/json", body='{}'))
            page.reload(wait_until="networkidle")
            assert page.locator("[data-research-runtime-error]").is_visible()
            assert "Research runtime unavailable" in page.locator("[data-research-runtime-error]").inner_text()
            page.screenshot(path=str(artifact_dir / "failed-stream-error.png"), full_page=True)
            (artifact_dir / "failed-stream-error.html").write_text(page.content(), encoding="utf-8")
            context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()
    assert not browser_errors, browser_errors
    manifest_lines = _write_artifact_manifest(artifact_dir)
    _assert_artifact_manifest(artifact_dir, manifest_lines)


def test_http_runtime_journey_has_no_js_navigation_redacted_settings_and_read_only_stream(monkeypatch) -> None:
    secret = "e2e-only-secret-must-never-be-rendered"
    monkeypatch.setenv("FINAHINK_USER_API_KEY", secret)
    app = _registered_runtime_app()
    server = create_server(app, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        pages = {}
        for path in ("/settings/data-connections", "/settings/providers", "/research/browser-e2e-run"):
            status, page = _request(base, path)
            assert status == 200 and isinstance(page, str)
            assert secret not in page
            assert str(app.artifact_root) not in page
            document = _DocumentProbe()
            document.feed(page)
            assert document.has("a", href="#main")
            assert document.has("main", id="main", tabindex="-1")
            assert document.has("nav", **{"aria-label": "Primary"})
            assert document.has("a", **{"aria-label": "Finathink home"})
            pages[path] = document

        settings = pages["/settings/data-connections"]
        assert settings.has("input", name="api_key", type="password", autocomplete="new-password")
        assert "API key (never displayed)" in settings.visible_text
        assert "Saving does not download data" in settings.visible_text
        assert "never stores, echoes, or exports the secret value" in pages["/settings/providers"].visible_text

        run_page = pages["/research/browser-e2e-run"]
        assert "READ-ONLY" in run_page.visible_text and "PAPER-ONLY" in run_page.visible_text
        assert "ANALYSTS_RUNNING" in run_page.visible_text
        assert "factor_scan" in run_page.visible_text
        assert run_page.has("section", **{"data-research-runtime": "", "data-payload-url": "/api/research/runs/browser-e2e-run/stream"})
        report_links = {href for href in run_page.values("a", "href") if href.startswith("/research/browser-e2e-run/report/")}
        assert {"/research/browser-e2e-run/report/complete", "/research/browser-e2e-run/report/experiments", "/research/browser-e2e-run/report/risk_attribution", "/research/browser-e2e-run/report/learning_history"} <= report_links
        for href in sorted(report_links):
            status, report = _request(base, href)
            assert status == 200 and isinstance(report, str)
            assert secret not in report and str(app.artifact_root) not in report
            report_document = _DocumentProbe()
            report_document.feed(report)
            assert report_document.has("main")
            if href.endswith(("/complete", "/experiments", "/risk_attribution", "/learning_history")):
                assert "READ-ONLY" in report_document.visible_text
                assert "PAPER-ONLY" in report_document.visible_text

        stream_path = "/api/research/runs/browser-e2e-run/stream"
        status, snapshot = _request(base, stream_path)
        assert status == 200 and isinstance(snapshot, dict)
        assert snapshot["schema_version"] == 3
        assert snapshot["run_id"] == "browser-e2e-run"
        assert snapshot["state"] == "ANALYSTS_RUNNING"
        assert snapshot["stream"] == {"mode": "SERVER_SNAPSHOT", "read_only": True, "paper_only": True}
        assert snapshot["stage_status"]["analysts"] == "CURRENT"
        assert snapshot["stage_status"]["risk_attribution"] == "PENDING"
        assert snapshot["tool_summaries"] == [{"name": "factor_scan", "status": "COMPLETE"}]
        assert snapshot["retries"] == {"count": 1}
        assert set(snapshot["report_links"].values()) == report_links
        serialized = json.dumps(snapshot)
        for prohibited in (secret, str(app.artifact_root), '"api_key"', '"prompt"', '"endpoint"', '"raw_response"'):
            assert prohibited not in serialized
        for path in (stream_path, "/research/browser-e2e-run", "/research/browser-e2e-run/report/complete"):
            status, rejected = _request(base, path, method="POST", body={})
            assert status == 405 and "read-only" in rejected["error"]
        assert _request(base, stream_path)[1] == snapshot
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


def test_http_missing_runtime_run_returns_explicit_failure_without_fixture_fallback() -> None:
    app = _registered_runtime_app()
    server = create_server(app, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        for path in ("/research/missing-run", "/research/missing-run/report/complete", "/api/research/runs/missing-run/stream"):
            status, failure = _request(base, path)
            assert status == 404
            assert failure == {"error": "research run not found"}
            assert "browser-e2e-run" not in json.dumps(failure)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


def test_http_smoke_covers_event_knowledge_quant_strategy_and_continuity(tmp_path) -> None:
    app = LocalApplication(LocalAppConfig(db_path=":memory:"))
    server = create_server(app, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, health = _request(base, "/api/health")
        assert status == 200 and health["status"] == "ok"
        status, events = _request(base, "/api/events")
        assert status == 200 and events["events"][0]["status"] == "CAPTURED"
        status, event_saved = _request(base, "/api/events/learn", method="POST", body={})
        assert status == 201 and event_saved["node_type"] == "event"
        status, knowledge = _request(base, "/api/knowledge?q=return")
        assert status == 200 and any(item["id"] == "return" for item in knowledge["concepts"])
        status, quant = _request(base, "/api/quant")
        assert status == 200 and quant["stage"] == "experiment"
        status, strategy = _request(base, "/api/strategy")
        assert status == 200 and strategy["real_money"] is False
        status, saved = _request(base, "/api/personal/save", method="POST", body={"title": "Saved journey", "payload": {"step": 1}})
        assert status == 201 and saved["saved"] is True
        status, personal = _request(base, "/api/personal")
        assert status == 200 and any(item["title"] == "Saved journey" for item in personal["nodes"])
        status, reopened = _request(base, "/api/reopen", method="POST", body={})
        assert status == 200 and reopened["reopened"] is True
        status, community = _request(base, "/api/community")
        assert status == 200 and community["leaderboards"] is False
        status, diagnostics = _request(base, "/api/diagnostics")
        assert status == 200 and diagnostics["offline"] is True
    finally:
        server.shutdown()
        server.server_close()
        app.close()

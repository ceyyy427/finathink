"""Phase 5: real page → archived input → spawned engine → offline report."""

import threading
from contextlib import contextmanager
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest

from finahinking.local_app import LocalAppConfig, LocalApplication, create_server
from finahinking.p8_2.data_sources import FixtureMarketDataSource
from tests.p7_5.test_phase4_browser import (
    browser,
    observed_page,
)

__all__ = ["browser", "observed_page"]


@contextmanager
def serve(config):
    app = LocalApplication(config)
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
def http_app(tmp_path):
    with serve(LocalAppConfig(db_path=str(tmp_path / "local.sqlite"), offline=True)) as value:
        yield value


def submit(page, reference):
    form = page.locator('[data-run-submit]')
    form.locator('[name="snapshot_ref"]').select_option(reference)
    form.locator('[name="instrument"]').fill("AAA")
    form.locator('[name="as_of"]').fill("2026-10-01")
    form.locator('[name="question"]').fill("检验选定快照的动量、费用与历史回撤")
    form.locator('button[type="submit"]').click()
    page.locator('[data-run-identity]').filter(has_text=reference).wait_for(timeout=30_000)
    page.wait_for_selector('[data-run-workspace][data-job-status="completed"]', timeout=30_000)
    return parse_qs(urlsplit(page.url).query)["job_id"][0]


def test_two_real_inputs_generate_distinct_reports_and_survive_page_reload(http_app, observed_page):
    app, base = http_app
    page, _, diagnostics = observed_page
    a = FixtureMarketDataSource(points=20).snapshot("AAA")
    b = replace(a, observations=tuple(replace(item, close=100 - index, high=max(item.open, 100 - index) + 1, low=min(item.open, 100 - index) - 1) for index, item in enumerate(a.observations)))
    references = [app.research_service.snapshots.save(item) for item in (a, b)]
    page.goto(base + "/research/jobs")
    page.wait_for_function("document.querySelector('[data-run-submit] [name=snapshot_ref]').options.length === 3")
    first = submit(page, references[0])
    link_a = page.locator('[data-run-report]').get_attribute("href")
    second = submit(page, references[1])
    link_b = page.locator('[data-run-report]').get_attribute("href")
    assert first != second and link_a != link_b
    assert app.research_service.get_job(first)["metrics"]["total_return"] > 0
    assert app.research_service.get_job(second)["metrics"]["total_return"] == 0
    assert "SAMPLE DATA" in page.locator('[data-run-identity]').inner_text()
    page.get_by_text('技术指标、模型记录与完整限制', exact=True).click()
    assert "not evidence of market performance" in page.locator('[data-run-data-note]').inner_text()
    for link, reference in zip((link_a, link_b), references, strict=True):
        response = page.request.get(base + link)
        assert response.status == 200 and reference in response.text()
        assert "Total return" in response.text()
    page.reload()
    page.wait_for_selector('[data-job-status="completed"]', timeout=15_000)
    assert page.locator('[data-run-report]').get_attribute("href") == link_b
    assert app.research_service.get_job(second)["attempts"] == 1
    assert app.overview()["metrics"]["research_runs"] == 2
    assert not diagnostics["errors"]


def test_browser_reopens_a_completed_job_after_server_restart(tmp_path, observed_page):
    page, _, diagnostics = observed_page
    config = LocalAppConfig(db_path=str(tmp_path / "restart.sqlite"), offline=True)
    with serve(config) as (app, base):
        reference = app.research_service.snapshots.save(FixtureMarketDataSource(points=20).snapshot("AAA"))
        page.goto(base + "/research/jobs")
        page.wait_for_function("document.querySelector('[data-run-submit] [name=snapshot_ref]').options.length === 2")
        job_id = submit(page, reference)
        link = page.locator('[data-run-report]').get_attribute("href")
        page.goto("about:blank")
    with serve(config) as (app, base):
        page.goto(f"{base}/research/jobs?job_id={job_id}")
        page.wait_for_selector('[data-job-status="completed"]', timeout=15_000)
        assert page.locator('[data-run-report]').get_attribute("href") == link
        assert app.research_service.get_job(job_id)["attempts"] == 1
        assert page.request.get(base + link).status == 200
    assert not diagnostics["errors"]


def test_page_can_cancel_a_queued_job_without_spawning_an_owner(http_app, observed_page):
    app, base = http_app
    app.research_service.close()
    reference = app.research_service.snapshots.save(FixtureMarketDataSource(points=20).snapshot("AAA"))
    page, _, diagnostics = observed_page
    page.goto(base + "/research/jobs")
    page.wait_for_function("document.querySelector('[data-run-submit] [name=snapshot_ref]').options.length === 2")
    form = page.locator('[data-run-submit]')
    form.locator('[name="snapshot_ref"]').select_option(reference)
    form.locator('[name="as_of"]').fill("2026-10-01")
    form.locator('button[type="submit"]').click()
    page.wait_for_selector('[data-job-status="queued"]', timeout=10_000)
    page.locator('[data-run-cancel]').click()
    page.wait_for_selector('[data-job-status="cancelled"]', timeout=10_000)
    assert not page.locator('[data-run-report]').is_visible()
    assert app.research_service.runtime._supervisor._process is None
    assert app._run_repository.count_runs() == 0
    assert not diagnostics["errors"]


def test_switching_jobs_disables_cancel_until_the_new_identity_is_loaded(http_app, observed_page):
    app, base = http_app
    app.research_service.close()
    reference = app.research_service.snapshots.save(FixtureMarketDataSource(points=20).snapshot("AAA"))
    jobs = [app.research_service.submit({"idempotency_key": key, "snapshot_ref": reference, "instrument": "AAA", "as_of": "2026-10-01", "question": "Inspect the selected daily baseline", "model_config_ref": "offline"}) for key in ("switch-a", "switch-b")]
    page, _, diagnostics = observed_page
    page.goto(base + "/research/jobs")
    page.wait_for_selector('[data-job-status="queued"]')
    assert not page.locator('[data-run-cancel]').is_disabled()
    pending = []

    def hold_first(route):
        if not pending:
            pending.append(route)
        else:
            route.continue_()

    page.route(f'**/api/research/jobs/{jobs[0]["job_id"]}', hold_first)
    page.locator('[data-run-history] button').filter(has_text=jobs[0]["job_id"]).click()
    page.wait_for_function("document.querySelector('[data-run-workspace]').dataset.jobStatus === 'loading'")
    assert page.locator('[data-run-cancel]').is_disabled()
    assert not page.locator('[data-run-report]').is_visible()
    assert len(pending) == 1
    pending[0].continue_()
    page.wait_for_selector('[data-job-status="queued"]')
    page.locator('[data-run-cancel]').click()
    page.wait_for_selector('[data-job-status="cancelled"]')
    assert app.research_service.get_job(jobs[0]["job_id"])["status"] == "cancelled"
    assert app.research_service.get_job(jobs[1]["job_id"])["status"] == "queued"
    assert not diagnostics["errors"]


@pytest.mark.parametrize("size", [(1440, 900), (1920, 1080), (375, 812)])
def test_workspace_keyboard_empty_state_and_responsive_layout(http_app, observed_page, size):
    _, base = http_app
    page, _, diagnostics = observed_page
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.emulate_media(reduced_motion="reduce")
    page.goto(base + "/research/jobs")
    page.wait_for_selector('[data-workspace-state="EMPTY"]')
    assert page.locator('[data-run-empty]').is_visible()
    assert page.locator('[data-run-cancel]').is_disabled()
    assert page.locator('[data-run-metrics]').evaluate("el => getComputedStyle(el).display") == "grid"
    page.locator('[data-run-refresh]').focus()
    assert page.evaluate("document.activeElement.matches('[data-run-refresh]')")
    assert page.locator('[data-run-refresh]').evaluate("el => getComputedStyle(el).outlineStyle") != "none"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert not diagnostics["errors"]


def test_workspace_error_has_a_visible_recovery_action(http_app, observed_page):
    _, base = http_app
    page, _, diagnostics = observed_page
    page.route("**/api/research/jobs?limit=20", lambda route: route.fulfill(status=503, content_type="application/json", body='{"error":"unavailable"}'))
    page.goto(base + "/research/jobs")
    page.wait_for_selector('[data-run-error]:visible')
    assert page.locator('[data-run-refresh]').is_visible()
    page.unroute("**/api/research/jobs?limit=20")
    diagnostics["errors"].clear()  # The intentionally injected HTTP 503 is expected.
    page.locator('[data-run-refresh]').click()
    page.wait_for_selector('[data-workspace-state="EMPTY"]')
    assert not page.locator('[data-run-error]').is_visible()
    assert not diagnostics["errors"]

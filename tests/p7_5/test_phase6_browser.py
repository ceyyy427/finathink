"""Real browser/HTTP/spawn path, controlled remote peer, no vendor API calls."""

import json
import threading

from finahinking.local_app import LocalAppConfig, LocalApplication, create_server
from finahinking.p8_2.data_sources import FixtureMarketDataSource
from tests.p7_5.test_phase4_browser import browser, observed_page
from tests.research.grounded_worker_peer import install_peer
from tests.research.test_context_bundle import material

__all__ = ["browser", "observed_page"]


def test_page_configures_an_inert_provider_then_authorizes_a_grounded_spawn_job(observed_page, tmp_path):
    page, _artifacts, diagnostics = observed_page
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "app.sqlite"), offline=False))
    install_peer(app.research_service)
    ref = app.research_service.snapshots.save(FixtureMarketDataSource(points=20).snapshot("AAA"))
    server = create_server(app, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page.goto(base + "/settings/providers")
        form = page.locator('[data-provider-form]')
        for field, value in {"provider_id": "desk", "model": "gpt-4o", "endpoint": "https://models.acme.com/v1", "credential_env": "FINAHINK_TEST_KEY", "role_models": '{"technical":"gpt-4.1"}'}.items():
            form.locator(f'[name="{field}"]').fill(value)
        form.locator('button[type="submit"]').click()
        page.locator('[data-provider-status]').get_by_text("配置已保存", exact=False).wait_for()
        assert not (app.artifact_root / "peer-wire.jsonl").exists()
        page.goto(base + "/research/jobs")
        form = page.locator('[data-run-submit]')
        form.locator('[name="snapshot_ref"]').select_option(ref)
        form.locator('[name="as_of"]').fill("2026-10-01")
        form.locator('[name="model_config_ref"]').select_option("desk")
        form.locator('details').locator('summary').click()
        form.locator('[name="materials"]').fill(json.dumps([material()]))
        form.locator('[name="allow_model_calls"]').check()
        form.locator('button[type="submit"]').click()
        page.locator('[data-run-workspace][data-job-status="completed"], [data-run-workspace][data-job-status="failed"]').wait_for(timeout=30000)
        error_log = app.artifact_root / "peer-error.log"
        assert page.locator('[data-run-workspace]').get_attribute("data-job-status") == "completed", error_log.read_text() if error_log.exists() else page.locator('[data-run-failure]').inner_text()
        assert "3 次调用" in page.locator('[data-run-model-summary]').inner_text()
        assert "INSUFFICIENT_DATA" in page.locator('[data-run-model-roles]').inner_text()
        assert "Test the interpretation out of sample." in page.locator('[data-run-synthesis]').inner_text()
        report = page.request.get(base + page.locator('[data-run-report]').get_attribute("href"))
        assert report.ok and "Test the interpretation out of sample." in report.text()
        assert "test-only-model-value" not in report.text()
        detail = page.request.get(base + page.locator('[data-run-detail]').get_attribute("href"))
        assert "<dd>USER_API</dd>" in detail.text()
        for width, height in ((1440, 900), (1920, 1080), (375, 812)):
            page.set_viewport_size({"width": width, "height": height})
            layout = form.evaluate("form => ({right:form.getBoundingClientRect().right, controlsRight:Math.max(...[...form.querySelectorAll('input,select,textarea,button')].filter(e=>e.offsetParent!==null).map(e=>e.getBoundingClientRect().right)), bound:document.querySelector('.workspace-inner').getBoundingClientRect().right, documentWidth:document.documentElement.scrollWidth, viewport:innerWidth})")
            assert layout["controlsRight"] <= layout["right"] + 2 and layout["right"] <= layout["bound"] + 2 and layout["documentWidth"] <= layout["viewport"] + 2, layout
            page.screenshot(path=str(_artifacts / f"expanded-{width}.png"), full_page=True)
        page.set_viewport_size({"width": 1440, "height": 900})
        assert diagnostics["errors"] == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()

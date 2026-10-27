import json
import threading

from finahinking.local_app import LocalAppConfig, LocalApplication, create_server
from tests.p7_5.test_phase4_browser import browser, observed_page
from tests.research.test_phase8_learning_flow import inputs

__all__ = ["browser", "observed_page"]


def test_learning_review_approves_actual_rerun_and_reopens_history(observed_page, tmp_path):
    page, artifacts, diagnostics = observed_page
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "app.sqlite"), offline=True))
    source, source_ref, evidence_ref = inputs(app.research_service)
    server = create_server(app, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page.emulate_media(reduced_motion="reduce")
        page.goto(base + "/research/learning")
        root = page.locator('[data-learning-workspace]')
        page.wait_for_function("() => document.querySelector('[data-learning-status]').textContent.includes('本地记录已就绪')")
        form = root.locator('[data-learning-form]')
        form.locator('[name="run_id"]').select_option(source["run_id"])
        form.locator('[name="evidence_snapshot_ref"]').select_option(evidence_ref)
        form.locator('button[type="submit"]').click()
        page.locator('[data-learning-state="PENDING_REVIEW"]').wait_for()
        assert "9,697.302024" in root.locator('[data-learning-outcome]').inner_text()
        assert root.locator('[data-learning-decay] tbody tr').count() == 3
        assert "0.9" in root.locator('[data-learning-preview]').inner_text()
        root.locator('[name="rationale"]').fill("已核对固定观察期的净损失与费用，批准为下一轮纸面研究配置。")
        root.locator('[data-learning-ack]').check()
        root.locator('[data-learning-approve]').focus()
        page.keyboard.press("Enter")
        page.locator('[data-learning-state="APPROVED"]').wait_for()
        assert root.locator('[data-learning-approve]').is_disabled()
        assert root.locator('[data-learning-reject]').is_disabled()
        assert float(root.locator('h3').first.evaluate("element => parseFloat(getComputedStyle(element).marginTop)")) >= 24
        for width, height in ((1440, 900), (1920, 1080), (375, 812)):
            page.set_viewport_size({"width": width, "height": height})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2")
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=str(artifacts / f"learning-review-{width}.png"), full_page=True)
        rerun = root.locator('[data-learning-rerun-form]')
        assert rerun.locator('[name="snapshot_ref"]').input_value() == source_ref
        rerun.locator('button[type="submit"]').click()
        page.locator('[data-learning-job-state="completed"]').wait_for(timeout=30000)
        assert "回溯比较" in root.locator('[data-learning-job]').inner_text()
        href = root.locator('[data-learning-report]').get_attribute("href")
        response = page.request.get(base + href)
        assert response.ok and "回溯比较" in response.text()
        assert len(app.research_service.learning.policies()) == 2
        page.reload()
        root.locator('[data-learning-history] button').first.click()
        page.locator('[data-learning-state="APPROVED"]').wait_for()
        assert "已核对固定观察期" in root.locator('[data-learning-admission]').inner_text()
        page.set_viewport_size({"width": 1440, "height": 900})
        page.keyboard.press("Tab")
        root.locator('[data-learning-original-report]').focus()
        assert root.locator('[data-learning-original-report]').evaluate("element => element.matches(':focus-visible') && getComputedStyle(element).outlineStyle !== 'none'")
        assert float(form.evaluate("element => parseFloat(getComputedStyle(element).rowGap)")) >= 12
        page.goto(base + href)
        assert page.get_by_role("heading", name="配置版本与复跑来源").is_visible()
        for width, height in ((1440, 900), (1920, 1080), (375, 812)):
            page.set_viewport_size({"width": width, "height": height})
            page.evaluate("window.scrollTo(0, 0)")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2")
            page.screenshot(path=str(artifacts / f"versioned-report-{width}.png"), full_page=True)
        assert not diagnostics["errors"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


def test_learning_loading_error_recovery_and_empty_states(observed_page, tmp_path):
    page, artifacts, diagnostics = observed_page
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "app.sqlite"), offline=True))
    server = create_server(app, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    pending = []
    try:
        page.route("**/api/research/learning", lambda route: pending.append(route))
        page.goto(base + "/research/learning", wait_until="domcontentloaded")
        root = page.locator('[data-learning-workspace]')
        assert root.get_attribute("aria-busy") == "true"
        assert "正在读取" in root.locator('[data-learning-status]').inner_text()
        page.screenshot(path=str(artifacts / "learning-loading.png"), full_page=True)
        page.wait_for_function("() => document.querySelector('[data-learning-status]').textContent.includes('正在读取')")
        pending[0].fulfill(status=200, content_type="application/json", body="{}")
        root.locator('[data-learning-status][role="alert"]').wait_for()
        assert root.get_attribute("aria-busy") == "false"
        assert "重试" in root.locator('[data-learning-status]').inner_text()
        page.screenshot(path=str(artifacts / "learning-error.png"), full_page=True)
        page.unroute("**/api/research/learning")
        root.locator('[data-learning-refresh]').click()
        page.wait_for_function("() => document.querySelector('[data-learning-status]').textContent.includes('本地记录已就绪')")
        assert "尚无复盘" in root.locator('[data-learning-history]').inner_text()
        assert root.locator('[data-learning-detail]').is_hidden()
        for width, height in ((1440, 900), (1920, 1080), (375, 812)):
            page.set_viewport_size({"width": width, "height": height})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2")
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=str(artifacts / f"learning-empty-{width}.png"), full_page=True)
        assert not diagnostics["errors"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


def test_switching_reruns_disables_old_cancel_until_new_job_is_bound(observed_page, tmp_path):
    page, _, diagnostics = observed_page
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "app.sqlite"), offline=True))
    service = app.research_service
    source, source_ref, _ = inputs(service)
    service.close()
    values = {"run_id": source["run_id"], "snapshot_ref": source_ref, "as_of": "2026-01-12", "policy_ref": service.learning.policies()[0]["policy_ref"]}
    one, two = [service.learning.rerun({**values, "idempotency_key": f"queued-{index}"}) for index in range(2)]
    server = create_server(app, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    pending = []
    try:
        page.goto(base + "/research/learning")
        root = page.locator('[data-learning-workspace]')
        root.locator('[data-learning-runs]').get_by_role("button", name=one["run_id"], exact=False).click()
        page.locator('[data-learning-job-state="queued"]').wait_for()
        assert root.locator('[data-learning-cancel]').is_enabled()
        page.route(f'**/api/research/jobs/{two["job_id"]}', lambda route: pending.append(route))
        root.locator('[data-learning-runs]').get_by_role("button", name=two["run_id"], exact=False).click()
        assert root.locator('[data-learning-cancel]').is_disabled()
        for route in pending:
            route.fulfill(status=200, content_type="application/json", body=json.dumps(service.get_job(two["job_id"])))
        pending.clear()
        page.unroute(f'**/api/research/jobs/{two["job_id"]}')
        root.locator('[data-learning-cancel]').click()
        page.locator('[data-learning-job-state="cancelled"]').wait_for()
        assert service.get_job(one["job_id"])["status"] == "queued"
        assert service.get_job(two["job_id"])["status"] == "cancelled"
        page.evaluate("window.scrollTo(0, 0)")
        assert not diagnostics["errors"]
    finally:
        for route in pending:
            route.fulfill(status=200, content_type="application/json", body=json.dumps(service.get_job(two["job_id"])))
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()

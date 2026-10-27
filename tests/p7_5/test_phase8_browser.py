import json
import threading
from dataclasses import replace

import pytest

from finahinking.local_app import LocalAppConfig, LocalApplication, create_server
from tests.p7_5.test_phase4_browser import browser, observed_page
from tests.research.test_phase8_learning_flow import inputs

__all__ = ["browser", "observed_page"]


def test_factor_monitoring_review_is_not_presented_as_paper_settlement(observed_page, tmp_path):
    from finahinking.research.factor_monitoring import evaluate_factor_monitoring
    from tests.research.test_factor_monitoring import history, policy

    page, artifacts, diagnostics = observed_page
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "app.sqlite"), offline=True))
    review = app.research_service.learning.review_factor_monitoring(
        evaluate_factor_monitoring("a" * 64, history(), policy()))
    server = create_server(app, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(f"http://127.0.0.1:{server.server_address[1]}/research/learning")
        root = page.locator('[data-learning-workspace]')
        root.locator('[data-learning-history] button').first.click()
        page.locator('[data-learning-state="PENDING_REVIEW"]').wait_for()
        assert "非纸面成交结算" in root.locator('[data-learning-binding]').inner_text()
        assert "不修改权重" in root.locator('[data-learning-outcome]').inner_text()
        assert "非成交估值" in root.locator('[data-learning-outcome] caption').inner_text()
        assert "独立最终测试窗口" == root.locator('[data-learning-decay] th').first.inner_text()
        assert root.locator('[data-learning-rerun-form]').is_hidden()
        root.locator('[name="rationale"]').fill("仅登记研究复核建议，没有已实现收益或自动调权。")
        root.locator('[data-learning-ack]').check()
        root.locator('[data-learning-approve]').click()
        page.locator('[data-learning-state="APPROVED"]').wait_for()
        assert app.research_service.learning.get(review["review_id"])["settlement"] is None
        assert not diagnostics["errors"]
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(artifacts / "factor-monitoring-review-1440.png"), full_page=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


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
        assert "响应的数据格式异常" in root.locator('[data-learning-status]').inner_text()
        assert "reading" not in root.locator('[data-learning-status]').inner_text()
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


@pytest.mark.parametrize("terminal", ["cancelled", "failed"])
def test_rerun_retry_preserves_ambiguous_active_key_but_rotates_after_terminal(observed_page, tmp_path, terminal):
    page, _, diagnostics = observed_page
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "app.sqlite"), offline=True))
    service = app.research_service
    source, source_ref, _ = inputs(service)
    snapshot = service.snapshots.load(source_ref)
    invalid_ref = service.snapshots.save(replace(snapshot, observations=snapshot.observations[:3]))
    server = create_server(app, host="127.0.0.1", port=0)
    # Server bootstrap starts its independent owner. Stop it afterwards so
    # queued/cancelled cases do not race an unintended real worker.
    service.close()
    assert service.runtime._supervisor._process is None, 'queued-case fixture must stop the server-started owner'
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    keys, accepted, created = [], [], []

    def lose_reply(route):
        response = route.fetch()
        accepted.append(response.json())
        route.fulfill(status=200, content_type="application/json", body="{")

    page.on("request", lambda request: keys.append(request.post_data_json["idempotency_key"]) if request.method == "POST" and request.url.endswith("/api/research/learning/rerun") else None)
    try:
        page.goto(base + "/research/learning")
        root = page.locator('[data-learning-workspace]')
        form = root.locator('[data-learning-rerun-form]')
        form.locator('[name="run_id"]').select_option(source["run_id"])
        form.locator('[name="snapshot_ref"]').select_option(invalid_ref)
        page.route("**/api/research/learning/rerun", lose_reply)
        form.locator('button[type="submit"]').click()
        root.locator('[data-learning-status][role="alert"]').wait_for()
        assert len(accepted) == 1
        created.append(accepted[0]["job_id"])
        page.unroute("**/api/research/learning/rerun")

        def submit():
            with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/api/research/learning/rerun")) as received:
                form.locator('button[type="submit"]').click()
            return received.value.json()

        retried = submit()
        assert retried["job_id"] == accepted[0]["job_id"]
        page.locator('[data-learning-job-state="queued"]').wait_for()
        active_retry = submit()
        assert active_retry["job_id"] == retried["job_id"]
        if terminal == "cancelled":
            root.locator('[data-learning-cancel]').click()
        else:
            service.start()
        page.locator(f'[data-learning-job-state="{terminal}"]').wait_for(timeout=30000)
        restarted = submit()
        created.append(restarted["job_id"])
        assert restarted["job_id"] != retried["job_id"]
        assert keys[0] == keys[1] == keys[2] and keys[3] != keys[2]
        if terminal == "cancelled":
            page.locator('[data-learning-job-state="queued"]').wait_for()
            root.locator('[data-learning-cancel]').click()
        page.locator(f'[data-learning-job-state="{terminal}"]').wait_for(timeout=30000)
        page.locator('[data-learning-workspace][aria-busy="false"]').wait_for()
        assert service.get_job(retried["job_id"])["status"] == terminal
        assert service.get_job(restarted["job_id"])["status"] == terminal
        assert not diagnostics["errors"]
        page.evaluate("window.scrollTo(0, 0)")
    finally:
        for job_id in set(created):
            service.cancel(job_id)
        page.locator('[data-learning-workspace][aria-busy="false"]').wait_for(timeout=5000)
        if root.get_attribute("data-learning-job-state") in {"queued", "running", "retryable", "loading"}:
            page.wait_for_function("() => ['cancelled','failed','completed'].includes(document.querySelector('[data-learning-workspace]').dataset.learningJobState)", timeout=5000)
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()

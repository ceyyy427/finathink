import threading

from finahinking.local_app import LocalAppConfig, LocalApplication, create_server
from tests.p7_5.test_phase4_browser import browser, observed_page

__all__ = ["browser", "observed_page"]


def test_factor_study_page_runs_frozen_test_and_reopens_verified_history(observed_page, tmp_path):
    page, artifacts, diagnostics = observed_page
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "app.sqlite"), offline=True))
    server = create_server(app, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page.goto(base + "/research/experiments")
        root = page.locator('[data-experiment-workspace]')
        root.locator('[data-study-sample]').click()
        root.locator('[name="snapshot_ref"] option').nth(1).wait_for(state="attached")
        form = root.locator('[data-study-form]')
        form.locator('[name="recipe_id"]').select_option("close_reversal")
        form.locator('[name="question"]').fill("研究均值回归的窗口稳定性")
        form.locator('[name="windows"]').fill("3,5,8")
        form.locator('[name="n_splits"]').fill("2")
        form.locator('[name="as_of"]').fill("2026-10-01")
        form.locator('button[type="submit"]').click()
        page.locator('[data-experiment-workspace][data-study-state="VALIDATED"]').wait_for(timeout=30000)
        assert root.locator('[data-study-candidates] tbody tr').count() == 3
        assert "最终测试尚未执行" in root.locator('[data-study-test-result]').inner_text()
        root.locator('[data-freeze-ack]').check()
        root.locator('[data-study-candidates] button').first.click()
        page.locator('[data-experiment-workspace][data-study-state="FROZEN"]').wait_for()
        root.locator('[data-study-test]').click()
        page.locator('[data-experiment-workspace][data-study-state="TEST_EVALUATED"]').wait_for(timeout=30000)
        assert root.locator('[data-study-test]').is_disabled()
        assert "PASS" in root.locator('[data-study-test-result]').inner_text()
        root.locator('[name="review_decision"]').select_option("REJECT")
        root.locator('[data-study-review]').click()
        page.locator('[data-experiment-workspace][data-study-state="REVIEWED"]').wait_for()
        href = root.locator('[data-study-report]').get_attribute("href")
        report = page.request.get(base + href)
        assert report.ok and "因子研究实验" in report.text()
        assert "风险与组合" in report.text() and "路径归因" in report.text()
        assert "PENDING_NEXT_OBSERVATION" in report.text()
        page.reload()
        root.locator('[data-study-history] button').first.click()
        page.locator('[data-experiment-workspace][data-study-state="REVIEWED"]').wait_for()
        assert "close_reversal" in root.locator('[data-study-binding]').inner_text()
        assert "折数 2" in root.locator('[data-study-binding]').inner_text()
        assert float(form.evaluate("element => parseFloat(getComputedStyle(element).rowGap)")) >= 12
        assert float(root.locator('[data-study-test]').evaluate("element => getComputedStyle(element).opacity")) < 1
        for width, height in ((1440, 900), (1920, 1080), (375, 812)):
            page.set_viewport_size({"width": width, "height": height})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2")
            if width == 375:
                wrapper = root.locator('[data-study-candidates]').locator('..')
                wrapper.focus()
                page.keyboard.press("ArrowRight")
                page.wait_for_function("element => element.scrollLeft > 0", arg=wrapper.element_handle())
            page.screenshot(path=str(artifacts / f"factor-study-{width}.png"), full_page=True)
        page.set_viewport_size({"width": 1440, "height": 900})
        page.keyboard.press("Tab")
        root.locator('[data-study-report]').focus()
        assert root.locator('[data-study-report]').evaluate("element => element.matches(':focus-visible')")
        assert root.locator('[data-study-report]').evaluate("element => getComputedStyle(element).outlineStyle !== 'none'")
        page.goto(base + href)
        for width, height in ((1440, 900), (1920, 1080), (375, 812)):
            page.set_viewport_size({"width": width, "height": height})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2"), page.evaluate("[...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > innerWidth + 2).slice(0, 12).map(e => ({tag: e.tagName, id: e.id, right: e.getBoundingClientRect().right, width: e.clientWidth, scroll: e.scrollWidth}))")
            if width == 375:
                region = page.get_by_role("region", name="约束影响表，可横向滚动")
                region.focus()
                page.keyboard.press("ArrowRight")
                page.wait_for_function("element => element.scrollLeft > 0", arg=region.element_handle())
            page.screenshot(path=str(artifacts / f"risk-report-{width}.png"), full_page=True)
        assert diagnostics["errors"] == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()

"""Use a saved panel from the workbench without pasting a contract."""

import json

from finahinking.data.connection_settings import InMemoryDataCredentialStore
from finahinking.local_app import LocalAppConfig
from tests.p7_5.test_phase4_browser import browser, http_app, observed_page
from tests.research.test_saved_panel_journeys import (
    STOCKS,
    TIMES,
    configure,
    owned_feed,
    running_panel_app,
    utc_day,
)
from tests.research.test_strategy_worker import completed

__all__ = ["browser", "http_app", "observed_page", "owned_feed"]


def test_prepare_save_and_use_panel_in_strategy(tmp_path, owned_feed, observed_page):
    from playwright.sync_api import expect

    transport, proof, calls, _, _ = owned_feed
    config = LocalAppConfig(db_path=str(tmp_path / "app.sqlite3"), offline=True)
    page, artifacts, diagnostics = observed_page
    with running_panel_app(config, transport, InMemoryDataCredentialStore()) as (app, base):
        configure(app, base, proof, ["prices", "rules"])
        page.goto(base + "/research/panels", wait_until="networkidle")
        assert calls == []
        page.get_by_text("从已保存的连接获取新快照", exact=True).click()
        form = page.locator("[data-panel-price]")
        form.get_by_label("行情连接").select_option("prices")
        form.get_by_label("股票标的").fill(",".join(STOCKS))
        form.get_by_label("行情开始日期").fill(utc_day(TIMES[0]))
        form.get_by_label("行情截止日期").fill(utc_day(TIMES[-1]))
        form.get_by_label("价格币种").select_option("CNY")
        form.get_by_label("这是原始价格，成交量按股或份记录").check()
        form.get_by_role("button", name="获取行情快照").click()
        expect(page.locator("[data-panel-snapshot-status]")).to_contain_text("CAPTURED · STKA、STKB、STKC")
        page.get_by_text("从数据接口添加字段附件", exact=True).click()
        page.get_by_label("字段连接").select_option("rules")
        page.get_by_label("cash_dividend 单位").select_option("CNY_per_share")
        page.get_by_role("button", name="获取字段并保存附件").click()
        page.get_by_text("字段附件已保存，可按上方规则或附加字段选用。", exact=True).wait_for()
        page.get_by_label("检查因子或策略所需字段").select_option("strategy:equal-weight")
        page.get_by_role("button", name="预览面板").click()
        page.get_by_text("面板可以保存。", exact=True).wait_for()
        assert page.get_by_role("table", name="字段覆盖及可用时间（UTC）").is_visible()
        page.get_by_label("面板名称").fill("试用股票面板")
        assert page.get_by_role("button", name="保存面板").is_disabled()
        page.get_by_role("button", name="预览面板").click()
        page.get_by_role("button", name="保存面板").click()
        page.get_by_text("面板已保存，可从因子、策略或研究工作台选用。", exact=True).wait_for()
        for width, height in [(1440, 900), (1920, 1080), (375, 812)]:
            page.set_viewport_size({"width": width, "height": height})
            assert page.locator("body").evaluate("el => el.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(artifacts / f"panel-{width}.png"), full_page=True)
        panel = app.research_service.panels.list()[0]
        page.get_by_role("link", name="研究策略", exact=True).click()
        select = page.get_by_role("combobox", name="已保存面板", exact=True)
        expect(select).to_have_value(panel["panel_ref"])
        assert not page.get_by_label("面板合同", exact=True).is_visible()
        page.get_by_role("button", name="提交纸面策略研究").click()
        page.get_by_role("link", name="打开后台任务").wait_for()
        job_id = page.get_by_role("link", name="打开后台任务").get_attribute("href").split("job=")[1]
        job = completed(app.research_service, job_id)
        assert job["strategy_study"]["binding"]["panel_ref"] == panel["panel_fingerprint"]
        assert job["strategy_study"]["result"]["ledger"]["fills"]
        assert not diagnostics["errors"]


def test_late_saved_list_cannot_switch_the_users_strategy_data_mode(http_app, observed_page):
    from playwright.sync_api import expect

    _, base = http_app
    page, _, _ = observed_page
    pending = []
    ref = "a" * 64
    with page.expect_request("**/api/research/panels/saved"):
        page.route("**/api/research/panels/saved", lambda route: pending.append(route))
        page.goto(base + "/research/strategies?panel=" + ref, wait_until="domcontentloaded")
    mode = page.locator('[data-strategy-study] [name="mode"]')
    expect(mode).to_have_value("saved")
    mode.select_option("sample")
    pending[0].fulfill(status=200, content_type="application/json", body=json.dumps({"panels": [
        {"panel_ref": ref, "name": "较晚返回的面板", "instrument_ids": ["STKA"], "last": "2026-06-01"},
    ]}))
    expect(page.locator("[data-saved-panel-select]")).to_have_value(ref)
    expect(mode).to_have_value("sample")
    expect(page.locator("[data-panel-picker]")).to_be_hidden()

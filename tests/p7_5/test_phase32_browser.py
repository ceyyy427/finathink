"""Follow the atlas from a published paper ledger into a reviewed frozen replay."""

import pytest

from tests.p7_5.test_phase4_browser import browser, http_app, observed_page
from tests.research.test_strategy_worker import completed

__all__ = ["browser", "http_app", "observed_page"]


@pytest.mark.parametrize("http_app", [False], indirect=True)
def test_published_ledger_to_reviewed_version_and_offline_download(http_app, observed_page):
    from playwright.sync_api import expect

    app, base = http_app
    page, artifacts, diagnostics = observed_page
    page.goto(base, wait_until="networkidle")
    expect(page.get_by_role("heading", name="研究总览", exact=True)).to_be_visible()
    page.get_by_role("link", name="创建纸面策略").click()
    page.get_by_label("策略模板").select_option("equal-weight")
    page.get_by_role("button", name="提交纸面策略研究").click()
    page.wait_for_function("() => document.querySelector('[data-strategy-status] a') !== null")
    job = app.research_service.list_jobs()["jobs"][0]
    source = completed(app.research_service, job["job_id"])
    stamp = source["strategy_study"]["binding"]["contract"]["observed_at"][-1][:10]
    page.goto(base, wait_until="networkidle")
    for width, height in ((1440, 900), (1920, 1080), (375, 812)):
        page.set_viewport_size({"width": width, "height": height})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), page.evaluate("""() => ({
            width: innerWidth, documentWidth: document.documentElement.scrollWidth,
            overflow: [...document.querySelectorAll('main *, header *')].filter(node =>
                node.getBoundingClientRect().right > innerWidth + 1).slice(0, 15).map(node => node.className.toString())
        })""")
        expect(page.locator(".atlas-spark")).to_be_visible()
        page.screenshot(path=str(artifacts / f"overview-{width}.png"))
    page.get_by_role("link", name="继续研究").click()
    expect(page.get_by_role("heading", name="运行复盘")).to_be_visible()
    slider = page.locator("[data-atlas-scrub]")
    slider.focus()
    slider.press("Home")
    expect(slider).to_have_value("0")
    readout = page.locator(".atlas-scrubber output")
    first = readout.inner_text()
    slider.press("ArrowRight")
    expect(readout).not_to_have_text(first)
    expect(page.locator(".atlas-run-metrics")).to_contain_text("累计收益")
    page.set_viewport_size({"width": 1440, "height": 900})
    page.screenshot(path=str(artifacts / "ledger-1440.png"))
    with page.expect_download() as result:
        page.get_by_role("link", name="导出离线报告").click()
    contents = result.value.path().read_text(encoding="utf-8")
    assert source["run_id"] in contents and "<html" in contents
    page.get_by_role("link", name="观察与结算").click()
    learn = page.locator("[data-strategy-learning]")
    expect(learn.locator("[name=run_id]").first).to_have_value(source["run_id"])
    learn.get_by_label("观察截止日").fill(stamp)
    learn.get_by_role("button", name="观察并结算账本").click()
    expect(learn.locator("[data-sl-receipt-binding]")).to_contain_text(source["run_id"])
    window = learn.locator('[data-sl-window-detail][data-status="SETTLED"]').first
    window.locator("summary").focus()
    window.locator("summary").press("Enter")
    fill = window.locator("[data-sl-fill]").first
    expect(fill).to_contain_text("实际结算时间")
    expect(fill).to_contain_text("手续费")
    receipt_url = learn.locator("[data-sl-raw-receipt]").get_attribute("href")
    receipt = page.request.get(base + receipt_url).json()
    sample = window.locator("[data-sl-sample-ref]").get_attribute("data-sl-sample-ref")
    assert any(row["sample_ref"] == sample for row in receipt["samples"])
    learn.get_by_role("button", name="审核结算样本").click()
    learn.get_by_role("button", name="生成纸面政策提案").click()
    approve = learn.get_by_role("button", name="批准纸面政策")
    expect(approve).to_be_visible()
    learn.get_by_label("观察截止日").fill("2000-01-01")
    expect(approve).to_be_hidden()
    learn.locator("[data-sl-history]").select_option(receipt["observation_ref"])
    expect(approve).to_be_visible()
    learn.get_by_label("审核依据").fill("逐项核对基准和已结算的共同窗口，批准在相同输入上比较预算。")
    learn.get_by_role("button", name="批准纸面政策").click()
    learn.get_by_role("button", name="用原冻结策略成对复跑").click()
    learn.get_by_role("link", name="打开运行审阅曲线").click(timeout=30000)
    expect(page.locator(".atlas-run")).to_contain_text("政策版本对照")
    expect(page.locator(".atlas-context")).to_contain_text("合成样例")
    expect(page.locator(".atlas-line")).to_have_count(3)
    expect(page.locator(".atlas-run-metrics")).to_contain_text("收益变化")
    for width, height in ((1440, 900), (1920, 1080), (375, 812)):
        page.set_viewport_size({"width": width, "height": height})
        page.emulate_media(reduced_motion="reduce")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(artifacts / f"paired-ledger-{width}.png"))
    page.reload(wait_until="networkidle")
    expect(page.locator(".atlas-line")).to_have_count(3)
    with page.expect_download() as replay_download:
        page.get_by_role("link", name="导出原始报告").click()
    assert replay_download.value.path().read_text(encoding="utf-8") == contents
    assert app.research_service.list_jobs()["total"] == 1
    assert not diagnostics["errors"]


@pytest.mark.parametrize("http_app", [False], indirect=True)
def test_four_atlas_views_and_explicit_example_are_read_only(http_app, observed_page):
    from playwright.sync_api import expect

    app, base = http_app
    page, artifacts, _ = observed_page
    page.goto(base, wait_until="networkidle")
    page.emulate_media(reduced_motion="reduce")
    for label, title, slug in (("证据", "证据溯源", "evidence"), ("因子实验", "因子实验", "factor"),
                               ("研究记录", "运行复盘", "runs")):
        page.get_by_role("navigation", name="研究视图").get_by_role("link", name=label, exact=True).click()
        expect(page.get_by_role("heading", level=1)).to_contain_text(title)
        if slug == "runs":
            expect(page.get_by_text("等待纸面账本")).to_be_visible()
            page.get_by_role("link", name="查看内置账本示例").click()
            expect(page.locator(".atlas-line")).to_have_count(2)
            expect(page.locator(".atlas-performance")).to_have_css("background-color", "rgb(19, 73, 64)")
            expect(page.locator(".atlas-line--primary")).to_have_css("fill", "none")
            expect(page.locator(".atlas-run")).to_contain_text("内置合成样例")
        for width, height in ((1440, 900), (1920, 1080), (375, 812)):
            page.set_viewport_size({"width": width, "height": height})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            page.screenshot(path=str(artifacts / f"{slug}-{width}.png"))
    assert app.research_service.list_jobs()["total"] == 0
    assert not app.research_service.snapshots.list_snapshots()

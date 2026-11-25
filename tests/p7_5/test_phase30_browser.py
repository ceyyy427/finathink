"""Use the workbench to preview, freeze and audit a real multi-factor run."""

from tests.p7_5.test_phase4_browser import browser, http_app, observed_page
from tests.research.test_factor_study_application import wait_study

__all__ = ["browser", "http_app", "observed_page"]


def test_question_to_scoped_review_and_offline_report(http_app, observed_page):
    from playwright.sync_api import expect

    app, base = http_app
    page, artifacts, diagnostics = observed_page
    page.emulate_media(reduced_motion="reduce")
    page.goto(base + "/research/workbench", wait_until="networkidle")
    page.get_by_role("link", name="从问题生成多候选").click()
    question = page.get_by_label("自然语言研究问题")
    preview = page.get_by_role("button", name="预览候选和反证")
    submit = page.get_by_role("button", name="登记并开始验证")
    plan = page.locator("[data-factor-discovery-plan]")
    expect(submit).to_be_disabled()
    question.fill("动量因子和均值回归因子")
    question.press("Enter")
    expect(plan.get_by_role("heading", name="可试用的研究问题")).to_be_visible()
    expect(submit).to_be_disabled()
    question.fill("请研究区间动量")
    preview.click()
    expect(submit).to_be_enabled()
    expect(plan.locator("details")).to_have_count(3)
    plan.locator("summary").first.press("Enter")
    expect(plan.get_by_role("link", name="核对公式与来源卡").first).to_be_visible()
    expect(plan.get_by_text("等价公式已合并", exact=False)).to_be_visible()
    assert app.research_service.list_jobs()["total"] == 0
    question.fill("动量因子")
    expect(submit).to_be_disabled()
    preview.click()
    expect(submit).to_be_enabled()
    for width, height in [(1440, 900), (1920, 1080), (375, 812)]:
        page.set_viewport_size({"width": width, "height": height})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(artifacts / f"discovery-preview-{width}.png"), full_page=True)
    submit.click()
    page.wait_for_url("**/research/factor-suites?study=*")
    study_id = page.url.split("?study=", maxsplit=1)[1]
    wait_study(app.research_service, study_id, "validation")
    page.reload(wait_until="networkidle")
    expect(page.locator("[data-discovery-candidate]")).to_have_count(3)
    expect(page.get_by_role("columnheader", name="全家族 Holm p 值")).to_be_visible()
    page.get_by_role("button", name="冻结候选", exact=True).click()
    expect(page.locator("[data-discovery-freeze]")).to_contain_text("冻结收据")
    assert not app.research_service.factor_study(study_id).get("test_result")
    page.get_by_role("button", name="运行一次最终测试").click()
    wait_study(app.research_service, study_id, "test")
    page.reload(wait_until="networkidle")
    review = page.locator("[data-discovery-review]")
    expect(review).to_contain_text("SYNTHETIC")
    expect(review).to_contain_text("资产范围")
    review.get_by_label("审核结论").select_option("REJECT")
    review.get_by_label("审核人标识").fill("browser-reviewer")
    review.get_by_role("checkbox").check()
    review.get_by_role("button", name="保存审核结论").click()
    expect(review).to_contain_text("已拒绝入库")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.screenshot(path=str(artifacts / "discovery-reviewed-mobile.png"), full_page=True)
    final = app.research_service.factor_study(study_id)
    page.get_by_role("link", name="查看此定义的审核历史").click()
    expect(page.get_by_role("heading", name="多候选的冻结诊断")).to_be_visible()
    expect(page.locator("[data-factor-lifecycle]")).to_contain_text("REJECTED")
    page.goto(base + final["test_job"]["report_url"], wait_until="networkidle")
    expect(page.get_by_role("heading", name="多候选研究设计")).to_be_visible()
    assert page.get_by_text("本次最终测试未读取", exact=True).count() == 2
    assert not diagnostics["errors"]

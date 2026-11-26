"""Portable workflow export, product comparison and byte-preserving download through the UI."""

import hashlib
import json

from playwright.sync_api import expect

from finahinking.research.learning_replays import LearningReplayService
from tests.p7_5.test_phase4_browser import browser, observed_page
from tests.p7_5.test_phase19_browser import enter
from tests.research.test_learning_replays import prepared
from tests.research.test_phase18_api import app_http
from tests.research.test_phase19_api import completed
from tests.research.test_strategy_worker import completed as complete_job

__all__ = ["app_http", "browser", "observed_page"]


def workflow_scope(page):
    expect(page.locator("[data-repro-scope]")).to_be_hidden()
    page.get_by_label("在研究包中携带已发表的标准化数据").check()
    expect(page.get_by_label("导出数据范围")).to_have_value("prices")
    page.get_by_label("导出数据范围").select_option("workflow")
    expect(page.get_by_label("导出对象")).to_be_visible()


def export_restore(page, button):
    with page.expect_download() as downloaded:
        page.get_by_role("button", name=button, exact=True).click()
    raw = downloaded.value.path().read_bytes()
    package = json.loads(raw)
    assert package["schema_version"] == "research-package.v2"
    page.locator("[data-repro-file]").set_input_files({
        "name": "published-research.json", "mimeType": "application/json", "buffer": raw,
    })
    page.get_by_role("button", name="恢复研究包", exact=True).click()
    expect(page.locator("[data-repro-packages]")).to_have_value(package["package_id"])
    page.get_by_role("button", name="复算已恢复产物").click()
    expect(page.locator("[data-repro-receipt] h4")).to_have_text("已复现", timeout=30000)
    return package


def test_workflow_package_receipt_and_original_report_download(app_http, observed_page):
    page, artifacts, _ = observed_page
    app = app_http[0]
    run = completed(app, "portable-browser")
    bundled = app.research_service.runs.get_run(run)["bundle"] / "complete_report.html"
    original = bundled.read_bytes()
    page.set_viewport_size({"width": 375, "height": 812})
    enter(app_http, page)
    page.get_by_label("左侧研究", exact=True).select_option(run)
    workflow_scope(page)
    export_restore(page, "导出左侧研究包")
    page.get_by_text("基线回测与风险 · 查看摘要与差异", exact=True).click()
    receipt = page.locator("[data-repro-receipt]")
    expect(receipt).to_contain_text("原产物摘要")
    expect(receipt).to_contain_text("1e-10")
    expect(receipt).to_contain_text("1e-9")
    page.get_by_role("button", name="读取历史文件清单").click()
    history = page.locator("[data-repro-history-list]")
    expect(history).to_contain_text(hashlib.sha256(original).hexdigest())
    with page.expect_download() as artifact:
        history.get_by_role("button", name="下载 "+run+".html", exact=True).first.click()
    assert artifact.value.path().read_bytes() == original
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.screenshot(path=str(artifacts / "workflow-receipt-mobile.png"), full_page=True)
    assert app.research_service.list_jobs()["total"] == 1


def test_approved_learning_pair_is_selectable_and_both_sides_replay(app_http, observed_page):
    page, _, _ = observed_page
    service = app_http[0].research_service
    pair = LearningReplayService(service).create(prepared(service))
    for side in pair["children"].values():
        complete_job(service, side["job_id"])
    jobs = service.list_jobs()["total"]
    enter(app_http, page)
    workflow_scope(page)
    page.get_by_label("导出对象").select_option(pair["pair_id"])
    export_restore(page, "导出学习成对研究包")
    coverage = page.get_by_role("table", name="每项产物的复现范围")
    for label in ("原政策复跑", "候选政策复跑", "学习审核与政策", "成对差异"):
        expect(coverage.get_by_role("row").filter(has_text=label)).to_contain_text("已复现")
    page.get_by_role("button", name="读取历史文件清单").click()
    evidence = page.locator("[data-repro-history-list]")
    expect(evidence).to_contain_text("学习审核与政策")
    with page.expect_download() as approval:
        evidence.get_by_role("button", name="下载归档证据 JSON").click()
    review = json.loads(approval.value.path().read_bytes())["learning_approval"]
    assert review == service.learning.get(pair["binding"]["review_id"])
    assert service.list_jobs()["total"] == jobs

"""Local model preview, real queue execution and content-bound manual/worker handoffs."""

import json
import shlex
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect

from finahinking.p8_2.data_sources import FixtureMarketDataSource
from finahinking.research.contracts import stable_digest
from finahinking.research.method_handoff import MethodHandoffPackage
from tests.p7_5.test_phase4_browser import browser, http_app, observed_page
from tests.research.test_method_handoff import artifact_for, response_for
from tests.research.test_phase18_api import app_http, post

__all__ = ["app_http", "browser", "http_app", "observed_page"]

PAGE = "/research/model-workbench"
API = "/api/research/model-workbench"


def submit_preview(page):
    button = page.get_by_role("button", name="提交已核对的研究")
    expect(button).to_be_disabled()
    page.locator("[data-mw-submit]").get_by_role("checkbox").check()
    expect(button).to_be_enabled()
    button.click()
    expect(page.locator("[data-mw-job]")).to_be_visible(timeout=30000)
    return parse_qs(urlsplit(page.url).query)["job"][0]


def freeze(page, purpose="research"):
    page.get_by_label("交接用途").select_option(purpose)
    page.get_by_role("button", name="冻结交接包").click()
    expect(page.locator("[data-mw-packet-heading]")).to_be_focused()
    return MethodHandoffPackage.from_dict(json.loads(page.locator("[data-mw-packet]").input_value()))


def completed(page):
    expect(page.locator("[data-mw-job]").get_by_text("已完成", exact=True)).to_be_visible(timeout=45000)
    expect(page.get_by_role("link", name="打开研究报告")).to_be_visible()


@pytest.mark.parametrize("http_app", [False], indirect=True)
def test_local_sample_preview_manual_artifact_and_readonly_mobile_restore(http_app, observed_page):
    app, base = http_app
    page, artifacts, _ = observed_page
    posts = []
    page.on("request", lambda r: posts.append(r.url) if r.method == "POST" else None)
    page.set_viewport_size({"width": 375, "height": 812})
    page.goto(base + "/settings/providers")
    page.get_by_role("link", name="在研究台查看各角色的模型和发送材料").click()
    expect(page.get_by_role("heading", level=1, name="模型与 Codex 研究台")).to_be_visible()
    expect(page.get_by_label("行情快照")).to_have_value("sample")
    expect(page.get_by_label("研究标的")).to_have_value("SYNTH")
    expect(page.get_by_label("研究截止日（UTC）")).to_have_value("2026-10-01")
    expect(page.get_by_role("button", name="冻结交接包")).to_be_disabled()
    assert not posts and app.research_service.list_jobs()["total"] == 0
    preview_button = page.get_by_role("button", name="预览研究材料")
    preview_button.focus()
    assert preview_button.evaluate("e => getComputedStyle(e).outlineStyle") == "solid"
    page.keyboard.press("Enter")
    preview = page.locator("[data-mw-preview]")
    expect(preview).to_be_visible()
    expect(preview).to_contain_text("0 次")
    expect(page.locator("#mw-preview-heading")).to_be_focused()
    page.get_by_label("研究问题").fill("核对研究截止时已观察到的量价变化。")
    expect(preview).to_be_hidden()
    assert app.research_service.list_jobs()["total"] == 0
    preview_button.click()
    job_id = submit_preview(page)
    completed(page)
    receipt = page.locator("[data-mw-job]")
    expect(receipt).to_contain_text("本地离线研究没有外部模型调用")
    expect(receipt).to_contain_text("已授权的预览指纹")
    with page.expect_download() as download:
        page.get_by_role("link", name="下载研究回执 JSON").click()
    saved = json.loads(download.value.path().read_bytes())
    assert saved["job_id"] == job_id and saved["dispatch_status"] == "OFFLINE"
    package = freeze(page)
    expect(page.locator("[data-mw-worker-state]")).to_contain_text("仅限离线研究")
    expect(page.get_by_role("button", name="授权一次领取")).to_be_disabled()
    with page.expect_download() as download:
        page.get_by_role("button", name="下载交接包").click()
    assert download.value.path().read_bytes() == package._json.encode("utf-8")
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=base)
    page.get_by_role("button", name="复制交接包").click()
    expect(page.locator("[data-mw-handoff-status]")).to_contain_text("交接包已复制")
    assert page.evaluate("navigator.clipboard.readText()") == package._json
    result = response_for(package)
    result["output_artifacts"] = [artifact_for(package)]
    output = result["output_artifacts"][0]
    output["payload"]["validation_spec"]["tolerance"] = 1.0
    output["content_digest"] = stable_digest(output["payload"])
    manual = page.get_by_label("类型化结果 JSON")
    manual.fill(json.dumps({**result, "package_digest": "0" * 64}))
    save_button = page.get_by_role("button", name="校验并保存回传")
    before = len(posts)
    save_button.click()
    expect(page.locator("[data-mw-handoff-status]")).to_contain_text("包指纹与所选交接包不同")
    assert len(posts) == before
    manual.fill(json.dumps(result))
    save_button.click()
    packet = page.locator("[data-mw-packet-facts]")
    expect(packet).to_contain_text("类型与证据绑定已核验")
    expect(packet).to_contain_text("研究计划")
    before = len(posts)
    page.reload()
    expect(packet).to_contain_text("手动交接")
    expect(packet).to_contain_text(package.package_digest)
    packet.get_by_text("阅读研究计划", exact=True).click()
    expect(packet.get_by_text(output["payload"]["hypotheses"][0], exact=True)).to_be_visible()
    expect(save_button).to_be_disabled()
    assert len(posts) == before and app.research_service.list_jobs()["total"] == 1
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.screenshot(path=str(artifacts / "manual-handoff-mobile.png"), full_page=True)


def test_saved_snapshot_role_mapping_online_receipt_and_scoped_worker(app_http, observed_page, monkeypatch):
    app, http, wire = app_http
    base = http.__closure__[0].cell_contents
    monkeypatch.setenv("FINAHINK_TEST_KEY", "controlled-local-test-value")
    original = app._provider_configs.get("desk")
    app._provider_configs.remove("desk")
    app._provider_configs.save(replace(original, role_models={"technical": "desk-technical", "news": "desk-news"}))
    service = app.research_service
    snapshot = FixtureMarketDataSource(points=19).snapshot("DESK")
    ref = service.snapshots.save(snapshot)
    page, artifacts, _ = observed_page
    page.goto(base + PAGE + "?snapshot_ref=" + ref)
    expect(page.get_by_label("行情快照")).to_have_value(ref)
    expect(page.get_by_label("研究标的")).to_have_value("DESK")
    page.get_by_label("模型配置").select_option("desk")
    expect(page.locator('[data-mw-role="technical"]')).to_have_text("desk-technical")
    page.get_by_text("补充研究摘要", exact=True).click()
    page.get_by_role("button", name="添加一条材料").click()
    page.get_by_label("材料所属角色").select_option("news")
    page.get_by_label("来源名称").fill("受控测试公告")
    stamp = snapshot.observations[-2].timestamp.strftime("%Y-%m-%dT%H:%M")
    page.get_by_label("观测时间（UTC）", exact=True).fill(stamp)
    page.get_by_label("可获得时间（UTC）", exact=True).fill(stamp)
    page.get_by_label("摘要内容").fill("这条公告记录了一个待核验的交付日期。")
    page.get_by_label("我有权向所选研究角色提供这条摘要。").check()
    page.get_by_role("button", name="预览研究材料").click()
    preview = page.locator("[data-mw-preview]")
    expect(preview).to_contain_text("3 次")
    news = preview.locator("details").filter(has=page.get_by_text("新闻分析师 · 有材料 · desk-news", exact=True))
    news.locator(":scope > summary").click()
    expect(news.get_by_role("heading", name="受控测试公告")).to_be_visible()
    expect(news.get_by_text("这条公告记录了一个待核验的交付日期。", exact=True)).to_be_visible()
    assert not (service.root / "peer-wire.jsonl").exists() and not wire.calls
    job_id = submit_preview(page)
    completed(page)
    evidence = http(API + "/jobs/" + job_id)[1]
    assert evidence["expected_calls"] == evidence["dispatched_calls"] == 3
    assert evidence["protocol_status"] == "ACCEPTED" and evidence["semantic_status"] == "REVIEW_REQUIRED"
    receipt = page.locator("[data-mw-job]")
    expect(receipt).to_contain_text("待人工核对")
    calls = receipt.get_by_role("table", name="逐次调用回执")
    expect(calls).to_contain_text("desk-technical")
    expect(calls).to_contain_text("输入 120 / 输出 40 / 合计 160")
    technical = receipt.locator('details[data-mw-disclosure="role:technical"]')
    technical.locator(":scope > summary").click()
    refresh = page.get_by_role("button", name="刷新任务记录")
    refresh.focus()
    page.keyboard.press("Enter")
    expect(page.locator("[data-mw-job-status]")).to_contain_text("当前研究已完成")
    expect(refresh).to_be_focused()
    assert technical.evaluate("element => element.open")
    with page.expect_download() as downloadable:
        page.get_by_role("link", name="下载研究回执 JSON").click()
    assert json.loads(downloadable.value.path().read_bytes()) == evidence
    package = freeze(page)
    authorize = page.get_by_role("button", name="授权一次领取")
    expect(authorize).to_be_disabled()
    page.locator("[data-mw-worker]").get_by_role("checkbox").check()
    authorize.click()
    command = page.get_by_label("Codex 执行器命令")
    expect(command).to_be_visible()
    args = shlex.split(command.input_value())
    assert args[:7] == ["finathink-codex", "--run", "--url", base, "--job", job_id, "--operation"]
    path = API + "/jobs/" + job_id + "/executions/" + args[7]
    status, claimed = post(app, http, path + "/claim", {})
    assert status == 200 and claimed["package"] == package.to_dict()
    result = response_for(package)
    result["output_artifacts"] = [artifact_for(package)]
    status, terminal = post(app, http, path + "/complete", {"lease_token": claimed["lease_token"], "response": result})
    assert status == 200 and terminal["status"] == "COMPLETED"
    page.get_by_role("button", name="刷新任务记录").click()
    packet = page.locator("[data-mw-packet-facts]")
    expect(packet).to_contain_text("类型与证据绑定已核验")
    expect(page.locator("[data-mw-worker-state]")).to_contain_text("未知")
    assert claimed["lease_token"] not in page.content()
    page.reload()
    expect(packet).to_contain_text(package.package_digest)
    expect(page.get_by_label("类型化结果 JSON")).to_be_disabled()
    page.screenshot(path=str(artifacts / "controlled-worker-receipt.png"), full_page=True)
    second = freeze(page, "backtest")
    assert second.package_digest != package.package_digest
    page.locator("[data-mw-worker]").get_by_role("checkbox").check()
    authorize.click()
    page.get_by_role("button", name="取消执行器任务").click()
    expect(packet).to_contain_text("已取消")
    page.reload()
    expect(packet).to_contain_text("已取消")
    assert len((service.root / "peer-wire.jsonl").read_text().splitlines()) == 3


def test_blocked_preview_and_cancelled_research_stay_visible(app_http, observed_page, monkeypatch):
    app, http, _ = app_http
    base = http.__closure__[0].cell_contents
    monkeypatch.delenv("FINAHINK_TEST_KEY", raising=False)
    page, _, _ = observed_page
    page.goto(base + PAGE)
    page.get_by_label("模型配置").select_option("desk")
    page.get_by_role("button", name="预览研究材料").click()
    expect(page.locator("[data-mw-preview]")).to_contain_text("当前进程无法使用")
    expect(page.get_by_role("button", name="提交已核对的研究")).to_be_disabled()
    assert app.research_service.list_jobs()["total"] == 0
    monkeypatch.setenv("FINAHINK_TEST_KEY", "controlled-local-test-value")
    page.get_by_label("研究问题").fill("hold-model-request")
    page.get_by_role("button", name="预览研究材料").click()
    job_id = submit_preview(page)
    page.get_by_role("button", name="取消研究任务").click()
    expect(page.locator("[data-mw-job]")).to_contain_text("已取消", timeout=30000)
    page.reload()
    expect(page.locator("[data-mw-job]")).to_contain_text(job_id)
    expect(page.get_by_role("button", name="取消研究任务")).to_be_disabled()
    assert http(API + "/jobs/" + job_id)[1]["status"] == "cancelled"


@pytest.mark.parametrize("http_app", [False], indirect=True)
def test_no_script_first_entry_links_and_material_names_are_server_rendered(http_app, browser):
    _, base = http_app
    context = browser.new_context(java_script_enabled=False, viewport={"width": 375, "height": 812})
    try:
        page = context.new_page()
        page.goto(base + PAGE)
        expect(page.get_by_role("heading", name="模型与 Codex 研究台")).to_be_visible()
        expect(page.get_by_label("研究标的")).to_have_value("SYNTH")
        expect(page.get_by_role("link", name="设置接口与角色模型")).to_be_visible()
        page.get_by_role("navigation", name="工作台目录").get_by_role("link", name="Codex 交接").click()
        assert urlsplit(page.url).fragment == "mw-codex"
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    finally:
        context.close()

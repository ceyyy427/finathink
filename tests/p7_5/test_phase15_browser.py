"""Installed-contract pages, real spawn computation, and offline evidence."""

import hashlib
import json
import math
from dataclasses import replace

import pytest

from finahinking.data.research_fields import ResearchFieldAttachment
from finahinking.p8_2.data_sources import FixtureMarketDataSource
from finahinking.research.contracts import stable_digest
from tests.p7_5.test_phase4_browser import browser, http_app, observed_page
from tests.research.test_factor_study_application import wait_study

__all__ = ["browser", "http_app", "observed_page"]


@pytest.mark.parametrize("width,height", [(1440, 900), (1920, 1080), (1024, 900), (375, 812), (390, 844)])
def test_asset_factor_layout_keyboard_and_reduced_motion(http_app, observed_page, width, height):
    app, base = http_app
    page, artifacts, diagnostics = observed_page
    page.set_viewport_size({"width": width, "height": height})
    page.emulate_media(reduced_motion="reduce")
    for path, title in (("/library/methods", "研究方法"), ("/library/behavior", "公开行为档案"),
                         ("/library/lessons", "反例与教训"), ("/research/factors/finathink-turnover-freefloat-mean", "因子")):
        page.goto(base + path, wait_until="networkidle")
        assert page.get_by_role("main").get_by_role("heading", level=1).count() == 1
        assert page.get_by_role("navigation", name="主导航", include_hidden=True).count() == 1
        if width < 760:
            assert page.get_by_role("button", name="展开导航", exact=True).is_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2"), path
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.matches(':focus-visible') && getComputedStyle(document.activeElement).outlineStyle !== 'none'")
        assert page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
        filename = path.rsplit("/", 1)[1]
        page.screenshot(path=str(artifacts / f"{filename}-{width}-viewport.png"))
        page.screenshot(path=str(artifacts / f"{filename}-{width}-full.png"), full_page=True)
        diagnostics["metrics"].setdefault("layouts", []).append({"path": path, "width": width, "title": title})
    assert app.research_service.list_jobs()["total"] == 0


def test_asset_search_loading_empty_error_retry_and_long_text(http_app, observed_page):
    app, base = http_app
    page, artifacts, diagnostics = observed_page
    page.goto(base + "/library/methods", wait_until="networkidle")
    root = page.locator("[data-asset-workspace]")
    initial = root.locator("[data-asset-results]").inner_text()
    page.evaluate("""() => { window.assetStates=[]; const root=document.querySelector('[data-asset-workspace]'); new MutationObserver(() => window.assetStates.push(root.dataset.assetState)).observe(root,{attributes:true,attributeFilter:['data-asset-state']}); }""")

    def fail(route):
        page.screenshot(path=str(artifacts / "asset-loading.png"))
        route.fulfill(status=200, content_type="application/json", body='{"schema_version":"invalid"}')

    page.route("**/api/research/assets?*", fail)
    query = "量化算法 / " + "很长的研究条件" * 15
    root.locator('[name="q"]').fill(query)
    root.locator('button[type="submit"]').click()
    page.wait_for_selector('[data-asset-state="error"]')
    assert root.locator("[data-asset-results]").inner_text() == initial
    assert root.locator("[name=q]").input_value() == query
    page.screenshot(path=str(artifacts / "asset-error.png"), full_page=True)
    page.unroute("**/api/research/assets?*", fail)
    root.locator("[data-asset-retry]").click()
    page.wait_for_selector('[data-asset-state="empty"]')
    assert "没有匹配" in root.locator("[data-asset-results]").inner_text()
    root.locator('[name="q"]').fill("")
    root.locator('button[type="submit"]').click()
    page.wait_for_selector('[data-asset-state="ready"]')
    assert root.locator(".asset-entry").first.evaluate("el => Boolean(el.querySelector('h2').compareDocumentPosition(el.querySelector('.asset-state')) & Node.DOCUMENT_POSITION_FOLLOWING)")
    assert "loading" in page.evaluate("window.assetStates")
    assert app.research_service.list_jobs()["total"] == 0
    diagnostics["metrics"]["asset_states"] = page.evaluate("window.assetStates")


@pytest.mark.parametrize("observed_page", [{"java_script_enabled": False}], indirect=True)
def test_asset_search_detail_and_factor_evidence_read_without_javascript(http_app, observed_page):
    app, base = http_app
    page, artifacts, _ = observed_page
    page.goto(base + "/library/methods")
    page.locator('[data-asset-results] h2 a').first.click()
    assert page.locator(".asset-document").is_visible()
    page.goto(base + "/research/factors/finathink-turnover-freefloat-mean")
    assert page.locator("[data-factor-section]").count() == 7
    assert page.locator("[data-stage-id]").count() == 9
    page.screenshot(path=str(artifacts / "no-javascript-evidence.png"), full_page=True)
    assert app.research_service.list_jobs()["total"] == 0


def method_response(package, question):
    expression = "rolling_mean(turnover_rate,5)"
    identifier = hashlib.sha256(f"{question}|{expression}|turnover_rate".encode()).hexdigest()[:12]
    payload = {"proposal_id": "factor-proposal-" + identifier, "expression": expression, "source_hypothesis": question,
        "required_fields": ["turnover_rate"], "constraints": {"max_window": 252, "point_in_time": True, "paper_only": True}, "paper_only": True}
    envelope = package["envelope"]
    return {"schema_version": "method-result.v1", "package_digest": package["package_digest"],
        "result": {"schema_version": "codex-task.v1", "input_digest": envelope["input_digest"], "task_id": envelope["task_id"],
            "role": envelope["role"], "paper_only": True, "status": "READY", "evidence_refs": list(package["context"]["evidence_index"])},
        "output_artifacts": [{"artifact_type": "FactorProposal", "schema_version": "factor-proposal.v1", "input_digest": envelope["input_digest"],
            "content_digest": stable_digest(payload), "payload": payload}]}


@pytest.mark.parametrize("tradability", [False, None], ids=["declared", "unknown"])
def test_methods_question_fields_typed_return_real_worker_report_and_readonly_offline(http_app, observed_page, tradability):
    app, base = http_app
    page, artifacts, diagnostics = observed_page
    service = app.research_service
    original = FixtureMarketDataSource(points=240).snapshot("CN:AAA")
    snapshot = replace(original, observations=tuple(replace(row, amount=row.close * row.volume, suspended=tradability, industry="fixture-industry") for row in original.observations))
    reference = service.snapshots.save(snapshot)
    page.goto(base + "/library/methods")
    page.locator('[data-asset-results] a').filter(has_text="带入研究问题").first.click()
    form = page.locator("[data-run-submit]")
    assert "提出可验证" in form.locator('[name="question"]').input_value()
    form.locator('[name="snapshot_ref"]').select_option(reference)
    form.locator('[name="instrument"]').fill("CN:AAA")
    form.locator('[name="as_of"]').fill("2026-10-01")
    question = "研究低换手相对低波动的增量信息"
    form.locator('[name="question"]').fill(question)
    form.locator('button[type="submit"]').click()
    page.wait_for_selector('[data-job-status="completed"]', timeout=30000)
    handoff = page.locator("[data-method-handoff]")
    handoff.locator("[data-method-export]").click()
    page.wait_for_function("() => document.querySelector('[data-method-package]').value.length > 0")
    package = json.loads(handoff.locator("[data-method-package]").input_value())
    handoff.locator('[name="response"]').fill(json.dumps(method_response(package, question)))
    handoff.locator("[data-method-accept]").click()
    page.wait_for_function("() => document.querySelector('[name=artifact_ref]').options.length > 0")
    fields = ResearchFieldAttachment(snapshot.fingerprint, "CN", {"turnover_rate": "ratio_free_float", "volume": "shares", "amount": "CNY"},
        [{"instrument": row.instrument, "timestamp": row.timestamp.isoformat(), "available_at": row.available_at.isoformat(),
          "turnover_rate": .02 + math.sin(number / 3) / 100} for number, row in enumerate(snapshot.observations)])
    handoff.get_by_text("绑定可选研究字段附件", exact=True).click()
    handoff.locator('[name="attachment"]').set_input_files({"name": "fields.json", "mimeType": "application/json", "buffer": json.dumps(fields.to_dict()).encode()})
    handoff.locator("[data-method-fields-save]").click()
    page.wait_for_function("() => document.querySelector('[data-method-status]').textContent.includes('已保存原快照')")
    handoff.locator("[data-method-study-create]").click()
    link = handoff.locator("[data-method-study-link]")
    link.wait_for(state="visible")
    study_id = link.get_attribute("href").split("study_id=", 1)[1]
    result = wait_study(service, study_id, "validation")
    validation = result["validation"]["validation_run"]
    diagnostics["metrics"]["stages"] = [{key: step[key] for key in ("stage_id", "status", "reason")} for step in validation["steps"]]
    assert validation["status"] in {"COMPLETE", "COMPLETE_WITH_BLOCKS"}
    assert next(step for step in validation["steps"] if step["stage_id"] == "portfolio_backtest")["status"] == ("COMPLETE" if tradability is False else "BLOCKED_DATA")
    link.click()
    page.wait_for_function("() => document.querySelectorAll('[data-study-stages] li').length === 9")
    page.screenshot(path=str(artifacts / "actual-nine-stage-run.png"), full_page=True)
    fp = result["validation"]["validation_run"]["fingerprint"]
    job = service.get_job(result["validation_job"]["job_id"])
    report = page.request.get(base + job["report_url"])
    assert report.ok and fp in report.text() and fields.fingerprint in report.text()
    page.goto(base + f'/research/factors/finathink-turnover-freefloat-mean?run_ref={job["run_id"]}')
    assert fp in page.content()
    assert page.locator('[data-factor-section="validation"] [data-stage-id]').count() == 9
    for width, height in ((1440, 900), (1920, 1080), (1024, 900), (375, 812), (390, 844)):
        page.set_viewport_size({"width": width, "height": height})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2")
        page.screenshot(path=str(artifacts / f"actual-factor-{width}-viewport.png"))
    page.locator('[data-factor-monitor-form] [name="as_of"]').fill("2026-10-27")
    page.locator('[data-factor-monitor-form] button').click()
    page.wait_for_function("() => document.querySelector('[data-factor-action-status]').textContent.includes('观察状态：OBSERVE')")
    assert service.learning.list_reviews() == []  # Validation is not a settled paper outcome.
    offline = artifacts / "frozen-factor-report.html"
    offline.write_bytes(report.body())
    requests = []
    page.on("request", lambda request: requests.append(request.url) if request.url.startswith(("http:", "https:")) else None)
    page.goto(offline.resolve().as_uri(), wait_until="networkidle")
    if tradability is False:
        assert page.locator("[data-report-equity]").is_visible()
    else:
        assert page.locator("[data-report-equity]").count() == 0
        assert "未生成收益曲线" in page.locator("#results").inner_text()
    assert page.locator("[data-validation-stage]").count() == 9
    for width, height in ((1440, 900), (1920, 1080), (1024, 900), (375, 812), (390, 844)):
        page.set_viewport_size({"width": width, "height": height})
        page.evaluate("window.scrollTo(0,0)")
        assert page.evaluate("scrollY === 0")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2")
        page.screenshot(path=str(artifacts / f"offline-report-{width}-viewport.png"))
        page.screenshot(path=str(artifacts / f"offline-report-{width}-full.png"), full_page=True)
        page.locator("#results").screenshot(path=str(artifacts / f"actual-results-{width}.png"))
        assert page.locator('.report-meta dt').evaluate_all("elements => elements.every(el => el.getBoundingClientRect().height <= parseFloat(getComputedStyle(el).lineHeight) + 1)")
        if tradability is False:
            assert page.locator('.report-chart-scale span, .report-chart-dates span').count() == 4
            assert page.locator('.report-chart-scale span, .report-chart-dates span').evaluate_all("elements => elements.every(el => parseFloat(getComputedStyle(el).fontSize) >= 12)")
            assert page.locator('[data-report-equity] svg').bounding_box()['height'] >= 200
    assert requests == []
    assert service.list_jobs()["total"] == 2
    diagnostics["metrics"]["validation_fingerprint"] = fp
    diagnostics["metrics"]["automatic_codex_driver"] = "NOT_VERIFIED"

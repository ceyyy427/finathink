import json
from dataclasses import replace
from html.parser import HTMLParser
from urllib.parse import urlencode

import pandas as pd
import pytest

from finahinking.local_app import LocalAppConfig, LocalApplication
from finahinking.p8_2.data_sources import FixtureMarketDataSource
from finahinking.research.atlas_labs import EVIDENCE, FACTORS, SOURCE, SUBMIT
from tests.research.test_factor_study_application import wait_study


class Elements(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.tags = []
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def with_attr(self, name):
        return [attrs for _, attrs in self.tags if name in attrs]


@pytest.fixture
def app(tmp_path):
    application = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "atlas.sqlite")))
    yield application
    application.close()


def test_reading_the_default_example_keeps_the_scoped_store_empty(app):
    status, _, page = app.route("GET", FACTORS)
    assert status == 200 and "静态示例" in page and "样本外检验" in page
    elements = Elements(page)
    points = json.loads(elements.with_attr("data-atlas-chart")[0]["data-points"])
    assert len(points) == 60
    assert len(elements.with_attr("data-atlas-plot")) == 2  # wide and narrow renderings share the rows
    status, _, evidence = app.route("GET", EVIDENCE)
    assert status == 200
    nodes = {item["data-atlas-node"] for item in Elements(evidence).with_attr("data-atlas-node")}
    assert nodes == {"snapshot", "close", "lag", "factor", "hypothesis"}
    assert not app.research_service.snapshots.list_snapshots()
    assert app.research_service.list_jobs()["total"] == 0


def test_chart_and_lineage_bind_the_same_captured_rows_without_exposing_the_holdout(app):
    original = FixtureMarketDataSource(points=120).snapshot("MY-ASSET")
    rows = tuple(replace(row, available_at=row.timestamp + pd.Timedelta(minutes=5)) for row in original.observations)
    snapshot = replace(original, observations=rows, as_of=rows[-1].available_at, mode="CAPTURED",
                       provenance={"units": {"price_currency": "USD", "volume_unit": "shares"}})
    dataset = app.research_service.snapshots.save(snapshot) + "~MY-ASSET"
    values = {"dataset": dataset, "short": "3", "long": "15", "fee": "12.5"}
    status, _, page = app.route("GET", FACTORS + "?" + urlencode(values))
    assert status == 200 and "已保存数据" in page
    points = json.loads(Elements(page).with_attr("data-atlas-chart")[0]["data-points"])
    assert len(points) == 96 - 15
    for i, (stamp, short, long) in enumerate(points, start=15):
        assert stamp == rows[i].timestamp.isoformat()
        assert short == pytest.approx(rows[i].close / rows[i - 3].close - 1)
        assert long == pytest.approx(rows[i].close / rows[i - 15].close - 1)

    def record(node, index):
        return app.route("GET", SOURCE + "?" + urlencode({**values, "node": node, "at": rows[index].timestamp.isoformat()}))

    current = record("close", 53)[2]
    assert current["snapshot_ref"] == snapshot.fingerprint and current["data_mode"] == "CAPTURED"
    assert current["records"] == [{"observation_ref": rows[53].fingerprint, "observation": rows[53].to_dict()}]
    assert current["records"][0]["observation"]["available_at"] != current["selected_at"]
    assert record("lag", 53)[2]["records"][0]["observation_ref"] == rows[50].fingerprint
    factor = record("factor", 53)[2]
    assert factor["formula"] == "return(close,3)" and factor["value"] == points[53 - 15][1]
    assert [r["observation_ref"] for r in factor["records"]] == [rows[53].fingerprint, rows[50].fingerprint]
    hypothesis = record("hypothesis", 53)[2]
    assert hypothesis["evaluation_status"] == "NOT_RUN" and hypothesis["fee_bps"] == 12.5
    assert record("lag", 0)[2]["records"] == []
    for node in ("snapshot", "close", "lag", "factor", "hypothesis"):
        assert record(node, 96)[0] == 422
    assert not app.research_service.factor_studies.list()


def test_saved_parameters_reach_canonical_validation_only_once(app):
    page = Elements(app.route("GET", FACTORS)[2])
    dataset = next(attr["value"] for tag, attr in page.tags if tag == "option" and "selected" in attr)
    values = {"dataset": dataset, "short": "4", "long": "20", "fee": "7.5"}
    assert app.route("POST", SUBMIT, body=values)[0] == 403
    values["_csrf"] = app.csrf_token
    assert app.route("POST", SUBMIT, body={**values, "long": "4"})[0] == 422
    assert app.route("POST", SUBMIT, body={**values, "fee": "NaN"})[0] == 422
    assert not app.research_service.snapshots.list_snapshots()
    app.research_service.start()
    status, _, result = app.route("POST", SUBMIT, body=values)
    assert status == 202
    assert app.route("POST", SUBMIT, body=values)[2]["study_id"] == result["study_id"]
    study = wait_study(app.research_service, result["study_id"], "validation")
    assert study["state"] == "VALIDATED" and study["test_job_id"] is None
    assert study["spec"]["windows"] == [4, 20]
    assert study["spec"]["cost"]["fee_bps"] == 7.5 and study["spec"]["cost"]["slippage_bps"] == 0
    assert study["spec"]["snapshot_ref"] == dataset.split("~")[0]
    assert all(item["phase"] == "VALIDATION" for item in study["validation"]["summaries"])
    assert app.research_service.list_jobs()["total"] == 1
    assert app.route("GET", result["study_url"])[0] == 200


def test_incomplete_or_substituted_snapshot_is_never_replaced_by_an_example(app):
    snapshot = FixtureMarketDataSource(points=100).snapshot("PRIVATE")
    rows = (replace(snapshot.observations[0], available_at=None), *snapshot.observations[1:])
    incomplete = replace(snapshot, observations=rows, mode="CAPTURED")
    key = app.research_service.snapshots.save(incomplete)
    for dataset in (key + "~PRIVATE", key + "~OTHER", "a" * 64 + "~PRIVATE"):
        query = urlencode({"dataset": dataset})
        page = app.route("GET", FACTORS + "?" + query)[2]
        assert not Elements(page).with_attr("data-atlas-chart") and 'role="alert"' in page
        assert app.route("GET", SOURCE + "?" + query)[0] in {404, 422}
    assert app.research_service.list_jobs()["total"] == 0


def test_browser_can_trace_select_zoom_recompute_and_open_the_durable_study(app):
    import os
    import threading

    playwright = pytest.importorskip("playwright.sync_api")
    from finahinking.local_app import create_server

    server = create_server(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with playwright.sync_playwright() as api:
            try:
                browser = api.chromium.launch()
            except playwright.Error:
                if os.environ.get("FINATHINK_REQUIRE_BROWSER") == "1":
                    raise
                pytest.skip("Chromium is unavailable")
            try:
                page = browser.new_page(viewport={"width": 1300, "height": 860})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + EVIDENCE)
                page.wait_for_selector('[data-atlas-enhanced="true"]')
                close = page.locator('.al-wide a[data-atlas-node="close"]')
                close.focus()
                close.press("ArrowDown")
                assert page.locator("[data-atlas-inspector] h2").inner_text() == "滞后收盘价"
                assert "node=lag" in page.url
                original = page.locator("[data-atlas-inspector] .al-source-link").get_attribute("href")
                assert page.request.get(base + original).json()["kind"] == "lag"
                original_width = page.locator("[data-atlas-stage]").bounding_box()["width"]
                page.get_by_role("button", name="放大证据关系图").click()
                assert page.locator("[data-atlas-stage]").bounding_box()["width"] > original_width
                page.goto(base + FACTORS)
                page.wait_for_selector('[data-atlas-enhanced="true"]')
                plot = page.locator(".al-plot.al-wide")
                plot.focus()
                plot.press("End")
                assert plot.get_attribute("aria-valuenow") == plot.get_attribute("aria-valuemax")
                url = page.locator("[data-atlas-evidence-link]").get_attribute("href")
                assert page.request.get(base + url.replace(EVIDENCE, SOURCE, 1)).json()["kind"] == "factor"
                page.locator('[name="short"]').fill("3")
                page.locator('[name="fee"]').fill("8.5")
                page.wait_for_selector('[data-atlas-chart][data-short="3"]')
                assert "fee=8.5" in page.url
                dataset_select = page.locator('select[name="dataset"]')
                dataset_select.select_option(index=1)
                page.locator("[data-atlas-source-note]").filter(has_text="AAA").wait_for()
                page.get_by_role("button", name="运行实验").click()
                link = page.get_by_role("link", name="查看验证记录")
                link.wait_for()
                study_path = link.get_attribute("href")
                study_id = study_path.split("study_id=")[1]
                study = wait_study(app.research_service, study_id, "validation")
                assert study["state"] == "VALIDATED" and study["spec"]["windows"] == [3, 20]
                assert study["spec"]["cost"]["fee_bps"] == 8.5 and study["spec"]["instrument"] == "AAA"
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.locator(".al-plot.al-narrow").is_visible()
                assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
                page.goto(base + EVIDENCE)
                page.locator('.al-narrow a[data-atlas-node="factor"]').click()
                assert page.locator("[data-atlas-inspector] h2").inner_text() == "动量因子"
                assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
                assert errors == []

                native = browser.new_context(java_script_enabled=False, viewport={"width": 390, "height": 844})
                static_page = native.new_page()
                static_page.goto(base + EVIDENCE)
                static_page.locator('.al-narrow a[data-atlas-node="lag"]').click()
                assert static_page.locator("[data-atlas-inspector] h2").inner_text() == "滞后收盘价"
                static_page.goto(base + FACTORS)
                static_page.locator('select[name="dataset"]').select_option(index=1)
                static_page.locator('[name="short"]').fill("3")
                static_page.locator('[name="fee"]').fill("8.5")
                static_page.get_by_role("button", name="更新图表").click()
                assert static_page.locator("[data-atlas-chart]").get_attribute("data-short") == "3"
                assert "_csrf" not in static_page.url
                static_page.get_by_role("button", name="运行实验").click()
                assert static_page.get_by_role("link", name="打开验证记录").get_attribute("href") == study_path
                assert app.research_service.list_jobs()["total"] == 1
                native.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

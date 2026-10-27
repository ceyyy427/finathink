"""Seven evidence sections, exact versions and genuine nine-stage states."""

import importlib
import importlib.util
from dataclasses import replace

import pytest

from finahinking.local_app import LocalAppConfig, LocalApplication
from finahinking.research.contracts import ResearchRunResult, ResearchRunState, ResearchState
from finahinking.research.factor_validation import (
    FactorValidationGateway,
    factor_validation_policy,
    run_factor_validation,
)
from finahinking.research.reports import ReportBundleWriter
from tests.research.test_factor_validation_pipeline import fixture_panel, selected


def detail_api():
    name = "finahinking.research.factor_detail_page"
    assert importlib.util.find_spec(name) is not None, "factor detail workspace is missing"
    return importlib.import_module(name)


def saved_run(app, *, fields=True, version=None, window=5, run_id="detail-run"):
    from finahinking.research.factor_recipes import pilot_factor_specs

    factor = next(spec for spec in pilot_factor_specs(window) if spec.factor_id == selected().factor_id)
    factor = factor if version is None else replace(factor, version=version)
    validation = run_factor_validation(factor, fixture_panel(fields=fields), factor_validation_policy(),
                                       gateway=FactorValidationGateway()).to_dict()
    result = ResearchRunResult(ResearchRunState(run_id, ResearchState.LEARNING_RECORDED, as_of="2024-06-28"))
    manifest = ReportBundleWriter().write(result, app.research_service.runs.report_root,
        quant_analytics={"publication_scope": {"kind": "PRE_TEST_ONLY"}, "factor_study": {"study_id": "detail-study", "charter": {"policy_fingerprint": "a" * 64,
            "method_binding": {"methods_digest": "b" * 64, "method_manifest_digest": "c" * 64}}, "validation_run": validation}})
    app.research_service.runs.record_result(result, manifest)
    return validation


def test_definition_navigation_never_borrows_another_window_and_run_navigation_is_exact(tmp_path):
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "app.sqlite"), offline=True))
    try:
        eight = saved_run(app, window=8, run_id="window-eight")
        path = f'/api/research/factors/{selected().factor_id}'
        default = app.route("GET", path)[2]
        assert default["factor_spec"]["window"] == 5
        assert default["selected_run"] is None
        assert all(row["status"] == "NOT_RUN" for row in default["track"])
        assert default["evidence"]["local_runs"][0]["factor_spec"]["window"] == 8
        assert default["comparison_runs"][0]["run_ref"] == "window-eight"
        assert 'href="?run_ref=window-eight"' in app.route("GET", path.replace("/api", "", 1))[2]
        exact = app.route("GET", path + "?run_ref=window-eight")[2]
        assert exact["factor_spec"]["window"] == 8
        assert exact["selected_run"]["definition_matches"] is True
        assert exact["selected_run"]["evaluation_fingerprint"] == eight["fingerprint"]
        for query in ("window=5", "version=0.9.0", "version=1.0.0&window=5"):
            assert app.route("GET", path + f"?run_ref=window-eight&{query}")[0] == 422
        saved_run(app, version="0.9.0", run_id="old-version")
        historical = app.route("GET", path + "?run_ref=old-version")[2]
        assert historical["factor_spec"]["version"] == "0.9.0"
        assert historical["implementation_available"] is False
        assert app.route("GET", path)[2]["selected_run"] is None
        five = saved_run(app, run_id="current-five")
        saved_run(app, window=8, run_id="newer-eight")
        latest_matching = app.route("GET", path)[2]
        assert latest_matching["selected_run"]["run_ref"] == "current-five"
        assert latest_matching["selected_run"]["evaluation_fingerprint"] == five["fingerprint"]
    finally:
        app.close()


def test_source_only_factor_has_seven_sections_and_no_local_endorsement(tmp_path):
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "app.sqlite"), offline=True))
    try:
        status, _, page = app.route("GET", "/research/factors/dshq-f-score")
        assert status == 200 and 'class="app-shell"' in page
        for section in ("source", "definition", "formula", "data", "validation", "lifecycle", "lessons"):
            assert f'data-factor-section="{section}"' in page
        assert "0.49" in page and "来源声明" in page
        assert 'data-factor-lifecycle="RESEARCH_APPROVED"' not in page
        assert "尚无本地运行" in page
        assert app.research_service.list_jobs()["total"] == 0
    finally:
        app.close()


@pytest.mark.parametrize("fields", [True, False])
def test_nine_steps_and_metrics_come_from_the_exact_verified_run(tmp_path, fields):
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "app.sqlite"), offline=True))
    try:
        validation = saved_run(app, fields=fields)
        service = detail_api()
        data = service.factor_detail(app.research_service, selected().factor_id, version="1.0.0", window=5, run_ref="detail-run")
        assert data["selected_run"]["evaluation_fingerprint"] == validation["fingerprint"]
        assert [step["status"] for step in data["track"]] == [step["status"] for step in validation["steps"]]
        assert len(data["track"]) == 9
        status, _, page = app.route("GET", f"/research/factors/{selected().factor_id}?version=1.0.0&window=5&run_ref=detail-run")
        assert status == 200 and validation["fingerprint"] in page
        assert "FORMULA_FIXTURE" in page
        assert "b" * 64 in page
        if not fields:
            assert "BLOCKED_DATA" in page and "NOT_RUN" in page
    finally:
        app.close()


def test_explicit_unknown_or_historical_version_never_silently_uses_current(tmp_path):
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "app.sqlite"), offline=True))
    try:
        saved_run(app, version="0.9.0")
        data = detail_api().factor_detail(app.research_service, selected().factor_id, version="0.9.0", run_ref="detail-run")
        assert data["factor_spec"]["version"] == "0.9.0"
        assert data["implementation_available"] is False
        assert app.route("GET", f"/research/factors/{selected().factor_id}?version=absent")[0] == 404
        assert app.route("GET", f"/research/factors/{selected().factor_id}?window=10000")[0] == 422
        assert app.route("GET", "/research/factors/unknown")[0] == 404
        assert app.route("POST", f"/api/research/factors/{selected().factor_id}/monitor", body={})[0] == 403
    finally:
        app.close()


def test_corrupt_report_is_shown_as_corrupt_not_unknown_numeric_success(tmp_path):
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "app.sqlite"), offline=True))
    try:
        saved_run(app)
        entry = app.research_service.runs.get_run("detail-run")
        (entry["bundle"] / "complete_report.html").write_text("corrupt fixture", encoding="utf-8")
        data = detail_api().factor_detail(app.research_service, selected().factor_id, run_ref="detail-run")
        assert data["selected_run"]["status"] == "CORRUPT"
        assert all(step["status"] == "NOT_RUN" for step in data["track"])
        assert data["selected_run"]["local_metric"] is None
    finally:
        app.close()


def test_method_handoff_http_adapter_requires_explicit_csrf_and_owner_job(tmp_path):
    from tests.research.test_method_factor_journey import prepared

    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "app.sqlite"), offline=True))
    try:
        parent, reference, _, _, fields = prepared(app.research_service)
        endpoint = f'/api/research/jobs/{parent["job_id"]}/methods'
        assert app.route("POST", endpoint, body={})[0] == 403
        result = app.route("POST", endpoint, body={"_csrf": app.csrf_token, "purposes": ["factor-mining"],
            "market": "CN", "available_fields": ["close", "volume", "amount", "turnover_rate"]})
        assert result[0] == 200 and result[2]["summary"]["automatic_codex_driver"] == "NOT_VERIFIED"
        fetched = app.route("GET", endpoint + "/" + reference)
        assert fetched[0] == 200
        from urllib.parse import quote

        assert app.route("GET", endpoint + "/" + quote(reference, safe=""))[0] == 200
        stored = app.route("POST", f'/api/research/jobs/{parent["job_id"]}/research-fields',
            body={"_csrf": app.csrf_token, "attachment": fields.to_dict()})
        assert stored[0] == 201 and stored[2]["attachment_ref"] == fields.fingerprint
        assert app.research_service.list_jobs()["total"] == 1
    finally:
        app.close()

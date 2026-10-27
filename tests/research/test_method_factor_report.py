"""One frozen numerical run powers offline graphics, text and provenance."""

import copy
import hashlib
import importlib
import importlib.util
import json
from dataclasses import replace

import pytest

from finahinking.research.asset_catalog import ResearchAssetCatalog
from finahinking.research.asset_contracts import MethodRouteRequest
from finahinking.research.contracts import ResearchRunResult, ResearchRunState, ResearchState
from finahinking.research.factor_validation import (
    FactorValidationGateway,
    factor_validation_policy,
    run_factor_validation,
)
from finahinking.research.method_context import load_methods
from finahinking.research.method_router import select_methods
from finahinking.research.reports import ReportBundleWriter, verify_report_bundle
from tests.research.test_factor_validation_pipeline import fixture_panel, selected


def api():
    name = "finahinking.research.report_sections"
    assert importlib.util.find_spec(name) is not None, "blue-white factor report sections are missing"
    return importlib.import_module(name)


@pytest.fixture(scope="module")
def computed():
    return run_factor_validation(selected(), fixture_panel(), factor_validation_policy(), gateway=FactorValidationGateway()).to_dict()


def methods():
    catalog = ResearchAssetCatalog.packaged()
    return load_methods(select_methods(MethodRouteRequest(purposes=("factor-mining",),
        available_fields=("close", "volume", "turnover_rate", "amount")), catalog), catalog)


def test_frozen_metrics_methods_chart_and_meta_have_one_identity(computed):
    loaded = methods()
    sections = api().build_method_factor_sections(computed, loaded, None, None)
    assert sections.metadata["validation_fingerprint"] == computed["fingerprint"]
    assert sections.metadata["methods_digest"] == loaded.fingerprint
    assert sections.metadata["snapshot_ref"] == computed["bindings"]["snapshot_ref"]
    assert sections.metadata["attachment_ref"] == computed["bindings"]["attachment_ref"]
    assert sections.html.count('data-validation-stage="') == 9
    assert 'data-report-equity' in sections.html
    assert "FORMULA_FIXTURE" in sections.html and 'data-report-admission="APPROVED"' not in sections.html
    assert sections.metadata["financial_falsification"] is False
    assert "factor-mining-workflow" in sections.html


def test_blocked_report_retains_failure_and_does_not_invent_a_curve():
    computed = run_factor_validation(selected(), fixture_panel(fields=False), factor_validation_policy(), gateway=FactorValidationGateway()).to_dict()
    sections = api().build_method_factor_sections(computed, None, None, None)
    assert "BLOCKED_DATA" in sections.html and "NOT_RUN" in sections.html
    assert 'data-report-equity' not in sections.html
    assert "未生成收益曲线" in sections.html


def test_source_claims_and_unsafe_markup_are_separate_from_local_values(computed):
    source = {"source_claims": [{"metric_name": "ICIR", "metric_value": 99.5,
        "definition": '<img src=x onerror="alert(1)">', "scope": "source-only", "source_resource_ids": []}],
        "missing_dependencies": [{"code": "MISSING_ORIGINAL_REPORT"}], "conflicts": []}
    sections = api().build_method_factor_sections(computed, None, source, None)
    assert 'data-report-source-claims' in sections.html and "99.5" in sections.html
    assert '<img src=x' not in sections.html and "&lt;img" in sections.html
    assert sections.metadata["validation_fingerprint"] == computed["fingerprint"]
    damaged = copy.deepcopy(computed)
    damaged["steps"][2]["metrics"].clear()
    with pytest.raises(ValueError):
        api().build_method_factor_sections(damaged, None, source, None)


def test_document_is_self_contained_blue_white_and_has_no_automatic_requests(computed):
    sections = api().build_method_factor_sections(computed, methods(), None, None)
    document = api().render_factor_report_document(sections, run_id="offline-report", question="核对因子假设")
    assert '<html lang="zh-CN"' in document and '#f5f8fd' in document and '#2563eb' in document
    assert 'default-src &#x27;none&#x27;' in document or "default-src 'none'" in document
    assert '<script' not in document and '<link' not in document and '<img' not in document
    assert 'href="#' in document and '<details' in document and '<table' in document


def test_writer_uses_new_template_but_replays_never_rewrite_a_published_report(tmp_path, computed):
    result = ResearchRunResult(ResearchRunState("offline-report", ResearchState.LEARNING_RECORDED, as_of="2024-06-28"))
    loaded = methods()
    analysis = {"publication_scope": {"kind": "PRE_TEST_ONLY"}, "factor_study": {
        "validation_run": computed, "charter": {"question": "离线核对", "method_binding": {
            "methods_digest": loaded.fingerprint, "method_manifest_digest": loaded.manifest_fingerprint,
            "loaded_methods": loaded.to_dict()}}}}
    manifest = ReportBundleWriter().write(result, tmp_path, quant_analytics=analysis)
    path = tmp_path / "offline-report" / "complete_report.html"
    original = hashlib.sha256(path.read_bytes()).hexdigest()
    assert manifest.source_snapshot["report_template"] == "blue-white.v1"
    assert computed["fingerprint"] in path.read_text()
    assert verify_report_bundle(path.parent / "manifest.json").ok
    replay = ReportBundleWriter().write(result, tmp_path, quant_analytics=analysis)
    assert replay.files == manifest.files
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original
    changed = replace(result, state=replace(result.state, failure_message="different result"))
    with pytest.raises((FileExistsError, ValueError)):
        ReportBundleWriter().write(changed, tmp_path, quant_analytics=analysis)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original
    stored = json.loads((path.parent / "manifest.json").read_text())
    assert stored["source_snapshot"]["method_factor_snapshot"]["validation_fingerprint"] == computed["fingerprint"]
    binding = stored["source_snapshot"]["factor_study"]["charter"]["method_binding"]
    assert "loaded_methods" not in binding
    assert binding["method_manifest_snapshot"] == loaded.to_dict()["manifest"]

"""Read-only real asset inventory, escaped source material and recoverable gaps."""

import importlib
import importlib.util

import pytest

from finahinking.local_app import LocalAppConfig, LocalApplication
from finahinking.research.asset_catalog import ResearchAssetCatalog
from finahinking.research.asset_contracts import AssetLoadError


def workspace(catalog=None):
    name = "finahinking.research.asset_workspace_page"
    assert importlib.util.find_spec(name) is not None, "research asset workspace is missing"
    return importlib.import_module(name).ResearchAssetWorkspace(catalog or ResearchAssetCatalog.packaged())


def test_actual_methods_behavior_and_lessons_with_bounded_pagination():
    service = workspace()
    methods = service.list(kind="methods", limit=2, offset=2)
    assert methods["total"] == 12 and len(methods["items"]) == 2
    assert methods["limit"] == 2 and methods["offset"] == 2
    assert service.list(kind="behavior")["total"] == 6
    assert service.list(kind="lessons")["total"] == 6
    assert service.list(kind="methods", query="无此方法")['items'] == []
    assert all(item["loaded"] is False for item in methods["items"])
    for values in ({"limit": 101}, {"limit": True}, {"offset": -1}, {"kind": "execute"},
                   {"tags": ["x"] * 17}, {"query": "x" * 201}):
        with pytest.raises(ValueError):
            service.list(**values)


def test_availability_follows_the_entry_title_in_all_library_kinds():
    module = importlib.import_module("finahinking.research.asset_workspace_page")
    for kind in ("methods", "behavior", "lessons"):
        body = module.render_asset_items(workspace().list(kind=kind, limit=1)["items"])
        assert body.index("<h2>") < body.index('class="asset-state"')


def test_detail_returns_version_and_source_but_not_arbitrary_paths():
    service = workspace()
    item = service.list(kind="methods")["items"][0]
    detail = service.detail(item["asset_id"])
    assert detail["version"] and detail["body"]
    assert detail["source_resources"]
    assert all("path" not in ref and "source_path" not in ref for ref in detail["source_resources"])
    for identifier in ("../outside", "unknown-id", "", "%2e%2e", True):
        with pytest.raises((ValueError, KeyError)):
            service.detail(identifier)


def test_empty_and_corrupt_resource_are_explicit_not_ready(monkeypatch):
    catalog = ResearchAssetCatalog.packaged()
    monkeypatch.setattr(catalog, "skills", lambda: ())
    assert workspace(catalog).list(kind="methods")["total"] == 0
    catalog = ResearchAssetCatalog.packaged()
    original = catalog.read_text

    def damaged(reference, **kwargs):
        if reference.startswith("method-"):
            raise AssetLoadError("HASH_MISMATCH", reference)
        return original(reference, **kwargs)

    monkeypatch.setattr(catalog, "read_text", damaged)
    result = workspace(catalog).list(kind="methods")
    assert result["items"] and all(item["availability"] == "CORRUPT" for item in result["items"])
    assert all(item["loaded"] is False for item in result["items"])


def test_library_routes_use_shell_escape_queries_and_do_not_submit_research(tmp_path):
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "app.sqlite"), offline=True))
    try:
        for kind in ("methods", "behavior", "lessons"):
            status, _, body = app.route("GET", f"/library/{kind}")
            assert status == 200 and 'class="app-shell"' in body
            assert 'data-asset-workspace' in body and 'name="q"' in body
            assert f'href="/library/{kind}" aria-current="page"' in body
            assert app.route("POST", f"/library/{kind}")[0] == 405
        query = app.route("GET", "/library/methods?q=%3Cscript%3Ealert(1)%3C/script%3E")[2]
        assert '<script>alert(1)</script>' not in query and '&lt;script&gt;' in query
        data = app.route("GET", "/api/research/assets?kind=methods&limit=2&offset=1")[2]
        assert data["total"] == 12 and len(data["items"]) == 2
        detail = app.route("GET", "/api/research/assets/" + data["items"][0]["asset_id"])
        assert detail[0] == 200 and detail[2]["body"]
        assert app.route("GET", "/api/research/assets/unknown-id")[0] == 404
        assert app.route("GET", "/api/research/assets?limit=1000")[0] == 422
        assert app.route("POST", "/api/research/assets", body={})[0] == 405
        assert app.research_service.list_jobs()["total"] == 0
    finally:
        app.close()


def test_corrupt_installed_catalog_has_a_shared_shell_recovery_page(tmp_path, monkeypatch):
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "app.sqlite"), offline=True))
    try:
        def broken():
            raise AssetLoadError("HASH_MISMATCH", "skills-index")

        monkeypatch.setattr(ResearchAssetCatalog, "packaged", broken)
        status, content_type, body = app.route("GET", "/library/methods")
        assert status == 503 and "html" in content_type
        assert 'class="app-shell"' in body and '资料' in body
        assert app.route("GET", "/api/research/assets")[0] == 503
        status, _, body = app.route("GET", "/research/experiments")
        assert status == 200 and 'data-experiment-workspace' in body
        assert '资料目录校验失败' in body
    finally:
        app.close()


def test_source_records_are_read_only_and_preserve_audit_vs_financial_evidence(tmp_path):
    service = workspace()
    data = service.list(kind="sources")
    assert data["total"] == 7
    assert all(i["loaded"] is False and i["evidence_status"] == "SOURCE_INTEGRITY_VERIFIED"
               for i in data["items"])
    alpha = service.detail("source-alphalens-reloaded")
    assert "Apache-2.0" in alpha["body"] and "财务效果" in alpha["body"]
    assert "alphalens-performance-reference" in {r["resource_id"] for r in alpha["source_resources"]}
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "app.sqlite"), offline=True))
    try:
        status, _, body = app.route("GET", "/library/sources")
        assert status == 200 and "开源来源" in body and 'class="app-shell"' in body
        assert "带入研究问题" not in body
        assert app.route("GET", "/api/research/assets?kind=sources")[2]["total"] == 7
        assert app.route("POST", "/library/sources")[0] == 405
        assert app.research_service.list_jobs()["total"] == 0
    finally:
        app.close()


def test_legacy_catalog_without_sources_still_has_working_method_detail(tmp_path):
    from tests.research.test_research_asset_catalog import _write_catalog

    _write_catalog(tmp_path)
    service = workspace(ResearchAssetCatalog(tmp_path))
    assert service.list(kind="sources")["total"] == 0
    assert service.detail("guide")["body"]


@pytest.mark.parametrize("change", ["module", "references", "phase", "source_id"])
def test_reviewed_but_invalid_source_summary_returns_structured_error(tmp_path, change):
    from tests.research.test_method_context import _change, _root

    root = _root(tmp_path)
    catalog = ResearchAssetCatalog(root)
    summary = catalog.source_intake_summary()
    item = summary["sources"][0]
    if change == "module":
        item["modules"][0]["disposition"] = "AUTORUN"
    elif change == "references":
        item["reference_resource_ids"] = "license"
    elif change == "phase":
        item["modules"][0]["phase"] = True
    else:
        item["source_id"] = "../outside"
    import json

    _change(root, "source-intake-summary", body=json.dumps(summary), reviewed=True)
    with pytest.raises(AssetLoadError) as exc:
        ResearchAssetCatalog(root).source_intake_summary()
    assert exc.value.code == "INVALID_SOURCE_SUMMARY"

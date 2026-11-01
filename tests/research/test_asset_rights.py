"""Per-file rights bind reviewed bytes, provenance and distribution notices."""

import hashlib
import importlib
import importlib.util
import json
from pathlib import Path

import pytest

from finahinking.research.asset_catalog import ResearchAssetCatalog
from finahinking.research.asset_contracts import AssetLoadError
from tests.research.test_research_asset_catalog import _write_catalog


def _api():
    name = "finahinking.research.asset_rights"
    assert importlib.util.find_spec(name), "per-file rights planning is not implemented"
    return importlib.import_module(name)


def _material(ref):
    return {"resource_id": ref["resource_id"], "sha256": ref["sha256"]}


def _review(ref, license_ref, notice_ref=None):
    return {
        "review_id": "rights-" + ref["resource_id"], "resource_id": ref["resource_id"],
        "resource_sha256": ref["sha256"], "license_id": ref["license_id"],
        "license_refs": [_material(license_ref)],
        "notice_refs": [_material(notice_ref)] if notice_ref else [],
        "decision": "DISTRIBUTION_APPROVED", "scope": "FILE_BYTES_ONLY",
        "reviewed_by": "fixture rights reviewer", "reviewed_on": "2026-10-31",
        "rationale": "Specific file and notices examined; no execution or financial approval.",
        "source_url": ref.get("source_url"), "source_path": ref.get("source_path"),
        "source_sha256": ref.get("source_sha256"),
    }


def _v2_catalog(root):
    manifest = _write_catalog(root)
    manifest["schema_version"] = "research-assets.v2"
    records = manifest["resources"]
    original_license = next(r for r in records if r["resource_id"] == "license")
    for rid, path, text, kind in (
        ("apache-license", "licenses/Apache-2.0.txt", "Apache License Version 2.0", "license"),
        ("apache-attribution", "licenses/ATTRIBUTION.txt", "Original authors; unmodified reference.", "notice"),
    ):
        body = text.encode()
        node = root / path
        node.parent.mkdir(parents=True, exist_ok=True)
        node.write_bytes(body)
        records.append({**original_license, "resource_id": rid, "path": path,
                        "sha256": hashlib.sha256(body).hexdigest(), "byte_size": len(body),
                        "kind": kind, "license_id": "Apache-2.0", "license_ref": "apache-license"})
    apache = next(r for r in records if r["resource_id"] == "apache-license")
    notice = next(r for r in records if r["resource_id"] == "apache-attribution")
    records[0].update(license_id="Apache-2.0", license_ref="apache-license")
    reviews = []
    for ref in records:
        if ref["availability"] == "PACKAGED":
            use_apache = ref["license_id"] == "Apache-2.0"
            review = _review(ref, apache if use_apache else original_license,
                             notice if use_apache else None)
            ref["rights_ref"] = review["review_id"]
            reviews.append(review)
    manifest["rights_reviews"] = reviews
    _save(root, manifest)
    return manifest


def _save(root, manifest):
    (root / "provenance/upstream-lock.json").write_text(json.dumps(manifest))


def test_reviewed_apache_asset_and_attribution_are_readable(tmp_path):
    _api()
    _v2_catalog(tmp_path)
    catalog = ResearchAssetCatalog(tmp_path)
    assert "研究" in catalog.read_text("guide")
    assert "Original authors" in catalog.read_text("apache-attribution")


@pytest.mark.parametrize("change", ["missing", "decision", "hash", "source", "notice", "scope"])
def test_unreviewed_or_changed_file_cannot_inherit_license_label(tmp_path, change):
    _api()
    manifest = _v2_catalog(tmp_path)
    ref = manifest["resources"][0]
    review = next(r for r in manifest["rights_reviews"] if r["resource_id"] == "guide")
    if change == "missing":
        ref.pop("rights_ref")
    elif change == "decision":
        review["decision"] = "REFERENCE_ONLY"
    elif change == "hash":
        review["resource_sha256"] = "0" * 64
    elif change == "source":
        review["source_path"] = "different/source.py"
    elif change == "notice":
        review["notice_refs"] = []
    else:
        review["scope"] = "ALL_REPOSITORY_FILES"
    _save(tmp_path, manifest)
    with pytest.raises(AssetLoadError):
        ResearchAssetCatalog(tmp_path).read_text("guide")


@pytest.mark.parametrize("resource_id", ["apache-license", "apache-attribution"])
def test_notice_or_license_changed_even_with_new_manifest_hash_is_blocked(tmp_path, resource_id):
    _api()
    manifest = _v2_catalog(tmp_path)
    ref = next(r for r in manifest["resources"] if r["resource_id"] == resource_id)
    body = b"replaced text"
    (tmp_path / ref["path"]).write_bytes(body)
    ref.update(sha256=hashlib.sha256(body).hexdigest(), byte_size=len(body))
    _save(tmp_path, manifest)
    with pytest.raises(AssetLoadError):
        ResearchAssetCatalog(tmp_path).read_text("guide")


def test_legacy_mit_catalog_preserves_old_contract(tmp_path):
    _api()
    _write_catalog(tmp_path)
    catalog = ResearchAssetCatalog(tmp_path)
    assert "研究" in catalog.read_text("guide")


def test_withdrawn_notice_review_blocks_dependent_resource(tmp_path):
    manifest = _v2_catalog(tmp_path)
    review = next(r for r in manifest["rights_reviews"]
                  if r["resource_id"] == "apache-attribution")
    review["decision"] = "BLOCKED"
    _save(tmp_path, manifest)
    with pytest.raises(AssetLoadError):
        ResearchAssetCatalog(tmp_path).read_text("guide")


def test_apache_resource_cannot_substitute_mit_license(tmp_path):
    manifest = _v2_catalog(tmp_path)
    ref = manifest["resources"][0]
    ref["license_ref"] = "license"
    review = next(r for r in manifest["rights_reviews"] if r["resource_id"] == "guide")
    review["license_refs"] = [_material(next(r for r in manifest["resources"]
                                            if r["resource_id"] == "license"))]
    _save(tmp_path, manifest)
    with pytest.raises(AssetLoadError) as exc:
        ResearchAssetCatalog(tmp_path).read_text("guide")
    assert exc.value.code == "LICENSE_ID_MISMATCH"


def _audit(*, nested=False):
    license_hash, nested_hash, file_hash = "a" * 64, "b" * 64, "c" * 64
    paths = ["factors/LICENSE", "LICENSE"] if nested else ["LICENSE"]
    audit = {
        "schema_version": "source-audit.v1", "status": "VERIFIED",
        "source_id": "sample", "repository": "https://github.com/example/sample",
        "commit": "1" * 40, "rights_status": "REVIEW_REQUIRED", "errors": [],
        "artifacts": [{"path": "factors/mean.py", "status": "VERIFIED",
                       "sha256": file_hash, "byte_size": 20, "license_candidates": paths,
                       "embedded_rights": []}],
        "license_artifacts": [{"path": "LICENSE", "sha256": license_hash,
                               "status": "VERIFIED"}],
        "file_reviews": [{"source_id": "sample", "commit": "1" * 40,
                          "path": "factors/mean.py", "sha256": file_hash,
                          "decision": "ADAPTATION_CANDIDATE", "license_id": "MIT",
                          "inspected_notices": [{"path": "LICENSE", "sha256": license_hash}],
                          "reviewed_by": "test reviewer", "reviewed_on": "2026-10-31",
                          "rationale": "Reviewed this file and applicable copyright."}],
    }
    if nested:
        audit["license_artifacts"].append({"path": "factors/LICENSE", "sha256": nested_hash,
                                           "status": "VERIFIED"})
    return audit


def test_nested_license_overrides_repository_label():
    api = _api()
    result = api.plan_asset_import(_audit(nested=True), existing_catalog=ResearchAssetCatalog.packaged())
    assert not result["eligible"]
    assert result["blocked"][0]["reason"] == "UNREVIEWED_APPLICABLE_NOTICE"


@pytest.mark.parametrize("restriction", ["All rights reserved; no redistribution",
                                          "Commons Clause License Condition",
                                          "SPDX-License-Identifier: GPL-3.0-only"])
def test_inspected_nested_restriction_cannot_be_labelled_mit(restriction):
    audit = _audit(nested=True)
    audit["license_artifacts"][1]["embedded_rights"] = [restriction]
    audit["file_reviews"][0]["inspected_notices"].append(
        {"path": "factors/LICENSE", "sha256": "b" * 64})
    result = _api().plan_asset_import(audit, existing_catalog=ResearchAssetCatalog.packaged())
    assert not result["eligible"]
    assert result["blocked"][0]["reason"] == "EMBEDDED_RIGHTS_CONFLICT"


def test_source_import_plan_is_per_file_and_has_no_execution_authority():
    api = _api()
    catalog = ResearchAssetCatalog.packaged()
    before = catalog.inventory()
    result = api.plan_asset_import(_audit(), existing_catalog=catalog)
    assert len(result["eligible"]) == 1 and not result["blocked"]
    assert result["status"] == "PROPOSAL_ONLY"
    assert result["required_notices"] == [{"path": "LICENSE", "sha256": "a" * 64}]
    assert catalog.inventory() == before


@pytest.mark.parametrize("change", ["no_review", "wrong_commit", "wrong_hash", "unknown_license",
                                     "source_blocked", "spdx_conflict"])
def test_no_review_or_conflict_never_becomes_eligible(change):
    api = _api()
    audit = _audit()
    review = audit["file_reviews"][0]
    if change == "no_review":
        audit["file_reviews"] = []
    elif change == "wrong_commit":
        review["commit"] = "2" * 40
    elif change == "wrong_hash":
        review["sha256"] = "0" * 64
    elif change == "unknown_license":
        review["license_id"] = "UNKNOWN"
    elif change == "source_blocked":
        audit["status"] = "BLOCKED"
    else:
        audit["artifacts"][0]["embedded_rights"] = ["SPDX-License-Identifier: GPL-3.0-only"]
    result = api.plan_asset_import(audit, existing_catalog=ResearchAssetCatalog.packaged())
    assert not result["eligible"] and (result["blocked"] or result["unresolved"])


def test_all_legacy_resources_and_unresolved_definitions_are_reconciled():
    api = _api()
    root = Path(__file__).resolve().parents[2]
    report = json.loads((root / "docs/research/quant-resource-reconciliation.json").read_text())
    catalog = ResearchAssetCatalog.packaged()
    assert len(report["resources"]) == 52
    assert len({r["resource_id"] for r in report["resources"]}) == 52
    for item in report["resources"]:
        current = catalog.get_resource(item["resource_id"])
        assert current.sha256 == item["previous_sha256"]
        assert current.availability == item["previous_availability"]
        assert item["disposition"] and item["reason"] and item["evidence_refs"]
    assert len(report["unresolved_definitions"]) == 8
    assert all(d["status"] == "UNRESOLVED" and d["reason"] for d in report["unresolved_definitions"])
    assert api.reconcile_resources(catalog, report)["status"] == "VERIFIED"


@pytest.mark.parametrize("change", ["version", "path", "omitted_id", "omitted_definition",
                                     "rewritten_baseline"])
def test_reconciliation_detects_identity_or_baseline_drift(tmp_path, change):
    from tests.research.test_method_context import _root

    root = _root(tmp_path)
    manifest_path = root / "provenance/upstream-lock.json"
    manifest = json.loads(manifest_path.read_text())
    report = json.loads((root / "provenance/resource-reconciliation.v1.json").read_text())
    ref = next(r for r in manifest["resources"] if r["resource_id"] == "method-factor-mining-workflow")
    if change == "version":
        ref["version"] = "99.0.0"
    elif change == "path":
        old = root / ref["path"]
        ref["path"] = "new-location.md"
        (root / ref["path"]).write_bytes(old.read_bytes())
    elif change == "omitted_id":
        report["resources"].pop()
    elif change == "omitted_definition":
        report["unresolved_definitions"].pop()
    else:
        next(r for r in report["resources"]
             if r["resource_id"] == ref["resource_id"])["previous_version"] = "99.0.0"
    _save(root, manifest)
    result = _api().reconcile_resources(ResearchAssetCatalog(root), report)
    assert result["status"] == "BLOCKED" and result["errors"]

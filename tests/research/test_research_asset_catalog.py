"""Read-only research methods have bounded, licensed, package-local resources."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
from dataclasses import FrozenInstanceError

import pytest


def _api():
    name = "finahinking.research.asset_catalog"
    assert importlib.util.find_spec(name) is not None, "research asset catalog is not implemented"
    return importlib.import_module(name)


def _write_catalog(root, *, path="skills/workflow/SKILL.md", availability="PACKAGED"):
    guide = "# 方法\n仅整理研究，不代表完成计算。\n"
    license_text = "MIT License\nCopyright (c) test contributors\n"
    index = {"schema_version": "research-methods.v1", "skills": [{
        "skill_id": "workflow", "name": "研究方法", "version": "1.0.0",
        "tags": ["factor"], "required_inputs": ["snapshot_ref"],
        "output_types": ["ResearchPlan"], "main_resource": "guide",
        "required_resources": ["guide"], "optional_resources": ["missing-report"],
        "policy_ref": "guide", "boundaries": ["paper-only"],
    }]}
    bodies = {"skills/workflow/SKILL.md": guide, "LICENSE": license_text,
              "skills/index.json": json.dumps(index, ensure_ascii=False)}
    for relative, body in bodies.items():
        file = root / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(body, encoding="utf-8")
    records = []
    for resource_id, relative in [("guide", "skills/workflow/SKILL.md"),
                                  ("license", "LICENSE"), ("skill-index", "skills/index.json")]:
        body = bodies[relative].encode()
        records.append({"resource_id": resource_id,
                        "path": path if resource_id == "guide" else relative,
                        "sha256": hashlib.sha256(body).hexdigest(),
                        "byte_size": len(body), "availability": availability if resource_id == "guide" else "PACKAGED",
                        "kind": "method" if resource_id == "guide" else "license" if resource_id == "license" else "metadata",
                        "author": "test contributors", "license_id": "MIT",
                        "license_ref": "license", "version": "1.0.0"})
    records.append({"resource_id": "missing-report", "path": None, "sha256": None,
                    "byte_size": None, "availability": "MISSING", "kind": "source-report",
                    "author": "upstream", "license_id": None, "license_ref": None,
                    "source_path": "C:\\research\\因子池\\report.md", "version": "unknown"})
    manifest = {"schema_version": "research-assets.v1", "upstreams": [], "resources": records}
    target = root / "provenance/upstream-lock.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return manifest


def test_asset_hash_mismatch_rejected(tmp_path):
    api = _api()
    _write_catalog(tmp_path)
    catalog = api.ResearchAssetCatalog(tmp_path)
    (tmp_path / "skills/workflow/SKILL.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(api.AssetLoadError) as error:
        catalog.read_text("guide")
    assert error.value.code == "HASH_MISMATCH"


@pytest.mark.parametrize("path", ["../outside.md", "/etc/passwd", "C:\\secret.md",
                                   "skills/../../secret.md", "skills//guide.md"])
def test_asset_path_cannot_escape_package(tmp_path, path):
    api = _api()
    _write_catalog(tmp_path, path=path)
    with pytest.raises(api.AssetLoadError) as error:
        api.ResearchAssetCatalog(tmp_path).read_text("guide")
    assert error.value.code == "INVALID_PATH"


def test_symlink_cannot_escape_package(tmp_path):
    api = _api()
    root = tmp_path / "package"
    _write_catalog(root)
    outside = tmp_path / "outside.md"
    outside.write_text("private", encoding="utf-8")
    guide = root / "skills/workflow/SKILL.md"
    guide.unlink()
    guide.symlink_to(outside)
    with pytest.raises(api.AssetLoadError) as error:
        api.ResearchAssetCatalog(root).read_text("guide")
    assert error.value.code == "INVALID_PATH"


def test_missing_source_is_not_a_local_file(tmp_path):
    api = _api()
    _write_catalog(tmp_path)
    catalog = api.ResearchAssetCatalog(tmp_path)
    refs = catalog.resources_for("workflow")
    missing = next(ref for ref in refs if ref.resource_id == "missing-report")
    assert missing.availability == "MISSING" and missing.path is None
    with pytest.raises(api.AssetLoadError) as error:
        catalog.read_text("missing-report")
    assert error.value.code == "MISSING_RESOURCE"


def test_bounded_utf8_reads_and_frozen_descriptors(tmp_path):
    api = _api()
    _write_catalog(tmp_path)
    catalog = api.ResearchAssetCatalog(tmp_path)
    assert "研究" in catalog.read_text("guide")
    with pytest.raises(api.AssetLoadError) as error:
        catalog.read_text("guide", max_bytes=12)
    assert error.value.code == "TOO_LARGE"
    with pytest.raises(FrozenInstanceError):
        catalog.get_skill("workflow").version = "unsafe"
    assert len(catalog.skills()) == 1
    assert catalog.get_skill("workflow").output_types == ("ResearchPlan",)


def test_missing_license_blocks_local_resource(tmp_path):
    api = _api()
    manifest = _write_catalog(tmp_path)
    manifest["resources"][0]["license_ref"] = None
    (tmp_path / "provenance/upstream-lock.json").write_text(json.dumps(manifest))
    with pytest.raises(api.AssetLoadError) as error:
        api.ResearchAssetCatalog(tmp_path).read_text("guide")
    assert error.value.code == "LICENSE_MISSING"


def test_packaged_catalog_is_the_installed_resource_authority():
    catalog = _api().ResearchAssetCatalog.packaged()
    assert "DeepSeek HARNESS Quant" in catalog.read_text("upstream-license")
    assert isinstance(catalog.skills(), tuple)

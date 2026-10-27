"""Research resources must survive an actual wheel installation."""

import json
import subprocess
import sys
import venv
import zipfile
from pathlib import Path

from tests.validation.test_artifact_install import ROOT, _run_checked


def test_research_catalog_installed_outside_checkout(tmp_path: Path):
    wheels = tmp_path / "wheel"
    wheels.mkdir()
    _run_checked([sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir",
                  str(wheels), str(ROOT)], cwd=tmp_path)
    wheel = next(wheels.glob("finathink-*.whl"))
    prefix = "finahinking/_package_data/research_assets/"
    with zipfile.ZipFile(wheel) as archive:
        manifest = json.loads(archive.read(prefix + "provenance/upstream-lock.json"))
        expected = {ref["path"] for ref in manifest["resources"]
                    if ref["availability"] == "PACKAGED"} | {"provenance/upstream-lock.json"}
        actual = {name.removeprefix(prefix) for name in archive.namelist()
                  if name.startswith(prefix) and not name.endswith("/")}
        assert actual == expected, "wheel contains missing or unregistered/unlicensed assets"
    env = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(env)
    python = env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    _run_checked([str(python), "-m", "pip", "install",
                  str(next(wheels.glob("finathink-*.whl")))], cwd=tmp_path)
    probe = """
from finahinking.research.asset_catalog import ResearchAssetCatalog
catalog = ResearchAssetCatalog.packaged()
assert 'DeepSeek HARNESS Quant' in catalog.read_text('upstream-license')
for skill in catalog.skills():
    assert catalog.read_text(skill.main_resource)
    for ref in catalog.resources_for(skill.skill_id):
        if ref.availability == 'PACKAGED':
            assert catalog.read_text(ref.resource_id)
inventory = catalog.inventory()
assert inventory.skill_count == 12
assert inventory.claim_count >= 14 and inventory.lesson_count >= 6
assert inventory.locally_reproduced_claim_count == 0
print('installed research assets verified')
"""
    result = subprocess.run([str(python), "-I", "-c", probe], cwd=tmp_path,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "installed research assets verified" in result.stdout

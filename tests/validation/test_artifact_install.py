"""Exercise the wheel from a clean interpreter outside the source checkout."""

from __future__ import annotations

import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run_checked(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a clean-install boundary and retain diagnostics when it fails.

    ``CalledProcessError`` only renders the command in pytest's default
    failure output; the captured pip/import traceback is otherwise hidden in
    ``stdout``/``stderr`` attributes.  Keep the subprocess isolated while
    turning those boundary diagnostics into a useful assertion failure.
    """

    result = subprocess.run(command, cwd=cwd, check=False, capture_output=True, text=True)
    if result.returncode:
        command_text = " ".join(command)
        raise AssertionError(
            f"subprocess failed ({result.returncode}): {command_text}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result


def test_wheel_install_exposes_migrations_fixtures_and_local_routes(tmp_path: Path) -> None:
    """A wheel must carry every resource used by the supported local journeys."""

    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    _run_checked(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(wheel_dir), str(ROOT)],
        cwd=tmp_path,
    )
    wheel = next(wheel_dir.glob("finathink-*.whl"))
    env_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True, system_site_packages=False).create(env_dir)
    python = env_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    _run_checked([str(python), "-m", "pip", "install", str(wheel)], cwd=tmp_path)

    probe = """
from pathlib import Path
import sqlite3
from finahinking.p6_5.repository import ADDITIVE_MIGRATION_PATHS, apply_migration
from finahinking.p7.repository import MIGRATION_PATH as P7_MIGRATION_PATH, apply_p7_migration
from finahinking.local_app import LocalApplication
from finahinking.resources import fixture_path
from finahinking.research.asset_workspace_page import ResearchAssetWorkspace
from finahinking.research.asset_catalog import ResearchAssetCatalog
from finahinking.research.report_sections import theme_report_document

assert all(path.is_file() for path in ADDITIVE_MIGRATION_PATHS)
assert P7_MIGRATION_PATH.is_file()
assert fixture_path('p6_5/bls_cpi_2024_2025.json').is_file()
assert ResearchAssetWorkspace(ResearchAssetCatalog.packaged()).list()['catalog_total'] > 0
assert '#f5f8fd' in theme_report_document('<html><head></head><body>offline</body></html>')
connection = sqlite3.connect(':memory:')
apply_migration(connection)
apply_p7_migration(connection)
app = LocalApplication(connection=connection)
event = app.route('GET', '/api/events')[2]['events'][0]
assert event['data_mode'] == 'SAMPLE'
assert event['source_state'] == 'CAPTURED'
assert event['evidence_ids']
quant = app.route('POST', '/api/quant', body={'question': 'How does the market relate to asset returns?'})[2]
assert quant['numeric_results']['sample_count'] >= 70
assert quant['artifact_fingerprint']
assert app.route('GET', '/api/strategy')[2]['execution'] == 'paper-only'
assert app.route('GET', '/api/p8_2b/knowledge/ols')[2]['unit']['unit_id'] == 'ols'
app.close()
"""
    _run_checked([str(python), "-c", probe], cwd=tmp_path)
    _run_checked([str(python), str(ROOT / 'scripts' / 'check_installed_journey.py'), '--state-root', str(tmp_path / 'first-use')], cwd=tmp_path)

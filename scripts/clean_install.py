"""Build and exercise a Finathink wheel in an external temporary venv.

The venv installs the wheel and its declared runtime dependencies, keeping
package resources and local product routes imported exclusively from the wheel.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(command: list[str], *, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, help="use an existing wheel instead of building one")
    parser.add_argument("--examples-output", type=Path, help="write the six verified synthetic workflow examples to this ZIP")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="finahinking-clean-install-") as directory:
        work = Path(directory)
        wheel = args.wheel
        if wheel is None:
            wheel_dir = work / "wheel"
            wheel_dir.mkdir()
            _run(
                [sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(wheel_dir), str(ROOT)],
                cwd=work,
            )
            wheel = next(wheel_dir.glob("finathink-*.whl"))
        elif not wheel.is_file():
            raise SystemExit(f"wheel not found: {wheel}")

        env_dir = work / "venv"
        venv.EnvBuilder(with_pip=True, system_site_packages=False).create(env_dir)
        python = env_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        _run([str(python), "-m", "pip", "install", str(wheel.absolute())], cwd=work)
        probe = """
import sqlite3
from finahinking.local_app import LocalApplication
from finahinking.p8_2b.catalog import get_knowledge_unit
from finahinking.p6_5.repository import ADDITIVE_MIGRATION_PATHS, apply_migration
from finahinking.p7.repository import MIGRATION_PATH, apply_p7_migration
from finahinking.resources import fixture_path

assert all(path.is_file() for path in ADDITIVE_MIGRATION_PATHS)
assert MIGRATION_PATH.is_file()
assert fixture_path('p6_5/bls_cpi_2024_2025.json').is_file()
connection = sqlite3.connect(':memory:')
apply_migration(connection)
apply_p7_migration(connection)
app = LocalApplication(connection=connection)
event = app.route('GET', '/api/events')[2]['events'][0]
assert event['source_state'] == 'CAPTURED' and event['evidence_ids']
quant = app.route('POST', '/api/quant', body={'question': 'How does the market relate to asset returns?'})[2]
assert quant['numeric_results']['sample_count'] >= 70
assert quant['artifact_fingerprint']
assert app.route('GET', '/api/strategy')[2]['execution'] == 'paper-only'
assert app.route('GET', '/api/p8_2b/knowledge/ols')[2]['unit']['unit_id'] == 'ols'
assert app.route('GET', '/research/atlas/factors')[0] == 200
assert app.route('GET', '/research/atlas/evidence')[0] == 200
assert app.route('GET', '/research/atlas/runs')[0] == 200
assert app.route('GET', '/api/research/learning/strategy/sources')[2] == {'sources': []}
assert app.route('GET', '/assets/research-serif.woff2')[2].startswith(b'wOF2')
assert app.route('GET', '/assets/research-relief.svg')[1] == 'image/svg+xml'
assert app.route('GET', '/assets/atlas-labs.css')[1].startswith('text/css')
assert app.route('GET', '/assets/model-workbench.css')[1].startswith('text/css')
assert app.route('GET', '/research/model-workbench')[0] == 200
assert app.route('GET', '/assets/katex/katex.min.css')[0] == 200
assert app.route('GET', '/assets/katex/fonts/KaTeX_Main-Regular.woff2')[0] == 200
assert get_knowledge_unit('sharpe').unit_id == 'sharpe'
app.close()
"""
        _run([str(python), "-c", probe], cwd=work)
        # Discovery must work from the installed console script with no running app.
        operator = python.with_name("finathink-codex" + (".exe" if python.suffix == ".exe" else ""))
        discover = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        discovery = subprocess.run([str(operator), "--url", "http://127.0.0.1:1", "--job", "installed-check",
            "--operation", "codex-op-" + "0" * 32], input=json.dumps(discover) + "\n", text=True,
            capture_output=True, timeout=15, check=True, cwd=work)
        assert {t["name"] for t in json.loads(discovery.stdout)["result"]["tools"]} == {
            "read_research_task", "submit_research_plan", "report_research_failure"}
        _run([str(python), str(ROOT / 'scripts' / 'check_model_workbench.py')], cwd=work)
        journey = [str(python), str(ROOT / 'scripts' / 'check_installed_journey.py'), '--state-root', str(work / 'first-use')]
        if args.examples_output is not None:
            journey.extend(['--examples-output', str(args.examples_output.resolve())])
        _run(journey, cwd=work)
        print(f"clean wheel install smoke passed: {wheel.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Content identity works in source checkouts and installed wheels without Git."""
import hashlib
import platform
from importlib.metadata import version
from pathlib import Path

from .contracts import stable_digest


def execution_identity(policy):
    root = Path(__file__).resolve().parents[1]
    sources = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
               for path in sorted(root.rglob('*.py')) if '__pycache__' not in path.parts}
    return {'schema_version': 'research-execution.v1', 'source_digest': stable_digest(sorted(sources.items())),
            'dependencies': {'python': platform.python_version(),
                             'numpy': version('numpy'), 'pandas': version('pandas')},
            'engine_version': policy['engine_version'], 'strategy_version': policy['strategy_version']}

"""Static, code-owned stage registry for spawned research workers.

The registry is deliberately small: a job may name an allow-listed stage, but
it may never provide a module path, serialized callable, or source code.
Deployments register trusted module-level functions during application
bootstrap and pass only the resulting stage names across the process boundary.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass

StageRunner = Callable[..., Mapping[str, object]]
_SAFE_REF = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$")
_SAFE_VERSION = re.compile(r"^[0-9A-Za-z][A-Za-z0-9_.:-]{0,63}$")
_SAFE_FILE = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class StageRegistryError(ValueError):
    """Raised when a stage violates the trusted bootstrap contract."""


def _validate_ref(value: object, field: str, *, pattern: re.Pattern[str] = _SAFE_REF) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise StageRegistryError(f"{field} must be a safe non-empty reference")
    return value


@dataclass(frozen=True, slots=True)
class StageSpec:
    name: str
    version: str
    runner_key: str
    paper_only: bool = True

    def __post_init__(self) -> None:
        _validate_ref(self.name, "name")
        _validate_ref(self.version, "version", pattern=_SAFE_VERSION)
        _validate_ref(self.runner_key, "runner_key")
        if self.paper_only is not True:
            raise StageRegistryError("research stages must be paper_only")

    def to_payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "runner_key": self.runner_key,
            "paper_only": self.paper_only,
        }


def _is_module_level_runner(runner: object) -> bool:
    if not inspect.isfunction(runner):
        return False
    if runner.__name__ == "<lambda>" or runner.__qualname__ != runner.__name__:
        return False
    if runner.__code__.co_freevars:
        return False
    if runner.__code__.co_name != runner.__name__ or runner.__code__.co_qualname != runner.__qualname__:
        return False
    module_name = runner.__module__
    if not module_name or module_name == "__main__":
        return False
    module = sys.modules.get(module_name)
    if module is None:
        return False
    # A callable can spoof ``__module__``/``__qualname__``.  Requiring the
    # exact object exported under its declared name makes the bootstrap
    # descriptor reconstructable without importing a task-supplied path.
    module_file = getattr(module, "__file__", None)
    code_file = getattr(runner.__code__, "co_filename", None)
    if not module_file or not code_file:
        return False
    try:
        if os.path.realpath(module_file) != os.path.realpath(code_file):
            return False
    except (OSError, TypeError):
        return False
    return getattr(module, runner.__name__, None) is runner


def _runner_identity(runner: StageRunner) -> dict[str, str]:
    return {
        "module": runner.__module__,
        "name": runner.__name__,
        "qualname": runner.__qualname__,
    }


class StageRegistry:
    """An insertion-order-independent allow-list of trusted stages."""

    def __init__(self) -> None:
        self._specs: dict[str, StageSpec] = {}
        self._runners: dict[str, StageRunner] = {}

    def register(self, spec: StageSpec, runner: StageRunner) -> None:
        if not isinstance(spec, StageSpec):
            raise StageRegistryError("spec must be StageSpec")
        if not _is_module_level_runner(runner):
            raise StageRegistryError("runner must be a module-level function")
        if spec.name in self._specs:
            raise StageRegistryError("stage name is already registered")
        if spec.runner_key in self._runners:
            raise StageRegistryError("runner_key is already registered")
        self._specs[spec.name] = spec
        self._runners[spec.runner_key] = runner

    def resolve(self, name: str) -> StageRunner:
        _validate_ref(name, "name")
        spec = self._specs.get(name)
        if spec is None:
            raise StageRegistryError("unknown stage")
        return self._runners[spec.runner_key]

    def snapshot(self) -> tuple[StageSpec, ...]:
        return tuple(self._specs[name] for name in sorted(self._specs))

    def digest(self) -> str:
        payload = [
            {
                **spec.to_payload(),
                "runner": _runner_identity(self._runners[spec.runner_key]),
            }
            for spec in self.snapshot()
        ]
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


def _workflow_result_payload(workflow: object) -> Mapping[str, object]:
    """Convert a workflow result into a fail-closed stage result."""

    try:
        state = getattr(getattr(workflow, "state", None), "current_state", None)
        state_name = getattr(state, "value", state)
        state_obj = getattr(workflow, "state", None)
        state_run_id = getattr(state_obj, "run_id", None)
        eligible = getattr(state_obj, "decision_eligible", False)
        decision = getattr(workflow, "decision", None)
        manifest = getattr(workflow, "manifest", None)
        files = getattr(manifest, "files", None)
        run_id = getattr(manifest, "run_id", None)
        from .contracts import ReportManifest

        valid_files = (
            isinstance(files, Mapping)
            and bool(files)
            and all(
                isinstance(path, str)
                and _SAFE_FILE.fullmatch(path)
                and isinstance(digest, str)
                and _SHA256.fullmatch(digest)
                for path, digest in files.items()
            )
        )
        # A ReportManifest is only an in-memory description.  It does not
        # carry the bundle root, the manifest file, or proof that every listed
        # digest was recomputed from durable bytes.  Treating its hex strings
        # as proof would let a fabricated object publish a false success.
        # The selected-snapshot runner below supplies a filesystem-backed
        # receipt. A memory-only legacy result remains explicitly blocked.
        if (
            state_name == "LEARNING_RECORDED"
            and eligible is True
            and decision is not None
            and isinstance(manifest, ReportManifest)
            and valid_files
            and isinstance(state_run_id, str)
            and state_run_id == run_id
            and isinstance(run_id, str)
            and _SAFE_REF.fullmatch(run_id)
        ):
            return {
                "status": "blocked",
                "state": "LEARNING_RECORDED",
                "failure_kind": "WORKFLOW_ARTIFACT_UNAVAILABLE",
            }
        failure_kind = getattr(getattr(workflow, "state", None), "failure_kind", None)
        failure_name = getattr(failure_kind, "value", failure_kind) or "WORKFLOW_ARTIFACT_UNAVAILABLE"
        return {
            "status": "blocked",
            "state": str(state_name or "UNKNOWN"),
            "failure_kind": str(failure_name if failure_name != "WORKFLOW_BLOCKED" else "WORKFLOW_ARTIFACT_UNAVAILABLE"),
        }
    except Exception:  # noqa: BLE001 - malformed workflow objects fail closed
        return {"status": "blocked", "state": "UNKNOWN", "failure_kind": "WORKFLOW_ARTIFACT_UNAVAILABLE"}


def run_default_workflow_stage(
    request: object,
    previous: Mapping[str, str],
    publish_checkpoint: Callable[[str], None] | None = None,
) -> Mapping[str, object]:
    """Run the built-in paper workflow from a trusted top-level entrypoint.

    Imports are intentionally inside the function.  Registry import and
    bootstrap remain side-effect free, while the child can reconstruct this
    fixed runner without accepting an import path from a task payload.
    """

    del previous
    invocation = request
    if not hasattr(request, "run_id"):
        # The supervisor process exposes only its private queue location to
        # the trusted built-in stage.  The worker still receives a digest-only
        # invocation and reconstructs the validated request from SQLite.
        queue_path = os.environ.get("FINATHINK_QUEUE_PATH")
        if not queue_path:
            return {"status": "blocked", "state": "UNKNOWN", "failure_kind": "REQUEST_UNAVAILABLE"}
        from .job_queue import JobQueue

        try:
            queue = JobQueue(queue_path)
            request = queue.load_request_snapshot(request.task_ref, expected_digest=queue.task_input_digest(request.task_ref))
        except (KeyError, ValueError, TypeError):
            return {"status": "blocked", "state": "UNKNOWN", "failure_kind": "REQUEST_UNAVAILABLE"}
    if getattr(request, "snapshot_ref", None) is not None:
        from .application_service import run_local_research_stage

        if not hasattr(invocation, "job_id") or publish_checkpoint is None:
            return {"status": "blocked", "failure_kind": "REQUEST_UNAVAILABLE"}
        return run_local_research_stage(invocation, None, publish_checkpoint)
    from .drivers import OfflineDriver
    from .tools import ResearchToolGateway
    from .workflow import ResearchOrchestrator

    workflow = ResearchOrchestrator().run(request, OfflineDriver(), ResearchToolGateway())
    return _workflow_result_payload(workflow)


def default_stage_registry() -> StageRegistry:
    """Return a fresh registry containing the trusted workflow entrypoint.

    The runner is module-level and fixed in this module; its implementation
    performs application imports only when a stage is actually executed.
    """

    registry = StageRegistry()
    registry.register(
        StageSpec(name="workflow", version="1.0", runner_key="workflow.default"),
        run_default_workflow_stage,
    )
    return registry


__all__ = [
    "StageRegistry",
    "StageRegistryError",
    "StageRunner",
    "StageSpec",
    "default_stage_registry",
    "run_default_workflow_stage",
]

"""Exercise the npm shim, native descendants and real deadline enforcement."""

import json
import os
import sys
import time
from types import SimpleNamespace

import pytest

from finahinking.research.codex_cli_worker import run_once
from finahinking.research.codex_process import prepared_process

windows = pytest.mark.skipif(os.name != "nt", reason="requires the Windows process and job APIs")


@pytest.fixture
def npm_shim(tmp_path, monkeypatch):
    shim_dir = tmp_path / "npm binaries"
    shim_dir.mkdir()
    entry = shim_dir / "codex.cmd"
    parent = shim_dir / "launcher.py"
    child = shim_dir / "model.py"
    ran = tmp_path / "invoked"
    ready = tmp_path / "ready.json"
    release = tmp_path / "release"
    escaped = tmp_path / "escaped"

    entry.write_text(f'@echo off\n@echo invoked>"{ran}"\n"{sys.executable}" "{parent}" %*\n', encoding="utf-8")
    parent.write_text(
        "import json, os, subprocess, sys, time\n"
        "from pathlib import Path\n"
        "assert 'web_search=\"disabled\"' in sys.argv and '--strict-config' in sys.argv\n"
        "assert json.loads(sys.stdin.read().split('JSON object.\\n', 1)[1]) == {'sample': 123}\n"
        f"subprocess.Popen([sys.executable, {str(child)!r}, str(os.getpid())])\n"
        "time.sleep(45)\n", encoding="utf-8")
    child.write_text(
        "import json, os, sys, time\n"
        "from pathlib import Path\n"
        f"ready = Path({str(ready)!r})\n"
        "intermediate = ready.with_suffix('.tmp')\n"
        "intermediate.write_text(json.dumps([int(sys.argv[1]), os.getpid()]))\n"
        "intermediate.replace(ready)\n"
        f"stop = time.monotonic() + 25\nwhile time.monotonic() < stop and not Path({str(release)!r}).exists():\n"
        "    time.sleep(.01)\n"
        f"Path({str(escaped)!r}).touch()\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(shim_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    return SimpleNamespace(entry=entry, ran=ran, ready=ready, release=release, escaped=escaped)


@windows
@pytest.mark.parametrize("terminal", ["CANCELLED", "TIMED_OUT"])
def test_npm_cmd_and_native_descendants_stop_at_cancel_or_deadline(npm_shim, terminal):
    import _winapi

    class Workbench:
        path = "/one-grant"
        package = SimpleNamespace(_json='{"sample":123}')

        def __init__(self):
            self.calls = []
            self.client = self
            self.expiry = time.time() + 12
            self.handles = []

        def get(self, path, *, timeout=8):
            assert path == self.path
            if npm_shim.ready.exists() and not self.handles:
                for pid in json.loads(npm_shim.ready.read_text()):
                    # SYNCHRONIZE | PROCESS_TERMINATE for cleanup on a regression.
                    handle = _winapi.OpenProcess(0x100001, False, pid)
                    self.handles.append(handle)
                if terminal == "CANCELLED":
                    return {"status": terminal}
                self.expiry = time.time() + 1
            elif self.handles and terminal == "TIMED_OUT":
                # Simulate a stalled status read across the shortened deadline.
                assert _winapi.WaitForSingleObject(self.handles[-1], 2250) == _winapi.WAIT_OBJECT_0
            status = "TIMED_OUT" if time.time() >= self.expiry else "DISPATCHED"
            return {"status": status, "lease_until": self.expiry}

        def call(self, name, values):
            self.calls.append(name)
            return {"status": values.get("failure_kind", "DISPATCHED")}

    worker = Workbench()
    try:
        result = run_once(worker)
        assert result["status"] == terminal
        assert worker.calls == ["read_research_task"]
        assert npm_shim.ran.exists() and len(worker.handles) == 2
        npm_shim.release.touch()
        for handle in worker.handles:
            assert _winapi.WaitForSingleObject(handle, 2000) == _winapi.WAIT_OBJECT_0
        assert not npm_shim.escaped.exists()
    finally:
        npm_shim.release.touch()
        for handle in worker.handles:
            if _winapi.WaitForSingleObject(handle, 0) == _winapi.WAIT_TIMEOUT:
                _winapi.TerminateProcess(handle, 1)
            _winapi.CloseHandle(handle)


@windows
def test_windows_job_assignment_failure_cannot_claim_or_execute(npm_shim, monkeypatch):
    from finahinking.research.codex_process import _WindowsJob

    def refused(self, process):
        raise OSError("simulated nested-job assignment rejection")

    def no_claim(*args):
        pytest.fail("claiming before validating the actual process containment")

    monkeypatch.setattr(_WindowsJob, "assign", refused)
    with pytest.raises(ValueError, match="受控 Windows 进程树"):
        run_once(SimpleNamespace(call=no_claim))
    assert not npm_shim.ran.exists()


def test_exited_launcher_cannot_leave_a_native_descendant_running(tmp_path):
    ready, release, escaped = [tmp_path / name for name in ("ready", "release", "escaped")]
    model = tmp_path / "model.py"
    model.write_text(
        "from pathlib import Path\nimport time\n"
        f"Path({str(ready)!r}).touch()\n"
        f"end = time.monotonic() + 15\nwhile time.monotonic() < end and not Path({str(release)!r}).exists():\n"
        "    time.sleep(.01)\n"
        f"Path({str(escaped)!r}).touch()\n", encoding="utf-8")
    launcher = tmp_path / "launcher.py"
    launcher.write_text(
        "import subprocess, sys, time\nfrom pathlib import Path\n"
        f"subprocess.Popen([sys.executable, {str(model)!r}])\n"
        f"while not Path({str(ready)!r}).exists(): time.sleep(.01)\n", encoding="utf-8")
    with open(os.devnull, "rb") as prompt, prepared_process(
            [sys.executable, str(launcher)], cwd=tmp_path, stdin=prompt, env=dict(os.environ)) as process:
        process.start()
        assert process.wait(timeout=8) == 0
    release.touch()
    time.sleep(.35)
    assert not escaped.exists()

"""The external worker binds a Codex result to one previously authorized research."""
import io
import json

import pytest


def test_worker_handshake_is_inert_and_rejects_other_tools():
    from finahinking.research.codex_operator import serve

    class NoNetwork:
        def call(self, *args):
            raise AssertionError("handshake must not claim a research job")

    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "clientInfo": {"name": "test", "version": "1"}, "capabilities": {}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "other", "arguments": {}}},
    ]
    source = io.BytesIO(b"".join((json.dumps(row) + "\n").encode() for row in requests))
    sink = io.BytesIO()
    serve(NoNetwork(), source, sink)
    first, listing, rejected = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert first["result"]["protocolVersion"] == "2025-06-18"
    assert {tool["name"] for tool in listing["result"]["tools"]} == {
        "read_research_task", "submit_research_plan", "report_research_failure"}
    assert rejected["error"]["code"] == -32602


@pytest.mark.parametrize("url", ["https://example.org", "http://127.0.0.1:8765/redirect",
                                  "http://name@127.0.0.1", "http://127.0.0.1/?url=x"])
def test_operator_only_accepts_the_explicit_local_application_origin(url):
    from finahinking.research.codex_operator import LocalExecution

    with pytest.raises(ValueError):
        LocalExecution(url, "job-one", "codex-op-" + "a" * 32)


def test_operator_research_result_binds_selected_evidence_and_model_authored_content():
    from finahinking.research.codex_operator import research_result
    from finahinking.research.contracts import stable_digest
    from finahinking.research.method_handoff import accept_method_result
    from tests.research.test_method_handoff import package

    frozen = package()
    refs = list(frozen.context.evidence_index)[:1]
    plan = {"hypothesis": "Test whether recent returns persist over the next five observations.",
            "evidence_refs": refs, "validation": "Freeze the cutoff and compare against a flat-return baseline.",
            "falsification": "Reject the hypothesis if post-cost out-of-sample returns are nonpositive.",
            "limitations": ["The synthetic sample is short."]}
    result = research_result(frozen, plan)
    out = result["output_artifacts"][0]
    assert out["payload"]["hypotheses"] == [plan["hypothesis"]]
    assert out["content_digest"] == stable_digest(out["payload"])
    assert accept_method_result(frozen, result, allowed_evidence_refs=refs).status == "ANALYZED"
    with pytest.raises(ValueError):
        research_result(frozen, {**plan, "evidence_refs": ["evidence:" + "a" * 64]})


@pytest.mark.parametrize("scenario, expected", [
    ("valid", "COMPLETED"), ("invalid", "INVALID_EXECUTOR_RESPONSE"), ("cancel", "CANCELLED"),
    ("cli_exit", "EXECUTOR_FAILED"),
])
def test_direct_worker_reads_frozen_input_and_fences_the_child_process(tmp_path, monkeypatch, scenario, expected):
    import os
    import sys
    import time

    from finahinking.research.codex_cli_worker import run_once
    from finahinking.research.codex_operator import research_result
    from tests.research.test_method_handoff import package

    frozen = package()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    binary = tmp_path / "codex"
    outcome = 'raise SystemExit(12)' if scenario == "cli_exit" else (
        'time.sleep(8)' if scenario == "cancel" else
        'target.write_text("[]" if ' + repr(scenario == "invalid") + ' else json.dumps(plan))')
    script = """import json, os, sys, time
from pathlib import Path
assert 'FINATHINK_PRIVATE_PROVIDER_VALUE' not in os.environ
args = sys.argv[1:]
assert args[args.index('--sandbox') + 1] == 'read-only'
assert '--ignore-user-config' in args and '--ignore-rules' in args and '--ephemeral' in args
assert '--strict-config' in args and 'skip_host_skill_discovery' in args and 'skill_search' in args
assert 'skills.include_instructions=false' in args and 'project_doc_max_bytes=0' in args
assert 'shell_tool' in args and 'unified_exec' in args and 'web_search="disabled"' in args
target = Path(args[args.index('--output-last-message')+1])
packet = json.loads(sys.stdin.read().split('JSON object.\\n', 1)[1])
refs = list(packet['context']['evidence_index'])
plan = {'hypothesis':'Prices will persist for five days.', 'validation':'Freeze the cutoff and hold out one week.',
        'falsification':'Reject if the after-cost spread is negative.', 'limitations':['Small synthetic sample.'],
        'evidence_refs':refs[:1]}
"""
    binary.write_text("#!" + sys.executable + "\n" + script + outcome + "\n", encoding="utf-8")
    binary.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FINATHINK_PRIVATE_PROVIDER_VALUE", "fixture-only")

    class Worker:
        package = frozen
        path = "/fixed"

        def __init__(self):
            self.client = self
            self.calls = []

        def get(self, path, *, timeout=8):
            assert path == self.path
            return {"status": "CANCELLED" if scenario == "cancel" else "DISPATCHED",
                    "lease_until": time.time() + 10}

        def call(self, name, data):
            self.calls.append(name)
            if name == "submit_research_plan":
                assert research_result(frozen, data)["output_artifacts"][0]["artifact_type"] == "ResearchPlan"
                return {"status": "COMPLETED"}
            return {"status": data.get("failure_kind", "DISPATCHED")}

    worker = Worker()
    began = time.monotonic()
    assert run_once(worker)["status"] == expected
    assert worker.calls[0] == "read_research_task" and worker.calls.count("read_research_task") == 1
    assert len(worker.calls) == (1 if scenario == "cancel" else 2)
    if scenario == "cancel":
        assert time.monotonic() - began < 4


def test_slow_status_read_does_not_extend_the_external_process_lease(tmp_path, monkeypatch):
    import os
    import sys
    import time

    from finahinking.research.codex_cli_worker import run_once
    from tests.research.test_method_handoff import package

    marker = tmp_path / "overran"
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    binary = tmp_path / "codex"
    binary.write_text(f"#!{sys.executable}\nimport time\nfrom pathlib import Path\n"
                      f"time.sleep(1.1)\nPath({str(marker)!r}).touch()\n", encoding="utf-8")
    binary.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])

    class SlowWorkbench:
        path = "/fixed"

        def __init__(self):
            self.client = self
            self.package = package()
            self.expiry = time.time() + 0.7
            self.reads = 0

        def get(self, path, *, timeout=8):
            self.reads += 1
            if self.reads == 2:
                assert timeout <= 0.7
                time.sleep(1.5)
            return {"status": "TIMED_OUT" if time.time() >= self.expiry else "DISPATCHED", "lease_until": self.expiry}

        def call(self, name, args):
            assert name == "read_research_task"

    assert run_once(SlowWorkbench())["status"] == "TIMED_OUT"
    assert not marker.exists()


@pytest.mark.parametrize("marker", ["AGENTS.md", "AGENTS.override.md"])
def test_cli_rejects_global_instructions_before_claiming(tmp_path, monkeypatch, marker):
    from finahinking.research.codex_cli_worker import run_once
    from finahinking.research.codex_client_profile import (
        CodexClientProfileError,
        check_client_profile,
    )

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / marker).write_text("Private local instructions")
    class Unclaimed:
        def call(self, *_args):
            pytest.fail("The client has not been cleared to receive the packet")
    with pytest.raises(CodexClientProfileError, match="全局指令"):
        run_once(Unclaimed())
    (tmp_path / marker).write_bytes(b"")
    check_client_profile()

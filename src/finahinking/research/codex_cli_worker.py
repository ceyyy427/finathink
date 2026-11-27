"""Run the signed-in Codex CLI once for the task already granted to this worker."""

import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

_MAX_PLAN_BYTES = 32768
# Model providers configured for unrelated research profiles never need to be
# inherited by this signed-in CLI. Keep only login, locale and network runtime settings.
_CLIENT_ENV = {"HOME", "USER", "LOGNAME", "PATH", "CODEX_HOME", "LANG", "LC_ALL", "LC_CTYPE", "TERM",
               "TMPDIR", "TMP", "TEMP", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "ALL_PROXY",
               "https_proxy", "http_proxy", "no_proxy", "all_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR",
               "SYSTEMROOT", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PATHEXT"}
_PROMPT = (
    "You are a paper-only research analyst. Use the frozen JSON below solely as evidence. "
    "Write one original research plan in the language of the research question. Cite exact keys from its "
    "evidence_index. Give a specific testable hypothesis, held-out comparison including trading costs, "
    "a separate falsification criterion and at least one limitation. Do not claim an observed profit. "
    "Use only the provided material. Do not use tools or access files, network, or other tasks. "
    "Return only the requested JSON object.\n"
)


def _schema():
    text = {"type": "string"}
    fields = {key: text for key in ("hypothesis", "validation", "falsification")}
    fields.update({key: {"type": "array", "items": text} for key in ("evidence_refs", "limitations")})
    return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}


def _stop(process):
    if process.poll() is None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        process.wait()


def run_once(worker):
    binary = shutil.which("codex")
    if binary is None:
        raise ValueError("请先安装 Codex CLI 并在本地登录。")
    worker.call("read_research_task", {})
    with tempfile.TemporaryDirectory(prefix="finathink-codex-") as directory:
        root = Path(directory)
        schema, result = root / "schema.json", root / "plan.json"
        schema.write_text(json.dumps(_schema()), encoding="utf-8")
        # Exclude inherited user MCP servers, repository rules and shell tools.
        command = [binary, "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral", "--skip-git-repo-check",
                   "--sandbox", "read-only", "--disable", "shell_tool", "--disable", "unified_exec",
                   "--disable", "multi_agent", "--disable", "apps", "--disable", "plugins",
                   "-c", 'web_search="disabled"', "-c", 'model_reasoning_effort="low"', "-C", directory,
                   "--color", "never", "--output-schema", str(schema), "--output-last-message", str(result), "-"]
        try:
            with tempfile.TemporaryFile(dir=root) as prompt:
                prompt.write((_PROMPT + worker.package._json).encode("utf-8"))
                prompt.seek(0)
                receipt = worker.client.get(worker.path)
                if receipt["status"] != "DISPATCHED":
                    return receipt
                process = subprocess.Popen(command, cwd=root, stdin=prompt, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, start_new_session=os.name == "posix",
                                           env={k: v for k, v in os.environ.items() if k in _CLIENT_ENV})
                try:
                    while process.poll() is None:
                        receipt = worker.client.get(worker.path)
                        if receipt["status"] != "DISPATCHED":
                            return receipt
                        delay = max(0, min(0.5, receipt["lease_until"] - time.time()))
                        if delay == 0:
                            return worker.call("report_research_failure", {"failure_kind": "EXECUTOR_TIMEOUT"})
                        try:
                            process.wait(timeout=delay)
                        except subprocess.TimeoutExpired:
                            pass
                finally:
                    _stop(process)
                if process.returncode:
                    return worker.call("report_research_failure", {"failure_kind": "EXECUTOR_FAILED"})
        except OSError:
            return worker.call("report_research_failure", {"failure_kind": "EXECUTOR_FAILED"})
        try:
            with result.open("rb") as stream:
                raw = stream.read(_MAX_PLAN_BYTES + 1)
            if len(raw) > _MAX_PLAN_BYTES:
                raise ValueError("Codex 方案过长。")
            plan = json.loads(raw)
            from .codex_operator import research_result

            research_result(worker.package, plan)
        except (OSError, ValueError, TypeError, RecursionError):
            return worker.call("report_research_failure", {"failure_kind": "INVALID_EXECUTOR_RESPONSE"})
        return worker.call("submit_research_plan", plan)

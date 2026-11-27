"""Run the signed-in Codex CLI once for the task already granted to this worker."""

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .codex_client_profile import CodexClientProfileError, check_client_profile
from .codex_process import prepared_process

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


def run_once(worker):
    check_client_profile()
    binary = shutil.which("codex")
    if binary is None:
        raise ValueError("请先安装 Codex CLI 并在本地登录。")
    with tempfile.TemporaryDirectory(prefix="finathink-codex-", dir="/tmp" if os.name == "posix" else None) as directory:
        root = Path(directory)
        schema, result = root / "schema.json", root / "plan.json"
        schema.write_text(json.dumps(_schema()), encoding="utf-8")
        # Exclude inherited user MCP servers, repository rules and shell tools.
        command = [binary, "exec", "--strict-config", "--ignore-user-config", "--ignore-rules", "--ephemeral", "--skip-git-repo-check",
                   "--sandbox", "read-only", "--disable", "shell_tool", "--disable", "unified_exec",
                   "--disable", "multi_agent", "--disable", "apps", "--disable", "plugins",
                   "--enable", "skip_host_skill_discovery", "--disable", "skill_search",
                   "--disable", "skill_mcp_dependency_install", "-c", "skills.include_instructions=false",
                   "-c", "project_doc_max_bytes=0",
                   "-c", 'web_search="disabled"', "-c", 'model_reasoning_effort="low"', "-C", directory,
                   "--color", "never", "--output-schema", str(schema), "--output-last-message", str(result), "-"]
        environment = {k: v for k, v in os.environ.items() if k in _CLIENT_ENV}
        with tempfile.TemporaryFile(dir=root) as prompt, prepared_process(
                command, cwd=root, stdin=prompt, env=environment) as process:
            worker.call("read_research_task", {})
            try:
                prompt.write((_PROMPT + worker.package._json).encode("utf-8"))
                prompt.flush()
                prompt.seek(0)
                receipt = worker.client.get(worker.path)
                if receipt["status"] != "DISPATCHED":
                    return receipt
                lease_until = receipt["lease_until"]
                remaining = lease_until - time.time()
                if remaining <= 0:
                    return worker.client.get(worker.path)
                deadline = time.monotonic() + remaining
                try:
                    check_client_profile()
                except CodexClientProfileError:
                    worker.call("report_research_failure", {"failure_kind": "EXECUTOR_FAILED"})
                    raise CodexClientProfileError("领取后发现 Codex 登录环境发生变化，本次执行已结束。请回到研究台查看回执。") from None
                if time.monotonic() >= deadline:
                    return worker.client.get(worker.path)
                process.start()
                # A stalled local status request must never keep the model process
                # alive beyond the grant. Use the server expiry to start a monotonic timer.
                timer = threading.Timer(max(0, deadline - time.monotonic()), process.stop)
                timer.daemon = True
                timer.start()
                try:
                    while process.poll() is None:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        try:
                            receipt = worker.client.get(worker.path, timeout=min(1.0, remaining))
                        except TimeoutError:
                            continue
                        if receipt["status"] != "DISPATCHED":
                            return receipt
                        if receipt["lease_until"] < lease_until:
                            lease_until = receipt["lease_until"]
                            deadline = min(deadline, time.monotonic() + lease_until - time.time())
                            timer.cancel()
                            timer.join()
                            timer = threading.Timer(max(0, deadline - time.monotonic()), process.stop)
                            timer.daemon = True
                            timer.start()
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        try:
                            process.wait(timeout=min(0.5, remaining))
                        except subprocess.TimeoutExpired:
                            pass
                finally:
                    timer.cancel()
                    try:
                        process.stop()
                    finally:
                        timer.join()
                if time.monotonic() >= deadline:
                    receipt = worker.client.get(worker.path)
                    if receipt["status"] != "DISPATCHED":
                        return receipt
                    return worker.call("report_research_failure", {"failure_kind": "EXECUTOR_TIMEOUT"})
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

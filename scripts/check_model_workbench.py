"""Exercise an installed model workbench with public synthetic inputs and emit a small receipt."""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from finahinking.local_app import LocalAppConfig, LocalApplication, create_server
from finahinking.research.codex_operator import LocalClient

BASE = "/api/research/model-workbench"
QUESTION = "用合成样本设计一个可否证的价格动量研究，说明观察窗口、时点和局限。"


@contextmanager
def workbench(url, directory, online):
    if url:
        yield url
        return
    app = LocalApplication(LocalAppConfig(db_path=str(directory / "state.sqlite3"), offline=not online))
    server = create_server(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


SOURCE_QUESTION = ("对每位有材料的分析师和经理，claims 中逐条引用原摘要原句，或只写一条真实价格观察，"
                   "格式为‘YYYY-MM-DD close = 数值’。把解释与待验证假设放进对应字段。")


def start(client, provider):
    preview = client.post(BASE + "/preview", {"snapshot_ref": "sample", "model_config_ref": provider,
        "question": QUESTION if provider == "offline" else SOURCE_QUESTION, "budget": {"max_attempts": 1, "max_provider_calls": 6, "max_wall_seconds": 300}})
    if preview["status"] != "READY":
        raise ValueError("预览阻断 " + ", ".join(b["code"] for b in preview["blockers"]))
    job = client.post(BASE + "/authorize", {"preview_ref": preview["preview_ref"],
        "preview_digest": preview["preview_digest"], "authorized": True})
    path = BASE + "/jobs/" + job["job_id"]
    until = time.monotonic() + 330
    while time.monotonic() < until:
        evidence = client.get(path)
        if evidence["status"] == "completed":
            return path, evidence
        if evidence["status"] not in {"queued", "running", "retryable"}:
            raise ValueError("研究未完成 " + str(evidence["failure_kind"] or evidence["status"]))
        time.sleep(0.25)
    raise TimeoutError("研究未在预算内完成。")


def codex_run(client, url, path, directory):
    binary = Path(sys.executable).with_name("finathink-codex")
    codex = shutil.which("codex")
    if not binary.is_file() or not codex:
        raise ValueError("请安装 Codex CLI 及项目 wheel。")
    handoff = client.post(path + "/handoffs", {"purposes": ["factor-mining"]})
    frozen = client.get(path + "/handoffs/" + handoff["handoff_ref"])
    grant = client.post(path + "/executions", {"authorized": True, "handoff_ref": frozen["handoff_ref"],
        "package_digest": frozen["package_digest"], "limits": {**frozen["limits"], "max_wall_seconds": 220}})
    operation = grant["operation_id"]
    command = [str(binary), "--run", "--url", url, "--job", grant["job_id"], "--operation", operation]
    outcome = None
    try:
        outcome = subprocess.run(command, cwd=directory, stdin=subprocess.DEVNULL, capture_output=True, timeout=260, check=False)
    finally:
        record = client.get(path + "/executions/" + operation)
        if record["status"] in {"AUTHORIZED", "DISPATCHED"}:
            client.post(path + "/executions/" + operation + "/cancel", {})
    if (outcome is None or outcome.returncode or record["protocol_status"] != "ANALYZED"
            or record["semantic_status"] != "TYPED_EVIDENCE_VERIFIED"):
        raise ValueError("Codex 独立回传未通过验证 " + record["status"] + " / " + record["semantic_status"])
    accepted = client.get(path + "/handoffs/" + handoff["handoff_ref"])
    plans = [a for a in accepted["artifacts"] if a["artifact_type"] == "ResearchPlan"]
    if len(plans) != 1 or plans[0]["artifact_ref"] not in record["artifact_refs"]:
        raise ValueError("无法重新读取已核验的研究方案。")
    return {"research_plan": plans[0]["payload"], "client": subprocess.check_output([codex, "--version"], text=True).strip(),
            **{k: record[k] for k in ("status", "package_digest", "protocol_status", "semantic_status",
                                      "artifact_types", "artifact_refs", "evidence_refs", "usage")}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="running local workbench origin with port")
    live = parser.add_mutually_exclusive_group()
    live.add_argument("--provider", help="authorize the chosen user-owned provider for this synthetic task")
    live.add_argument("--codex", action="store_true", help="authorize one synthetic handoff to the signed-in Codex CLI")
    options = parser.parse_args(argv)
    if options.provider and not options.url:
        parser.error("--provider requires --url for the saved user configuration")
    with tempfile.TemporaryDirectory(prefix="finathink-model-check-") as temp:
        directory = Path(temp)
        with workbench(options.url, directory, options.codex) as url:
            client = LocalClient(url)
            path, evidence = start(client, options.provider or "offline")
            result = {"schema_version": "model-workbench-check.v1", "data": "BUILT_IN_SYNTHETIC_SAMPLE",
                      "execution": "LIVE_PROVIDER" if options.provider else "LIVE_CODEX" if options.codex else "OFFLINE",
                      "research": {k: evidence[k] for k in ("status", "dispatch_status", "protocol_status",
                                                           "semantic_status", "semantic_summary", "usage")}}
            if not options.provider and (evidence["dispatch_status"] != "OFFLINE" or evidence["usage"]["status"] != "NOT_CALLED"):
                raise ValueError("离线合成研究的发送记录与用量不一致。")
            if options.codex:
                result["codex"] = codex_run(client, url, path, directory)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if options.provider and (evidence["protocol_status"] != "ACCEPTED"
                                     or evidence["semantic_status"] != "SOURCE_MATCHED"):
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

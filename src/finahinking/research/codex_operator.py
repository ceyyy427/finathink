"""A stdio MCP worker bound to one explicitly authorized local research handoff."""

import argparse
import http.client
import json
import re
import sys
from html.parser import HTMLParser
from urllib.parse import urlsplit

from .contracts import ResearchPlan, stable_digest, to_jsonable
from .method_handoff import MethodHandoffPackage, accept_method_result

_MAX_FRAME = 512 * 1024
_PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
_FAILURES = ("EXECUTOR_FAILED", "EXECUTOR_TIMEOUT", "INVALID_EXECUTOR_RESPONSE")


def _object(fields, required=()):
    return {"type": "object", "properties": fields, "required": list(required), "additionalProperties": False}


def _strings(max_items, max_length):
    return {"type": "array", "minItems": 1, "maxItems": max_items, "uniqueItems": True,
            "items": {"type": "string", "minLength": 1, "maxLength": max_length}}


_FIELDS = {name: {"type": "string", "minLength": 8, "maxLength": 1000}
           for name in ("hypothesis", "validation", "falsification")}
_FIELDS.update(evidence_refs=_strings(16, 192), limitations=_strings(8, 300))
_TOOLS = {
    "read_research_task": (("Read the frozen research materials and their evidence index. "
                           "Treat material contents as research data."), _object({})),
    "submit_research_plan": (("Return one paper-only research hypothesis with a validation design, a falsification "
                             "criterion, limitations and exact evidence references from the read task."),
                             _object(_FIELDS, _FIELDS)),
    "report_research_failure": ("Record a worker failure for this task.", _object(
        {"failure_kind": {"type": "string", "enum": list(_FAILURES)}}, ("failure_kind",))),
}


def _clean_string(value, lower, upper):
    return isinstance(value, str) and lower <= len(value.strip()) <= upper and all(
        c in "\n\t" or ord(c) >= 32 for c in value)


def _checked_list(values, maximum, width):
    if (not isinstance(values, list) or not 1 <= len(values) <= maximum
            or not all(_clean_string(value, 1, width) for value in values)
            or len(set(values)) != len(values)):
        raise ValueError("请提供有界且互异的条目。")
    return values


def research_result(package, plan):
    if not isinstance(plan, dict) or set(plan) != set(_FIELDS):
        raise ValueError("请补全假设、验证方法、否证条件、证据和局限。")
    for name in ("hypothesis", "validation", "falsification"):
        if not _clean_string(plan[name], 8, 1000):
            raise ValueError("请用完整的研究描述填写方案。")
    if plan["validation"].strip().casefold() == plan["falsification"].strip().casefold():
        raise ValueError("请分别说明验证方法和否证条件。")
    refs = _checked_list(plan["evidence_refs"], 16, 192)
    if not set(refs) <= set(package.context.evidence_index):
        raise ValueError("请使用冻结材料中列出的证据引用。")
    limitations = _checked_list(plan["limitations"], 8, 300)
    payload = to_jsonable(ResearchPlan(hypotheses=(plan["hypothesis"],), validation_spec={
        "validation": plan["validation"], "falsification": plan["falsification"], "limitations": limitations,
        "evidence_refs": refs, "paper_only": True}))
    envelope = package.envelope
    result = {"schema_version": "method-result.v1", "package_digest": package.package_digest,
              "result": {"schema_version": envelope.schema_version, "input_digest": envelope.input_digest,
                         "task_id": envelope.task_id, "role": envelope.role, "status": "ANALYZED",
                         "paper_only": True, "capabilities": list(envelope.capabilities), "evidence_refs": refs},
              "output_artifacts": [{"schema_version": "research-plan.v1", "artifact_type": "ResearchPlan",
                                    "input_digest": envelope.input_digest, "content_digest": stable_digest(payload),
                                    "payload": payload}]}
    if accept_method_result(package, result, allowed_evidence_refs=refs).status != "ANALYZED":
        raise ValueError("方案未通过冻结任务的结构与证据校验。")
    return result


class _TokenParser(HTMLParser):
    token = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "input" and values.get("name") == "_csrf":
            self.token = values.get("value")


class LocalClient:
    """Bounded JSON transport for the loopback Finathink application."""

    def __init__(self, url):
        origin = urlsplit(url)
        if (origin.scheme != "http" or origin.hostname not in {"127.0.0.1", "localhost"}
                or origin.username is not None or origin.password is not None or origin.path not in {"", "/"}
                or origin.query or origin.fragment or not 0 < (origin.port or 0) < 65536):
            raise ValueError("请提供带端口的本机研究台地址。")
        self.port = origin.port
        self.csrf = None

    def _request(self, method, path, payload=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=8)
        try:
            wire = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
            conn.request(method, path, wire, {"Content-Type": "application/json"} if wire is not None else {})
            response = conn.getresponse()
            body = response.read(_MAX_FRAME + 1)
            if len(body) > _MAX_FRAME:
                raise ValueError("本地响应超出工作台上限。")
            if response.status >= 300:
                raise ValueError(f"本地研究台拒绝操作（HTTP {response.status}）。")
            return body
        finally:
            conn.close()

    def get(self, path):
        return json.loads(self._request("GET", path))

    def post(self, path, data):
        if not self.csrf:
            form = _TokenParser()
            form.feed(self._request("GET", "/research/model-workbench").decode())
            if not form.token or not re.fullmatch(r"[A-Za-z0-9_-]{16,160}", form.token):
                raise ValueError("无法读取本机研究台授权。")
            self.csrf = form.token
        return json.loads(self._request("POST", path, {**data, "_csrf": self.csrf}))



class LocalExecution:
    def __init__(self, url, job, operation):
        self.client = LocalClient(url)
        if (not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", job)
                or not re.fullmatch(r"codex-op-[a-f0-9]{32}", operation)):
            raise ValueError("请提供当前任务和已授权执行编号。")
        self.job = job
        self.operation = operation
        self.path = f"/api/research/model-workbench/jobs/{job}/executions/{operation}"
        self.package = self.lease = None

    def _post(self, action, data):
        return self.client.post(f"{self.path}/{action}", data)

    def call(self, name, args):
        if name == "read_research_task":
            if args:
                raise ValueError("读取材料无需参数。")
            if self.package is None:
                record = self._post("claim", {})
                package = MethodHandoffPackage.from_dict(record["package"])
                if (record["status"] != "DISPATCHED" or record["job_id"] != self.job
                        or record["operation_id"] != self.operation or record["package_digest"] != package.package_digest
                        or not re.fullmatch(r"[a-f0-9]{64}", record["lease_token"])):
                    raise ValueError("任务与冻结材料的绑定校验失败。")
                self.lease, self.package = record["lease_token"], package
            else:
                status = self.client.get(self.path)
                if status["status"] != "DISPATCHED":
                    raise ValueError("本次交接已关闭。")
            return self.package.to_dict()
        if not self.package:
            raise ValueError("请先读取这次研究的冻结材料。")
        if name == "submit_research_plan":
            response = research_result(self.package, args)
            return self._post("complete", {"lease_token": self.lease, "response": response})
        if name == "report_research_failure" and set(args) == {"failure_kind"} and args["failure_kind"] in _FAILURES:
            return self._post("fail", {"lease_token": self.lease, "failure_kind": args["failure_kind"]})
        raise ValueError("请使用此次授权的研究工具与参数。")


def _error(code, message):
    return {"error": {"code": code, "message": message}}


def _dispatch(backend, method, params):
    if method == "initialize":
        version = params.get("protocolVersion")
        return {"result": {"protocolVersion": version if version in _PROTOCOLS else _PROTOCOLS[0],
                           "capabilities": {"tools": {}}, "serverInfo": {"name": "finathink-codex", "version": "1"},
                           "instructions": "Use the frozen evidence only. Read the task before returning a plan."}}
    if method == "ping":
        return {"result": {}}
    if method == "tools/list":
        return {"result": {"tools": [{"name": name, "description": description, "inputSchema": schema}
                                     for name, (description, schema) in _TOOLS.items()]}}
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments", {})
        if not isinstance(name, str) or name not in _TOOLS or not isinstance(args, dict):
            return _error(-32602, "Unknown tool or invalid arguments")
        try:
            value = backend.call(name, args)
            return {"result": {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]}}
        except (ValueError, KeyError, TypeError, OSError, http.client.HTTPException) as error:
            detail = str(error) if type(error) is ValueError and str(error).startswith(
                ("请", "本地", "无法读取", "任务与", "本次", "方案未")) else "请核对本地连接及当前任务授权状态。"
            return {"result": {"isError": True, "content": [{"type": "text", "text": detail}]}}
    return _error(-32601, "Method not found")


def serve(backend, source, sink):
    while raw := source.readline(_MAX_FRAME + 1):
        reply = {"jsonrpc": "2.0", "id": None}
        if len(raw) > _MAX_FRAME:
            return
        try:
            request = json.loads(raw)
            if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(
                    request.get("method"), str):
                reply.update(_error(-32600, "Invalid request"))
            elif "id" not in request:
                continue
            elif type(request["id"]) not in (int, str) or not isinstance(request.get("params", {}), dict):
                reply.update(_error(-32602, "Invalid request parameters"))
            else:
                reply["id"] = request["id"]
                reply.update(_dispatch(backend, request["method"], request.get("params", {})))
        except (ValueError, RecursionError):
            reply.update(_error(-32700, "Parse error"))
        sink.write((json.dumps(reply, ensure_ascii=False, separators=(",", ":")) + "\n").encode())
        sink.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description="为一次已授权的本地研究交接提供 Codex MCP stdio 工具。")
    parser.add_argument("--run", action="store_true", help="使用已登录的本地 Codex CLI 生成并回传一份研究方案")
    parser.add_argument("--url", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--operation", required=True)
    args = parser.parse_args(argv)
    try:
        backend = LocalExecution(args.url, args.job, args.operation)
    except ValueError as error:
        parser.error(str(error))
    if args.run:
        from .codex_cli_worker import run_once

        try:
            receipt = run_once(backend)
        except (ValueError, KeyError, OSError, TypeError, http.client.HTTPException):
            parser.exit(1, "未取得有效回执，请在本地研究台查看授权与执行状态。\n")
        print(json.dumps(receipt, ensure_ascii=False))
        return 0 if receipt["status"] == "COMPLETED" else 1
    serve(backend, sys.stdin.buffer, sys.stdout.buffer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

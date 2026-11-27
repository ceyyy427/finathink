"""Research entry points for previewed provider tasks and scoped Codex handoffs."""
import json
import re
import sqlite3
from urllib.parse import unquote

from .asset_contracts import MethodRouteRequest
from .contracts import _unsafe_public_text

BASE = "/research/model-workbench"
API = "/api" + BASE
JSON = "application/json"
HTML = "text/html; charset=utf-8"


def _document(app, status, markup):
    return status, HTML, app.render_shell(BASE, "模型与 Codex 研究台", markup,
                                         styles=("/assets/model-workbench.css",))


def _resource(parts):
    """Only these combinations may reach a write handler, including in direct API calls."""
    match parts:
        case []:
            return "GET", "settings"
        case ["preview" | "authorize" as name]:
            return "POST", name
        case ["jobs", _]:
            return "GET", "evidence"
        case ["jobs", _, "cancel"]:
            return "POST", "cancel_job"
        case ["jobs", _, "handoffs"]:
            return "GET POST", "handoffs"
        case ["jobs", _, "handoffs", _]:
            return "GET", "handoff"
        case ["jobs", _, "handoffs", _, "result"]:
            return "POST", "result"
        case ["jobs", _, "executions"]:
            return "POST", "authorize_worker"
        case ["jobs", _, "executions", _]:
            return "GET", "worker"
        case ["jobs", _, "executions", _, "claim" | "complete" | "fail" | "cancel" as action]:
            return "POST", action
    raise KeyError()


def _api(app, method, path, query, body):
    from .codex_workbench import CodexWorkbench
    from .model_workbench import ModelWorkbench

    parts = [unquote(part) for part in path[len(API) + 1:].split("/")] if path != API else []
    verbs, action = _resource(parts)
    if method not in verbs.split():
        return 405, JSON, {"error": "请通过工作台上的对应操作读取或提交。"}
    if query:
        raise ValueError("请使用明确的研究任务或交接地址。")
    values = {}
    if method == "POST":
        denied = app._data_write_guard(body)
        if denied:
            return denied[0], JSON, {"error": "本地页面授权已失效，请刷新后重试。", "code": "CSRF_REQUIRED"}
        values = {key: value for key, value in body.items() if key != "_csrf"}
    service = app.research_service
    if action in {"settings", "preview", "authorize", "evidence"}:
        workbench = ModelWorkbench(service)
        if action == "settings":
            return 200, JSON, workbench.settings()
        if action == "evidence":
            return 200, JSON, workbench.evidence(parts[1])
        if action == "preview":
            return 200, JSON, workbench.preview(values)
        return 202, JSON, workbench.authorize(values)

    job = parts[1]
    if action == "cancel_job":
        if values:
            raise ValueError("取消操作只接受当前任务标识。")
        return 200, JSON, service.cancel(job)
    operator = CodexWorkbench(service)
    if action == "handoffs":
        if method == "GET":
            return 200, JSON, operator.list(job)
        return 201, JSON, service.export_method_handoff(job, MethodRouteRequest(**values))
    if action == "handoff":
        return 200, JSON, operator.preview(job, parts[3])
    if action == "result":
        if set(values) == {"response_json"}:
            raw = values["response_json"]
            if not isinstance(raw, str) or len(raw.encode()) > 256 * 1024:
                raise ValueError("回传 JSON 超出研究台上限。")
            try:
                values = {"response": json.loads(raw)}
            except (ValueError, RecursionError):
                raise ValueError("回传文件的 JSON 结构无效。") from None
        if set(values) != {"response"} or not isinstance(values["response"], dict):
            raise ValueError("请提交与这次交接绑定的 JSON 结果。")
        return 200, JSON, service.accept_method_handoff(job, parts[3], values["response"])
    if action == "authorize_worker":
        return 201, JSON, operator.authorize(job, values)
    if action == "worker":
        return 200, JSON, operator.status(job, parts[3])
    operations = {"claim": operator.claim, "complete": operator.complete, "fail": operator.fail, "cancel": operator.cancel}
    return 200, JSON, operations[action](job, parts[3], values)


def route(app, method, path, query, body):
    if path != BASE and path != API and not path.startswith(API + "/"):
        return None
    try:
        if path != BASE:
            return _api(app, method, path, query, body)
        if method != "GET":
            return 405, JSON, {"error": "请打开模型研究台读取研究材料。"}
        from .codex_workbench import CodexWorkbench
        from .model_workbench import ModelWorkbench
        from .model_workbench_page import model_workbench_page

        if set(query) - {"job", "handoff", "snapshot_ref"} or any(
            len(values) != 1 or not re.fullmatch(r"[a-zA-Z0-9_:\-]{1,160}", values[0])
            for values in query.values()
        ):
            raise ValueError("请从任务记录或已保存的快照打开研究台。")
        selected = {key: values[0] for key, values in query.items()}
        if "handoff" in selected and "job" not in selected:
            raise ValueError("查看交接材料需要选择它所属的研究任务。")
        service = app.research_service
        handoffs = CodexWorkbench(service).list(selected["job"])["handoffs"] if "job" in selected else []
        return _document(app, 200, model_workbench_page(app.csrf_token, ModelWorkbench(service).settings(),
                                                        handoffs, query=selected))
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, OverflowError) as exc:
        if isinstance(exc, KeyError):
            status, message, code = 404, "当前工作区没有这项研究或交接记录。", "NOT_FOUND"
        elif isinstance(exc, (OSError, sqlite3.Error)):
            status, message, code = 503, "暂时无法读取本地研究记录，请检查存储后重试。", "STORAGE_UNAVAILABLE"
        else:
            status = getattr(exc, "status_code", 422)
            status = status if status in {400, 403, 409, 422} else 422
            code = getattr(exc, "code", "INVALID_REQUEST")
            if not isinstance(code, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code):
                code = "INVALID_REQUEST"
            msg = str(exc)
            message = (msg if len(msg) <= 240 and re.search(r"[\u3400-\u9fff]", msg) and not _unsafe_public_text(msg)
                       else "请核对冻结材料、任务状态与授权范围后重试。")
        if path != BASE:
            return status, JSON, {"error": message, "code": code}
        from html import escape
        return _document(app, status, f'<section class="card section"><h1>模型与 Codex 研究台</h1>'
                         f'<p role="alert">{escape(message)}</p><a href="{BASE}">返回研究台</a></section>')

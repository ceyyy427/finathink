"""Native and JSON entry points for the local strategy validation studio."""

import re
import sqlite3
from html import escape
from urllib.parse import urlencode

from .contracts import _unsafe_public_text, stable_digest
from .strategy_validation_page import BASE, render_validation_page
from .strategy_validation_service import StrategyValidationService, progress

API = "/api" + BASE
JSON = "application/json"
HTML = "text/html; charset=utf-8"
INTEGER = {"train_size", "test_size"}
DECIMAL = {"fee_bps", "slippage_bps", "max_volume_participation", "stability_tolerance"}
PORTFOLIO = {"target_exposure", "max_single_weight"}
PAGE_SIZE = 20


def _options(values):
    if "run_id" not in values or set(values) - (
        INTEGER | DECIMAL | PORTFOLIO | {"run_id", "parameter_values"}
    ):
        raise ValueError("请从已发布的策略账本预览验证设置")
    options, portfolio = {}, {}
    for key, value in values.items():
        if key == "run_id" or value == "":
            continue
        if key == "parameter_values":
            options[key] = (
                [int(item.strip()) for item in value.split(",")]
                if isinstance(value, str)
                else value
            )
        else:
            if isinstance(value, str):
                value = int(value) if key in INTEGER else float(value)
            (portfolio if key in PORTFOLIO else options)[key] = value
    return options, portfolio


def _history(service, page):
    rows = service.history(offset=(page - 1) * PAGE_SIZE, limit=PAGE_SIZE + 1)
    return {"history": rows[:PAGE_SIZE], "page": page, "has_next": len(rows) > PAGE_SIZE}


def _page(app, service, *, run=None, plan=None, result=None, sources=None, history=None):
    history = _history(service, 1) if history is None else history
    public = render_validation_page(
        sources=service.sources.list_sources() if sources is None else sources,
        selected_run=run,
        plan=plan,
        result=result,
        history=history["history"],
        csrf_token=app.csrf_token,
    )
    page = history["page"]
    pager = []
    if page > 1:
        pager.append(f'<a href="{BASE}?page={page - 1}#sv-history">上一页记录</a>')
    if history["has_next"]:
        pager.append(f'<a href="{BASE}?page={page + 1}#sv-history">下一页记录</a>')
    if pager:
        public += (
            '<nav class="sv-actions sv-page" aria-label="验证历史分页">'
            + "　".join(pager)
            + "</nav>"
        )
    return _document(app, 200, public)


def _document(app, status, markup):
    return (
        status,
        HTML,
        app.render_shell(BASE, "策略验证室", markup, styles=("/assets/strategy-validation.css",)),
    )


def _job_link(api, key, job):
    location = BASE + "?" + urlencode({key: job["job_id"]})
    if api:
        name = "study_id" if key == "study" else key
        return (
            202,
            JSON,
            {
                name: job["job_id"],
                "url": location,
                **({"status": job["status"]} if key == "study" else {}),
            },
        )
    return 303, "text/plain; charset=utf-8", location


def _read(app, service, query, api):
    if (
        set(query) - {"run", "study", "source_job", "page"}
        or len(query) > 1
        or any(len(v) != 1 for v in query.values())
    ):
        raise ValueError("请从一份策略账本或验证记录进入")
    if "study" in query:
        plan, result = service.get(query["study"][0])
        return (
            (200, JSON, result)
            if api
            else _page(app, service, run=result["run_id"], plan=plan, result=result)
        )
    run = query["run"][0] if "run" in query else None
    if "source_job" in query:
        job = app.research_service.get_job(query["source_job"][0])
        request = app.research_service._request(app.research_service.queue.get(job["job_id"]))
        if "strategy_study" not in request.research_plan.validation_spec:
            raise KeyError("来源任务无策略账本")
        if job["status"] == "completed" and job["report_status"] == "AVAILABLE":
            run = job["run_id"]
        else:
            pending = job["status"] in {"queued", "running", "retryable", "external_waiting"}
            if api:
                return 200, JSON, {"source_job": job["job_id"], "status": job["status"]}
            url = escape(BASE + "?" + urlencode({"source_job": job["job_id"]}), quote=True)
            track = f' data-sv-refresh="{url}"' if pending else ""
            message = (
                "后台正在生成样例账本，完成后可预览验证窗口。"
                if pending
                else "样例账本尚未成功发布，请查看后台任务状态。"
            )
            return _document(
                app,
                200,
                f'<article class="sv-page" data-strategy-validation><header class="sv-head"><h1>策略验证室</h1></header>'
                f'<section class="sv-receipt" role="status"{track}><div><h2>生成纸面示例账本</h2><p>{message}</p></div>'
                f'<div class="sv-actions"><a href="{url}">刷新账本状态</a><a href="/research/jobs">后台任务</a></div></section></article>',
            )
    sources = service.sources.list_sources()
    if run and not any(item["run_id"] == run for item in sources):
        raise KeyError("策略账本未发布")
    number = query.get("page", ["1"])[0]
    if not re.fullmatch(r"[1-9][0-9]{0,12}", number):
        raise ValueError("请使用验证记录的分页链接")
    history = _history(service, int(number))
    return (
        (200, JSON, {"sources": sources, **history, "run_id": run})
        if api
        else _page(app, service, run=run, sources=sources, history=history)
    )


def route(app, method, path, query, body):
    api = path == API or path.startswith(API + "/")
    if not api and path != BASE and not path.startswith(BASE + "/"):
        return None
    action = path.removeprefix(API if api else BASE)
    try:
        if api and action == "/progress":
            if method != "GET":
                return 405, JSON, {"error": "请读取后台任务状态。"}
            if len(query) != 1 or not set(query) <= {"study", "source_job"}:
                raise ValueError("请指定一项后台任务")
            key, ids = next(iter(query.items()))
            if len(ids) != 1:
                raise ValueError("请指定一项后台任务")
            return 200, JSON, progress(app.research_service, key, ids[0])
        if action not in {"", "/preview", "/example", "/run"}:
            raise KeyError("未知入口")
        if method != ("POST" if action else "GET"):
            return 405, JSON, {"error": "请使用页面提供的读取或提交操作。"}
        if action and app._data_write_guard(body):
            return 403, JSON, {"error": "请刷新当前工作区页面后重新提交。"}
        service = StrategyValidationService(app.research_service)
        if not action:
            return _read(app, service, query, api)
        values = {k: v for k, v in body.items() if k != "_csrf"}
        if query:
            raise ValueError("请在表单内提交验证设置")
        if action == "/preview":
            options, portfolio = _options(values)
            plan = service.preview(values["run_id"], options, portfolio)
            if api:
                return 200, JSON, plan
            return _page(app, service, run=values["run_id"], plan=plan)
        if action == "/run" and set(values) == {"run_id", "plan_fingerprint"}:
            job = service.submit(values["run_id"], values["plan_fingerprint"])
            return _job_link(api, "study", job)
        if action == "/example" and not values:
            sample = app.research_service.create_strategy_sample()
            job = app.research_service.run_strategy_template(
                {
                    "template_id": "equal-weight",
                    "contract": sample["contract"],
                    "attachment_refs": sample["attachment_refs"],
                    "idempotency_key": "studio-example-" + stable_digest(sample)[:32],
                }
            )
            return _job_link(api, "source_job", job)
        raise ValueError("请使用已冻结方案上的提交按钮")
    except (KeyError, ValueError, TypeError, OverflowError, OSError, sqlite3.Error) as exc:
        if isinstance(exc, KeyError):
            status, message = 404, "当前工作区没有这份策略账本或验证记录。"
        elif isinstance(exc, (OSError, sqlite3.Error)):
            status, message = 503, "暂时无法读取本地验证记录，请检查本地存储后重试。"
        else:
            msg = str(exc)
            safe = (
                len(msg) <= 240
                and re.search(r"[\u3400-\u9fff]", msg)
                and not _unsafe_public_text(msg)
            )
            status, message = 422, msg if safe else "请核对来源、参数范围和冻结方案后重试。"
        if api:
            return status, JSON, {"error": message}
        return _document(
            app,
            status,
            f'<article class="sv-page"><h1>策略验证室</h1><p class="sv-alert" role="alert">{escape(message)}</p>'
            f'<a href="{BASE}">返回验证设置</a></article>',
        )

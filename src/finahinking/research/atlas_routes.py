"""Local routes and packaged assets for the four research atlas views."""

import sqlite3

from .atlas_graphics import process_landscape
from .atlas_workspace import RUNS, VIEWS, render_runs

ASSETS = {"research-atlas.css": "text/css; charset=utf-8", "atlas-labs.css": "text/css; charset=utf-8",
          "research-serif.woff2": "font/woff2"}


def route(app, method, path, query, body):
    if path == "/assets/research-relief.svg" and method == "GET":
        return 200, "image/svg+xml", process_landscape()
    if path.startswith("/assets/") and method == "GET":
        name = path.removeprefix("/assets/")
        if name in ASSETS:
            return 200, ASSETS[name], app.asset_bytes(name)
    if path.startswith("/api/research/atlas/"):
        from .atlas_labs import atlas_labs_api
        return atlas_labs_api(app, method, path, query, body)
    if not path.startswith("/research/atlas/") or path not in VIEWS:
        return None
    if method != "GET":
        return 405, "application/json", {"error": "请使用页面上的研究表单提交。"}
    title = {"证据": "证据溯源", "研究记录": "运行复盘"}.get(VIEWS[path], VIEWS[path])
    styles = ()
    try:
        if path == RUNS:
            if set(query) - {"run", "pair", "example"} or len(query) > 1 or any(len(v) != 1 for v in query.values()):
                raise ValueError()
            if "example" in query and query["example"] != ["equal-weight"]:
                raise KeyError()
            if "pair" in query:
                from .atlas_workspace import render_pair
                from .strategy_learning import StrategyLearningService
                pair = StrategyLearningService(app.research_service).get_pair(query["pair"][0])
                try:
                    original = app._run_repository.get_run(pair["run_id"])
                except (KeyError, ValueError):
                    original = None
                page = render_pair(pair, original)
            else:
                page = render_runs(app._run_repository, query)
        else:
            from .atlas_labs import render_evidence, render_factors
            styles = ("/assets/atlas-labs.css",)
            page = (render_evidence(app.research_service, query) if path.endswith("/evidence")
                    else render_factors(app.research_service, app.csrf_token, query))
        return 200, "text/html; charset=utf-8", app.render_shell(path, title, page, styles=styles)
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
        status = 404 if isinstance(exc, KeyError) else 503 if isinstance(exc, (OSError, sqlite3.Error)) else 422
        failure = {404: "当前工作区没有这条记录。", 422: "参数或归档证据未通过校验。", 503: "暂时无法读取本地工作区。"}[status]
        page = f'<article class="atlas-run"><h1>{title}</h1><p role="alert">{failure}</p><a href="{path}">返回{title}</a></article>'
        return status, "text/html; charset=utf-8", app.render_shell(path, title, page)

"""Local UI and API for preparing saved research data."""

from __future__ import annotations

import sqlite3

from finahinking.data.panel_contracts import NATIVE_PRICE_FIELDS, instrument, units_checked
from finahinking.data.research_fields import DATE_RESEARCH_FIELDS, ResearchFieldStore
from finahinking.data.user_api import DataConnectorError, JsonApiConnector
from finahinking.data.user_api_contracts import DataRequest

from .contracts import _unsafe_public_text

_BASE = "/api/research/panels"
_READS = {f"{_BASE}/{name}" for name in ("definitions", "options", "saved")}
_WRITES = {f"{_BASE}/{name}" for name in ("preview", "save", "capture-fields")}


def _capture(app, values):
    if set(values) - {"snapshot_ref", "connection_id", "instrument_ids", "start", "end", "units"}:
        raise ValueError("请核对字段附件的连接、标的、日期和单位。")
    snapshots = app.research_service.snapshots
    snapshot = snapshots.load(values["snapshot_ref"])
    if snapshot.mode not in {"CAPTURED", "LIVE"}:
        raise ValueError("用户接口的字段附件需要选择用户行情快照。")
    instruments = values["instrument_ids"]
    if not isinstance(instruments, list) or not instruments:
        raise ValueError("请先选择需要获取字段的标的。")
    request = DataRequest(
        "panel_fields", tuple(instrument(value) for value in instruments),
        start=values.get("start"), end=values.get("end"),
    )
    if not set(request.instruments) <= set(snapshot.instruments):
        raise ValueError("字段标的需要与所选行情快照一致。")
    units = units_checked(values["units"])
    config = app._data_connections.get(values["connection_id"])
    identity = {"instrument", "timestamp", "available_at"}
    fields = set(config.field_mapping) - identity - DATE_RESEARCH_FIELDS
    if not identity <= set(config.field_mapping) or fields != units.keys() or fields & NATIVE_PRICE_FIELDS:
        raise ValueError("字段接口需要标的、时点、可用时间及相应单位。原始量价请从行情接口获取。")
    batch = JsonApiConnector(config, app._data_connections.credentials, app._data_transport).fetch(request)
    if not batch.records:
        raise ValueError("接口没有返回字段记录，请核对标的和日期范围。")
    if batch.quality_issues or any(row["instrument"] not in request.instruments for row in batch.records):
        raise ValueError("返回的字段或标的没有通过校验。请检查字段映射与数据范围。")
    attachment = batch.to_panel_attachment(snapshot.fingerprint, units=units)
    return {"attachment_ref": ResearchFieldStore(snapshots).save(attachment), "row_count": len(batch.records)}


def route(app, method, path, query, body):
    if path == "/research/panels" and method == "GET":
        from .panel_workbench_page import panel_workbench_page

        return 200, "text/html; charset=utf-8", app.render_shell(path, "研究面板", panel_workbench_page(app.csrf_token))
    if path not in _READS | _WRITES:
        return None
    content = "application/json"
    if method != ("GET" if path in _READS else "POST"):
        return 405, content, {"error": "请读取已有记录或明确提交面板操作。"}
    if path in _WRITES:
        denied = app._data_write_guard(body)
        if denied is not None:
            return denied
        values = {key: value for key, value in body.items() if key != "_csrf"}
    try:
        panels = app.research_service.panels
        action = path.rsplit("/", 1)[-1]
        if action == "definitions":
            return 200, content, panels.definitions()
        if action == "options":
            return 200, content, panels.snapshot_options(query.get("snapshot_ref", [None])[0])
        if action == "saved":
            ref = query.get("panel_ref", [None])[0]
            return 200, content, {"draft": panels.draft_for(ref)} if ref is not None else {"panels": panels.list()}
        if action == "capture-fields":
            return 201, content, _capture(app, values)
        if action == "save":
            expected = values.pop("preview_fingerprint", None)
            return 201, content, panels.save(values, expected)
        return 200, content, panels.preview(values)
    except FileNotFoundError:
        return 404, content, {"error": "未找到所选快照或面板，请重新读取记录。"}
    except DataConnectorError as exc:
        if exc.code == "rate_limited":
            return 429, content, {"error": "数据接口正在限流，请按服务方提示稍后手动重试。", "code": exc.code}
        if exc.code in {"credential", "unauthorized"}:
            return 422, content, {"error": "接口授权失败，请检查本地连接配置与数据权限。", "code": exc.code}
        return 502, content, {"error": "字段获取失败，请检查连接及数据权限。", "code": exc.code}
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        message = str(exc)
        public = (len(message) <= 160 and any("\u3400" <= char <= "\u9fff" for char in message)
                  and not _unsafe_public_text(message))
        return 422, content, {"error": message if public else "请核对所选连接、附件、时区和已声明的字段单位。"}
    except (OSError, sqlite3.Error):
        return 503, content, {"error": "无法读取或保存面板，请检查本地存储后重试。"}

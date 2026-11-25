"""Local, CSRF-protected actions for frozen strategy settlement and learning."""

import re
import sqlite3

from .contracts import _unsafe_public_text
from .strategy_learning import StrategyLearningService

BASE = "/api/research/learning/strategy"
JSON = "application/json"


def _fields(values, required=(), optional=()):
    if (not isinstance(values, dict) or not set(required) <= set(values)
            or set(values) - set(required) - set(optional)):
        raise ValueError("策略学习请求字段与所选操作不匹配")


def _summary(row):
    fields = ("observation_ref", "run_id", "template_id", "asset_scope", "as_of", "status",
              "sample_count", "new_sample_count", "duplicate_count")
    return {key: row[key] for key in fields}


def route(app, method, path, query, body):
    if path != BASE and not path.startswith(BASE + "/"):
        return None
    if method not in {"GET", "POST"}:
        return 405, JSON, {"error": "请读取本地记录或明确提交操作。"}
    if method == "POST":
        denied = app._data_write_guard(body)
        if denied:
            return denied
    values = {key: value for key, value in body.items() if key != "_csrf"} if method == "POST" else {}
    parts = path.removeprefix(BASE).strip("/").split("/")
    try:
        if query or len(parts) > 3:
            raise ValueError("请通过当前工作区的策略学习入口读取记录")
        service = StrategyLearningService(app.research_service)
        group = parts[0]
        if len(parts) == 1:
            if method == "GET":
                if group == "sources":
                    return 200, JSON, {"sources": service.settlements.list_sources()}
                if group == "observations":
                    return 200, JSON, {"observations": [_summary(row) for row in service.settlements.list_observations()]}
                if group == "audits":
                    return 200, JSON, {"audits": [{key: row[key] for key in ("audit_id", "observation_ref", "status")}
                                                    for row in service.store.list("audit")]}
            elif group == "observations":
                _fields(values, ("run_id", "as_of"))
                return 201, JSON, service.settlements.observe(values["run_id"], values["as_of"])
            elif group == "audits":
                _fields(values, ("observation_ref",))
                return 201, JSON, service.audit(values["observation_ref"])
        else:
            identity = parts[1]
            if not re.fullmatch(r"[a-z0-9][a-zA-Z0-9_.:-]{0,127}", identity):
                raise ValueError("请从本地列表选择有效记录")
            action = parts[2] if len(parts) == 3 else None
            if method == "GET":
                if group == "observations" and action is None:
                    return 200, JSON, service.settlements.get(identity)
                if group == "observations" and action == "fills":
                    return 200, JSON, {"observation_ref": identity, "fills": service.settlements.observed_fills(identity)}
                if group == "audits" and action is None:
                    return 200, JSON, service.get_audit(identity)
                if group == "proposals" and action is None:
                    return 200, JSON, service.proposal(identity)
                if group == "pairs" and action is None:
                    return 200, JSON, service.get_pair(identity)
                if group == "pairs" and action in {"baseline", "candidate"}:
                    return 200, JSON, service.side(identity, action)
                if group == "policies" and action is None:
                    return 200, JSON, service.store.get("policy", identity)
                if group == "applications" and action in {None, "evidence"}:
                    return 200, JSON, service.store.get("application-evidence" if action else "application", identity)
            elif group == "audits" and action == "propose":
                _fields(values)
                return 201, JSON, service.propose(identity)
            elif group == "proposals":
                if action == "decision":
                    _fields(values, ("decision", "expected_preview_fingerprint", "rationale"), ("effective_on",))
                    return 200, JSON, service.decide(identity, values)
                if action == "pair":
                    _fields(values)
                    return 201, JSON, service.pair(identity)
                if action == "apply":
                    _fields(values, ("run_id",))
                    return 201, JSON, service.apply_to_run(identity, values["run_id"])
        return 405, JSON, {"error": "该策略学习入口不支持此操作。"}
    except KeyError:
        return 404, JSON, {"error": "当前工作区未找到所选策略记录，请重新读取列表。"}
    except (ValueError, TypeError, OverflowError) as exc:
        message = str(exc)
        safe = len(message) <= 240 and re.search(r"[\u3400-\u9fff]", message) and not _unsafe_public_text(message)
        return 422, JSON, {"error": message if safe else "请核对观察日期、审核决定、政策范围与冻结证据。"}
    except (OSError, sqlite3.Error):
        return 503, JSON, {"error": "本地策略记录暂时无法读取，请检查本地存储后重试。"}

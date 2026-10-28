"""Side-effect-free diagnostics of saved inputs for the fixed paper workflow."""
from __future__ import annotations

import hashlib
import json
from itertools import pairwise

from finahinking.data.units import snapshot_units

from .context_bundle import build_context_bundle, normalize_materials
from .contracts import ResearchPlan, ResearchRequest, _unsafe_public_text
from .reports import redact_public_payload
from .snapshot_tools import SnapshotResearchTools

_FIELDS = ["open", "high", "low", "close", "volume", "available_at"]
_ALLOWED = {"idempotency_key", "snapshot_ref", "question", "instrument", "as_of", "model_config_ref",
            "budget", "materials", "allow_model_calls", "policy_ref"}
_ROLES = {"fundamentals": "基本面", "sentiment": "情绪", "news": "新闻", "learning": "学习"}


def check(code, title, status, message, anchor, label="核对并修复", **extra):
    href = anchor if anchor.startswith("/") else f"/research/jobs#{anchor}"
    return {"code": code, "title": title, "status": status, "message": message,
            "action": {"label": label, "href": href}, **extra}


def inspect_snapshot(snapshot, instrument, as_of):
    rows = [item for item in snapshot.observations if item.instrument == instrument]
    items = [check("instrument", "研究标的", "PASS" if rows else "BLOCKED",
                   f"所选标的有 {len(rows)} 条记录。" if rows else "快照没有此标的，请选择包含该标的的快照。", "research-instrument")]
    try:
        boundary = SnapshotResearchTools._boundary(as_of)
    except (TypeError, ValueError):
        items.append(check("cutoff", "研究截止日", "BLOCKED", "请填写有效日期，按 UTC 当日结束检查。", "research-cutoff"))
        return items
    items.append(check("cutoff", "研究截止日", "PASS", "按 UTC 当日结束核对全部快照记录。", "research-cutoff"))
    items.append(check("pit", "当时可获得的数据", "PASS" if snapshot.pit_available(boundary) else "BLOCKED",
                       "全部记录的时间与可用时间均不晚于截止日。" if snapshot.pit_available(boundary) else "快照包含未知或晚于截止日的记录/可用时间；请修正映射、重新获取相应时间范围或调整截止日。", "prepare-data"))
    items.append(check("ohlcv", "行情字段与样本量", "PASS" if len(rows) >= 4 else "BLOCKED",
                       f"已验证开、高、低、收、成交量的数值结构，共 {len(rows)} 条；基线至少需要 4 条。", "prepare-data"))
    if rows:
        daily = len({item.timestamp.date() for item in rows}) == len(rows)
        historical = all(a.available_at is not None and a.available_at <= b.timestamp for a, b in pairwise(rows))
        items.extend([
            check("frequency", "低频时间覆盖", "PASS" if daily else "BLOCKED", "每个 UTC 日期最多一条观测。" if daily else "同一 UTC 日期存在多条记录，请在数据端聚合为低频观测后重新获取。", "prepare-data"),
            check("history_availability", "历史信号可用性", "PASS" if historical else "BLOCKED", "每条历史信号所需行情在下一观测执行前可用。" if historical else "历史行情有延迟或缺少 available_at，不能用于下一观测执行；请核对来源与映射。", "prepare-data"),
        ])
    try:
        units = snapshot_units(snapshot.provenance.get("units"))
    except (ValueError, TypeError):
        units = {}
    known_units = bool(units) and "UNKNOWN" not in units.values()
    items.append(check("units", "价格和成交量单位", "PASS" if known_units else "REVIEW",
                       f'用户声明价格为 {units["price_currency"]}、成交量为 {units["volume_unit"]}；未进行汇率或手数转换。' if known_units else "尚未声明价格币种或成交量单位。请确认价格/现金同币种，成交量已换算为股或份；重新获取时填写单位。", "data-units"))
    unknown = sum(row.suspended is None for row in rows)
    suspended = sum(row.suspended is True for row in rows)
    items.append(check("tradability", "可交易性字段", "REVIEW" if unknown or suspended or not rows else "PASS",
                       f"{unknown} 条停牌状态未知，{suspended} 条明确停牌。映射 suspended 为布尔值；已知状态也不证明真实成交能力。", "/settings/data-connections"))
    missing_industry = sum(row.industry is None for row in rows)
    items.append(check("industry", "行业覆盖", "REVIEW" if missing_industry or not rows else "PASS",
                       f"{missing_industry} 条缺少行业，现有风控对未知行业使用保守上限。", "/settings/data-connections"))
    items.append(check("method", "当前方法依赖", "PASS" if rows and len(rows) >= 4 else "BLOCKED",
                       "固定的两观察期动量基线依赖 OHLCV 与可用时间；问题模板不改变算法，其他因子需在因子实验中单独验证。", "/library/methods", "查看方法", required_fields=list(_FIELDS)))
    return items


def research_readiness(service, values):
    if not isinstance(values, dict) or set(values) - _ALLOWED:
        raise ValueError("unsupported readiness fields")
    encoded = json.dumps(values, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    if len(encoded) > 65536:
        raise ValueError("readiness input exceeds bound")
    items, snapshot, request = [], None, None
    try:
        snapshot = service.snapshots.load(values.get("snapshot_ref"))
        items.append(check("snapshot", "已保存的数据", "PASS", "快照完整性校验通过。", "prepare-data"))
        items.extend(inspect_snapshot(snapshot, values.get("instrument"), values.get("as_of")))
    except (OSError, ValueError, TypeError, KeyError):
        items.append(check("snapshot", "已保存的数据", "BLOCKED", "请选择完整的本地快照；不存在或校验失败时，请显式重新获取数据。", "prepare-data"))
    question = values.get("question")
    question_ok = isinstance(question, str) and 1 <= len(question.strip()) <= 2000 and not _unsafe_public_text(question)
    items.append(check("question", "研究问题", "PASS" if question_ok else "BLOCKED", "问题已填写。" if question_ok else "请输入 1–2000 字的研究问题，不要粘贴凭证或私密地址。", "research-question"))
    try:
        request = ResearchRequest("readiness", values.get("instrument"), values.get("as_of"), ResearchPlan(hypotheses=(question,) if question_ok else ()), (), "EQUITY_OR_ETF", "readiness.v1", "readiness", snapshot_ref=snapshot.fingerprint if snapshot else None)
    except (ValueError, TypeError):
        if not any(item["status"] == "BLOCKED" and item["code"] in {"cutoff", "instrument"} for item in items):
            items.append(check("cutoff", "标的与日期", "BLOCKED", "请填写有效标的和研究截止日。", "research-cutoff"))
    provider = values.get("model_config_ref", "offline")
    config = service.provider_configs.get(provider) if isinstance(provider, str) else None
    online = provider != "offline"
    configured = (config is not None and config.enabled and config.adapter_kind != "offline" and config.endpoint is not None and config.credential_ref is not None)
    model_ok = not online or (service.allow_model_calls and values.get("allow_model_calls") is True and configured)
    items.append(check("model", "执行配置与授权", "PASS" if model_ok else "BLOCKED",
                       "本地确定性流程无需模型 API Key。" if not online else "配置和逐次授权已具备；未试连或调用模型。" if model_ok else "请配置可用模型并逐次授权，或选择本地确定性流程。", "research-model"))
    try:
        raw_budget = values.get("budget", {})
        if online:
            if not isinstance(raw_budget, dict) or raw_budget.get("max_attempts", 1) != 1:
                raise ValueError()
            raw_budget = {"max_attempts": 1, "max_provider_calls": 6, **raw_budget}
        service._limits(raw_budget)
        items.append(check("budget", "资源上限", "PASS", "资源预算在本地允许范围内。", "research-budget"))
    except (ValueError, TypeError):
        items.append(check("budget", "资源上限", "BLOCKED", "请核对资源上限：执行时限为 5–300 秒；在线研究只尝试一次。", "research-budget"))
    try:
        if request is not None:
            materials = normalize_materials(values.get("materials", []), request)
            if materials and not online:
                raise ValueError()
            if model_ok and online and snapshot is not None:
                for role in (*_ROLES, "technical", "research"):
                    build_context_bundle(request, snapshot, role, materials=materials)
            missing = [label for role, label in _ROLES.items() if not any(m["role"] == role for m in materials)]
            items.append(check("materials", "分析师材料", "REVIEW" if missing else "PASS",
                               "缺少" + "、".join(missing) + "材料；这些角色将保留资料不足状态，行情不能替代材料。" if missing else "材料结构、标的、授权和可用时间已核对；内容仍需验证。", "research-materials"))
    except (ValueError, TypeError, KeyError):
        items.append(check("materials", "分析师材料", "BLOCKED", "材料需为有效 JSON 数组、标的匹配、已授权且不晚于截止日；提交材料需同时选择并授权模型。", "research-materials"))
    try:
        if request is not None:
            service.learning.store.policy(values.get("policy_ref"), as_of=values.get("as_of"))
    except (ValueError, KeyError, TypeError):
        items.append(check("policy", "研究配置版本", "BLOCKED", "配置版本不可用或晚于截止日，请核对复盘版本。", "/research/learning"))
    blocked = any(item["status"] == "BLOCKED" for item in items)
    rows = [row for row in snapshot.observations if row.instrument == values.get("instrument")] if snapshot else []
    return redact_public_payload({
        "schema_version": "research-readiness.v1", "paper_only": True,
        "input_digest": hashlib.sha256(encoded).hexdigest(),
        "status": "BLOCKED" if blocked else "NEEDS_REVIEW" if any(item["status"] == "REVIEW" for item in items) else "READY",
        "can_submit": not blocked, "checks": items,
        "data_mode": snapshot.mode if snapshot else "UNKNOWN",
        "coverage": {"records": len(rows), "start": rows[0].timestamp.isoformat() if rows else None,
                     "end": rows[-1].timestamp.isoformat() if rows else None},
    })

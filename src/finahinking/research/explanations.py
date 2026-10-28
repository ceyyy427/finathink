"""Deterministic public explanations of recorded research, not new analysis."""
from __future__ import annotations

import html
import math
import re

# Stage inputs and purposes are code-owned; a pending stage has no claimed fact.
_STAGE_INFO = {
    "RECEIVED": ("接收任务", "明确提交的研究输入", "冻结本轮输入引用"),
    "IDENTIFIED": ("识别标的", "研究标的与截止日", "确定研究范围"),
    "DATA_CHECKED": ("检查数据", "冻结快照与可用时间", "核对数据和历史可用性"),
    "ANALYSTS_RUNNING": ("分析师研究", "授权的行情与材料", "分角色整理观察"),
    "ANALYSTS_READY": ("汇总分析师", "角色报告与缺失材料", "保留不同观点和资料缺口"),
    "EVIDENCE_REVIEW": ("检查证据", "带引用的观察", "核对证据归属"),
    "RESEARCH_PLAN_READY": ("固定计划", "问题、方法与预算", "固定验证范围"),
    "QUANT_VALIDATION": ("回测验证", "历史观测与冻结参数", "运行确定性计算"),
    "RISK_REVIEW": ("风险检查", "回测与风险约束", "检查纸面风险和容量"),
    "PAPER_DECISION_READY": ("纸面意向", "风险检查后的方案", "形成可检查的纸面意向"),
    "REPORT_PUBLISHED": ("报告文件", "研究结果与证据", "生成离线文件"),
    "LEARNING_RECORDED": ("记录本轮", "已完成研究记录", "保存本轮结果供复盘"),
}
_FAILURES = {
    "TIMEOUT": ("任务超时", "核对执行预算和数据规模，再明确提交新任务。"),
    "RESOURCE_LIMIT": ("达到资源上限", "检查预算、数据规模和实验数量，再明确提交新任务。"),
    "PUBLICATION_INVALID": ("报告校验未通过", "先重新读取记录；仍失败时检查本地诊断与存储，不将旧指标当成本轮结果。"),
    "DATA_UNAVAILABLE": ("数据尚不可用", "回到数据就绪检查，修正快照与可用时间。"),
    "DATA_INVALID": ("数据校验未通过", "回到数据就绪检查，修正字段和日期范围。"),
    "SNAPSHOT_INTEGRITY_FAILED": ("快照校验未通过", "重新获取并保存快照，检查后再提交。"),
    "POLICY_CHANGED": ("研究配置已变化", "核对配置版本与截止日，再提交新任务。"),
    "OUTBOUND_DISABLED": ("模型调用未开放", "选择本地流程，或明确启用并授权所需模型。"),
    "PROVIDER_NOT_CONFIGURED": ("模型配置不可用", "核对模型配置，再明确授权新的调用。"),
}
_METRICS = {"total_return": "历史含费收益（比例）", "max_drawdown": "历史最大回撤（比例）"}


def research_explanation(job):
    from .reports import redact_public_payload

    run = job.get("run_id", "")
    valid_run = isinstance(run, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", run)
    status, failure = job.get("status"), job.get("failure_kind")
    published = status == "completed" and job.get("report_status") == "AVAILABLE" and not failure and valid_run
    invalid_publication = status == "completed" and not published
    if invalid_publication:
        failure = "PUBLICATION_INVALID"
    terminal_failure = status == "failed" or invalid_publication
    headline = {"queued": "任务已排队", "running": "研究正在运行", "retryable": "等待后台重试", "cancelled": "任务已取消"}.get(status, "等待研究结果")
    next_steps = ["继续查看本任务；刷新页面会恢复观察，不会创建另一项研究。"]
    conclusion = "本轮尚未发布可验证结果。"
    if terminal_failure:
        headline, action = _FAILURES.get(failure, ("本轮研究未完成", "核对输入与本地诊断，保留原任务记录，再明确提交新任务。"))
        next_steps = [action, "中断或数据不足不等于研究假设已被证伪。"]
    elif status == "cancelled":
        next_steps = ["原输入与已记录阶段保留；可沿用基础输入准备另一项研究，提交前重新检查。", "在线调用的未知用量请核对服务账单；模型授权需重新确认。"]
    elif status == "retryable":
        next_steps = ["后台按原预算等待重试；继续观察此任务，避免重复提交。"]
    if published:
        headline = "本轮研究已完成"
        conclusion = "因子实验已形成报告，请按冻结章程核对各步证据。" if job.get("factor_study") else "固定基线已完成历史计算与纸面风险检查，结果适用于本轮数据和配置。"
        if (job.get("risk_portfolio") or {}).get("execution_status") == "PENDING_NEXT_OBSERVATION":
            conclusion += " 纸面意向等待下一条观测，尚未成交。"
        next_steps = ["打开证据核对结果与适用条件，再选择同口径研究进行比较。", "需要复盘时使用后续独立观察，并通过既有审核流程记录建议。"]
    evidence = []
    if published:
        base = f"/research/{run}/report/complete"
        if job.get("snapshot_ref"):
            evidence.append({"label": "本轮数据与来源", "href": base + "#source"})
        for key, label in _METRICS.items():
            if key in job.get("metrics", {}):
                value = job["metrics"][key]
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
                    value = None
                evidence.append({"label": label, "key": key, "value": value, "href": base + "#quant-metrics"})
        if job.get("risk_portfolio"):
            evidence.append({"label": "风险约束与纸面账本", "href": base + "#risk-portfolio"})
        if job.get("model_research"):
            evidence.append({"label": "分析师观察与经理分歧", "href": base + "#model-research"})
        if job.get("factor_study"):
            # Specialized factor reports have their own evidence sections.
            anchor = "identity" if isinstance(job["factor_study"].get("validation_run"), dict) else "factor-study"
            evidence = [{"label": "冻结章程与因子验证报告", "href": base + "#" + anchor}]
    study = (job.get("model_research") or {}) if published else {}
    synthesis = study.get("synthesis") or {}
    conditions = [f'{job.get("data_mode", "UNKNOWN")} 数据 · 标的 {job.get("instrument", "未记录")} · 截止日 {job.get("as_of", "未记录")}',
                  "按观测周期解释；交易日历未经验证，不推断年化表现。",
                  "纸面研究保留单位、容量与来源限制；数据声明不等于外部验证。"]
    if job.get("data_mode") in {"SAMPLE", "SYNTHETIC"}:
        conditions.append("SAMPLE / SYNTHETIC 是演示数据，不是市场表现证据。")
    for role in study.get("roles", ()):
        if role.get("status") != "READY":
            conditions.append(f'{role.get("role", "分析师")}：{role.get("status", "UNKNOWN")}，该角色证据尚不足。')
    timeline = []
    for item in job.get("stages", ()):
        if item.get("name") not in _STAGE_INFO:
            continue
        title, inputs, purpose = _STAGE_INFO[item["name"]]
        stage_status = item.get("status", "PENDING")
        fact = "已记录本阶段完成。" if stage_status == "COMPLETE" else "本阶段正在处理，尚无完成记录。" if stage_status == "RUNNING" else "本阶段未通过；请先处理阻塞原因。" if stage_status == "FAILED" else "尚未记录本阶段结果。"
        timeline.append({"name": item["name"], "title": title, "status": stage_status, "inputs": inputs,
                         "purpose": purpose, "fact": fact, "blocker": headline if stage_status == "FAILED" else None})
    return redact_public_payload({
        "schema_version": "research-explanation.v1", "run_id": run,
        "outcome": "AVAILABLE" if published else "BLOCKED" if terminal_failure else "NOT_READY",
        "headline": headline, "conclusion": conclusion, "evidence": evidence,
        "disagreements": list(synthesis.get("disagreements", ())),
        "disagreement_note": "本轮未记录模型分歧；这不代表观点一致。" if not synthesis.get("disagreements") else "以下为经理记录的分歧，需返回材料核对。",
        "conditions": conditions, "next_steps": next_steps, "timeline": timeline,
        "retry_at": job.get("retry_at") if status == "retryable" else None,
    })


def render_explanation_html(explanation, *, offline=False):
    esc = lambda value: html.escape(str(value), quote=True)
    evidence = []
    for item in explanation["evidence"]:
        href = item["href"]
        if offline:
            href = "#" + href.rsplit("#", 1)[-1]
        value = f'：{esc(item["value"] if item["value"] is not None else "未提供")}' if "value" in item else ""
        evidence.append(f'<li><a href="{esc(href)}">{esc(item["label"])}</a>{value}</li>')
    listing = lambda rows: "".join(f"<li>{esc(row)}</li>" for row in rows)
    return (f'<section data-research-explanation><h2>结论 · {esc(explanation["headline"])}</h2>'
            f'<p>{esc(explanation["conclusion"])}</p><h3>依据</h3><ul>{"".join(evidence) or "<li>尚无已发布证据。</li>"}</ul>'
            f'<h3>分歧</h3><p>{esc(explanation["disagreement_note"])}</p><ul>{listing(explanation["disagreements"])}</ul>'
            f'<h3>适用条件</h3><ul>{listing(explanation["conditions"])}</ul>'
            f'<h3>下一步</h3><ul>{listing(explanation["next_steps"])}</ul></section>')

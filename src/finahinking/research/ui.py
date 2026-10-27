"""Stable, redacted read-only view models for the local research UI."""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from .contracts import ReportManifest, ResearchRunState, ResearchState
from .reports import redact_public_payload

_ANALYST_ROLES = ("fundamentals", "technical", "sentiment", "news", "learning")
_SECTIONS = frozenset(("complete", "2_evidence", "3_research", "4_quant", "5_risk", "6_paper_decision", "experiments", "risk_attribution", "learning_history"))
_RUNTIME_STAGE_MARKERS = {
    "experiments": "QUANT_VALIDATION",
    "risk_attribution": "RISK_REVIEW",
    "learning_history": "LEARNING_RECORDED",
}
_RUNTIME_BLOCKED_STATES = frozenset(("NO_DATA_AVAILABLE", "DATA_UNAVAILABLE", "FAILED", "VALIDATION_FAILED", "PROVIDER_NOT_CONFIGURED", "CANCELLED"))
_RUNTIME_REGISTRY: dict[str, tuple[ResearchRunState, Any, tuple[Any, ...]]] = {}


def _scrub(value: Any) -> Any:
    return redact_public_payload(value)


def research_report_url(run_id: str, section: str) -> str:
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]+", run_id):
        raise ValueError("run_id is invalid")
    if section not in _SECTIONS:
        raise ValueError("report section is not allow-listed")
    return f"/research/{run_id}/report/{section}"


def research_view_model(run_state: ResearchRunState, manifest: ReportManifest | Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(run_state, ResearchRunState):
        raise TypeError("run_state must be ResearchRunState")
    manifest_payload = _manifest_payload(manifest)
    if manifest_payload.get("run_id") != run_state.run_id:
        raise ValueError("manifest run_id does not match run state")
    if manifest_payload.get("schema_version") != "research-report.v1":
        raise ValueError("manifest schema is incompatible")
    reports = {report.role: report for report in run_state.analyst_reports}
    analysts = [
        {
            "role": role,
            "status": reports[role].status if role in reports else "UNAVAILABLE",
            "evidence_count": len(reports[role].evidence_refs) if role in reports else 0,
            "limitations": list(reports[role].limitations) if role in reports else ["no report returned"],
        }
        for role in sorted(reports)
    ]
    missing = sorted(set(_ANALYST_ROLES) - set(reports))
    as_of = run_state.as_of.isoformat() if run_state.as_of is not None else manifest_payload.get("source_snapshot", {}).get("as_of")
    files = manifest_payload.get("files", {}) if isinstance(manifest_payload, Mapping) else {}
    report_links = {section: research_report_url(run_state.run_id, section) for section in sorted(_SECTIONS) if (section == "complete" and "complete_report.html" in files) or section in files or f"{section}/index.html" in files}
    snapshot = manifest_payload.get("source_snapshot", {}) if isinstance(manifest_payload, Mapping) else {}
    if not isinstance(snapshot, Mapping):
        snapshot = {}
    role_status = snapshot.get("role_status")
    if not isinstance(role_status, Mapping):
        role_status = {role: (reports[role].status if role in reports else "UNAVAILABLE") for role in _ANALYST_ROLES}
    stage_status = snapshot.get("stage_status")
    if not isinstance(stage_status, Mapping):
        stage_status = _stage_status(run_state)
    missing_evidence = snapshot.get("missing_evidence")
    if not isinstance(missing_evidence, (list, tuple)):
        missing_evidence = [f"{role}:evidence" for role, report in sorted(reports.items()) if not report.evidence_refs]
    checkpoint_status = snapshot.get("checkpoint_status", {"state": "NOT_ATTACHED"})
    if not isinstance(checkpoint_status, Mapping):
        checkpoint_status = {"state": str(checkpoint_status)}
    factor_proposals = snapshot.get("factor_proposals", [])
    if not isinstance(factor_proposals, (list, tuple)):
        factor_proposals = []
    provider_readiness = snapshot.get("provider_readiness", {"status": "UNKNOWN"})
    if not isinstance(provider_readiness, Mapping):
        provider_readiness = {"status": str(provider_readiness)}
    return _scrub(
        {
            "schema_version": 1,
            "run_id": run_state.run_id,
            "state": run_state.current_state.value,
            "state_history": [item.value for item in run_state.state_history],
            "as_of": as_of,
            "dataset_fingerprint": snapshot.get("dataset_fingerprint"),
            "data_mode": snapshot.get("data_mode", "UNKNOWN"),
            "data_limitations": snapshot.get("data_limitations", []),
            "mode": "USER_API" if snapshot.get("model_research", {}).get("execution_mode") == "provider-grounded" else "OFFLINE",
            "model_research": snapshot.get("model_research"),
            "factor_study": snapshot.get("factor_study"),
            "risk_portfolio": snapshot.get("risk_portfolio"),
            "policy_lineage": snapshot.get("policy_lineage"),
            "paper_only": True,
            "decision_eligible": run_state.decision_eligible,
            "failure_kind": run_state.failure_kind.value if run_state.failure_kind else None,
            "failure_message": run_state.failure_message,
            "analysts": analysts,
            "missing_analysts": missing,
            "role_status": dict(sorted((str(key), str(value)) for key, value in role_status.items())),
            "stage_status": dict(sorted((str(key), str(value)) for key, value in stage_status.items())),
            "missing_evidence": sorted(str(item) for item in missing_evidence),
            "checkpoint_status": dict(checkpoint_status),
            "factor_proposals": list(factor_proposals),
            "provider_readiness": dict(provider_readiness),
            "report_tree": {
                "analysts": {role: f"1_analysts/{role}.html" for role in _ANALYST_ROLES},
                "stages": {section: f"{section}/index.html" for section in ("2_evidence", "3_research", "4_quant", "5_risk", "6_paper_decision", "experiments", "risk_attribution", "learning_history")},
                "complete": "complete_report.html",
            },
            "report_links": report_links,
            "limitations": sorted({limitation for report in reports.values() for limitation in report.limitations}),
        }
    )


def register_runtime_snapshot(run_state: ResearchRunState, manifest: ReportManifest | Mapping[str, Any], events: tuple[Any, ...] = ()) -> None:
    """Register typed server state for the local read-only stream."""

    if not isinstance(run_state, ResearchRunState):
        raise TypeError("run_state must be ResearchRunState")
    _RUNTIME_REGISTRY[run_state.run_id] = (run_state, manifest, tuple(events))


def _runtime_manifest_digest(manifest: ReportManifest | Mapping[str, Any]) -> str:
    payload = _manifest_payload(manifest)
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")).hexdigest()


def _runtime_event_metadata(events: tuple[Any, ...], manifest: ReportManifest | Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    values: list[Mapping[str, Any]] = []
    source = _manifest_payload(manifest).get("source_snapshot", {})
    if isinstance(source, Mapping):
        values.append(source)
    for event in events:
        metadata = getattr(event, "metadata", None)
        if isinstance(metadata, Mapping):
            values.append(metadata)
    return tuple(values)


def _runtime_latest(metadata: tuple[Mapping[str, Any], ...], keys: tuple[str, ...], default: Any) -> Any:
    for item in reversed(metadata):
        for key in keys:
            if key in item:
                return item[key]
    return default


def _runtime_tools(metadata: tuple[Mapping[str, Any], ...]) -> list[dict[str, Any]]:
    items: list[Any] = []
    for source in metadata:
        value = next((source[key] for key in ("tool_summaries", "tool_summary", "tools") if key in source), None)
        if isinstance(value, Mapping):
            items.append(value)
        elif isinstance(value, (list, tuple)):
            items.extend(value)
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        summary = {key: item[key] for key in ("name", "tool", "status", "digest", "duration_ms") if key in item}
        if summary:
            result.append(_scrub(summary))
    return result


def research_runtime_view_model(
    run_id: str | ResearchRunState,
    *,
    state: ResearchRunState | None = None,
    manifest: ReportManifest | Mapping[str, Any] | None = None,
    events: tuple[Any, ...] = (),
) -> dict[str, Any]:
    """Build a redacted server-owned live snapshot without doing browser math."""

    if isinstance(run_id, ResearchRunState) and state is None:
        state = run_id
        run_id = state.run_id
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]+", run_id):
        raise ValueError("run_id is invalid")
    if state is None:
        registered = _RUNTIME_REGISTRY.get(run_id)
        if registered is None:
            raise ValueError("runtime snapshot is not registered")
        state, manifest, events = registered
    if state.run_id != run_id:
        raise ValueError("runtime run_id does not match state")
    if manifest is None:
        manifest = {"run_id": run_id, "schema_version": "research-report.v1", "files": {}, "source_snapshot": {}}
    if _manifest_payload(manifest).get("run_id") != run_id:
        raise ValueError("runtime run_id does not match manifest")
    base = research_view_model(state, manifest)
    metadata = _runtime_event_metadata(tuple(events), manifest)
    stage_status = dict(base.get("stage_status", {}))
    if state.current_state in {ResearchState.ANALYSTS_RUNNING, ResearchState.ANALYSTS_READY}:
        stage_status["analysts"] = "CURRENT" if state.current_state is ResearchState.ANALYSTS_RUNNING else "COMPLETE"
    aliases = {
        "data": "DATA_CHECKED",
        "research_plan": "RESEARCH_PLAN_READY",
        "factor_research": "FACTOR_RESEARCH_READY",
        "portfolio": "PORTFOLIO_REVIEWED",
        "paper": "PAPER_DECISION_READY",
        "learning": "LEARNING_RECORDED",
        "report": "REPORT_PUBLISHED",
    }
    terminal = state.current_state.value
    history = {item.value for item in state.state_history if item.value != terminal}
    for name, marker in aliases.items():
        if marker == terminal:
            stage_status[name] = "CURRENT"
        elif marker in history:
            stage_status[name] = "COMPLETE"
        elif terminal in _RUNTIME_BLOCKED_STATES:
            stage_status[name] = "CANCELLED" if state.current_state is ResearchState.CANCELLED else "BLOCKED"
        else:
            stage_status.setdefault(name, "PENDING")
    for name, marker in _RUNTIME_STAGE_MARKERS.items():
        if marker == terminal:
            stage_status[name] = "CURRENT"
        elif marker in history:
            stage_status[name] = "COMPLETE"
        elif terminal in _RUNTIME_BLOCKED_STATES:
            stage_status[name] = "CANCELLED" if state.current_state is ResearchState.CANCELLED else "BLOCKED"
        else:
            stage_status.setdefault(name, "PENDING")
    retry_value = _runtime_latest(metadata, ("retries", "retry_count", "attempts"), 0)
    retries = {"count": int(retry_value) if isinstance(retry_value, (int, float)) and retry_value >= 0 else 0}
    checkpoint = _runtime_latest(metadata, ("checkpoint", "checkpoint_status", "checkpoint_state"), {"state": "NOT_ATTACHED"})
    if not isinstance(checkpoint, Mapping):
        checkpoint = {"state": str(checkpoint)}
    learning = _runtime_latest(metadata, ("learning_proposal", "learning"), {"status": "NO_LEARNING_UPDATE"})
    if not isinstance(learning, Mapping):
        learning = {"status": str(learning)}
    experiments = _runtime_latest(metadata, ("experiments", "factor_experiments"), [])
    if not isinstance(experiments, (list, tuple, Mapping)):
        experiments = []
    risk_attribution = _runtime_latest(metadata, ("risk_attribution", "risk"), {"status": "PENDING"})
    if not isinstance(risk_attribution, Mapping):
        risk_attribution = {"status": str(risk_attribution)}
    learning_history = _runtime_latest(metadata, ("learning_history", "learning_records"), [])
    if not isinstance(learning_history, (list, tuple, Mapping)):
        learning_history = []
    model = {
        **base,
        "schema_version": 3,
        "stage_status": dict(sorted(stage_status.items())),
        "stages": dict(sorted(stage_status.items())),
        "roles": list(base.get("analysts", ())),
        "tool_summaries": _runtime_tools(metadata),
        "retries": retries,
        "checkpoint": dict(checkpoint),
        "learning_proposal": dict(learning),
        "experiments": experiments,
        "risk_attribution": dict(risk_attribution),
        "learning_history": learning_history,
        "manifest_digest": _runtime_manifest_digest(manifest),
        "stream": {"mode": "SERVER_SNAPSHOT", "read_only": True, "paper_only": True},
    }
    return _scrub(model)


def _manifest_payload(value: ReportManifest | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, ReportManifest):
        return {
            "run_id": value.run_id,
            "schema_version": value.schema_version,
            "files": dict(value.files),
            "source_snapshot": _coerce_public(value.source_snapshot),
            "created_at": _coerce_public(value.created_at),
        }
    if not isinstance(value, Mapping):
        raise TypeError("manifest must be ReportManifest or mapping")
    return _coerce_public(dict(value))


def _coerce_public(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {item.name: _coerce_public(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _coerce_public(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_coerce_public(item) for item in value]
    return str(value)


def _stage_status(run_state: ResearchRunState) -> dict[str, str]:
    stages = (
        ("analysts", "ANALYSTS_READY"),
        ("evidence", "EVIDENCE_REVIEW"),
        ("research", "RESEARCH_PLAN_READY"),
        ("quant", "QUANT_VALIDATION"),
        ("risk", "RISK_REVIEW"),
        ("paper_decision", "PAPER_DECISION_READY"),
        ("publication", "REPORT_PUBLISHED"),
        ("learning", "LEARNING_RECORDED"),
    )
    terminal = run_state.current_state.value
    history = {item.value for item in run_state.state_history if item.value != terminal}
    return {
        name: (
            "CURRENT" if state_name == terminal else
            "COMPLETE" if state_name in history else
            "CANCELLED" if terminal == "CANCELLED" else
            "BLOCKED" if terminal in _RUNTIME_BLOCKED_STATES else
            "PENDING"
        )
        for name, state_name in stages
    }


def render_status_wall_html(model: Mapping[str, Any], *, title: str = "Finathink research status") -> str:
    """Render a read-only, no-JS status wall from a server-owned view model."""

    safe = _scrub(dict(model))
    esc = lambda value: html.escape(str(value if value is not None else "—"), quote=True)
    role_status = safe.get("role_status", {}) if isinstance(safe, Mapping) else {}
    stage_status = safe.get("stage_status", {}) if isinstance(safe, Mapping) else {}
    missing = safe.get("missing_evidence", []) if isinstance(safe, Mapping) else []
    role_cards = "".join(
        f'<li data-role="{esc(role)}"><strong>{esc(role)}</strong><span>{esc(status)}</span></li>'
        for role, status in sorted(role_status.items())
    ) or "<li>No analyst status recorded.</li>"
    stage_rows = "".join(
        f'<tr><th scope="row">{esc(stage)}</th><td>{esc(status)}</td></tr>'
        for stage, status in sorted(stage_status.items())
    ) or '<tr><td colspan="2">No stage status recorded.</td></tr>'
    limitation_rows = "".join(f"<li>{esc(item)}</li>" for item in missing) or "<li>None recorded.</li>"
    safe_title = _scrub(title)
    run_id = esc(safe.get("run_id"))
    state = esc(safe.get("state"))
    payload_json = html.escape(json.dumps(safe, ensure_ascii=False, sort_keys=True), quote=True)
    metrics = safe.get("quant_metrics", {})
    metric_rows = "".join(
        f'<tr><th scope="row">{esc(str(key).replace("_", " ").capitalize())}</th><td>{esc(value)}</td></tr>'
        for key, value in metrics.items()
    ) if isinstance(metrics, Mapping) else ""
    evidence_html = (
        f'<section aria-labelledby="source"><h2 id="source">Research input</h2><p>Dataset fingerprint</p><pre>{esc(safe.get("dataset_fingerprint"))}</pre></section>'
        if safe.get("dataset_fingerprint") else ""
    )
    if metric_rows:
        evidence_html += f'<section aria-labelledby="quant-metrics"><h2 id="quant-metrics">Historical metrics</h2><table><tbody>{metric_rows}</tbody></table><p>In-sample deterministic baseline, including declared transaction costs. Not an out-of-sample forecast.</p></section>'
    if (safe.get("publication_scope") or {}).get("kind") == "PRE_TEST_ONLY":
        evidence_html += '<p>验证阶段：辅助回测、风险与归因仅使用最终测试期之前的观察。本报告不展示测试期价格与绩效。</p>'
    if safe.get("data_mode"):
        data_notes = "".join(f'<li>{esc(note)}</li>' for note in safe.get("data_limitations", []))
        source_json = esc(json.dumps(safe.get("source_metadata", {}), ensure_ascii=False, sort_keys=True))
        evidence_html += f'<section class="note" aria-labelledby="data-mode"><h2 id="data-mode">Data mode: {esc(safe["data_mode"])}</h2><ul>{data_notes}</ul><details><summary>Source metadata</summary><pre>{source_json}</pre></details></section>'
    model_research = safe.get("model_research")
    lineage = safe.get("policy_lineage")
    if isinstance(lineage, Mapping):
        kind = "回溯比较 · 不计入配置生效后的新学习" if lineage.get("retrospective") else "按所选版本执行的纸面研究"
        evidence_html += f'<section aria-labelledby="policy-lineage"><h2 id="policy-lineage">配置版本与复跑来源</h2><p>v{esc(lineage.get("version"))} · {esc(kind)}</p><p>配置引用</p><pre>{esc(lineage.get("policy_ref"))}</pre><p>实际参数指纹</p><pre>{esc(lineage.get("runtime_fingerprint"))}</pre><p>父版本 {esc(lineage.get("parent_ref"))} · 批准可用时间 {esc(lineage.get("available_at"))} · 复跑原运行 {esc(lineage.get("rerun_of"))}</p><p>旧观察使用新配置时属于回溯分析，不是当时可获得的预测或独立样本外证据。</p></section>'
    risk_portfolio = safe.get("risk_portfolio")
    if isinstance(risk_portfolio, Mapping):
        effects = "".join(f'<tr><th scope="row">{esc(item.get("constraint"))}</th><td>{esc(item.get("before_weight"))}</td><td>{esc(item.get("after_weight"))}</td><td>{esc(item.get("cash_added"))}</td><td>{esc(item.get("reason"))}</td></tr>' for item in risk_portfolio.get("constraint_effects", []))
        attribution = risk_portfolio.get("attribution", {})
        contribution_rows = "".join(f'<tr><th scope="row">{esc(label)}</th><td>{esc(attribution.get(key))}</td></tr>' for key, label in (("strategy", "原始策略（零成本）"), ("cost", "成本差额"), ("risk_scaling", "风险缩放差额"), ("net_return", "约束后净收益"), ("residual", "加总残差")))
        exposure = risk_portfolio.get("exposure", {})
        risk_details = esc(json.dumps({key: risk_portfolio.get(key) for key in ("exposure", "stress", "review", "allocation", "paper_ledger")}, ensure_ascii=False, sort_keys=True))
        evidence_html += f'<section aria-labelledby="risk-portfolio"><h2 id="risk-portfolio">风险与组合 · PAPER-ONLY</h2><p>行业来源 {esc(risk_portfolio.get("industry_status"))} · 历史 CVaR {esc(exposure.get("cvar"))} · 样本数 {esc(risk_portfolio.get("cvar_sample_size"))}</p><p>{esc(risk_portfolio.get("factor_definition"))}</p><p>计划现金比例 {esc(risk_portfolio.get("planned_cash_weight"))} · 纸面执行 {esc(risk_portfolio.get("execution_status"))}。计划仓位不等于已成交仓位；没有下一观测时保留现金与待执行状态。</p><h3>约束对仓位与现金的影响</h3><div tabindex="0" role="region" aria-label="约束影响表，可横向滚动" style="overflow-x:auto"><table><thead><tr><th>约束</th><th>约束前</th><th>约束后</th><th>增留现金</th><th>依据</th></tr></thead><tbody>{effects}</tbody></table></div><h3>路径归因</h3><p>同一数据与执行容量下，原始零成本 → 原始含成本 → 前缀风控含成本；这是有顺序的反事实差额，不是唯一因果解释或收益预测。</p><table><tbody>{contribution_rows}</tbody></table><details><summary>暴露、压力情景、配置与纸面账本证据</summary><pre>{risk_details}</pre></details></section>'
    factor_study = safe.get("factor_study")
    if isinstance(factor_study, Mapping):
        rows = "".join(f'<tr><th scope="row">{esc(item.get("experiment_id"))}</th><td>{esc(item.get("parameters", {}).get("window"))}</td><td>{esc(item.get("failure_reason") or item.get("metrics", {}).get("information_coefficient"))}</td><td>{esc(item.get("metrics", {}).get("sample_size"))}</td></tr>' for item in factor_study.get("summaries", []))
        test = factor_study.get("test_evaluation") or {}
        evidence_html += f'<section aria-labelledby="factor-study"><h2 id="factor-study">因子研究实验 · PAPER-ONLY</h2><p>章程 {esc(factor_study.get("study_id"))} · 快照 {esc(factor_study.get("snapshot_ref"))}</p><p>验证排名不使用测试指标。测试状态 {esc(test.get("oos_status", "HIDDEN"))}；PASS 表示证据可计算，不代表盈利。审核不自动写入生产因子库。</p><table><thead><tr><th>候选</th><th>窗口</th><th>验证 IC / 失败原因</th><th>样本</th></tr></thead><tbody>{rows}</tbody></table></section>'
    if isinstance(model_research, Mapping):
        roles = "".join(f'<li><strong>{esc(item.get("role"))} · {esc(item.get("status"))}</strong><p>{esc(" / ".join(item.get("claims", [])))}</p></li>' for item in model_research.get("roles", []))
        synthesis = model_research.get("synthesis", {})
        hypotheses = "".join(f'<li>{esc(item)}</li>' for item in synthesis.get("hypotheses", []))
        disagreements = "".join(f'<li>{esc(item)}</li>' for item in synthesis.get("disagreements", []))
        evidence_html += f'<section aria-labelledby="model-research"><h2 id="model-research">模型材料研究 · PAPER-ONLY</h2><p>模型观察待复核；数值指标由服务器引擎计算。引用校验不证明内容真实。</p><ul>{roles}</ul><h3>待验证假设</h3><ul>{hypotheses}</ul><h3>分歧与待确认事项</h3><ul>{disagreements}</ul></section>'
    report_style = """body{font:16px/1.5 system-ui,sans-serif;max-width:980px;margin:0 auto;padding:clamp(1rem,4vw,2rem);color:#173137;overflow-wrap:anywhere}h1{margin-bottom:.25rem}.meta{color:#617174}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(180px,100%),1fr));gap:.7rem;padding:0;list-style:none}.grid li{border:1px solid #c9c2b5;padding:.8rem;display:flex;justify-content:space-between;gap:.5rem}table{width:100%;table-layout:fixed;border-collapse:collapse;margin:1rem 0;font-variant-numeric:tabular-nums}th,td{text-align:left;border-bottom:1px solid #c9c2b5;padding:.6rem;overflow-wrap:anywhere}code,pre{max-width:100%;overflow:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}.note{border-left:3px solid #c36e48;padding-left:1rem}:focus-visible{outline:2px solid #173137;outline-offset:3px}summary{min-height:44px;cursor:pointer}@media(max-width:480px){th,td{padding:.4rem;font-size:.875rem}}"""
    report_style += '.grid li{flex-wrap:wrap}[aria-label="约束影响表，可横向滚动"] table{min-width:44rem}'
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="description" content="Read-only multi-agent research status wall"><title>{esc(safe_title)} · {run_id}</title><style>{report_style}</style></head><body><main><h1>{esc(safe_title)}</h1><p class="meta">Run {run_id} · State {state} · PAPER-ONLY · READ-ONLY</p>{evidence_html}<section aria-labelledby="roles"><h2 id="roles">Analyst roles</h2><ul class="grid">{role_cards}</ul></section><section aria-labelledby="stages"><h2 id="stages">Stage wall</h2><table><thead><tr><th>Stage</th><th>Status</th></tr></thead><tbody>{stage_rows}</tbody></table></section><section class="note" aria-labelledby="evidence"><h2 id="evidence">Missing evidence</h2><ul>{limitation_rows}</ul></section><noscript><p>This report is fully readable without JavaScript. Values are server-provided; no financial indicators are calculated in the browser.</p></noscript><details><summary>Server payload (redacted)</summary><pre>{payload_json}</pre></details></main></body></html>'''

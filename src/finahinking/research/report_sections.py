"""Deterministic blue-white offline sections from one verified factor run."""

from __future__ import annotations

import html
import json
import math
import re
from dataclasses import dataclass

from finahinking.workbench_ui import blue_white_report_css

from .contracts import stable_digest
from .factor_validation import FactorValidationRun
from .method_context import LoadedMethods


@dataclass(frozen=True, slots=True)
class ReportSections:
    _json: str

    @property
    def html(self):
        return json.loads(self._json)["html"]

    @property
    def metadata(self):
        return json.loads(self._json)["metadata"]

    @property
    def fingerprint(self):
        return stable_digest(json.loads(self._json))


def _plain(value):
    return value.to_dict() if hasattr(value, "to_dict") else (value or {})


def _pre(value):
    return '<pre tabindex="0" aria-label="冻结证据内容">' + html.escape(json.dumps(value, ensure_ascii=False, indent=2)) + '</pre>'


def _section(identifier, title, content):
    return f'<section class="report-section" id="{identifier}"><h2>{title}</h2>{content}</section>'


def _equity_chart(portfolio):
    chart = portfolio.get("performance", {}).get("chart_data", {})
    equity, times = chart.get("equity", []), chart.get("timestamps", [])
    if len(equity) != len(times) or not 2 <= len(equity) <= 10000:
        return '<p>未生成收益曲线：缺少同一账本的完整时序。</p>'
    valid = [value for value in equity if type(value) in {int, float} and math.isfinite(value)]
    if len(valid) < 2:
        return '<p>未生成收益曲线：有效观测不足。</p>'
    lower, upper = min(valid), max(valid)
    span = upper - lower or max(abs(upper) * .01, 1)
    segments, segment = [], []
    for index, value in enumerate(equity):
        if type(value) not in {int, float} or not math.isfinite(value):
            if segment:
                segments.append(segment)
                segment = []
            continue
        segment.append(f'{index / (len(equity) - 1) * 900:.3f},{220 - (value - lower) / span * 200:.3f}')
    if segment:
        segments.append(segment)
    lines = ''.join(f'<polyline points="{" ".join(points)}" fill="none" stroke="#2563eb" stroke-width="2" vector-effect="non-scaling-stroke"/>' for points in segments)
    rows = ''.join(f'<tr><td>{html.escape(str(time))}</td><td>{html.escape(str(value)) if value is not None else "未知"}</td></tr>' for time, value in zip(times, equity))
    return (f'<figure data-report-equity><div class="report-chart-row"><div class="report-chart-scale" aria-label="净值范围"><span>{upper:.2f}</span><span>{lower:.2f}</span></div>'
        '<svg viewBox="0 0 900 240" preserveAspectRatio="none" role="img" tabindex="0" aria-labelledby="equity-title equity-desc">'
        '<title id="equity-title">候选实际含费纸面回测净值</title><desc id="equity-desc">来自同一次冻结运行的实际账本，不是已实现投资收益；完整读数见下方表格。</desc>'
        '<path d="M0 0V240H900" fill="none" stroke="#b6c7df" vector-effect="non-scaling-stroke"/>'
        + lines + '</svg></div>'
        f'<div class="report-chart-dates"><span>{html.escape(str(times[0])[:10])}</span><span>{html.escape(str(times[-1])[:10])}</span></div>'
        '<figcaption>实际净值含声明费用与滑点；未知点不补值或跨缺口连线。图形不计算新指标。</figcaption></figure>'
        '<details><summary>逐观察净值表（图形的同一数据）</summary><table><caption>候选实际账本</caption><thead><tr><th scope="col">时间</th><th scope="col">净值</th></tr></thead><tbody>' + rows + '</tbody></table></details>')


def build_method_factor_sections(validation_run, loaded_methods, evidence_view, lifecycle_view) -> ReportSections:
    run = validation_run if isinstance(validation_run, FactorValidationRun) else FactorValidationRun.from_dict(validation_run)
    value = run.to_dict()
    loaded = loaded_methods if isinstance(loaded_methods, LoadedMethods) else LoadedMethods.from_dict(loaded_methods) if loaded_methods is not None else None
    evidence, lifecycle = _plain(evidence_view), _plain(lifecycle_view)
    steps = {step["stage_id"]: step for step in value["steps"]}
    bindings = value["bindings"]
    metadata = {"template_version": "blue-white.v1", "validation_fingerprint": run.fingerprint,
        "binding_digest": value["binding_digest"], "factor_spec_fingerprint": bindings["factor_spec_fingerprint"],
        "snapshot_ref": bindings["snapshot_ref"], "attachment_ref": bindings["attachment_ref"],
        "runtime_policy_fingerprint": stable_digest(value["policy"]), "source_policy_fingerprint": bindings["source_policy_fingerprint"],
        "code_version": bindings["code_version"], "methods_digest": None if loaded is None else loaded.fingerprint,
        "method_manifest_digest": None if loaded is None else loaded.manifest_fingerprint,
        "source_evidence_fingerprint": stable_digest(evidence), "lifecycle_fingerprint": stable_digest(lifecycle),
        "reproduction_scope": value["reproduction_scope"], "financial_falsification": False, "production_write": False}
    rows = ''.join(f'<dt>{html.escape(key)}</dt><dd>{html.escape(str(item)) if item is not None else "未记录"}</dd>' for key, item in metadata.items())
    identity = _section("identity", "研究身份与范围", f'<p class="report-status">{html.escape(value["status"])} · {value["reproduction_scope"]} · PAPER-ONLY</p>'
        '<p>公式样本只证明工程合同。来源效果、本地计算和用途审核分开；本报告不是盈利承诺。</p><dl class="report-meta">' + rows + '</dl>'
        + '<details><summary>数据、成本、执行与样本窗口</summary>' + _pre(bindings) + '</details>')
    methods = _section("methods", "本次方法与原式", '<p>方法来自此运行冻结的清单，不从今天的目录补写。</p>'
        + (_pre(loaded.to_dict()["manifest"]) if loaded else '<p>此记录没有完整方法清单；保留原摘要，不声称重新加载。</p>') + _pre(value["factor_spec"]))
    track = ''.join(f'<li class="report-stage" data-validation-stage="{html.escape(step["stage_id"])}"><h3>{index + 1}. {html.escape(step["name"])}</h3>'
        f'<strong class="stage-{step["status"]}">{step["status"]}</strong><p>{html.escape(step["reason"] or "本步已保留实际计算证据。")}</p>'
        + ('<details><summary>指标与产物引用</summary>' + _pre({"artifact_ref": step["artifact_ref"], "metrics": step["metrics"]}) + '</details>' if step["metrics"] else '') + '</li>'
        for index, step in enumerate(value["steps"]))
    validation = _section("validation", "九步验证记录", '<ol class="report-stages">' + track + '</ol>')
    portfolio = steps["portfolio_backtest"]
    result = _section("results", "本地实际结果", _equity_chart(portfolio["metrics"]) if portfolio["status"] == "COMPLETE" else '<p>未生成收益曲线：回测未完成或数据阻断，不能补造净值。</p>')
    claims = evidence.get("source_claims", [])
    claim_rows = ''.join(f'<tr><td>{html.escape(str(claim.get("metric_name")))}</td><td>{html.escape(str(claim.get("metric_value")))}</td><td>{html.escape(str(claim.get("scope")))}</td><td>{html.escape(str(claim.get("definition")))}</td></tr>' for claim in claims)
    sources = _section("sources", "来源声明（非本地结果）", '<p>不将上游 ICIR 放入本地指标或合成排名。</p><table data-report-source-claims><caption>来源口径单列</caption><thead><tr><th>指标</th><th>来源数值</th><th>适用范围</th><th>定义</th></tr></thead><tbody>'
        + (claim_rows or '<tr><td colspan="4">没有绑定来源指标；不继承同名因子的成绩。</td></tr>') + '</tbody></table>'
        + '<details><summary>依赖缺口与冲突</summary>' + _pre({key: evidence.get(key, []) for key in ("missing_dependencies", "conflicts")}) + '</details>')
    admission = _section("admission", "用途与报告生成时状态", '<p>本次计算完成不等于已批准研究或生产用途。后续审核是独立事件，不改写这份报告。</p>'
        + _pre(lifecycle or {"state": "OBSERVATION" if steps["archive_decision"]["metrics"].get("decision") == "OBSERVATION_ONLY" else "VALIDATING", "use_scopes": [], "production_write": False}))
    lessons = _section("limitations", "反例、局限与下一步", '<p>缺数据先补真实输入；执行错误先修程序；结果不佳按原预设条件留档，不调整方向或偷看最终区间来制造通过。</p>'
        + '<ul>' + ''.join('<li>' + html.escape(item) + '</li>' for item in value["limitations"]) + '</ul>'
        '<p>取得下一独立窗口后才复核变化；需要修改时进入预览与显式审核。容量使用声明的参与率与固定滑点，不代表真实市场冲击已校准。</p>')
    return ReportSections(json.dumps({"metadata": metadata, "html": identity + methods + validation + result + sources + admission + lessons},
                                     ensure_ascii=False, allow_nan=False, sort_keys=True))


def render_factor_report_document(sections, *, run_id, question):
    esc = html.escape
    toc = ''.join(f'<a href="#{identifier}">{label}</a>' for identifier, label in (("identity", "身份与范围"),
        ("methods", "方法与原式"), ("validation", "九步验证"), ("results", "实际结果"), ("sources", "来源"), ("admission", "用途"), ("limitations", "下一步")))
    csp = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta http-equiv="Content-Security-Policy" content="{esc(csp, quote=True)}"><title>Finathink 研究报告 · {esc(run_id)}</title><style>{blue_white_report_css()}</style></head>'
        f'<body><main><header class="report-header"><h1>Finathink 研究报告</h1><p>{esc(question)}</p><p class="meta">运行 {esc(run_id)} · 单文件离线阅读 · 原始证据可追溯</p></header>'
        f'<nav class="report-toc" aria-label="报告目录">{toc}</nav>{sections.html}<footer><p>方法、数据、政策和计算均使用此报告的冻结版本。离线打开不访问模型、数据 API 或 CDN。</p></footer></main></body></html>')


def theme_report_document(document):
    """Theme newly generated legacy subpages, never files already published."""
    csp = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"
    additions = ('<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta http-equiv="Content-Security-Policy" content="{html.escape(csp, quote=True)}">'
        f'<style>{blue_white_report_css()}</style>')
    document = re.sub(r'<html lang="en">', '<html lang="zh-CN">', document, count=1)
    return document.replace('</head>', additions + '</head>', 1)

"""Trace an exact definition through source, verified runs and explicit use scopes."""

from __future__ import annotations

import html
import json
import re
from dataclasses import asdict

from .asset_catalog import ResearchAssetCatalog
from .asset_contracts import FactorSpec
from .contracts import stable_digest
from .factor_recipes import family_factor_specs, pilot_factor_specs, resolve_factor_spec

_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def factor_directory(catalog=None):
    catalog = catalog or ResearchAssetCatalog.packaged()
    document = json.loads(catalog.read_text("source-definitions"))
    local = (*pilot_factor_specs(), *family_factor_specs())
    return [{"factor_id": row["definition_id"], "title": row["name"], "version": row["version"],
             "status": row["definition_status"], "implementation": "SOURCE_ONLY"} for row in document["definitions"]] + [
        {"factor_id": spec.factor_id, "title": resolve_factor_spec(spec).title, "version": spec.version,
         "status": "DEFINED", "implementation": "LOCAL_FORMULA_PILOT", "window": spec.window} for spec in local]


def factor_detail(app, factor_id, *, version=None, window=None, run_ref=None):
    if not isinstance(factor_id, str) or not _LABEL.fullmatch(factor_id):
        raise ValueError("因子身份无效")
    if version is not None and (not isinstance(version, str) or not _LABEL.fullmatch(version)):
        raise ValueError("定义版本无效")
    if window is not None and (type(window) is not int or not 1 <= window <= 252):
        raise ValueError("观察窗口必须在1至252之间")
    if run_ref is not None and (not isinstance(run_ref, str) or not _LABEL.fullmatch(run_ref)):
        raise ValueError("报告引用无效")
    catalog = ResearchAssetCatalog.packaged()
    if not any(row["factor_id"] == factor_id for row in factor_directory(catalog)):
        raise KeyError(factor_id)
    refs = () if run_ref is None else (run_ref,)
    evidence = app.factor_evidence(factor_id, run_refs=refs)
    candidates = [spec for spec in (*pilot_factor_specs(window or 5), *family_factor_specs()) if spec.factor_id == factor_id]
    for row in evidence["local_runs"]:
        if row.get("factor_spec") is not None:
            candidates.append(FactorSpec(**{key: value for key, value in row["factor_spec"].items() if key != "schema_version"}))
    spec = next((candidate for candidate in candidates if (version is None or candidate.version == version)
                 and (window is None or candidate.window == window)), None)
    if spec is None and version is not None and evidence["definition"].get("version") != version:
        raise KeyError("指定定义版本没有对应证据")
    available = False
    if spec is not None:
        try:
            resolve_factor_spec(spec)
            available = True
        except ValueError:
            pass
        evidence = app.factor_evidence(spec, run_refs=refs)
    selected = next((row for row in evidence["local_runs"] if row["run_ref"] == run_ref), None) if run_ref else next(iter(evidence["local_runs"]), None)
    if run_ref is not None and selected is None:
        raise KeyError("该报告不属于所选因子")
    policy = json.loads(catalog.read_text("factor-validation-v1"))
    actual = {} if selected is None else {step["stage_id"]: step for step in selected.get("stages", ())}
    track = [{**stage, **actual.get(stage["stage_id"], {"status": "NOT_RUN", "reason": "NO_VERIFIED_LOCAL_RUN"}),
              "metrics": (selected.get("local_metric") or {}).get(stage["stage_id"], {}) if selected else {}}
             for stage in policy["stages"]]
    return {"schema_version": "factor-detail.v1", "factor_id": factor_id,
        "version": spec.version if spec else evidence["definition"].get("version"),
        "factor_spec": None if spec is None else spec.to_dict(), "implementation_available": available,
        "evidence": evidence, "lifecycle": evidence["lifecycle"], "selected_run": selected, "track": track,
        "lessons": [asdict(lesson) for lesson in catalog.lessons_for(())], "production_write": False}


def _pre(value):
    return '<pre class="factor-code" tabindex="0" aria-label="原始证据内容">' + html.escape(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)) + '</pre>'


def _section(key, title, body):
    return f'<section class="factor-section" id="factor-{key}" data-factor-section="{key}"><h2>{title}</h2>{body}</section>'


def factor_detail_page(data, csrf):
    esc = html.escape
    evidence, selected, spec = data["evidence"], data["selected_run"], data["factor_spec"]
    definition, lifecycle = evidence["definition"], data["lifecycle"]
    navigation = ''.join(f'<a href="#factor-{key}">{label}</a>' for key, label in (
        ("source", "来源"), ("definition", "定义"), ("formula", "公式"), ("data", "数据"),
        ("validation", "九步验证"), ("lifecycle", "用途审核"), ("lessons", "反例")))
    claims = ''.join('<li>' + esc(str(row["metric_name"])) + ' = ' + esc(str(row["metric_value"]))
        + ' · 来源声明，非本地结果<p>' + esc(row["scope"]) + '</p></li>' for row in evidence["source_claims"])
    source = _section("source", "来源声明与本地证据", '<p>两个口径独立保留，不合并排名，不将资料已打包当作效果已复现。</p>'
        + ('<ul>' + claims + '</ul>' if claims else '<p>此本地定义没有继承来源指标。</p>') + _pre(evidence["conflicts"] or "无已登记的来源/本地对照冲突。"))
    definition_html = _section("definition", "定义、版本与实现", _pre(spec or definition)
        + f'<p>当前可信实现：{"可解析" if data["implementation_available"] else "未安装对应实现；历史资料仍可复查"}。工程试点不等于市场效果。</p>')
    formula = spec["formula"] if spec else definition.get("formula") or "原式尚未核定，不使用同名近似实现替代。"
    formula_html = _section("formula", "公式与语义", _pre(formula)
        + ('<p>方向：' + esc(spec["direction"]) + ' · 频率：' + esc(spec["frequency"]) + ' · 缺失：' + esc(spec["missing_policy"]) + '</p>' if spec else ''))
    scope = selected.get("scope", {}) if selected else {}
    data_html = _section("data", "数据与时间域", _pre(scope or "尚无本地数据绑定；不假定公告、行业、限价规则或席位归属。")
        + '<details><summary>资料依赖与校验问题</summary>' + _pre(evidence["missing_dependencies"]) + '</details>')
    steps = ''.join(f'<li class="factor-stage factor-stage--{esc(step["status"].lower())}" data-stage-id="{esc(step["stage_id"])}" data-stage-status="{esc(step["status"])}">'
        f'<h3>{index + 1}. {esc(step.get("name", step["stage_id"]))}</h3><span>{esc(step["status"])}</span>'
        f'<p>{esc(step.get("reason") or "已保留原计算事实，不在浏览器重算。")}</p>'
        + ('<details><summary>查看本步实际指标</summary>' + _pre(step["metrics"]) + '</details>' if step["metrics"] else '') + '</li>'
        for index, step in enumerate(data["track"]))
    identity = ('<p>报告：' + esc(selected["run_ref"]) + ' · ' + esc(selected["status"]) + ' · '
        + esc(selected.get("reproduction_scope", "UNAVAILABLE")) + '</p>' + _pre(selected.get("evaluation_fingerprint") or "产物损坏或不可用，不展示数值。")) if selected else '<p>尚无本地运行；九步均未执行。</p>'
    methods = selected.get("method_binding", {}) if selected else {}
    validation = _section("validation", "九步验证与本次方法", identity + '<details><summary>本次方法与指纹</summary>'
        + _pre(methods or "此记录没有冻结方法清单，不用当前目录补写历史加载状态。") + '</details><ol class="factor-track">' + steps + '</ol>')
    use = _section("lifecycle", "范围用途与显式审核", f'<p data-factor-lifecycle="{esc(lifecycle["state"])}">当前投影：{esc(lifecycle["state"])}</p>'
        '<p>批准仅适用于原公式、市场、股票池、时间域、用途、政策与评估；缺数据和程序错误不等于金融失效。</p>'
        + _pre(lifecycle["use_scopes"] or "尚无范围用途决定。")
        + '<details><summary>不可变审核历史</summary>' + _pre(lifecycle["history"]) + '</details>')
    lessons = _section("lessons", "反例与重访条件", ''.join('<details><summary>' + esc(row["title"]) + '</summary><p>'
        + esc(row["summary"]) + '</p><p>重访：' + esc(row["revisit_when"]) + '</p><p>来源反例，不自动构成本因子的金融证伪。</p></details>' for row in data["lessons"]))
    options = ''.join(f'<option value="{esc(row["run_ref"], quote=True)}"' + (' selected' if selected and row['run_ref'] == selected['run_ref'] else '')
        + f'>{esc(row["run_ref"])} · {esc(row["status"])}</option>' for row in evidence["local_runs"])
    selector = f'<form class="asset-search" method="get"><label>定义版本<input name="version" value="{esc(data["version"] or "", quote=True)}" maxlength="128"></label>'
    if spec:
        selector += f'<label>观察窗口<input name="window" type="number" min="1" max="252" value="{spec["window"]}"></label>'
    selector += (f'<label>本地报告<select name="run_ref"><option value="">最近报告</option>{options}</select></label>' if options else '') + '<button type="submit">读取此定义</button></form>'
    actions = ''
    if selected and selected.get("local_metric") is not None and selected.get("definition_matches"):
        scope_payload = {"factor_id": data["factor_id"], "factor_ref": selected["factor_ref"], "market": scope["universe"]["market"],
            "universe_ref": stable_digest(scope["universe"]), "time_domain": scope["final_test_window"],
            "purpose": "FORMULA_RESEARCH" if selected["reproduction_scope"] == "FORMULA_FIXTURE" else "PAPER_RESEARCH",
            "policy_fingerprint": selected["policy_fingerprint"], "evaluation_fingerprint": selected["evaluation_fingerprint"], "run_ref": selected["run_ref"]}
        actions = ('<section class="factor-section"><h2>记录研究决定</h2><p>此操作只写审核事件，不改生产注册表。</p><form data-factor-review-form>'
            f'<input type="hidden" name="scope" value="{esc(json.dumps(scope_payload), quote=True)}">'
            '<label>审核人<input name="reviewer" required maxlength="128" value="local-user"></label>'
            '<label>决定<select name="decision"><option value="OBSERVE">保持观察</option><option value="APPROVE_FOR_RESEARCH">批准此范围研究</option><option value="REJECT">拒绝研究用途</option><option value="RETIRE">退出原批准</option></select></label>'
            '<button type="submit">明确记录决定</button></form></section>')
    return (f'<section data-factor-detail data-factor-id="{esc(data["factor_id"], quote=True)}" data-factor-csrf="{esc(csrf, quote=True)}">'
        f'<header class="overview-header"><div><h1>因子证据</h1><p class="lede">{esc(data["factor_id"])}</p></div></header>'
        f'<nav class="factor-toc" aria-label="因子证据分区">{navigation}</nav>{selector}'
        + source + definition_html + formula_html + data_html + validation + use + lessons + actions
        + '<section class="factor-section"><h2>刷新观察建议</h2><p>只读取已完成的可比报告；不自动调权或调用模型。</p>'
        '<form data-factor-monitor-form><label>观察截止日（UTC）<input type="date" name="as_of" required></label><button type="submit">显式刷新观察</button></form></section>'
        '<p role="status" aria-live="polite" data-factor-action-status>未提交新的审核或观察请求。</p></section>')


FACTOR_DETAIL_CSS = """
.factor-toc{display:flex;flex-wrap:wrap;gap:12px 20px;padding-bottom:20px;border-bottom:1px solid var(--border)}
.factor-section{padding:28px 0;border-bottom:1px solid var(--border);min-width:0}.factor-section h2{margin:0 0 14px}
.factor-section p{max-width:76ch}.factor-code{white-space:pre-wrap;overflow-wrap:anywhere;max-height:400px;overflow:auto;padding:16px;background:var(--surface);border:1px solid var(--border);border-radius:8px;font-size:12px;line-height:1.65}
.factor-track{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;padding:0;list-style:none}
.factor-stage{padding:18px;background:var(--surface);border:1px solid var(--border);border-radius:8px;min-width:0;overflow-wrap:anywhere}
.factor-stage h3{margin:0 0 10px;font-size:14px}.factor-stage>span{font-size:12px;color:var(--text-secondary)}
.factor-stage--complete>span{color:var(--success)}.factor-stage--blocked_data>span{color:var(--warning)}.factor-stage--execution_error>span{color:var(--error)}
.factor-section form{display:flex;flex-wrap:wrap;gap:12px;align-items:end}.factor-section details{margin:12px 0}
@media(max-width:720px){.factor-track{grid-template-columns:minmax(0,1fr)}.factor-code{max-height:320px}}
"""

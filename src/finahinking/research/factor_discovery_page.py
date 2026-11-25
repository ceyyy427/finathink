"""Question preview and audit controls in the existing factor workbench."""

import json
from html import escape
from urllib.parse import quote, urlencode

from .factor_discovery import discovery_questions
from .panel_workbench_page import saved_panel_select

_STATUSES = {
    "REGISTERED": "已登记", "RUNNING": "计算中", "PASS": "通过登记条件", "FALSIFIED": "未达登记阈值",
    "NEEDS_DATA": "资料或有效样本不足", "EXECUTION_FAILED": "执行失败", "CANCELLED": "已取消",
    "REPORT_UNAVAILABLE": "报告待恢复",
}


def esc(value):
    return escape(str(value), quote=True)


def discovery_form(csrf):
    questions = discovery_questions()
    options = "".join(f'<option value="{esc(question)}"></option>' for question in questions)
    return f'''<section class="panel" id="multi-factor-discovery">
<h2>从问题生成多候选</h2>
<form method="post" action="/api/research/factor-suites/discovery/preview" data-factor-discovery>
<input type="hidden" name="csrf" value="{esc(csrf)}">
<label>自然语言研究问题<input name="discovery_question" maxlength="1000" required list="discovery-questions" value="{esc(questions[0])}"></label>
<datalist id="discovery-questions">{options}</datalist>
<label>多候选输入数据<select name="mode"><option value="sample">合成演练 · 8 标的 × 100 观察点</option>
<option value="saved">已保存面板</option></select></label>
{saved_panel_select("discovery-panel-ref", "多候选研究面板")}
<label data-discovery-market hidden>市场标签<input name="market" maxlength="32" placeholder="例如 CN_A 或 SYNTHETIC" autocomplete="off"></label>
<div class="action-row"><button type="submit" disabled data-factor-preview>预览候选和反证</button>
<button type="button" disabled data-factor-submit>登记并开始验证</button></div>
<p role="status" data-factor-discovery-status>预览时可以核对候选公式、来源、评分方向与费用。</p>
<div data-factor-discovery-plan></div>
<noscript><p>启用 JavaScript 后可预览和提交候选研究。</p></noscript>
</form></section>'''


def _fact_rows(items):
    return "".join(f"<dt>{esc(label)}</dt><dd>{esc(value)}</dd>" for label, value in items)


def discovery_audit(study, family):
    plan = study["spec"]["multi_factor_discovery"]
    validation = study.get("validation") or {}
    results = {row["factor_id"]: row for row in validation.get("suite_validation", {}).get("trials", [])}
    states = {row["factor_id"]: row["state"] for row in study["trials"] if row["phase"] == "validation"}
    ranks = {factor_id: rank for rank, factor_id in enumerate(validation.get("ranking", []), start=1)}
    job = study.get("validation_job", {})
    unavailable = job.get("status") == "completed" and job.get("report_status") != "AVAILABLE"
    rows = []
    for candidate in plan["candidates"]:
        factor_id = candidate["factor_id"]
        result = results.get(factor_id, {})
        state = "REPORT_UNAVAILABLE" if unavailable else states.get(factor_id, "")
        note = result.get("failure_reason") or "、".join(result.get("missing_reason", [])) or "—"
        adjusted = family["holm"].get(factor_id)
        rows.append(f'<tr data-discovery-candidate><th scope="row">{esc(candidate["title"])}<br>'
                    f'<code>{esc(candidate["factor_spec"]["formula"])}</code></th>'
                    f'<td>{esc(ranks.get(factor_id, "—"))}</td><td>{esc(_STATUSES.get(state, state))}</td>'
                    f'<td>{esc("—" if adjusted is None else format(adjusted, ".4g"))}</td><td>{esc(note)}</td></tr>')
    source_rows = "".join(f'<li>{esc(row["title"])} · <code>{esc(row["repository"])}@{esc(row["commit"])}</code>'
                          f' · {esc(row["path"])} · {esc(row["license_id"])}</li>' for row in plan["sources"])
    exclusions = "".join(f'<li>{esc(row["factor_id"])} · {esc("等价公式已合并" if row["reason"] == "DUPLICATE_FORMULA" else "超出候选预算")}</li>'
                         for row in plan["excluded"])
    result = f'''<section class="section" aria-labelledby="candidate-audit-title">
<h3 id="candidate-audit-title">多候选登记与验证排序</h3><p>{esc(plan["question"])}</p>
<p>登记分母 {family["registered_count"]} · 已发布验证 {family["published_count"]} · 候选上限 {plan["budget"]["candidate_limit"]}</p>
<div class="suite-table" role="region" tabindex="0" aria-label="候选排名与状态"><table>
<thead><tr><th scope="col">候选</th><th scope="col">排名</th><th scope="col">验证状态</th>
<th scope="col">全家族 Holm p 值</th><th scope="col">数据或执行说明</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>
<details><summary>来源与公式去重</summary><ul>{source_rows}</ul><ul>{exclusions}</ul></details></section>'''
    receipt = study.get("freeze_receipt")
    if receipt:
        facts = _fact_rows((
            ("公式", receipt["factor_spec"]["formula"]), ("公式版本", receipt["factor_spec"]["version"]),
            ("登记方向", "高值优先" if receipt["direction"] == 1 else "低值优先"),
            ("固定参数窗口", receipt["parameters"]["window"]),
            ("交易费用", f'{receipt["cost"]["fee_bps"]:g} bps'), ("滑点", f'{receipt["cost"]["slippage_bps"]:g} bps'),
            ("保留测试期", " 至 ".join(receipt["test_period"])),
            ("验证试验指纹", receipt["trial_ref"]), ("冻结收据", receipt["fingerprint"]),
        ))
        result += f'<section class="section" data-discovery-freeze><h3>冻结证据</h3><dl class="report-meta">{facts}</dl></section>'
    scope = study.get("review_scope")
    if scope is None:
        return result
    facts = _fact_rows((
        ("公式指纹", scope["factor_ref"]), ("市场标签", scope["market"]),
        ("资产范围", "、".join(study["spec"]["instrument_ids"])), ("资产范围指纹", scope["universe_ref"]),
        ("评估时间", " 至 ".join(scope["time_domain"])), ("用途", "公式研究"),
        ("最终评估指纹", scope["evaluation_fingerprint"]),
    ))
    result += f'<section class="section" data-discovery-review><h3>公式研究入库审核</h3><dl class="report-meta">{facts}</dl>'
    if study["state"] == "REVIEWED":
        event = study.get("admission")
        label = "已批准用于上述公式研究" if study["review_decision"] == "APPROVE_FOR_RESEARCH" else "已拒绝入库"
        result += f'<p>{esc(label)} · {esc(study["reviewer_id"])}</p>'
        if event:
            result += f'<p>审核收据 <code>{esc(event["event_ref"])}</code></p>'
            target = quote(scope["factor_id"], safe="") + "?" + urlencode({"run_ref": event["decision"]["run_ref"]})
            result += f'<p><a href="/research/factors/{esc(target)}#factor-lifecycle">查看此定义的审核历史</a></p>'
    else:
        trial = study["test_result"]["suite_validation"]["trials"][0]
        encoded_scope = json.dumps(scope, ensure_ascii=False).replace("<", "\\u003c")
        approve = ('<option value="APPROVE_FOR_RESEARCH">批准用于公式研究</option>'
                   if trial["status"] == "PASS" and study["spec"]["data_mode"] != "UNKNOWN" else "")
        result += f'''<form data-suite-action="review">
<script type="application/json" data-suite-review-scope>{encoded_scope}</script>
<label>审核结论<select name="decision" required><option value="">请选择</option>{approve}<option value="REJECT">拒绝入库</option></select></label>
<label>审核人标识<input name="reviewer_id" required maxlength="128" placeholder="例如 local-reviewer"></label>
<label class="suite-confirm"><input type="checkbox" required>我已核对上列公式、市场、标的、时间和用途</label>
<button type="submit">保存审核结论</button></form>'''
    return result + "</section>"

"""HTML views of the frozen strategy validation plan and its recorded evidence."""

from datetime import datetime
from functools import lru_cache
from html import escape
from math import isfinite
from numbers import Real
from urllib.parse import urlencode

from .strategy_catalog import StrategyCatalog

BASE = "/research/strategy-validation"
STATUSES = {
    "queued": "已排队", "running": "正在验证", "retryable": "等待重试", "external_waiting": "等待后台处理",
    "completed": "已完成", "cancelled": "已取消", "failed": "验证失败",
    "PAPER_SIMULATED": "纸面模拟", "THEORETICAL": "理论研究", "RESEARCH_ONLY": "理论研究",
    "UNTRADEABLE": "无法成交", "INSUFFICIENT_DATA": "数据不足", "NEEDS_DATA": "待补数据",
    "RECONCILED": "已对平", "MISMATCH": "存在对账差异", "CASH_ONLY": "全现金",
    "COMPLETE": "完整", "PARTIAL": "部分可用", "SOLVED": "约束可行", "INFEASIBLE": "约束不可行",
}
SCENARIOS = {"baseline": "基准情景", "higher_fees": "提高费用", "higher_slippage": "提高滑点", "lower_liquidity": "收紧流动性"}
PARAMETERS = {"rebalance_every": "调仓间隔", "window": "回看窗口", "event_horizon": "事件观察期"}
MODES = {"SAMPLE": "合成样例", "SYNTHETIC": "合成样例", "PAPER": "纸面快照", "CAPTURED": "用户快照", "LIVE": "用户快照"}
REASONS = {
    "LOT_SIZE": "部分委托未达到最小成交单位。", "SETTLEMENT_LOCK": "部分证券仍在交收锁定期。",
    "INSUFFICIENT_CASH": "可用现金不足。", "VOLUME_PARTICIPATION": "成交受市场成交量参与上限约束。",
    "SUSPENDED": "标的暂停交易。", "CLOSED_SESSION": "所在市场休市。",
    "PRICE_LIMIT": "价格触及限制。", "TRADE_NOT_ALLOWED": "所选标的无法交易。",
    "SHORT_UNSUPPORTED": "纸面账本不支持卖空。", "INVALID_POINT_IN_TIME_INPUT": "存在时间点无效的输入。",
    "MISSING_DECISION_EVIDENCE": "缺少决策时点的观测或风险证据。",
    "MISSING_CORPORATE_ACTION_EVIDENCE": "缺少公司行动记录。", "MISSING_EXECUTION_MARK": "缺少成交估值。",
    "MISSING_POSITION_MARK": "缺少持仓估值。", "MISSING_EXECUTION_RULES": "缺少交易规则。",
    "INSUFFICIENT_OBSERVATIONS": "区间内的有效观测不足，请缩短窗口或使用更长的面板。",
    "POLICY_NOT_EFFECTIVE": "冻结的政策在训练开始时尚未生效。",
    "NO_ELIGIBLE_TRAIN_CANDIDATE": "训练期没有可用于选参的候选结果。",
    "INTENTIONAL_CASH": "策略按信号保持全现金。", "INSUFFICIENT_SIGNAL_HISTORY": "当前价格历史不足以形成可执行信号。",
    "NO_FILLS": "没有成交记录。", "EVENT_STATISTICS_ONLY": "只统计已成熟事件的价格变化。",
    "BORROW_NOT_SUPPORTED": "理论多空价差未计入借券和交易成本，各次收益独立计算。",
    "NO_THEORETICAL_SIGNAL": "没有形成理论交易信号。", "NO_MATURE_THEORETICAL_SIGNAL": "没有已成熟的理论信号。",
    "MISSING_FORWARD_PRICE": "缺少前瞻价格。", "BENCHMARK_OR_LEDGER_UNAVAILABLE": "缺少完整的基准或策略账本。",
    "TARGET_EXCEEDS_ELIGIBLE_NAME_CAPACITY": "合格标的的权重上限之和低于目标仓位。",
    "NO_ELIGIBLE_INSTRUMENTS": "没有符合条件的标的。", "THEORETICAL_TEMPLATE": "所选模板只产生理论研究统计。",
    "MISSING_LEDGER_MARKS": "原账本缺少权益估值。", "NONPOSITIVE_EQUITY": "原账本权益为零或负值。",
    "INSUFFICIENT_COMMON_INTERVALS": "共同收益区间不足。", "MISSING_PRICES_OR_SPLITS": "缺少价格或拆分调整记录。",
    "NONFINITE_COVARIANCE": "协方差计算未得到有限数值。",
}
PENDING = {"queued", "running", "external_waiting", "retryable"}


def _h(value):
    return escape(str(value), quote=True)


def _number(value):
    return isinstance(value, Real) and not isinstance(value, bool) and isfinite(value)


def _fmt(value, style="number"):
    if not _number(value):
        return "—"
    if style == "pct":
        return f"{value:.2%}"
    if style == "signed":
        return f"{value:+.2%}" if value else "0.00%"
    if style == "money":
        return f"{value:,.2f}"
    if 0 < abs(value) < 1e-5:
        return f"{value:.3g}"
    return f"{value:,.6f}".rstrip("0").rstrip(".")


def _status(value):
    return _h(STATUSES.get(value, value or "无记录"))


def _date(value):
    if not value:
        return "—"
    stamp = str(value)
    day, _, time = stamp.partition("T")
    label = day
    if time and time[:8] != "00:00:00":
        label += " " + time[:8]
    return f'<time datetime="{_h(stamp)}" title="{_h(stamp)}">{_h(label)}</time>'


def _url(**fields):
    return _h(BASE + "?" + urlencode(fields))


def _hidden(name, value):
    return f'<input type="hidden" name="{name}" value="{_h(value)}">'


def _local_link(path, label, *, download=False):
    if isinstance(path, str) and path.startswith("/research/") and "\\" not in path and not any(c.isspace() for c in path):
        attrs = ' download="strategy-validation.html"' if download else ""
        return f'<a href="{_h(path)}"{attrs}>{_h(label)} <span aria-hidden="true">{"↓" if download else "↗"}</span></a>'
    return ""


@lru_cache(maxsize=1)
def _templates():
    return {t["template_id"]: t["title"] for t in StrategyCatalog().templates()}


def _strategy(source):
    key = source.get("template_id")
    return _templates().get(key, key or "归档策略")


def _facts(items):
    return '<dl class="sv-facts">' + "".join(
        f'<div><dt>{_h(label)}</dt><dd>{value}</dd></div>' for label, value in items
    ) + "</dl>"


def _table(caption, headers, rows):
    if not rows:
        return '<p class="sv-note">此项尚无可核对的记录。</p>'
    headings = "".join(f'<th scope="col">{_h(item)}</th>' for item in headers)
    body = "".join('<tr><th scope="row">' + cells[0] + '</th>' + "".join(f'<td>{item}</td>' for item in cells[1:]) + '</tr>' for cells in rows)
    return (f'<div class="sv-scroll" role="region" aria-label="{_h(caption)}" tabindex="0"><table>'
            f'<caption class="sv-sr">{_h(caption)}</caption><thead><tr>{headings}</tr></thead><tbody>{body}</tbody></table></div>')


def _reasons(values):
    return '<ul class="sv-reasons">' + "".join(f'<li>{_h(REASONS.get(v, v))}</li>' for v in values) + '</ul>' if values else ""


def _field(name, label, value, *, bounds=None, step=None, placeholder=None):
    attrs = ""
    if bounds:
        attrs += f' min="{bounds[0]}" max="{bounds[1]}" step="{step or "1"}"'
    if placeholder:
        attrs += f' placeholder="{_h(placeholder)}"'
    field_type = "number" if bounds else "text"
    required = " required" if name in {"train_size", "test_size", "target_exposure", "max_single_weight", "stability_tolerance"} else ""
    return f'<label>{_h(label)}<input name="{name}" type="{field_type}" value="{_h(value)}"{attrs}{required}></label>'


def _plan_form(sources, run, plan, csrf):
    if not sources:
        return f'''<section class="sv-editor" aria-labelledby="sv-editor-heading"><h2 id="sv-editor-heading">选择策略来源</h2>
<p>工作区里还没有已发布的策略报告。可以用固定的股票与 ETF 合成样例（SAMPLE）生成第一份账本。</p>
<form action="{BASE}/example" method="post">{_hidden("_csrf",csrf)}
<button type="submit" class="sv-primary">生成纸面示例账本 <span aria-hidden="true">→</span></button></form>
<a class="sv-alternative" href="/research/strategies">从自己的面板创建纸面策略 ↗</a></section>'''
    options = []
    for source in sources:
        run_id = source["run_id"]
        label = " · ".join(filter(None, (_strategy(source), MODES.get(source.get("data_mode")), run_id[-10:])))
        options.append(f'<option value="{_h(run_id)}"{" selected" if run_id == run else ""}>{_h(label)}</option>')
    values = plan.get("options") or {}
    portfolio = plan.get("portfolio") or {}
    standard = "".join([
        _field("train_size", "训练期数", values.get("train_size", 32), bounds=(6,120)),
        _field("test_size", "测试期数", values.get("test_size", 16), bounds=(3,60)),
    ])
    details = "".join([
        _field("parameter_values", "候选参数（逗号分隔，最多 5 个）", ", ".join(map(str, values.get("parameter_values") or [])), placeholder="使用模板参数附近的值"),
        _field("stability_tolerance", "稳定区间容差（训练得分差）", values.get("stability_tolerance", .002), bounds=(0,.1), step="any"),
        _field("fee_bps", "基准情景费用（基点）", values.get("fee_bps") if values.get("fee_bps") is not None else "", bounds=(0,100), step="any", placeholder="按冻结方案"),
        _field("slippage_bps", "基准情景滑点（基点）", values.get("slippage_bps") if values.get("slippage_bps") is not None else "", bounds=(0,100), step="any", placeholder="按冻结方案"),
        _field("max_volume_participation", "成交量参与上限（比例）", values.get("max_volume_participation") if values.get("max_volume_participation") is not None else "", bounds=(0,1), step="any", placeholder="按冻结方案"),
        _field("target_exposure", "组合目标仓位（比例）", portfolio.get("target_exposure", .8), bounds=(0,1), step="any"),
        _field("max_single_weight", "单一标的权重上限（比例）", portfolio.get("max_single_weight", .4), bounds=(0,1), step="any"),
    ])
    return f'''<section class="sv-editor" aria-labelledby="sv-editor-heading"><h2 id="sv-editor-heading">验证设置</h2>
<form action="{BASE}/preview" method="post">{_hidden("_csrf", csrf)}
<label>策略来源<select name="run_id" required><option value=""{"" if run else " selected"}>请选择已发布的策略报告</option>{"".join(options)}</select></label>
<div class="sv-fields">{standard}</div><details class="sv-options"><summary>参数、交易成本与组合约束</summary>
<div class="sv-fields sv-advanced">{details}</div><p class="sv-note">1 个基点等于 0.01%。仓位和参与率使用 0 至 1 的比例。</p></details>
<button type="submit" class="sv-primary">预览验证方案 <span aria-hidden="true">→</span></button></form></section>'''


def _axis_value(stamp):
    return datetime.fromisoformat(stamp).timestamp()


def _timeline(plan):
    rows = plan.get("windows") or []
    actual = bool(rows)
    if actual:
        keys = ("train_start","train_end","test_start","test_end")
        stamps = [_axis_value(r[k]) for r in rows for k in keys if r.get(k)]
        lo, hi = (min(stamps), max(stamps)) if stamps else (0,1)

        def scale(stamp):
            if not stamp:
                return None
            return round(4 + 792 * (_axis_value(stamp) - lo) / max(hi-lo,1),2)

        coords = [tuple(scale(r.get(k)) for k in keys) for r in rows]
    else:
        # Only the method diagram uses schematic coordinates.
        coords = [(4,360,368,508),(146,502,510,650),(288,644,652,792)]
    ribbons = []
    for i, (a,b,c,d) in enumerate(coords):
        y = 14 + i * 40
        ribbons.append(f'<line x1="0" x2="800" y1="{y+7}" y2="{y+7}" class="sv-track"/>')
        if a is not None and b is not None:
            ribbons.append(f'<rect x="{a}" y="{y}" width="{max(2,b-a):.2f}" height="14" fill="url(#sv-train-hatch)"/>')
        if c is not None and d is not None:
            ribbons.append(f'<rect x="{c}" y="{y}" width="{max(2,d-c):.2f}" height="14" class="sv-test-band"/>'
                           f'<line x1="{c}" x2="{c}" y1="{y-4}" y2="{y+18}" class="sv-seam"/>')
    height = 20 + len(coords)*40
    actual_dates = [r[key] for r in rows for key in ("train_start","train_end","test_start","test_end") if r.get(key)]
    axis = (_date(min(actual_dates)), _date(max(actual_dates))) if actual_dates else (("—","—") if actual else ("较早观测", "较晚观测"))
    labels = "".join(f'<span>窗口 {i+1}</span>' for i in range(len(coords)))
    chart = f'''<div class="sv-timeline"><div class="sv-rowlabels">{labels}</div>
<svg viewBox="0 0 800 {height}" preserveAspectRatio="none" width="800" height="{height}" role="img" aria-label="滚动窗口的训练区间和样本外测试区间">
<defs><pattern id="sv-train-hatch" width="8" height="8" patternUnits="userSpaceOnUse"><rect width="8" height="8" fill="#6f9381"/>
<path d="M-2 2l4 -4m-2 10l8 -8m-2 10l4 -4" stroke="#a2b9a1" stroke-width="1"/></pattern></defs>{"".join(ribbons)}</svg>
<div class="sv-dates"><span>{axis[0]}</span><span>{axis[1]}</span></div></div>'''
    caption = "冻结面板的实际区间 · UTC" if actual else "方法示意 · 预览后显示实际日期"
    legend = '<div class="sv-legend"><span>训练选参</span><span>样本外测试</span></div>'
    details = ""
    if rows:
        data = [[f"窗口 {i+1}", _date(r["train_start"]), _date(r["train_end"]), _date(r["test_start"]), _date(r["test_end"]), _fmt(r.get("train_observations")), _fmt(r.get("test_observations"))] for i,r in enumerate(rows)]
        details = '<details class="sv-chart-data"><summary>读取各窗口起止日期</summary>' + _table("窗口日期（含首尾）", ["窗口","训练开始","训练结束","测试开始","测试结束","训练期数","测试期数"], data) + '</details>'
    opts = plan.get("options") or {}
    short = actual and any(r.get("train_observations",opts.get("train_size")) != opts.get("train_size") or
                           r.get("test_observations",opts.get("test_size")) != opts.get("test_size") for r in rows)
    incomplete = '<p>部分窗口的观测不足。可缩短训练期或测试期后重新预览。</p>' if short else ""
    return f'''<figure class="sv-plate"><figcaption><span>滚动验证区间</span><small>{caption}</small></figcaption>{legend}{chart}
<p>每个窗口先用训练期选择参数，再固定参数检验随后的观测。</p>{incomplete}{details}</figure>'''


def _confirmation(plan, run, csrf=None):
    fingerprint = plan.get("plan_fingerprint")
    if not plan:
        return ""
    scenarios = plan.get("scenarios") or []
    rows = [[_h(SCENARIOS.get(s["id"],s["id"])), _fmt(s.get("fee_bps")), _fmt(s.get("slippage_bps")),
             _fmt(s.get("max_volume_participation"),"pct"), _fmt(s.get("starting_cash"),"money")] for s in scenarios]
    values = "、".join(_fmt(v) for v in (plan.get("parameter_values") or []))
    detail = _table("冻结执行情景", ["执行情景","费用 / bp","滑点 / bp","成交参与上限","起始资金"], rows) if rows else ""
    objective_names = {"net_return":"纸面净收益率","mean_forward_spread_price_return":"平均前瞻价差收益率",
                       "mean_matured_event_price_return":"已成熟事件平均价格收益率"}
    objective = objective_names.get(plan.get("objective"),"模板得分")
    tol = _fmt((plan.get("options") or {}).get("stability_tolerance"))
    method = f'<p class="sv-lede">按训练期{objective}选参，最高分附近的连续候选值构成稳定区间。本次得分容差为 {tol}。</p>'
    action = ""
    if csrf is not None and fingerprint:
        action = f'''<form action="{BASE}/run" method="post">{_hidden("_csrf",csrf)}{_hidden("run_id",run)}{_hidden("plan_fingerprint",fingerprint)}
<button type="submit" class="sv-primary">运行冻结方案 <span aria-hidden="true">→</span></button></form>'''
    source = plan.get("source") or {}
    inputs = [("快照指纹",source.get("snapshot_ref")),("来源报告指纹",source.get("report_digest")),
              ("来源账本指纹",source.get("ledger_digest")), ("执行政策引用",source.get("policy_ref")),
              ("面板指纹",source.get("panel_ref")),("策略模板指纹",source.get("template_digest")),
              ("执行政策指纹",source.get("policy_digest"))]
    provenance = "".join(f'<p class="sv-fingerprint">{k} <code>{_h(v)}</code></p>' for k,v in inputs if v)
    if provenance:
        provenance = f'<details class="sv-detail"><summary>核对输入与版本指纹</summary>{provenance}</details>'
    return f'''<section class="sv-block sv-frozen"><div class="sv-section-head"><h2>冻结方案</h2>
<span>{_h(PARAMETERS.get(plan.get("parameter"), plan.get("parameter") or "模板参数"))} · {values or "—"}</span></div>
{method}{detail}{provenance}<p class="sv-fingerprint">方案指纹 <code>{_h(fingerprint or "—")}</code></p>{action}</section>'''



def _curve(scenario, title, currency, *, offline):
    series = scenario.get("series") or []
    if not series or any(not _number(mark.get("equity")) for mark in series):
        return ""
    baseline = all(_number(mark.get("benchmark")) for mark in series)
    values = [mark["equity"] for mark in series] + ([mark["benchmark"] for mark in series] if baseline else [])
    low, high = min(values), max(values)
    if high == low:
        pad = max(.01, abs(high)*.002)
        low, high = low-pad, high+pad
    steps = max(1,len(series)-1)
    def line(field, css_class):
        coords = [(round(i/steps*800,2),round(4+(high-mark[field])/(high-low)*122,2)) for i,mark in enumerate(series)]
        if len(coords) == 1:
            return f'<circle cx="{coords[0][0]}" cy="{coords[0][1]}" r="3" class="{css_class}"/>'
        attrs = " ".join(f"{x},{y}" for x,y in coords)
        return f'<polyline points="{attrs}" class="{css_class}"/>'
    cells = [[_date(mark.get("timestamp")), _fmt(mark.get("equity"),"money"),_fmt(mark.get("benchmark"),"money")] for mark in series]
    data = _table(f"{title}逐期权益", ["观测日期（UTC）",f"策略权益 · {currency}",f"基准权益 · {currency}"],cells)
    legend = '<div class="sv-line-key"><span>策略</span>' + ('<span>同日基准</span>' if baseline else '') + '</div>'
    comparison = line("benchmark","sv-benchmark") if baseline else ""
    grid = "".join(f'<line x1="0" x2="800" y1="{y}" y2="{y}" class="sv-gridline"/>' for y in (4,65,126))
    control = "" if offline else f'''<div class="sv-scrub" data-sv-scrub hidden><label>逐期读数<input type="range" aria-label="{_h(title)}逐期读数" min="0" max="{len(series)-1}" value="{len(series)-1}" step="1"></label><output></output></div>'''
    return f'''<figure class="sv-curve" data-sv-curve><figcaption>{_h(title)} · 纸面权益 <small>{_h(currency)}</small></figcaption>{legend}
<div class="sv-curve-plot"><div class="sv-yscale"><span>{_fmt(high,"money")}</span><span>{_fmt(low,"money")}</span></div>
<svg viewBox="-3 0 806 130" preserveAspectRatio="none" width="800" height="130" role="img" aria-label="{_h(title)}逐期权益轨迹，详见逐期表">
{grid}{comparison}{line("equity","sv-equity")}<line data-sv-cursor x1="800" x2="800" y1="0" y2="130" hidden/></svg>
<div class="sv-dates"><span>{cells[0][0]}</span><span>{cells[-1][0]}</span></div></div>{control}
<details class="sv-chart-data"><summary>查看 {len(series)} 期原始读数</summary>{data}</details></figure>'''


def _selection(selection):
    candidates = selection.get("candidates") or []
    if not candidates:
        return ""
    rows = [[_fmt(row.get("value")),_fmt(row.get("score")), _status(row.get("status")),
             _reasons(row.get("reasons")) or "—"] for row in candidates]
    stable = selection.get("stable_regions") or []
    stable_labels = ["、".join(_fmt(v) for v in group.get("values",[])) for group in stable]
    return (f'<details class="sv-detail"><summary>训练期参数选择 · 入选 {_fmt(selection.get("selected_value"))}</summary>'
            f'<p>容差内的候选区间为 {_h(" / ".join(stable_labels) or "尚无可用区间")}。</p>' +
            _table("训练期参数得分", ["参数值","训练得分","证据状态","原因"],rows) + '</details>')


def _scenario_metrics(scenarios):
    rows = []
    for row in scenarios:
        values = row.get("metrics") or {}
        name = _h(SCENARIOS.get(row.get("id"),row.get("id","—")))
        rows.append([name,_status(row.get("status")), _fmt(values.get("net_return"),"signed"),
                     _fmt(values.get("benchmark_return"),"signed"), _fmt(values.get("fees"),"money"),
                     _fmt(values.get("slippage"),"money"), _fmt(values.get("fill_count"))+" / "+_fmt(values.get("order_count"))])
    return _table("样本外执行情景比较", ["执行情景","证据状态","净收益率","基准收益率","费用","滑点成本","成交 / 订单"],rows)


def _research_metrics(values):
    names = {"observation_count":"前瞻观测数", "mean_forward_spread_price_return":"平均前瞻价差收益率",
             "mean_matured_event_price_return":"已成熟事件平均价格收益率", "matured_events":"已成熟事件数",
             "pending_events":"待成熟事件数", "missing_marks":"缺失估值数"}
    metrics = _facts([(label, _fmt(values[name], "signed" if name.endswith("return") else "number"))
                      for name,label in names.items() if name in values])
    groups = values.get("groups") or {}
    if groups:
        metrics += _table("已成熟事件分组",["事件类别","已成熟事件数","平均价格收益率"],
                          [[_h(key),_fmt(row.get("count")),_fmt(row.get("mean_price_return"),"signed")] for key,row in groups.items()])
    obs = values.get("observations") or []
    if obs:
        metrics += _table("逐次前瞻信号",["决策日","入场日","出场日","价差收益率","理论权重"],
                          [[_date(row.get("decision_at")),_date(row.get("entry_at")),_date(row.get("exit_at")),
                            _fmt(row.get("spread_price_return"),"signed"),
                            "、".join(f"{_h(k)} {_fmt(v,'signed')}" for k,v in row.get("weights",{}).items())] for row in obs])
    return metrics


def _window(window, seq, currency, *, offline):
    selection = window.get("selection") or {}
    scenarios = window.get("scenarios") or []
    theory = any(r.get("status") == "THEORETICAL" for r in scenarios)
    summary = _scenario_metrics(scenarios) if scenarios and not theory else ""
    charts = []
    for row in scenarios:
        name = SCENARIOS.get(row.get("id"),row.get("id","—"))
        curve = _curve(row, name, (row.get("metrics") or {}).get("currency") or currency, offline=offline)
        reasons = _reasons(row.get("reasons"))
        research = _research_metrics(row.get("research_metrics") or row.get("metrics") or {}) if row.get("status") == "THEORETICAL" else ""
        if row.get("status") == "PAPER_SIMULATED":
            metrics = row.get("metrics") or {}
            excess = metrics.get("excess_return")
            points = f"{excess * 100:+.2f} 个百分点" if _number(excess) else "—"
            curve += _facts([("起始资金", _fmt(metrics.get("starting_cash"),"money")),
                             ("期末权益", _fmt(metrics.get("final_equity"),"money")),
                             ("毛损益", _fmt(metrics.get("gross_pnl"),"money")),
                             ("净损益", _fmt(metrics.get("net_pnl"),"money")), ("相对基准",points)])
        digest = row.get("ledger_digest")
        if digest:
            curve += f'<p class="sv-fingerprint">情景账本指纹 <code>{_h(digest)}</code></p>'
        if curve or reasons or research:
            opened = " open" if row.get("id") == "baseline" or offline else ""
            charts.append(f'<details class="sv-detail sv-evidence"{opened}><summary>{_h(name)} · {_status(row.get("status"))}</summary>{reasons}{research}{curve}</details>')
    note = f'<p class="sv-note">费用与滑点成本以 {_h(currency)} 计。净收益率已计入两项成本。</p>' if any(r.get("status") == "PAPER_SIMULATED" for r in scenarios) else ""
    return f'''<article class="sv-window" id="sv-window-{seq}"><header><h3>窗口 {seq}</h3><span>{_date(window.get("test_start"))} — {_date(window.get("test_end"))}</span>
<strong>{_status(window.get("status"))}</strong></header>
<p class="sv-note">训练期 {_date(window.get("train_start"))} 至 {_date(window.get("train_end"))}</p>
{_reasons(window.get("reasons"))}{_selection(selection)}{summary}{note}{"".join(charts)}</article>'''


def _walkforward(walk, currency, *, offline):
    windows = walk.get("windows") or []
    counts = walk.get("status_counts") or {}
    tally = " · ".join(f"{_status(key)} {count}" for key,count in counts.items() if type(count) is int and count)
    rows = []
    for index, window in enumerate(windows,1):
        baseline = next((row for row in window.get("scenarios",[]) if row.get("id") == "baseline"),{})
        metrics = baseline.get("metrics") or {}
        measure = next((metrics[key] for key in ("net_return","mean_forward_spread_price_return","mean_matured_event_price_return")
                        if _number(metrics.get(key))), None)
        rows.append([f'<a href="#sv-window-{index}">窗口 {index}</a>', _status(window.get("status")),
                     _fmt((window.get("selection") or {}).get("selected_value")), _fmt(window.get("test_observations")),
                     _fmt(measure,"signed"), _fmt(metrics.get("benchmark_return"),"signed")])
    template = walk.get("template_id")
    metric_title = {"event-study":"平均事件收益率","multifactor-neutral":"平均前瞻价差收益率",
                    "pairs-mean-reversion":"平均前瞻价差收益率"}.get(template,"基准情景净收益率")
    overview = _table("三个样本外窗口对照",["窗口","证据状态","入选参数","测试期数",metric_title,"市场基准收益率"],rows) if rows else ""
    method = ("各窗口的价格收益按观测取平均，未计入交易费用。"
              if template in {"event-study","multifactor-neutral","pairs-mean-reversion"}
              else "每个窗口独立回放。纸面账户从相同的初始资金和零持仓开始。")
    body = "".join(_window(item,i,currency,offline=offline) for i,item in enumerate(windows,1))
    if not body:
        body = '<p>验证记录没有可读取的样本外窗口。</p>'
    return f'''<section class="sv-block" id="sv-results"><div class="sv-section-head"><h2>样本外结果</h2><span>{tally}</span></div>
<p class="sv-note">{method}</p>{overview}{body}</section>'''


def _portfolio(portfolio, *, offline):
    if not portfolio:
        return '<section class="sv-block"><h2>原账本组合诊断</h2><p>尚无可读取的组合诊断记录。</p></section>'
    account = portfolio.get("account") or {}
    attribution = portfolio.get("attribution") or {}
    solver = portfolio.get("solver") or {}
    risk = portfolio.get("risk") or {}
    industry = portfolio.get("industry") or {}
    factors = portfolio.get("factors") or {}
    currency = portfolio.get("currency") or "账本币种"
    positions = account.get("holdings") or []
    dates = _date(portfolio.get("as_of"))
    main = _facts([("期初权益",_fmt(account.get("opening_equity"),"money")),("期末权益",_fmt(account.get("closing_equity"),"money")),
                   ("可用现金",_fmt(account.get("available_cash"),"money")),("待结算现金",_fmt(account.get("unsettled_cash"),"money"))])
    holdings = _table("期末持仓",["标的","数量","价格","市值","占总权益"],[
        [_h(row["instrument"]),_fmt(row.get("quantity")),_fmt(row.get("price"),"money"),_fmt(row.get("value"),"money"),_fmt(row.get("weight"),"pct")] for row in positions
    ])
    reconcile = _facts([(label,_fmt(attribution.get(field),fmt)) for field,label,fmt in (
        ("gross_pnl","毛损益（含股息）","money"),("dividends","现金股息","money"),("fees","交易费用","money"),
        ("slippage","滑点成本","money"),("net_pnl","净损益","money"),("residual","对账差额","money"),
        ("gross_return","毛收益率","signed"),("net_return","净收益率","signed"),("turnover","总成交额 / 期初权益","pct"))])
    reconcile = _facts([("对账状态",_status(attribution.get("status")))]) + reconcile
    if attribution.get("by_asset"):
        reconcile += _table("逐标的损益对账",["标的","价格损益","股息","毛损益","费用","滑点","净损益"],
                            [[_h(r["instrument"]), *[_fmt(r.get(k),"money") for k in ("price_pnl","dividends","gross_pnl","fees","slippage","net_pnl")]]
                             for r in attribution["by_asset"]])
    solved = '<p>对可交易标的求解满足目标仓位与权重上限、最接近原持仓的假设组合。</p>' + _facts([("状态",_status(solver.get("status"))),("目标仓位",_fmt(solver.get("target_exposure"),"pct")),
                     ("单一权重上限",_fmt(solver.get("max_single_weight"),"pct")),("求解后现金权重",_fmt(solver.get("cash_weight"),"pct"))])
    allocation = _table("组合求解权重",["标的","目标权重"],[[_h(asset),_fmt(weight,"pct")] for asset,weight in (solver.get("weights") or {}).items()])
    sectors = [[_h(name),_fmt(weight,"pct")] for name,weight in (industry.get("weights") or {}).items()]
    styles = [[_h("风格贝塔" if name == "style_beta" else name), _status(row.get("status")), _fmt(row.get("value")),
               _fmt(row.get("known_value")), _fmt(row.get("known_weight"),"pct"), _fmt(row.get("missing_weight"),"pct")]
              for name,row in factors.items() if isinstance(row,dict)]
    exposure = _facts([("行业覆盖状态",_status(industry.get("status"))),("已识别行业权重",_fmt(industry.get("known_weight"),"pct")),
                       ("缺失行业权重",_fmt(industry.get("missing_weight"),"pct"))])
    exposure += _table("行业暴露",["行业","占总权益"],sectors) + _table("因子暴露覆盖",["因子","状态","组合暴露","已知暴露","已知权重","缺失权重"],styles)
    riskdata = _facts([("风险状态",_status(risk.get("status"))),("观测数",_fmt(risk.get("observations"))),
                       ("组合方差",_fmt(risk.get("variance"))),("单期波动率",_fmt(risk.get("volatility"),"pct"))])
    riskdata += _table("资产对组合风险的贡献",["标的","方差贡献","占组合方差"],[
        [_h(row.get("instrument","")), _fmt(row.get("variance")), _fmt(row.get("share"),"pct")] for row in (risk.get("contributions") or [])
    ])
    riskdata += '<p class="sv-note">按期末持仓和最多 60 个拆分调整后的价格收益区间计算，波动率以单个观测期计。</p>'
    if risk.get("reason"):
        riskdata += _reasons([risk["reason"]])
    shock_names = {"MARKET_DOWN_10":"市场下跌 10%", "EQUITY_DOWN_20_ETF_DOWN_05":"股票下跌 20%，ETF 下跌 5%"}
    asset_kinds = {"CASH":"现金","EQUITY":"股票","ETF":"ETF"}
    stress = _table("静态压力冲击",["冲击情景","状态","组合变动","估算损益","各资产类别冲击"],[
        [_h(shock_names.get(row.get("scenario"),row.get("scenario","—"))),_status(row.get("status")),_fmt(row.get("portfolio_return"),"signed"),_fmt(row.get("pnl"),"money"),
         "、".join(f"{_h(asset_kinds.get(k,k))} {_fmt(v,'signed')}" for k,v in (row.get("shocks") or {}).items()) or "—"]
        for row in (portfolio.get("stress") or [])
    ])
    reason = _reasons([solver["reason"]]) if solver.get("reason") else ""
    sections = [("持仓与现金",main+holdings),("收益归因与对账",reconcile),("组合约束求解",solved+reason+allocation),
                ("行业与因子暴露",exposure),("风险贡献与压力测试",riskdata+stress)]
    body = "".join(f'<details class="sv-detail"{" open" if i == 0 or offline else ""}><summary>{name}</summary>{content}</details>' for i,(name,content) in enumerate(sections))
    if portfolio.get("status") in {"RESEARCH_ONLY","NEEDS_DATA"} and not account.get("closing_equity"):
        body = ('<p>当前来源没有足够的纸面账本记录可用于持仓、归因和风险核对。</p>' +
                _reasons([portfolio["reason"]] if portfolio.get("reason") else []))
    digest = portfolio.get("ledger_digest")
    trace = f'<p class="sv-fingerprint">来源账本指纹 <code>{_h(digest)}</code></p>' if digest else ""
    return f'''<section class="sv-block" id="sv-portfolio"><div class="sv-section-head"><h2>原账本组合诊断</h2><span>{_status(portfolio.get("status"))}</span></div>
<p class="sv-lede">取自原策略的冻结账本。观察截止 {dates}，金额以 {_h(currency)} 计。</p>{body}{trace}</section>'''


def _result_body(result, *, offline):
    currency = (result.get("portfolio") or {}).get("currency") or "账本币种"
    return _walkforward(result.get("walkforward") or {}, currency, offline=offline) + _portfolio(result.get("portfolio"),offline=offline)


def _result_header(result):
    state = result.get("status")
    url = _url(study=result["study_id"])
    report = _local_link(result.get("report_url"),"打开验证报告") if state == "completed" else ""
    actions = f'<a href="{url}">{"刷新验证进度" if state in PENDING else "验证记录链接"} <span aria-hidden="true">↗</span></a>{report}'
    text = {
        "failed":"打开后台任务核对失败原因，检查来源或验证参数后可以重新预览。",
        "cancelled":"这次验证已取消。可以重新预览方案并查看可用记录。",
    }.get(state, "")
    phase = _status(state)
    if state in PENDING:
        text = "后台已登记这份冻结方案。可以离开页面，稍后从验证记录查看结果。"
        labels = " ".join(f'data-{key}="{_status(key)}"' for key in sorted(PENDING))
        phase = f'<span data-sv-phase {labels}>{phase}</span>'
    failure = result.get("failure_kind")
    if failure:
        text += " 失败类型为 " + str(failure)
    if state == "completed":
        actions += _local_link(result.get("report_url"),"下载离线报告",download=True)
        actions += '<a href="#sv-portfolio">原账本组合诊断 ↓</a>'
    if state in {"failed","retryable","external_waiting","cancelled"}:
        actions += '<a href="/research/jobs">后台任务 ↗</a>'
    refresh = f' data-sv-refresh="{url}"' if state in PENDING else ""
    return f'''<div class="sv-receipt" role="status"{refresh}><div><h2>验证记录 · {phase}</h2>
<code>{_h(result["study_id"])}</code>{f"<p>{_h(text)}</p>" if text else ""}</div><div class="sv-actions">{actions}</div></div>'''


def _history(history):
    if not history:
        return ""
    rows = []
    for study in history:
        ref = study["study_id"]
        rows.append([f'<a href="{_url(study=ref)}">{_h(ref)}</a>', _status(study.get("status")), _h(study.get("run_id") or "—")])
    return f'<section class="sv-block" id="sv-history"><h2>验证记录</h2>{_table("已登记的验证任务",["验证任务","队列状态","来源策略运行"],rows)}</section>'


def render_validation_page(*, sources, selected_run, plan, result, history, csrf_token, message=""):
    """Render a native validation workspace; every write requires a submitted form."""
    plan, result = plan or {}, result or {}
    run = selected_run or result.get("run_id") or plan.get("run_id") or ""
    source = next((item for item in sources if item["run_id"] == run), {})
    source_title = _strategy(source) if source else ""
    ctx = ""
    if source:
        mode = MODES.get(source.get("data_mode"),source.get("data_mode",""))
        report = _local_link(source.get("report_url"),"原策略报告")
        ctx = f'''<div class="sv-source"><p><strong>来源</strong> {_h(source_title)} · {_h(mode)} <span>截至 {_date(source.get("as_of"))}</span></p>{report}</div>'''
    head = f'''<header class="sv-head"><div><h1>策略验证室</h1>
<p>从一份已发布策略出发，逐窗检验样本外表现，核对交易成本和组合风险。</p></div>
<div class="sv-headlinks"><a href="/research/atlas/runs">运行复盘 ↗</a>{'<a href="#sv-history">验证记录 ↓</a>' if history else ""}</div></header>'''
    alert = f'<div class="sv-alert" role="alert">{_h(message)}</div>' if message else ""
    plate = _timeline(plan)
    editor = _plan_form(sources, run, plan, csrf_token)
    editor_first = not sources
    content = editor + plate if editor_first else plate + editor
    preparation = f'<div class="sv-workbench{" sv-workbench-empty" if editor_first else ""}">{content}</div>'
    if result:
        setup = f'<details class="sv-reconfigure"><summary>查看方案与验证设置</summary>{preparation}{_confirmation(plan,run)}</details>'
        body = ctx + _result_header(result) + setup
        if result.get("status") == "completed":
            body += _result_body(result,offline=False)
    else:
        body = ctx + preparation + _confirmation(plan,run,csrf_token)
    return f'<article class="sv-page" data-strategy-validation aria-label="策略验证室">{head}{alert}{body}{_history(history or [])}</article>'


def render_validation_report(plan, result):
    """Portable, server-rendered evidence with readable charts and complete values."""
    walk = result.get("walkforward") or {}
    template = plan.get("template_id") or walk.get("template_id")
    label = _templates().get(template,template or "归档策略")
    mode = (plan.get("source") or {}).get("data_mode")
    mode_label = f"{MODES.get(mode,mode)}（{mode}）" if mode else ""
    origin = " · ".join(filter(None,(str(label), mode_label, result.get("run_id") or plan.get("run_id"))))
    return f'''<article class="sv-page sv-offline"><header class="sv-head"><div><h1>策略验证报告</h1>
<p>{_h(origin)}</p></div></header>
{_timeline(plan)}{_confirmation(plan,plan.get("run_id"))}
{_result_body(result,offline=True)}</article>'''

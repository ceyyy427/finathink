"""Server-rendered form structure for the research panel workspace."""

from html import escape

from finahinking.data.panel_contracts import CURRENCIES


def saved_panel_select(field_id="saved-panel-ref", label="已保存面板"):
    field_id, label = escape(field_id, quote=True), escape(label)
    return f"""<div data-panel-picker>
<label for="{field_id}">{label}</label>
<select id="{field_id}" name="saved_panel_ref" data-saved-panel-select>
<option value="">请选择研究面板</option></select>
<p class="field-help"><a href="/research/panels">准备或查看研究面板</a></p></div>"""


def panel_workbench_page(csrf):
    currencies = "".join(f'<option value="{item}">{item}</option>' for item in sorted(CURRENCIES))
    return f"""<style>
.panel-workbench form {{ display:grid; grid-template-columns:minmax(0,1fr); gap:12px }}
.panel-workbench fieldset {{ min-width:0 }}
.panel-workbench .panel-choices {{ display:grid; gap:6px; max-height:18rem; overflow:auto }}
.panel-workbench .panel-choices label {{ display:flex; align-items:flex-start; gap:8px; overflow-wrap:anywhere }}
.panel-workbench input[type=checkbox] {{ width:1.1rem; min-height:1.1rem; flex:none }}
.panel-workbench select {{ max-width:100% }}
.panel-workbench :is(code, td, li) {{ overflow-wrap:anywhere }}
.panel-workbench [data-panel-preview] {{ min-width:0; max-width:100%; overflow-wrap:anywhere }}
.panel-workbench [data-panel-preview] .table-wrap {{ overflow-x:auto }}
.panel-workbench [data-panel-preview] table {{ min-width:36rem }}
.panel-workbench [data-panel-saved] li {{ margin-block:.8rem }}
</style><div class="panel-workbench" data-panel-workbench data-csrf="{escape(csrf, quote=True)}">
<header><h1>研究面板</h1><p class="lede">选用自己的行情快照和规则附件，核对每个决策时点可用的资料。保存后可在不同研究里复用。</p></header>
<section class="panel section" aria-labelledby="panel-prices-title">
<h2 id="panel-prices-title">行情快照</h2>
<p><a href="/settings/data-connections">配置数据接口和字段映射</a></p>
<details><summary>从已保存的连接获取新快照</summary>
<form data-panel-price>
<label>行情连接<select name="connection_id" required><option value="">请选择行情连接</option></select></label>
<label>股票标的<input name="equities" placeholder="用逗号分隔" maxlength="240"></label>
<label>ETF 标的<input name="etfs" placeholder="用逗号分隔" maxlength="240"></label>
<label>行情开始日期<input name="start" type="date" required></label>
<label>行情截止日期<input name="end" type="date" required></label>
<label>价格币种<select name="price_currency" required><option value="">请选择</option>{currencies}</select></label>
<label><input type="checkbox" name="raw_units" required> 这是原始价格，成交量按股或份记录</label>
<div><button type="submit" disabled>获取行情快照</button></div>
</form></details>
<p role="status" data-panel-price-status></p>
</section>
<section class="panel section" aria-labelledby="panel-build-title"><h2 id="panel-build-title">准备面板</h2>
<form data-panel-draft>
<label>行情快照<select name="snapshot_ref" required><option value="">正在读取快照…</option></select></label>
<p class="field-help" data-panel-snapshot-status role="status">选择快照后查看标的和可用附件。</p>
<fieldset><legend>标的</legend><div class="panel-choices" data-panel-assets></div></fieldset>
<label>研究开始日期（UTC）<input name="start" type="date" required></label>
<label>研究截止日期（UTC）<input name="end" type="date" required></label>
<label>资产时区<input name="timezone" required value="UTC" maxlength="64" list="panel-timezones" spellcheck="false"></label>
<datalist id="panel-timezones"><option value="UTC"><option value="Asia/Shanghai"><option value="Asia/Hong_Kong"><option value="America/New_York"><option value="Europe/London"></datalist>
<fieldset><legend>规则附件</legend><div data-panel-rules></div></fieldset>
<fieldset><legend>附加研究字段</legend><div class="panel-choices" data-panel-attachments></div></fieldset>
<p class="field-help">一个附件可同时供多个规则使用。财务、ETF 或行业字段也可来自独立接口。</p>
<label>检查因子或策略所需字段<select name="target"><option value="">查看基础字段</option></select></label>
<label>面板名称<input name="name" value="我的研究面板" maxlength="100" required></label>
<div class="action-row"><button type="submit" disabled>预览面板</button>
<button type="button" class="button-secondary" data-panel-save disabled>保存面板</button></div>
<p data-panel-status role="status"></p>
<div data-panel-preview></div>
</form>
<details><summary>从数据接口添加字段附件</summary>
<form data-panel-fields>
<label>字段连接<select name="connection_id" required><option value="">请选择字段连接</option></select></label>
<label>字段开始日期（可选）<input name="start" type="date"></label>
<label>字段截止日期（可选）<input name="end" type="date"></label>
<div data-panel-field-units></div>
<p class="field-help">使用上方所选快照和标的。日历记录的时点要与决策时刻一致，财务记录要提供公告或修订可用时间。</p>
<div><button type="submit" disabled>获取字段并保存附件</button></div>
<p role="status" data-panel-fields-status></p></form>
</details>
<details data-panel-extension hidden><summary>为所选快照追加行情</summary>
<form data-panel-extend><label>新的行情截止日期<input type="date" name="end" required></label>
<div><button type="submit">获取增量行情</button></div>
<p class="field-help">更新沿用原连接和计量口径。新快照需要自己的规则和字段附件。</p>
<p role="status" data-panel-extend-status></p></form>
</details>
</section>
<section class="panel section" aria-labelledby="panel-saved-title"><h2 id="panel-saved-title">已保存面板</h2>
<p role="status" data-panel-saved-status>正在读取已保存面板…</p>
<ul data-panel-saved></ul></section>
<noscript><p>启用 JavaScript 后可获取快照、预览并保存面板。</p></noscript>
</div>"""

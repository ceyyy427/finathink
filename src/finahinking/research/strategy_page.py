"""Server-rendered strategy directory and paper research controls."""

from html import escape


def strategy_page(catalog, csrf, *, selected=None):
    templates = "".join(
        f'<option value="{escape(item["template_id"], quote=True)}"{" selected" if item["template_id"] == selected else ""}>{escape(item.get("title", item["template_id"]))}</option>'
        for item in catalog.templates()
    )
    rules = "".join(
        f'<li>{escape(rule.get("title", rule["rule_id"]))}</li>' for rule in catalog.risk_rules()
    )
    return f"""<section class="panel"><h1>策略与纸面账本</h1>
<p>选择模板和冻结面板。股票与 ETF 共用现金、持仓与费用记录。</p>
<form data-strategy-study method="post" action="/api/research/strategies">
<input type="hidden" name="csrf" value="{escape(csrf, quote=True)}">
<label>策略模板<select name="template_id">{templates}</select></label>
<label>数据<select name="mode"><option value="sample">固定股票与 ETF 合成样例</option>
<option value="saved">已保存的用户面板</option></select></label>
<details><summary>用户面板与模板参数</summary>
<label>面板合同<textarea name="contract" rows="4"></textarea></label>
<label>附件引用<textarea name="attachment_refs" rows="2">[]</textarea></label>
<label>模板参数<textarea name="parameters" rows="2">{{}}</textarea></label>
</details>
<button type="submit">提交纸面策略研究</button>
<p role="status" data-strategy-status>选择数据后提交后台任务。</p>
</form>
<p><a href="/research/jobs">后台研究与报告</a> · <a href="/research/workbench">返回量化研究工作台</a></p>
</section><section class="panel section" id="risk-rules"><h2>风险规则</h2><ul>{rules}</ul></section>"""

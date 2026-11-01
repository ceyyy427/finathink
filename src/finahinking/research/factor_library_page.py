"""Small server-rendered library integrated with the existing application shell."""
import html
import json
from urllib.parse import quote, urlsplit

from .factor_library import FactorLibrary

FAMILY_NAMES = dict(zip(('trend_momentum','reversal','volatility_tail','liquidity_turnover','value',
    'quality_profitability_accrual','growth_investment','industry_style','event_institutional','etf_macro'),
    ('趋势动量','反转','波动与尾部','流动性与换手','价值','质量与盈利','成长与投资','行业与风格','事件与机构','ETF 与宏观')))
CSS = '''<style>.factor-library-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr));gap:16px}.factor-library-card{padding:20px;border:1px solid var(--border,#dce5ee);border-radius:16px;background:var(--surface,#fff);min-width:0}.factor-library-card code,.factor-library-page pre{white-space:pre-wrap;overflow-wrap:anywhere}.factor-library-page input,.factor-library-page select,.factor-library-page textarea{max-width:100%;box-sizing:border-box}.factor-library-page textarea{width:100%;min-height:120px}.factor-library-page a:focus-visible,.factor-library-page button:focus-visible,.factor-library-page textarea:focus-visible,.factor-library-page summary:focus-visible{outline:3px solid #2563eb;outline-offset:4px}.factor-library-page form{display:grid;gap:16px}.factor-library-page .library-filter{display:flex;flex-wrap:wrap;gap:12px}.factor-library-page [role=status]{overflow-wrap:anywhere}.factor-library-page dt{font-weight:700}.factor-library-page dd{margin:4px 0 16px;overflow-wrap:anywhere}</style>'''


def _esc(v): return html.escape(str(v),quote=True)


SOURCE_LABELS = {
    'formula_status': {'EXPLICIT':'原式明确', 'UNRESOLVED':'原式待解析'},
    'adaptation_status': {'INDEPENDENT_IMPLEMENTATION':'已独立适配', 'REFERENCE_ONLY':'仅作来源参考'},
    'evidence_status': {'DEFINITION_CHECKED':'定义已核对', 'SOURCE_REPORTED':'仅有来源记录'},
}


def _source_card(card):
    statuses = ' · '.join(label + '：' + SOURCE_LABELS[key].get(card.get(key), '状态待核对')
        for key, label in (('formula_status','原式'),('adaptation_status','适配'),('evidence_status','证据')))
    url = card.get('source_url', '')
    try:
        parsed = urlsplit(url)
        safe_url = parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password and not any(ord(c) <= 32 for c in url)
    except (ValueError, TypeError):
        safe_url = False
    link = (f'<a data-source-link href="{_esc(url)}" rel="noopener noreferrer">查看固定提交源码</a>'
            if safe_url
            else '<span>来源链接尚未通过 HTTPS 校验</span>')
    return f'''<details data-source-card="{_esc(card['card_id'])}" data-adaptation="{_esc(card.get('adaptation_status'))}">
<summary>{_esc(card.get('title',card['card_id']))} · 来源卡<small data-source-status style="display:block">{_esc(statuses)}</small></summary>
<p>{_esc(card.get('explanation',card.get('reading_note','')))}</p>
<h4>来源原式</h4><pre data-source-formula>{_esc(card.get('original_formula') or '原式尚待解析，保留来源线索。')}</pre>
<p>{link} · 提交 <code>{_esc(card.get('commit'))}</code></p>
<dl><dt>来源位置</dt><dd>{_esc(card.get('path'))} · 行 {_esc(card.get('line_start'))}–{_esc(card.get('line_end'))}</dd>
<dt>文件摘要</dt><dd>{_esc(card.get('sha256'))}</dd><dt>许可证</dt><dd>{_esc(card.get('license_id'))}</dd>
<dt>阅读说明</dt><dd>{_esc(card.get('reading_note',''))}</dd></dl></details>'''


def _definition_notes(row):
    policy_fields = (('window_semantics','窗口与观察频率'),('financial_period_policy','财务期间口径'),
        ('pit_policy','时点可用性'),('missing_policy','缺失值处理'),('adjustment_policy','复权约定'),
        ('adaptation_notes','本地适配说明'))
    policies = ''.join(f'<dt>{label}</dt><dd data-definition-policy="{key}">{_esc(row.get(key,"待补充"))}</dd>'
                       for key,label in policy_fields)
    knowledge = ''.join(f'<li><a href="/knowledge/items/{quote(ref, safe="")}">{_esc(ref)}</a></li>'
                        for ref in row.get('knowledge_refs',()))
    example = row.get('numeric_example', {})
    inputs = example.get('inputs', {})
    times = inputs.get('times', [])
    sample_rows = ''.join('<tr><th scope="row">'+_esc(asset)+'</th><td>'+_esc(field)+'</td><td>'+_esc(inputs.get('field_units',{}).get(field,''))+'</td><td>'+_esc(', '.join(str(v) for v in values))+'</td></tr>'
        for asset, fields in inputs.get('instruments',{}).items() for field,values in fields.items())
    return f'''<section class="panel section" data-definition-notes><h2>口径与学习路径</h2><dl>{policies}</dl>
<h3>关联知识</h3><ul>{knowledge or '<li>暂无关联知识</li>'}</ul>
<h3>独立数值样例</h3><p>目标资产 {_esc(example.get('instrument'))} · 观察时间 {_esc(example.get('observed_at'))}</p>
<p data-numeric-times>按时间顺序：{_esc(' → '.join(times))}</p>
<div class="table-wrap" tabindex="0" role="region" aria-label="独立数值样例输入，可左右滚动"><table data-numeric-inputs><caption>已审核的合成输入，数值按以上观察顺序排列</caption><thead><tr><th scope="col">资产</th><th scope="col">字段</th><th scope="col">单位</th><th scope="col">输入值</th></tr></thead><tbody>{sample_rows or '<tr><td colspan="4">样例输入待补齐。</td></tr>'}</tbody></table></div>
<p data-numeric-calculation>手算过程：{_esc(example.get('calculation_reason'))}</p><p data-numeric-expected>独立预期值：{_esc(example.get('expected'))}</p>
<p>这些数值用于核对定义与实现；你的研究结果由所选数据重新计算。</p></section>'''


def library_page(csrf, query):
    library=FactorLibrary(); factor_id=query.get('factor',[None])[0]
    if factor_id:
        row=library.get(factor_id)
        sources=[c for c in library.cards() if c['card_id'] in row['source_card_ids']]
        source_html=''.join(_source_card(c) for c in sources)
        field_html=''.join('<li><code>'+_esc(k)+'</code> · '+_esc(v)+'</li>' for k,v in row['field_units'].items())
        body=f'''<p><a href="/research/factor-library">← 因子库</a></p><header class="overview-header"><div><p class="eyebrow">FACTOR DEFINITION / RESEARCH</p><h1>{_esc(row.get('title',factor_id))}</h1><p>{_esc(FAMILY_NAMES[row['family']])} · 定义版本 {_esc(row['version'])}</p></div></header>
<section class="panel"><h2>固定定义与适用范围</h2><pre>{_esc(row['formula'])}</pre><dl><dt>计算范围 / 输出单位</dt><dd>{_esc(row['scope'])} / {_esc(row['units'])}</dd><dt>方向说明</dt><dd>{_esc(row['direction_scope'])}</dd><dt>反例与局限</dt><dd>{_esc(row['counterexample'])}</dd><dt>定义指纹</dt><dd>{_esc(row['formula_fingerprint'])}</dd></dl><h3>所需字段</h3><ul>{field_html}</ul><p>字段必须带可用时间与单位；训练、验证和一次性测试使用同一份不可变面板。</p>{source_html}</section>
{_definition_notes(row)}
<section class="panel section"><h2>从定义准备研究</h2><p>从数据接口准备面板合同与已保存的字段附件引用。量价、交易日历、成分和交易规则共同参与就绪校验。</p><a href="/settings/data-connections">准备用户数据连接</a><form data-library-study method="post" action="/api/research/factor-library/studies">
<input type="hidden" name="factor_id" value="{_esc(factor_id)}"><input type="hidden" name="factor_version" value="{_esc(row['version'])}"><input type="hidden" name="csrf" value="{_esc(csrf)}">
<label>研究问题<textarea name="question" maxlength="2000" required>检验{_esc(row.get('title',factor_id))}在当前样本的解释力与稳定性</textarea></label>
<label>面板合同 JSON<textarea name="contract" required spellcheck="false" placeholder='{{"schema_version":"panel-contract.v1", …}}'></textarea></label>
<label>字段附件引用 JSON 数组<textarea name="attachment_refs" required spellcheck="false" placeholder='["已保存的附件指纹"]'>[]</textarea></label>
<button type="submit">固定定义与输入，提交后台验证</button></form><p role="status" aria-live="polite" data-library-status>提交后可在因子实验历史中冻结候选、读取一次测试集并记录研究审核。</p></section>'''
        script=''
    else:
        q=query.get('q',[''])[0].strip()[:100];family=query.get('family',[''])[0]
        entries=[r for r in library.entries() if (not family or r['family']==family) and (not q or q.casefold() in json.dumps(r,ensure_ascii=False).casefold())]
        opts='<option value="">全部家族</option>'+''.join(f'<option value="{k}" {"selected" if family==k else ""}>{v}</option>' for k,v in FAMILY_NAMES.items())
        cards=''.join(f'''<article class="factor-library-card"><p class="eyebrow">{_esc(FAMILY_NAMES[r['family']])} · 研究定义</p><h2><a href="/research/factor-library?factor={quote(r['factor_id'])}">{_esc(r.get('title',r['factor_id']))}</a></h2><p><code>{_esc(r['formula'])}</code></p><p>{_esc(r['scope'])} · {_esc(r['units'])} · {len(r['required_fields'])} 项输入</p><p>来源卡 {len(r['source_card_ids'])} · v{_esc(r['version'])}</p></article>''' for r in entries)
        inventory=library.inventory()
        body=f'''<header class="overview-header"><div><p class="eyebrow">FACTOR LIBRARY / RESEARCH</p><h1>因子定义库</h1><p class="lede">从明确原式出发，检查字段与时点，再进入可复现的后台研究。</p></div></header><p>{inventory['source_card_count']} 张来源卡 · {inventory['executable_formula_count']} 个独立公式 · {inventory['family_count']} 个家族</p><p>来源状态、可执行定义和本地验证证据分别记录；研究结果由你的固定输入计算。</p><form class="library-filter" method="get"><label>搜索定义<input name="q" value="{_esc(q)}" placeholder="名称、字段或公式"></label><label>家族<select name="family">{opts}</select></label><button type="submit">筛选</button></form><p role="status">找到 {len(entries)} 个定义</p><div class="factor-library-grid">{cards or '<p class="empty-state">未找到匹配定义，请调整关键词或家族。</p>'}</div><section class="panel section"><h2>来源卡目录</h2><p>原式尚未明确的来源卡保留来源信息；可执行定义链接其固定提交和文件摘要。</p><details><summary>查看全部来源卡与审阅状态</summary><div>{''.join(_source_card(c) for c in library.cards())}</div></details></section>'''
        script=''
    return CSS+'<section class="factor-library-page">'+body+'</section>'+script

"""Diagnostics within the established blue-white AppShell."""
import html
import json

DIAGNOSTIC_CSS = '''
[data-interface-workspace] {min-width:0}
[data-interface-workspace] .diagnostic-intro {max-width:72ch}
[data-interface-workspace] .diagnostic-heading {display:flex;align-items:center;justify-content:space-between;gap:var(--space-4);flex-wrap:wrap}
[data-interface-workspace] .diagnostic-section {margin-block:var(--space-8);padding-block-start:var(--space-5);border-top:1px solid var(--border)}
[data-interface-workspace] .diagnostic-form {display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:var(--space-4);align-items:end;margin-block:var(--space-4)}
[data-interface-workspace] label {display:flex;flex-direction:column;gap:var(--space-2);min-width:0}
[data-interface-workspace] :is(input,select) {width:100%;min-width:0;font:inherit}
[data-interface-workspace] .diagnostic-consent {display:flex;flex-direction:row;align-items:flex-start;grid-column:1/-1;max-width:72ch}
[data-interface-workspace] .diagnostic-consent input {width:auto;margin-block-start:5px}
[data-interface-workspace] .diagnostic-help {color:var(--text-secondary);font-size:.875rem;max-width:75ch}
[data-interface-workspace] :is(dd,code,p,a,button) {overflow-wrap:anywhere}
[data-interface-workspace] :is(th,td) {font-variant-numeric:tabular-nums}
[data-interface-workspace] details {margin-block:var(--space-4)}
[data-interface-workspace] summary {cursor:pointer;color:var(--text-primary)}
[data-interface-workspace] .diagnostic-receipt {background:var(--surface-elevated);border:1px solid var(--border);border-radius:var(--radius-md);padding:var(--space-5);margin-block:var(--space-5)}
[data-interface-workspace] button:disabled {opacity:.6;cursor:not-allowed}
[data-interface-workspace] [role=status] {min-height:1.5em}
[data-interface-workspace] .diagnostic-history {list-style:none;padding:0;display:grid;gap:var(--space-3)}
[data-interface-workspace] .diagnostic-history li {display:flex;align-items:center;flex-wrap:wrap;gap:var(--space-3);border-bottom:1px solid var(--border);padding-block:var(--space-3)}
[data-interface-workspace] .diagnostic-history label {flex-direction:row;align-items:center;min-height:44px}
[data-interface-workspace] h3 {margin-block:var(--space-6) var(--space-3)}
[data-interface-workspace] .diagnostic-history input {width:auto}
[data-interface-workspace] .table-wrap {max-width:100%;overflow:auto}
[data-interface-workspace] pre {white-space:pre-wrap;overflow-wrap:anywhere}
'''


def diagnostic_page(csrf_token, health, records=()):
    esc = lambda value: html.escape(str(value), quote=True)
    rows = ''.join(f'<li><a href="/api/diagnostics/interfaces/{esc(row["receipt_id"])}" download="finathink-interface.json">{esc(row["kind"])} · {esc(row["status"])} · {esc(row["created_at"])}</a></li>' for row in records)
    return f'''<section data-interface-workspace data-interface-ready="false" lang="zh-CN">
<h1>接口与研究质量</h1>
<p class="lede diagnostic-intro">先确认连接怎样响应，再查看模型怎样处理同一份研究材料。</p>
<p class="diagnostic-intro">检查结果保存在本地，可以重新读取和下载。固定任务采用合成 SAMPLE 数据；报告保留输入、引用和调用记录，方便逐项核对。</p>
<input type="hidden" data-interface-csrf value="{esc(csrf_token)}">
<p role="status" aria-live="polite" data-interface-status>正在读取本地记录…</p>
<p><a class="button-secondary" href="/api/diagnostics/bundle" download="finathink-diagnostics.json">下载脱敏诊断</a></p>
<section class="diagnostic-section" aria-labelledby="interface-checks">
<div class="diagnostic-heading"><h2 id="interface-checks">接口检查</h2><button type="button" data-interface-local>运行本地兼容检查</button></div>
<p class="diagnostic-help">在本机临时 HTTP 服务上检查成功响应、认证、限流、JSON、字段、断流、超时与响应大小。包含数据接口及两类兼容模型协议，不连接用户接口。合成评分示例用于演示评估流程。</p>
<details data-interface-probe-details><summary>检查我的已保存接口</summary>
<p>使用已保存的连接与本地凭证引用。本次仅发送一个请求：数据最多一页；模型仅接收固定合成技术样例。单次网络超时上限5秒，模型输出上限1024单位。</p>
<form class="diagnostic-form" data-interface-probe>
<label>接口类型<select name="target_kind"><option value="data">数据接口</option><option value="model">模型接口</option></select></label>
<label>已保存配置<select name="config_ref" required><option value="">正在读取配置</option></select></label>
<label data-interface-data-field>标的<input name="instrument" value="AAA" maxlength="32" required></label>
<label data-interface-data-field>开始日（UTC）<input name="start" type="date" value="2026-01-01"></label>
<label data-interface-data-field>结束日（UTC）<input name="end" type="date" value="2026-10-01"></label>
<label data-interface-data-field>研究截止日（UTC）<input name="as_of" type="date" value="2026-10-01"></label>
<label class="diagnostic-consent"><input type="checkbox" name="authorize" required>我授权本次请求访问所选用户接口，模型请求可能产生费用。结果不确定时先读取原记录。</label>
<div class="action-row"><button type="submit">检查一次</button><button type="button" data-interface-new>准备下一次检查</button></div>
</form><p><a href="/settings/data-connections">配置数据接口</a> · <a href="/settings/providers">配置模型接口</a></p></details>
<div class="diagnostic-receipt" data-interface-result hidden tabindex="-1" aria-label="接口检查结果"></div>
<div class="action-row"><a data-interface-export hidden download="finathink-interface.json">下载这次检查</a><button type="button" data-interface-refresh>重新读取本地记录</button></div>
<ul class="diagnostic-history" data-interface-history>{rows or '<li>尚无接口检查记录。</li>'}</ul>
</section>
<section class="diagnostic-section" aria-labelledby="quality-checks"><h2 id="quality-checks">固定任务质量评估</h2>
<p class="diagnostic-intro">材料齐全、材料不足和观点分歧三个固定样例，沿用研究 worker 运行五类分析师与研究经理。记录结构接受率、输入引用归属、缺失材料识别、用量和模型调用耗时。</p>
<form class="diagnostic-form" data-quality-submit>
<label>固定任务<select name="case_id" required><option value="complete">材料齐全</option><option value="sparse">材料不足</option><option value="conflict">观点分歧</option></select></label>
<label>模型配置<select name="model_config_ref" required><option value="">正在读取配置</option></select></label>
<label>总时长上限（秒）<input name="max_wall_seconds" type="number" min="5" max="300" value="120" required></label>
<label class="diagnostic-consent"><input name="allow_model_calls" type="checkbox" required>我授权将固定合成样例发送给所选模型，最多6次调用、仅1次任务尝试；使用我的模型 API 额度。</label>
<div class="action-row"><button type="submit">运行固定任务</button><button type="button" data-quality-new>准备下一次任务</button></div></form>
<p class="diagnostic-help" data-quality-case-note>每个样例的版本、问题、数据与材料保持固定。模型映射在提交时冻结；比较仅适用于相同样例、配置规则与预算。</p>
<p role="status" aria-live="polite" data-quality-status>尚未选择质量任务。</p>
<div data-quality-result class="diagnostic-receipt" hidden tabindex="-1" aria-label="研究质量结果"></div>
<div class="action-row"><button type="button" data-quality-refresh>读取所选任务状态</button><button type="button" data-quality-cancel disabled>取消所选任务</button><a data-quality-run hidden>打开研究任务</a><a data-quality-export hidden download="finathink-research-quality.json">下载质量记录</a></div>
<h3>本地质量任务</h3><p class="diagnostic-help">选择2至4个任务比较。失败、未发送和未知用量保留在各自记录中；引用有效率仅校验输入证据归属。</p>
<ul class="diagnostic-history" data-quality-history><li>尚无固定质量任务。</li></ul>
<button type="button" data-quality-compare>比较所选任务</button><div data-quality-comparison hidden aria-live="polite"></div>
</section>
<details class="diagnostic-section"><summary>本地健康状态与恢复</summary>
<h2>本地状态</h2><pre>{esc(json.dumps(health, ensure_ascii=False, indent=2, sort_keys=True))}</pre>
<div class="action-row"><a href="/api/personal/backup">单独备份个人笔记</a></div>
<p>完整研究备份与恢复请使用快速开始中的维护命令。先关闭应用，恢复到新目录。结果不确定时先查找原任务，避免重复外发。分享导出前请自行核对内容。</p></details>
<noscript><p>启用 JavaScript 后可运行交互检查。已有接口检查可从上方链接下载；<a href="/api/diagnostics/bundle">读取本地健康诊断</a>，或打开已发布的离线 HTML 报告。</p></noscript>
</section>'''

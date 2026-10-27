"""Learning extension of the established research AppShell."""

import html

LEARNING_CSS = """
[data-learning-workspace] {min-width:0}
[data-learning-workspace] .form-grid {display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr));gap:var(--space-4);align-items:end}
[data-learning-workspace] label {display:flex;flex-direction:column;gap:var(--space-2);min-width:0}
[data-learning-workspace] :is(input,select,textarea) {width:100%;min-width:0;font-size:1rem}
[data-learning-workspace] .learning-actions {display:flex;flex-wrap:wrap;gap:var(--space-3);align-items:center;margin-block:var(--space-4)}
[data-learning-workspace] .learning-ack {display:flex;flex-direction:row;align-items:flex-start}
[data-learning-workspace] .learning-ack input {width:auto;margin-block-start:5px}
[data-learning-workspace] :is(p,code,dd,button,a) {overflow-wrap:anywhere}
[data-learning-workspace] .learning-evidence {font-size:.875rem;color:var(--text-secondary)}
[data-learning-workspace] td {font-variant-numeric:tabular-nums}
[data-learning-workspace] button:disabled {opacity:.55;cursor:not-allowed}
[data-learning-workspace] .learning-rule {max-width:75ch;margin-block:var(--space-4)}
[data-learning-detail] :is(h2,h3) {margin-block:var(--space-6) var(--space-3)}
"""


def learning_workspace_page(csrf_token):
    return f'''<section data-learning-workspace data-learning-state="EMPTY" lang="zh-CN">
<h1>复盘与学习</h1><p class="lede">用结算证据审视假设，让下一轮研究有据可循。</p>
<p>复盘后先查看依据与修改预览，再决定批准或拒绝。批准只创建一个纸面配置版本；原运行、原报告和旧配置保持不变。</p>
<input type="hidden" data-learning-csrf value="{html.escape(csrf_token, quote=True)}">
<p role="status" aria-live="polite" data-learning-status>正在读取本地研究、数据快照与版本…</p>
<section class="panel section"><h2>选择研究与后续观察</h2>
<form class="form-grid" data-learning-form>
<label>原研究运行<select name="run_id" required><option value="">请选择已有完整报告的研究</option></select></label>
<label>后续数据快照<select name="evidence_snapshot_ref" required><option value="">请选择用户已保存的观察数据</option></select></label>
<label>复盘截止日（UTC）<input name="as_of" type="date" required></label>
<button type="submit">生成复盘与修改预览</button></form>
<p class="field-help">沿用原研究的配置、费用和下一观察成交规则。只使用研究截止日之后的观察；首个成交观察后固定5个观察间隔估值。数据未到期、未知可用时间或未成交时不生成已实现学习结果。</p>
<p><a href="/research/jobs">准备数据并运行研究</a> · <button type="button" data-learning-refresh>重新读取本地记录</button></p></section>
<section class="section" data-learning-detail hidden aria-label="所选复盘详情">
<h2>假设与纸面结果</h2><ul data-learning-hypotheses></ul>
<p data-learning-binding class="learning-evidence"></p>
<div class="table-wrap" tabindex="0" role="region" aria-label="复盘结果，可横向滚动"><table data-learning-outcome><caption>固定观察末价估值，非平仓收益；含入场费用与滑点。价格和现金使用输入数据声明的共同单位。</caption><thead><tr><th scope="col">观察</th><th scope="col">结果</th></tr></thead><tbody></tbody></table></div>
<h3>因子衰减诊断</h3><p>按已知观察计算不同标签跨度的 IC；属于描述性复盘，不是新增样本外检验。缺失值保留为未知。</p>
<div class="table-wrap" tabindex="0" role="region" aria-label="因子衰减，可横向滚动"><table data-learning-decay><thead><tr><th scope="col">标签跨度（观察数）</th><th scope="col">IC</th></tr></thead><tbody></tbody></table></div>
<h2>审核修改预览</h2><p data-learning-basis class="learning-rule"></p>
<div class="table-wrap" tabindex="0" role="region" aria-label="配置修改预览，可横向滚动"><table data-learning-preview><caption>只展示服务器计算的原值与提议值；审批前不应用</caption><thead><tr><th scope="col">配置</th><th scope="col">原版本</th><th scope="col">建议新版本</th></tr></thead><tbody></tbody></table></div>
<p class="learning-evidence" data-learning-preview-binding></p>
<form data-learning-review-form><label>审核依据<textarea name="rationale" rows="2" maxlength="500" required></textarea></label>
<p><label class="learning-ack"><input type="checkbox" data-learning-ack>我已核对观察期、费用、数据与修改预览，知道批准不代表策略有效或盈利。</label></p>
<div class="learning-actions"><button type="submit" name="decision" value="APPROVED" data-learning-approve disabled>批准为新配置</button><button type="submit" name="decision" value="REJECTED" data-learning-reject disabled>拒绝此提案</button><a data-learning-original-report>打开原始报告</a></div></form>
<p data-learning-admission></p></section>
<section class="panel section"><h2>按版本复跑</h2><p>选择批准后的配置，或回到旧版本。复跑在新运行中执行；相同提交标识不重复排队，原报告不被覆盖。旧数据使用新配置时明确标记为回溯比较，不能再作为该配置生效后的学习证据。</p>
<form class="form-grid" data-learning-rerun-form><label>复跑原研究<select name="run_id" required><option value="">请选择原研究</option></select></label><label>配置版本<select name="policy_ref" required></select></label>
<label>复跑数据快照<select name="snapshot_ref" required><option value="">请选择快照</option></select></label><label>运行截止日（UTC）<input name="as_of" type="date" required></label><button type="submit">用所选版本创建新运行</button></form>
<p class="field-help">复跑为本地确定性纸面研究，不继承原任务的模型外发授权。批准日期按UTC日末保守处理；版本是否可用由服务器校验。</p>
<p role="status" aria-live="polite" data-learning-job>尚无本页复跑任务。</p><div class="learning-actions"><a data-learning-report hidden>打开新运行报告</a><button type="button" data-learning-cancel disabled>取消复跑</button></div>
<h3>最近的版本复跑</h3><ul class="overview-list" data-learning-runs></ul></section>
<section class="section"><h2>本地复盘记录</h2><p>显示最近100次复盘；版本可追溯到原研究、观察快照和不可变审核记录。</p><ul class="overview-list" data-learning-history></ul><a href="/research/compare">比较同条件运行</a></section>
<noscript><p>复盘交互需要启用 JavaScript。已发布的离线 HTML 报告仍可直接阅读。</p></noscript>
</section>'''

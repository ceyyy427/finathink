"""Native controls for the settled strategy learning path."""

from html import escape


def strategy_learning_page(csrf_token):
    return f'''<section class="section" id="strategy-learning" data-strategy-learning lang="zh-CN">
<h2>策略账本结算与学习</h2>
<p>选择已发布的策略报告，按原账本、交易日历和可比基准核对成熟观察窗。</p>
<input type="hidden" data-sl-csrf value="{escape(csrf_token, quote=True)}">
<p role="status" aria-live="polite" data-sl-status>正在读取策略报告…</p>
<form class="form-grid" data-sl-observe>
<label>已发布的策略报告<select name="run_id" required><option value="">请选择策略报告</option></select></label>
<label>观察截止日（UTC）<input type="date" name="as_of" required></label>
<button type="submit">观察并结算账本</button></form>
<p><a data-sl-original hidden>打开原策略报告</a> · <a href="/research/strategies">策略研究</a></p>
<label>已保存的策略观察<select data-sl-history><option value="">请选择观察记录</option></select></label>
<button type="button" data-sl-refresh>重新读取记录</button>
<section data-sl-receipt hidden><h3>已结算观察</h3><p data-sl-receipt-text></p>
<p class="learning-evidence" data-sl-receipt-binding></p><p><a data-sl-raw-receipt>读取冻结观察收据（JSON）</a></p>
<div class="table-wrap" tabindex="0" role="region" aria-label="策略观察窗口">
<table><thead><tr><th scope="col">起始日</th><th scope="col">结束日</th><th scope="col">状态</th><th scope="col">费用后收益</th><th scope="col">基准收益</th><th scope="col">成交笔数</th></tr></thead><tbody data-sl-window-rows></tbody></table></div>
<button type="button" data-sl-audit>审核结算样本</button></section>
<section data-sl-audit-detail hidden><h3>样本审核与政策范围</h3><p data-sl-audit-text></p>
<p>固定试验规则使用至少三个互不重叠的五间隔结算窗。平均超额收益低于零，且超过半数窗口落后基准时，可提议将纸面信号预算乘以 0.8，最低为 0.1。</p>
<button type="button" data-sl-propose>生成纸面政策提案</button></section>
<section data-sl-proposal hidden><h3>政策提案与明确审核</h3><p data-sl-proposal-text></p>
<details><summary>查看政策、证据和预览指纹</summary><dl data-sl-fingerprints></dl></details>
<form data-sl-decision><label>审核依据<textarea name="rationale" maxlength="500" rows="2" required></textarea></label>
<label>批准生效日（UTC，留空即审核时生效）<input type="date" name="effective_on"></label>
<div class="learning-actions"><button type="submit" name="decision" value="APPROVED">批准纸面政策</button>
<button type="submit" name="decision" value="REJECTED">拒绝提案</button></div></form>
<p data-sl-admission></p>
<button type="button" data-sl-pair hidden>用原冻结策略成对复跑</button></section>
<section data-sl-comparison hidden><h3>冻结策略版本对照</h3><p data-sl-pair-text></p>
<div class="table-wrap" tabindex="0" role="region" aria-label="策略版本指标">
<table><thead><tr><th scope="col">指标</th><th scope="col">原政策</th><th scope="col">已批准政策</th><th scope="col">变化</th></tr></thead><tbody data-sl-metrics></tbody></table></div>
<p><a data-sl-run-review>打开运行审阅曲线</a> · <a data-sl-baseline>原政策复跑证据</a> · <a data-sl-candidate>批准政策复跑证据</a></p></section>
<form class="form-grid" data-sl-apply hidden><label>待核验的后续策略报告<select name="run_id" required aria-describedby="strategy-apply-scope"></select></label>
<button type="submit">将该版本用于所选后续策略</button><p class="field-help" id="strategy-apply-scope">候选列表按模板、原政策和报告截止日初筛。提交时核对首个观察是否已到生效时间，以及资产、参数和基准是否符合批准范围。</p></form><p data-sl-applied></p>
</section>'''

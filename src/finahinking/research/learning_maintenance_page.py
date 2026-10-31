"""Native forms inherit the established blue-white AppShell and evidence tables."""
import html


def reliability_page(csrf):
    return f'''<section class="section" data-learning-reliability id="cohort-evidence" lang="zh-CN">
<input type="hidden" data-cohort-csrf value="{html.escape(csrf, quote=True)}">
<h2>样本评估与成对复跑</h2><p class="lede">先检查分离窗口的共同证据，再比较批准前后的配置。</p>
<p>同配置、同数据模式的结算自动进入候选范围。固定门槛为20个不重叠窗口；日期范围仍由你选择，请同时检查反例和被排除的记录。</p>
<p role="status" aria-live="polite" data-cohort-status>正在读取样本评估与版本记录…</p>
<section class="panel section"><h3>评估已结算样本</h3>
<form class="form-grid" data-cohort-form><label>范围锚点复盘<select name="anchor_review_id" required data-cohort-anchor><option value="">先完成一次普通纸面复盘</option></select></label>
<label>开始日期（UTC）<input type="date" name="start" required></label><label>证据截止日（UTC）<input type="date" name="as_of" required></label><button type="submit" data-cohort-assess disabled>评估全部候选</button></form>
<p class="field-help">已观察且在截止时刻可用的证据才参与。候选超过200条时需缩小范围。评估与生成建议均不自动修改配置。</p>
<label>已保存的样本评估<select data-cohort-saved><option value="">尚无样本评估</option></select></label>
<div data-cohort-result><p>选择已结算复盘和日期，查看样本覆盖、收益分布与反例。</p></div>
<button type="button" data-cohort-propose disabled>生成建议并打开审核</button></section>
<section class="panel section" id="paired-replay"><h3>父版本与新版本成对复跑</h3>
<form class="form-grid" data-pair-form><label>已批准的配置复盘<select name="review_id" required><option value="">先审核一项数值配置</option></select></label><label>共同冻结数据<select name="snapshot_ref" required><option value="">请选择已保存快照</option></select></label><label>共同截止日（UTC）<input type="date" name="as_of" required></label><button type="submit" data-pair-create disabled>创建两项离线复跑</button></form>
<p class="field-help">两边只改变配置版本。使用学习期或批准前数据会标为回溯比较；复跑不自动成为新的学习证据。</p>
<label>已保存的成对任务<select data-pair-saved><option value="">尚无成对任务</option></select></label>
<div class="learning-actions"><button type="button" data-pair-refresh disabled>刷新复跑状态</button><button type="button" data-pair-resume disabled>继续未排队的任务</button></div>
<div data-pair-result><p>批准配置后，可在同一份冻结输入上比较两个版本。</p></div></section>
<button type="button" data-cohort-refresh>重新读取样本与版本</button> · <a href="/research/maintenance">查看本地任务维护</a>
<noscript><p>样本评估和成对复跑需要启用 JavaScript；<a href="/research/compare">已有离线报告可继续阅读</a>。</p></noscript></section>'''


def maintenance_page():
    return '''<section data-task-maintenance lang="zh-CN"><h1>本地任务维护</h1><p class="lede">查看队列、已记录的阶段耗时与本地存储余量。</p>
<p>本页只读取当前工作区的任务；缺少的资源指标保留为未知。</p>
<p role="status" aria-live="polite" data-maintenance-status>正在读取本地维护信息…</p>
<div class="learning-actions"><button type="button" data-maintenance-refresh>重新读取维护信息</button><a href="/research/jobs">打开任务与取消操作</a><a href="/research/learning#cohort-evidence">打开样本评估</a><a href="/diagnostics">打开诊断与恢复</a></div>
<section class="section"><h2>队列与存储</h2><dl data-maintenance-facts></dl></section>
<section class="section"><h2>耗时较长的已记录阶段</h2><p>按最近100项任务中保存的阶段耗时排序；没有记录不等于耗时为零。</p><div data-maintenance-stages>尚无已记录的阶段耗时。</div></section>
<section class="section"><h2>任务历史</h2><p>列表仅展示元数据，报告尚未校验。打开任务详情时会检查产物完整性。</p><ul class="overview-list" data-maintenance-jobs></ul><p data-maintenance-page></p><div class="learning-actions"><button type="button" data-maintenance-first>返回第一页</button><button type="button" data-maintenance-next disabled>读取下一页</button></div></section>
<p data-maintenance-limits></p><noscript><p>实时维护信息需要启用 JavaScript；<a href="/research/compare">可直接打开已有研究报告</a>。</p></noscript></section>'''

"""Existing AppShell body for the reference-bound factor experiment journey."""

import html
import json


def factor_workspace_page(csrf):
    from .factor_detail_page import factor_directory
    from .factor_recipes import research_recipes

    try:
        directory = ''.join(f'<li><a href="/research/factors/{html.escape(item["factor_id"], quote=True)}?version={html.escape(item["version"], quote=True)}">{html.escape(item["title"])}</a> · {html.escape(item["implementation"])}</li>' for item in factor_directory())
    except ValueError:
        directory = '<li role="alert">资料目录校验失败，旧实验入口仍可使用。请检查安装包后重新读取。</li>'
    return f'''
<section data-experiment-workspace data-study-state="EMPTY">
<header class="overview-header"><div><p class="eyebrow">FACTOR RESEARCH / PAPER-ONLY</p><h1>因子实验</h1><p class="lede">先固定问题、样本与成本，再验证候选；冻结后只查看一次测试集。</p></div></header>
<p class="field-help">旧实验入口有 {len(research_recipes())} 个受控配方；工程复现不代表盈利。附加字段配方需走类型化研究入口，不使用成交量代替真实换手率。</p>
<p><a href="/research/factor-library">打开跨家族因子定义库，准备真实后台研究 →</a></p>
<details id="factor-directory"><summary>查看来源与本地定义目录</summary><ul>{directory}</ul></details>
<input type="hidden" data-study-csrf value="{html.escape(csrf, quote=True)}">
<p data-study-status role="status" aria-live="polite">正在读取本地目录和快照…</p>
<section class="panel"><h2>01 / 新建研究章程</h2><p class="field-help">下面用于创建新研究，不会改写当前历史记录；当前章程的实际设置见候选区。</p>
<p><button type="button" data-study-sample>生成明确标记的示例快照</button> <a href="/research/jobs">准备用户数据快照</a></p>
<form class="form-grid study-form" data-study-form>
<label>已冻结快照<select name="snapshot_ref" required><option value="">请选择数据</option></select></label>
<label>研究标的<input name="instrument" value="AAA" maxlength="64" required></label>
<label>研究截止日（UTC 当日结束）<input name="as_of" type="date" required></label>
<label>受控配方<select name="recipe_id" required><option value="">正在读取目录</option></select></label>
<label>研究问题<textarea name="question" required maxlength="2000">研究均值回归在不同窗口下的稳定性</textarea></label>
<label>候选窗口（观察数，以逗号分隔，最多 8 项）<input name="windows" value="3,5,8" required></label>
<label>扩展式验证折数<input name="n_splits" type="number" min="1" max="4" value="3" required></label>
<label>标签跨度（观察数）<input name="label_horizon" type="number" min="1" max="20" value="1" required></label>
<label>隔离间隔（观察数）<input name="embargo" type="number" min="0" max="20" value="1" required></label>
<label>费用 bps<input name="fee_bps" type="number" min="0" max="100" step="0.1" value="5" required></label>
<label>滑点 bps<input name="slippage_bps" type="number" min="0" max="100" step="0.1" value="5" required></label>
<label>平台最低 |验证 IC|<input name="min_abs_ic" type="number" min="0" max="1" step="0.01" value="0.02" required></label>
<label>相邻点最大指标差<input name="max_neighbor_delta" type="number" min="0" max="1" step="0.01" value="0.1" required></label>
<button type="submit">固定章程并提交验证</button>
</form><p class="field-help" data-study-recipe-note></p>
<p class="field-help">默认 60% 训练 / 20% 验证 / 20% 测试；标签跨界会被 purge，隔离间隔按观察数计。费用为秩换手诊断口径，不冒充组合净收益。</p></section>
<section class="panel section"><h2>02 / 候选、验证与冻结</h2><p data-study-binding>尚未提交研究。请选择快照与受控配方。</p>
<ol data-study-stages aria-label="实验阶段"></ol>
<p class="learning-evidence" data-study-method-binding></p>
<p><label class="study-freeze-ack"><input type="checkbox" data-freeze-ack> 我确认先依据验证证据冻结候选，不根据测试结果换选。</label></p>
<div class="table-wrap" tabindex="0" role="region" aria-label="候选证据，可左右滚动查看全部字段"><table data-study-candidates><caption>候选证据 · 排名仅依据验证集，测试结果单列；窄屏可左右滚动</caption><thead><tr><th scope="col">候选 / 窗口</th><th scope="col">状态</th><th scope="col">验证 IC</th><th scope="col">样本</th><th scope="col">校正 p</th><th scope="col">操作</th></tr></thead><tbody><tr><td colspan="6">尚无实验结果。</td></tr></tbody></table></div>
<h3>可比条件与验证平台</h3><ul class="overview-list" data-study-groups></ul><p class="field-help" data-study-multiplicity></p></section>
<section class="panel section"><h2>03 / 冻结后测试与审核</h2><p data-study-test-result>测试集未读取。</p>
<div class="action-row"><button type="button" data-study-test disabled>读取一次冻结测试集</button><button type="button" data-study-cancel disabled>取消当前计算</button><a data-study-report hidden>打开完整 HTML 报告</a></div>
<p class="field-help">同一标的的测试区间（含重叠区间）在本地账本中只消费一次；换问题、成本或快照不能重开。PASS 表示证据可计算，不表示盈利。中断后占用仍保留。</p>
<div class="form-grid"><label>研究审核<select name="review_decision"><option value="">请选择</option><option value="REJECT">拒绝候选</option><option value="APPROVE_FOR_RESEARCH">保留为待准入研究提案</option></select></label><label>审核标识<input name="reviewer_id" value="local-reviewer" maxlength="128"></label><button type="button" data-study-review disabled>记录审核决定</button></div><p class="field-help">审核仅记录研究决定，不自动写入生产因子库或改变投资策略。</p></section>
<section class="panel section"><h2>本地实验历史</h2><p>重启后可继续读取同一章程、报告与测试占用记录。</p><ul class="overview-list" data-study-history></ul><a href="/research/compare">查看可比条件与运行差异</a></section>
</section>'''


def quantitative_comparison_section(payload):
    esc = lambda value: html.escape(str(value), quote=True)
    rows = []
    by_id = {item["run_id"]: item for item in payload.get("runs", [])}
    for group in payload.get("groups", []):
        conditions = group["conditions"]
        values = "".join(f'<li><code>{esc(identifier)}</code><pre>{esc(json.dumps(by_id[identifier]["metrics"], ensure_ascii=False, sort_keys=True))}</pre></li>' for identifier in group["run_ids"])
        differences = "".join(f'<li>{esc(item["run_id"])} 相对 {esc(item["reference_run_id"])}：{esc(item["metrics"])}</li>' for item in group["differences"]) or '<li>只有一个成员，尚无同条件差异。</li>'
        rows.append(f'<article class="panel section"><h3>条件组 {esc(group["group_id"][:12])}</h3><p>{esc(conditions["version"])} · {esc(conditions["metric_basis"])} · 数据 {esc(conditions["dataset_fingerprint"])}</p><p>样本 {esc(conditions["sample_period"])} · 成本 {esc(conditions["cost"])}</p><details><summary>验证 IC / 测试 IC / 基线指标（按方法分组）</summary><ul>{values}</ul></details><h4>服务器计算的指标差异</h4><ul>{differences}</ul></article>')
    unknown = sum(item.get("comparability") == "UNKNOWN" for item in payload.get("runs", []))
    return '<section class="section" data-quantitative-comparison><h2>量化可比条件与指标差异</h2><p class="field-help">这是全部已登记研究的条件分组快照，不随元数据勾选变化；刷新页面重新读取。只有相同数据、实际样本、成本、分割和方法的结果才显示差异，不计算跨组排名或胜者。</p>' + f'<p>缺少明确条件的历史记录：{unknown}，不假定可比。</p>' + ("".join(rows) or '<p class="empty-state">尚无完整条件的量化结果；先完成一次因子实验或研究任务。</p>') + '</section>'

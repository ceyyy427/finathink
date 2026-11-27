"""Server-rendered entry and local records for the model and Codex research workspace."""

import json
from html import escape
from urllib.parse import quote, urlencode

BASE = "/research/model-workbench"
API = "/api/research/model-workbench"
ROLES = {
    "fundamentals": "基本面分析师", "technical": "技术分析师", "sentiment": "市场情绪分析师",
    "news": "新闻分析师", "learning": "学习分析师", "research": "研究经理",
}
JOB_STATES = {
    "queued": "已排队", "running": "研究中", "external_waiting": "等待外部处理",
    "retryable": "等待重试", "completed": "已完成", "failed": "失败", "cancelled": "已取消",
}


def _h(value):
    return escape(str(value), quote=True)


def _json_attr(value):
    return _h(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _field(query, key):
    value = (query or {}).get(key, "")
    return value[0] if isinstance(value, (list, tuple)) and value else value


def _option(value, title, selected=False):
    return f'<option value="{_h(value)}"{" selected" if selected else ""}>{_h(title)}</option>'


def _snapshot_fields(settings, requested):
    snapshots = list(settings.get("snapshots", ()))
    selected = next((s for s in snapshots if requested in (s["snapshot_ref"], s.get("fingerprint"))), None)
    if selected is None and not requested:
        selected = next((s for s in snapshots if s["snapshot_ref"] == "sample"), snapshots[0] if snapshots else {})
    missing = selected is None
    selected = selected or {}
    options = ['<option value="" selected>链接中的快照不可用，请另选快照</option>'] if missing else []
    for item in snapshots:
        ref = item["snapshot_ref"]
        instruments = item.get("instruments", [])
        label = "内置合成样例 · SAMPLE" if ref == "sample" else f'{item.get("data_mode", "快照")} · {ref[:10]}'
        if instruments:
            label += " · " + " / ".join(instruments[:3])
        as_of = str(item.get("as_of") or "")[:10]
        if as_of:
            label += " · " + as_of
        options.append(f'<option value="{_h(ref)}" data-instruments="{_json_attr(instruments)}" '
                       f'data-as-of="{_h(as_of)}"{" selected" if item is selected else ""}>{_h(label)}</option>')
    tickers = "".join(_option(i, i) for i in selected.get("instruments", ()))
    if len(selected.get("instruments", ())) > 1:
        tickers = '<option value="" selected>请选择标的</option>' + tickers
    return "".join(options), tickers, _h(str(selected.get("as_of") or "")[:10])


def _provider_options(settings):
    options = ['<option value="offline" data-models="{}" selected>本地离线研究</option>']
    for provider in settings.get("providers", ()):
        pid = provider["provider_id"]
        if pid == "offline":
            continue
        model = provider.get("model") or ""
        label = " · ".join(filter(None, (pid, model)))
        options.append(f'<option value="{_h(pid)}" data-model="{_h(model)}" '
                       f'data-models="{_json_attr(provider.get("role_models") or {})}" '
                       f'data-status="{_h(provider.get("status") or "")}">{_h(label)}</option>')
    return "".join(options)


def _history(history):
    items = []
    for job in history:
        job_id = job["job_id"]
        selection = job.get("selection") or {}
        title = job.get("question") or selection.get("question") or job.get("instrument") or job.get("run_id") or job_id
        href = BASE + "?" + urlencode({"job": job_id}) + "#mw-records"
        status = str(job.get("status") or "")
        items.append(f'<li><a href="{_h(href)}">{_h(title)}</a><code>{_h(job_id[-12:])}</code><span>{_h(JOB_STATES.get(status.lower(), status))}</span>'
                     f'<a class="mw-json" href="{API}/jobs/{_h(quote(job_id, safe=""))}" download>下载回执 JSON</a></li>')
    return "".join(items) or "<li>此工作区还没有研究任务。</li>"


def _material_template():
    roles = "".join(_option(k, v) for k, v in ROLES.items() if k != "research")
    return f'''<template data-mw-material-template><fieldset class="mw-material" data-mw-material>
<legend>研究摘要</legend><div class="mw-fields">
<label>材料所属角色<select name="material_role">{roles}</select></label>
<label>材料标的<input name="material_instrument" required maxlength="64"></label>
<label class="mw-wide">来源名称<input name="source_label" required maxlength="120" placeholder="例如公告标题或研究笔记名称"></label>
<label>观测时间（UTC）<input name="observed_at" type="datetime-local" step="1" required></label>
<label>可获得时间（UTC）<input name="available_at" type="datetime-local" step="1" required></label>
<label class="mw-wide">摘要内容<textarea name="summary" rows="3" maxlength="2000" required></textarea></label>
</div><label class="mw-check"><input type="checkbox" name="material_authorized" required>我有权向所选研究角色提供这条摘要。</label>
<button type="button" class="mw-secondary" data-mw-remove>移除这条材料</button></fieldset></template>'''


def model_workbench_page(csrf, settings, handoffs=(), *, query=None):
    snapshots, instruments, as_of = _snapshot_fields(settings, _field(query, "snapshot_ref"))
    provider_options = _provider_options(settings)
    roles = "".join(f'<div><dt>{name}</dt><dd data-mw-role="{role}">本地规则</dd></div>' for role, name in ROLES.items())
    job_id = _field(query, "job")
    package_links = ""
    if job_id and handoffs:
        rows = handoffs.get("handoffs", ()) if isinstance(handoffs, dict) else handoffs
        for item in rows:
            ref = item["handoff_ref"]
            href = BASE + "?" + urlencode({"job": job_id, "handoff": ref}) + "#mw-codex"
            package_links += f'<li><a href="{_h(href)}">交接包 · {_h(str(item["package_digest"])[:12])}</a></li>'
    return f'''<article class="mw-page" data-model-workbench data-mw-ready="false" data-csrf="{_h(csrf)}" lang="zh-CN">
<header class="mw-heading"><div><h1>模型与 Codex 研究台</h1>
<p>选择行情快照，核对各角色的研究材料与调用回执。需要外部研究时，可以把同一任务交给 Codex。</p></div>
<nav aria-label="工作台目录"><a href="#mw-compose">研究设置</a><a href="#mw-records">任务记录</a><a href="#mw-codex">Codex 交接</a></nav></header>
<noscript><p class="mw-message">启用 JavaScript 后可预览、提交研究和处理交接。任务记录中的 JSON 链接可直接读取已保存的证据。</p></noscript>
<section id="mw-compose" class="mw-composer" aria-labelledby="mw-compose-heading">
<div class="mw-form-column"><h2 id="mw-compose-heading">发起研究</h2><form data-mw-compose autocomplete="off"><fieldset class="mw-group" data-mw-draft-fields>
<legend class="mw-sr">研究设置</legend><input type="hidden" name="_csrf" value="{_h(csrf)}"><div class="mw-fields">
<label class="mw-wide">研究问题<textarea name="question" rows="2" maxlength="2000" required>这段行情中的价格与成交量有哪些变化？还需要哪些材料来解释？</textarea></label>
<label class="mw-wide">行情快照<select name="snapshot_ref" required>{snapshots}</select></label>
<label>研究标的<select name="instrument" required>{instruments}</select></label>
<label>研究截止日（UTC）<input name="as_of" type="date" value="{as_of}" required></label>
<label class="mw-wide">模型配置<select name="model_config_ref" required>{provider_options}</select></label></div>
<details class="mw-fold"><summary>补充研究摘要</summary><p class="mw-note" data-mw-material-note>选择用户模型后可补充带来源的摘要。离线研究只使用本地行情快照。</p>
<div data-mw-materials></div><button type="button" class="mw-secondary" data-mw-add disabled>添加一条材料</button></details>
<details class="mw-fold"><summary>调用与时间预算</summary><div class="mw-fields mw-inset">
<label>最长运行时间（秒）<input type="number" name="max_wall_seconds" min="1" max="300" value="120" required></label>
<label>模型调用上限（次）<input type="number" name="max_provider_calls" min="1" max="10" value="1" required></label></div>
<p class="mw-note">预览会列出本次预期调用次数与所有资源上限。离线研究的外部调用次数为零。</p></details>
<button type="submit" class="mw-primary" data-mw-preview-button disabled>预览研究材料 <span aria-hidden="true">→</span></button></fieldset></form></div>
<aside class="mw-roster" aria-labelledby="mw-roster-heading"><div class="mw-roster-head"><h2 id="mw-roster-heading">角色分工</h2><span data-mw-mode>离线</span></div>
<p data-mw-provider-note>本地规则处理所选快照。研究经理汇总各分析师引用的材料。</p><dl>{roles}</dl>
<a href="/settings/providers">设置接口与角色模型 <span aria-hidden="true">↗</span></a></aside></section>
<p data-mw-compose-status class="mw-message" role="status" aria-live="polite"></p>
<section data-mw-preview class="mw-section mw-receipt" aria-labelledby="mw-preview-heading" hidden>
<h2 id="mw-preview-heading" tabindex="-1">冻结材料预览</h2><div data-mw-preview-content></div>
<form data-mw-submit><label class="mw-check"><input name="authorized" type="checkbox" required disabled><span data-mw-consent-label>我确认按以上材料指纹和预算提交本次研究。</span></label>
<button type="submit" class="mw-primary" disabled>提交已核对的研究 <span aria-hidden="true">→</span></button></form></section>
<section id="mw-records" class="mw-section" aria-labelledby="mw-record-heading"><div class="mw-section-heading">
<h2 id="mw-record-heading" tabindex="-1">任务记录</h2><button class="mw-secondary" type="button" data-mw-refresh disabled>刷新任务记录</button></div>
<p data-mw-job-status class="mw-message" role="status" aria-live="polite"></p>
<div data-mw-job hidden></div><div class="mw-actions" data-mw-job-actions hidden>
<a data-mw-report hidden>打开研究报告 <span aria-hidden="true">↗</span></a><a data-mw-job-json download>下载研究回执 JSON</a>
<button type="button" class="mw-secondary" data-mw-cancel disabled>取消研究任务</button></div>
<h3 class="mw-subheading">本地任务历史</h3><ul class="mw-history" data-mw-history>{_history(settings.get("history", ()))}</ul></section>
<section id="mw-codex" class="mw-section" aria-labelledby="mw-codex-heading"><div class="mw-section-heading"><h2 id="mw-codex-heading" tabindex="-1">Codex 交接</h2></div>
<p class="mw-lede">把所选任务的快照、证据引用与研究方法冻结成一个包，可手动交给 Codex，也可授权已接入的执行器领取一次。</p>
<form data-mw-freeze class="mw-inline"><label>交接用途<select name="purpose"><option value="research">研究计划</option><option value="factor-mining">因子提案</option><option value="backtest">回测验收</option></select></label>
<button class="mw-primary" type="submit" disabled>冻结交接包</button></form>
<p data-mw-handoff-status class="mw-message" role="status" aria-live="polite">先从任务记录中选择或提交一项研究。</p>
<ul class="mw-history" data-mw-handoffs>{package_links}</ul>
<div data-mw-handoff hidden><h3 tabindex="-1" data-mw-packet-heading>已冻结的交接包</h3><div data-mw-packet-facts></div>
<details class="mw-fold" data-mw-packet-details><summary>查看交接包 JSON</summary><label>交接包内容<textarea class="mw-codebox" data-mw-packet readonly rows="9" spellcheck="false"></textarea></label></details>
<div class="mw-actions"><button type="button" class="mw-secondary" data-mw-download disabled>下载交接包</button>
<button type="button" class="mw-secondary" data-mw-copy disabled>复制交接包</button></div>
<div class="mw-handoff-paths"><section><h3>手动回传</h3>
<p class="mw-note" data-mw-manual-note>粘贴 Codex 生成的 method-result.v1 结果。服务器会核对任务、包指纹、证据归属与产物类型。</p>
<details class="mw-fold"><summary>回传格式与证据要求</summary><p class="mw-note">在 <code>result</code> 中引用包内的 <code>task_id</code>、<code>role</code>、<code>input_digest</code> 和证据引用。顶层须携带 <code>package_digest</code>。可在 <code>output_artifacts</code> 中提交研究计划或因子提案，并附上内容指纹。</p></details>
<form data-mw-manual><label>类型化结果 JSON<textarea name="response" class="mw-codebox" rows="5" maxlength="262144" required spellcheck="false"></textarea></label>
<button type="submit" class="mw-primary" disabled>校验并保存回传</button></form></section>
<section><h3>执行器领取</h3><p class="mw-note">授权后，已接入的执行器可以在下列限制内领取此包。派发与回传分别记录。</p>
<div data-mw-worker-state></div><div data-mw-command-block hidden><p class="mw-note">在本机终端运行命令即可启动只读 Codex 研究，结果会回传到此任务。</p><label>Codex 执行器命令<input data-mw-command class="mw-codebox" readonly spellcheck="false"></label><button type="button" class="mw-secondary" data-mw-command-copy>复制命令</button></div><form data-mw-worker><label class="mw-check"><input name="authorized" type="checkbox" required disabled>我授权执行器按此包指纹和上限领取一次任务。</label>
<div class="mw-actions"><button type="submit" class="mw-primary" disabled>授权一次领取</button>
<button type="button" class="mw-secondary" data-mw-worker-cancel disabled>取消执行器任务</button></div></form></section></div>
</div></section>{_material_template()}</article>'''

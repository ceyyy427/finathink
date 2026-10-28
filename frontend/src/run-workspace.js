// Reference-only submission and bounded observation; no financial computation.
import { readLocalJSON, userErrorMessage } from './local-request.js';
import { mountMethodHandoff } from './method-handoff.js';
import { createReadinessCheck, renderReadiness, renderExplanation } from './research-guidance.js';
const TERMINAL = new Set(['completed', 'failed', 'cancelled']);
const STATUSES = new Set(['queued', 'running', 'retryable', ...TERMINAL]);
const REF = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const mounted = new WeakMap();
const LABELS = { queued: '等待运行', running: '运行中', retryable: '等待重试', completed: '运行完成', failed: '运行失败', cancelled: '已取消' };
const STAGES = { RECEIVED: '接收任务', IDENTIFIED: '识别标的', DATA_CHECKED: '校验数据与 PIT', ANALYSTS_RUNNING: '分析师材料研究', ANALYSTS_READY: '汇总角色结果', EVIDENCE_REVIEW: '检查证据', RESEARCH_PLAN_READY: '固定研究计划', QUANT_VALIDATION: '确定性回测', RISK_REVIEW: '纸面风险检查', PAPER_DECISION_READY: '纸面决策', REPORT_PUBLISHED: '生成报告文件', LEARNING_RECORDED: '记录本轮结果' };

export function modelCallSummary(job) {
  if (job.execution_mode !== 'provider-grounded') return '本地离线流程 · 不调用模型。';
  const study = job.model_research; const calls = study?.calls ?? [];
  const dispatched = calls.filter((item) => item.dispatched).length;
  const usage = study?.usage_status === 'UNKNOWN' ? '中断或未确认的用量未知，不能按零计费。' : '用量以已返回记录为准；费用以用户服务账单为准。';
  return `材料研究 · ${job.model_config_ref} · 已登记 ${calls.length} 次调用尝试，出站记录 ${dispatched} 条；${usage}模型观察待复核。`;
}

export function normalizeJob(payload) {
  if (!payload || payload.schema_version !== 1 || payload.paper_only !== true || !['local-deterministic', 'provider-grounded'].includes(payload.execution_mode)) throw new TypeError('Invalid job boundary');
  if (!REF.test(payload.job_id ?? '') || !REF.test(payload.run_id ?? '') || !/^[0-9a-f]{64}$/.test(payload.snapshot_ref ?? '') || !STATUSES.has(payload.status)) throw new TypeError('Invalid job identity');
  if (payload.run_url !== `/research/${payload.run_id}` || (payload.report_url != null && payload.report_url !== `/research/${payload.run_id}/report/complete`)) throw new TypeError('Invalid job report identity');
  if (!payload.metrics || typeof payload.metrics !== 'object' || Array.isArray(payload.metrics) || Object.values(payload.metrics).some((value) => value !== null && (typeof value !== 'number' || !Number.isFinite(value)))) throw new TypeError('Invalid job metrics');
  if (!Array.isArray(payload.stages) || payload.stages.length > 24 || payload.stages.some((stage) => !Object.hasOwn(STAGES, stage.name) || !['COMPLETE', 'RUNNING', 'FAILED', 'PENDING'].includes(stage.status))) throw new TypeError('Invalid job stages');
  return { ...payload, metrics: { ...payload.metrics }, stages: payload.stages.map((stage) => ({ ...stage })) };
}

const readJSON = readLocalJSON;

export function createJobObserver({ fetcher = globalThis.fetch, interval = 1000, maxPolls = 300, setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
  let generation = 0; let timer; let controller;
  function stop() {
    generation++;
    if (timer !== undefined) clearTimer(timer);
    timer = undefined;
    controller?.abort();
  }
  async function watch(id, onUpdate, onError = () => {}) {
    stop();
    if (!REF.test(id)) { onError(new Error('任务标识无效。')); return; }
    const current = generation; let polls = 0;
    controller = new AbortController(); const signal = controller.signal;
    async function poll() {
      try {
        const job = normalizeJob(await readJSON(fetcher, `/api/research/jobs/${encodeURIComponent(id)}`, { signal }));
        if (current !== generation) return;
        if (job.job_id !== id) throw new Error('任务归属校验失败。');
        polls++;
        onUpdate(job);
        if (TERMINAL.has(job.status)) return;
        if (polls >= maxPolls) { onError(new Error('自动跟踪已暂停；后台任务仍受原有预算约束。点击“重新读取记录”继续查看。')); return; }
        timer = setTimer(poll, interval);
      } catch (error) {
        if (current === generation && !signal.aborted) onError(error);
      }
    }
    return poll();
  }
  return { watch, stop };
}

export function mountRunWorkspace(root, { fetcher = globalThis.fetch, location = globalThis.window?.location, history = globalThis.window?.history } = {}) {
  if (mounted.has(root)) return mounted.get(root);
  const query = (selector) => root.querySelector(selector);
  const form = query('[data-run-submit]');
  const captureForm = query('[data-snapshot-capture]');
  const sampleForm = query('[data-snapshot-sample]');
  const errorBox = query('[data-run-error]');
  const observer = createJobObserver({ fetcher });
  const csrf = query('[data-run-csrf]')?.value;
  const methodRoot = query('[data-method-handoff]');
  const methodWorkspace = methodRoot ? mountMethodHandoff(methodRoot, { fetcher, csrf }) : null;
  let lastJob; let selectedId; let snapshots = []; let requestKey; let lastIntent; let stopped = false; let historyOffset = 0; let loadingHistory = false; let loadGeneration = 0;
  const text = (selector, value) => { const target = query(selector); if (target) target.textContent = value ?? ''; };
  function showError(error) {
    if (stopped) return;
    root.dataset.workspaceState = 'ERROR';
    if (errorBox) { errorBox.textContent = `${userErrorMessage(error)} 输入已保留；结果不确定时请先重新读取任务记录，不要重复新建。`; errorBox.hidden = false; }
  }
  function clearError() { if (errorBox) errorBox.hidden = true; }
  const post = (url, value) => readJSON(fetcher, url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...value, _csrf: csrf }) });
  function element(tag, value) { const node = root.ownerDocument.createElement(tag); if (value !== undefined) node.textContent = value; return node; }
  const readiness = createReadinessCheck({ request: values => post('/api/research/readiness', values), onChange: (status, result) => { if (!stopped) renderReadiness(root, status, result); } });
  function currentValues() {
    const fields = Object.fromEntries(new FormData(form));
    let materials;
    try { materials = fields.materials?.trim() ? JSON.parse(fields.materials) : []; }
    catch { throw new Error('材料必须为 JSON 数组，不要粘贴凭证。'); }
    if (!Array.isArray(materials)) throw new Error('材料必须为 JSON 数组。');
    return { snapshot_ref: fields.snapshot_ref, question: fields.question, instrument: fields.instrument, as_of: fields.as_of, model_config_ref: fields.model_config_ref, budget: { max_wall_seconds: Number(fields.max_wall_seconds) }, allow_model_calls: fields.allow_model_calls === 'true', materials };
  }
  form?.addEventListener('input', () => readiness.invalidate());
  form?.addEventListener('change', () => readiness.invalidate());
  query('[data-readiness-check]')?.addEventListener('click', async () => {
    const button = query('[data-readiness-check]'); button.disabled = true; clearError();
    try { await readiness.check(currentValues()); } catch (error) { showError(error); }
    finally { button.disabled = false; }
  });
  const historyLabel = (job) => `${job.instrument} · ${LABELS[job.status]} · ${job.created_at} · ${job.job_id}`;
  function render(job) {
    if (stopped) return;
    lastJob = job;
    for (const button of root.querySelectorAll('[data-history-job]')) {
      if (button.dataset.historyJob === job.job_id) button.textContent = historyLabel(job);
    }
    renderExplanation(root, job);
    const restore = query('[data-run-restore]'); if (restore) restore.disabled = false;
    text('[data-guide-step="run"]', `03 运行研究 · ${LABELS[job.status]}`);
    text('[data-guide-step="report"]', `04 阅读报告 · ${job.report_status === 'AVAILABLE' ? '可阅读' : '待生成'}`);
    root.dataset.jobStatus = job.status;
    methodWorkspace?.setJob(job);
    root.dataset.workspaceState = 'READY';
    const empty = query('[data-run-empty]'); if (empty) empty.hidden = true;
    text('[data-run-state]', `${LABELS[job.status]} · ${STAGES[job.state] ?? job.state} · 第 ${job.attempts} 次尝试`);
    text('[data-run-budget]', `自提交耗时（含排队与重试）：${Number.isFinite(job.elapsed_seconds) ? `${job.elapsed_seconds} 秒` : '尚未提供'}；执行时限：${job.budget?.max_wall_seconds ?? '未知'} 秒。模型与数据费用由用户自己的接口计费，离线流程不调用模型。`);
    text('[data-run-identity]', `${job.data_mode ?? 'UNKNOWN'} DATA · ${job.instrument} · ${job.as_of} · ${job.snapshot_ref}`);
    text('[data-run-data-note]', (job.data_limitations ?? []).join(' '));
    const study = job.model_research;
    text('[data-run-model-summary]', modelCallSummary(job));
    const calls = query('[data-run-call-records]');
    if (calls) calls.replaceChildren(...(study?.calls ?? []).map((item) => element('li', `${item.role} · ${item.model} · ${item.status} · 上下文 ${item.context_bytes} B · ${item.duration_ms == null ? '耗时未确认' : `${item.duration_ms} ms`} · 用量 ${!item.dispatched ? '未出站' : item.usage?.total_units ?? '未知'}`)));
    const roles = query('[data-run-model-roles]');
    if (roles) roles.replaceChildren(...(study?.roles ?? []).map((item) => element('li', `${item.role} · ${item.status} · ${(item.claims ?? []).join(' / ')} ${(item.limitations ?? []).join(' / ')}`)));
    const synthesis = query('[data-run-synthesis]');
    if (synthesis) synthesis.replaceChildren(...(study ? [element('h3', '经理综合 · 待验证假设'), ...(study.synthesis?.hypotheses ?? []).map((item) => element('p', item)), element('h3', '分歧与待确认事项'), ...(study.synthesis?.disagreements ?? []).map((item) => element('p', item))] : []));
    text('[data-run-failure]', job.failure_kind ? `需要处理：${job.failure_kind}（阶段：${STAGES[job.failure_stage] ?? job.failure_stage ?? '发布校验'}）。检查数据与配置后，新建研究任务。` : '');
    const stages = query('[data-run-stages]');
    if (stages) stages.replaceChildren(...job.stages.map((stage) => { const node = element('li', `${STAGES[stage.name]} · ${stage.status}`); node.dataset.stageStatus = stage.status; return node; }));
    const metrics = query('[data-run-metrics]');
    if (metrics) metrics.replaceChildren(...Object.entries(job.metrics).flatMap(([key, value]) => [element('dt', key.replaceAll('_', ' ')), element('dd', value == null ? '未提供' : new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 6 }).format(value))]));
    const cancel = query('[data-run-cancel]'); if (cancel) cancel.disabled = TERMINAL.has(job.status) || job.cancel_requested === true;
    for (const [selector, url] of [['[data-run-report]', job.report_url], ['[data-run-detail]', job.report_status === 'AVAILABLE' ? job.run_url : null]]) {
      const link = query(selector); if (link) { link.hidden = !url; if (url) link.setAttribute('href', url); else link.removeAttribute('href'); }
    }
  }
  async function selectJob(id) {
    if (stopped) return;
    selectedId = id; lastJob = null; clearError();
    query('[data-run-restore]')?.setAttribute('disabled', '');
    for (const selector of ['[data-run-explanation]', '[data-run-call-records]', '[data-run-model-roles]', '[data-run-synthesis]', '[data-run-stages]']) query(selector)?.replaceChildren();
    for (const selector of ['[data-run-identity]', '[data-run-failure]', '[data-run-data-note]', '[data-run-model-summary]']) text(selector, '');
    query('[data-run-cancel]')?.setAttribute('disabled', '');
    for (const selector of ['[data-run-report]', '[data-run-detail]']) { const link = query(selector); if (link) link.hidden = true; }
    root.dataset.jobStatus = 'loading';
    text('[data-run-state]', `正在读取任务 ${id}…`);
    query('[data-run-metrics]')?.replaceChildren();
    if (location?.href && history) { const url = new URL(location.href); url.searchParams.set('job_id', id); history.replaceState(null, '', `${url.pathname}${url.search}`); }
    return observer.watch(id, render, showError);
  }
  function snapshotDescription() {
    if (!form) return;
    const selected = snapshots.find((item) => item.snapshot_ref === form.elements.namedItem('snapshot_ref').value);
    text('[data-snapshot-description]', selected ? `${selected.mode} · ${selected.instruments.join('、')} · ${selected.record_count} 条 · 截止 ${selected.as_of.slice(0, 10)}；请检查本轮可用性。` : '尚未选择数据，不会自动使用演示数据。');
    text('[data-snapshot-reference]', selected?.snapshot_ref ?? '尚未选择快照');
    text('[data-guide-step="data"]', `01 准备数据 · ${selected ? '已选择' : '待准备'}`);
    if (selected) {
      const instrument = form.elements.namedItem('instrument');
      if (!selected.instruments.includes(instrument.value)) instrument.value = selected.instruments[0] ?? '';
      if (!form.elements.namedItem('as_of').value) form.elements.namedItem('as_of').value = selected.as_of.slice(0, 10);
    }
  }
  function fillOptions(select, values, label, value, placeholder) {
    if (!select) return;
    const prior = select.value;
    const empty = element('option', placeholder); empty.value = '';
    select.replaceChildren(empty, ...values.map((item) => { const option = element('option', label(item)); option.value = value(item); return option; }));
    if (values.some((item) => value(item) === prior)) select.value = prior;
  }
  async function load({ observe = true, historyOnly = false } = {}) {
    if (stopped) return;
    const generation = ++loadGeneration; const observedAtStart = lastJob;
    clearError(); root.dataset.workspaceState = 'LOADING'; loadingHistory = true;
    for (const selector of ['[data-run-history-prev]', '[data-run-history-next]']) { const button = query(selector); if (button) button.disabled = true; }
    const loading = query('[data-run-loading]'); if (loading) loading.hidden = false;
    try {
      const [catalog, recent, connections, configured] = await Promise.all([
        historyOnly ? null : readJSON(fetcher, '/api/research/snapshots'), readJSON(fetcher, `/api/research/jobs?limit=20${historyOffset ? `&offset=${historyOffset}` : ''}`), historyOnly ? null : readJSON(fetcher, '/api/data/connections'),
        historyOnly ? null : readJSON(fetcher, '/api/research/providers/config'),
      ]);
      if (stopped || generation !== loadGeneration) return;
      if (!historyOnly) {
        snapshots = catalog.snapshots;
        text('[data-run-data-state]', snapshots.length ? `数据：已保存 ${snapshots.length} 份快照；请在下方选择本轮输入。` : connections.connections.length ? '数据：已有接口配置。请先显式获取快照，保存连接不会自动读取数据。' : '数据：尚无快照。可先生成 SAMPLE 演示数据，或配置自己的数据接口。');
        text('[data-run-model-state]', configured.outbound_enabled === false ? '模型：当前为离线流程，无需 API Key，可直接生成研究报告。' : '模型：可选择已配置接口，或保留本地离线流程。配置保存不代表连接已验证；调用前需明确授权。');
        fillOptions(form?.elements.namedItem('snapshot_ref'), snapshots, (item) => `${item.mode} · ${item.instruments.join(', ')} · ${item.record_count} 行 · ${item.snapshot_ref.slice(0, 12)}`, (item) => item.snapshot_ref, '请选择数据快照');
        fillOptions(captureForm?.elements.namedItem('connection_id'), connections.connections, (item) => item.display_name, (item) => item.connection_id, '请选择已保存的连接');
        fillOptions(form?.elements.namedItem('model_config_ref'), [{ provider_id: 'offline', model: '本地确定性流程 / 不调用模型' }, ...(configured.outbound_enabled === false ? [] : (configured.providers ?? []).filter((item) => item.enabled && item.adapter_kind !== 'offline' && item.endpoint_configured && item.credential_ref))], (item) => `${item.provider_id} · ${item.model}`, (item) => item.provider_id, '请选择执行配置');
        snapshotDescription();
      }
      const list = query('[data-run-history]');
      if (list) list.replaceChildren(...recent.jobs.map(normalizeJob).map((job) => {
        const current = lastJob !== observedAtStart && lastJob?.job_id === job.job_id ? lastJob : job;
        const li = element('li'); const button = element('button', historyLabel(current));
        button.dataset.historyJob = job.job_id;
        button.type = 'button'; button.className = 'button-secondary'; button.addEventListener('click', () => selectJob(job.job_id)); li.append(button); return li;
      }));
      if (list && !recent.jobs.length) list.append(element('li', '尚无后台任务。准备快照后，提交第一项离线研究。'));
      text('[data-run-history-page]', `${recent.total ?? recent.jobs.length} 项任务 · 当前 ${recent.jobs.length ? historyOffset + 1 : 0}–${historyOffset + recent.jobs.length}`);
      const previous = query('[data-run-history-prev]'); if (previous) previous.disabled = historyOffset === 0;
      const next = query('[data-run-history-next]'); if (next) next.disabled = historyOffset + recent.jobs.length >= (recent.total ?? recent.jobs.length);
      root.dataset.workspaceState = recent.jobs.length ? 'READY' : 'EMPTY';
      const requested = location?.href ? new URL(location.href).searchParams.get('job_id') : null;
      if (observe && (requested || selectedId || recent.jobs[0])) await selectJob(requested || selectedId || recent.jobs[0].job_id);
    } catch (error) { if (generation === loadGeneration) showError(error); }
    finally { if (generation === loadGeneration) { loadingHistory = false; if (loading) loading.hidden = true; } }
  }
  const templates = { baseline: '检验这个快照上的动量基线，并检查费用与历史回撤。', risk: '复核所选快照的费用、滑点、成交容量和历史损失情景，列出纸面风险与不确定性。', materials: '结合已授权的基本面、新闻和情绪材料，区分事实、假设与分歧，并核对对应证据。' };
  query('[data-run-template]')?.addEventListener('change', (event) => { if (templates[event.target.value]) { form.elements.namedItem('question').value = templates[event.target.value]; readiness.invalidate(); } });
  for (const [selector, direction] of [['[data-run-history-prev]', -1], ['[data-run-history-next]', 1]]) query(selector)?.addEventListener('click', () => {
    if (!loadingHistory) { historyOffset = Math.max(0, historyOffset + direction * 20); load({ observe: false, historyOnly: true }); }
  });
  form?.elements.namedItem('snapshot_ref').addEventListener('change', () => { snapshotDescription(); readiness.invalidate(); });
  form?.addEventListener('submit', async (event) => {
    event.preventDefault(); clearError();
    const button = form.querySelector('button[type="submit"]'); button.disabled = true;
    try {
      const values = currentValues();
      const checked = await readiness.check(values);
      if (!checked || JSON.stringify(values) !== JSON.stringify(currentValues())) return;
      if (!checked.can_submit) { query('[data-readiness-summary]')?.focus(); return; }
      const intent = JSON.stringify(values);
      if (lastIntent !== intent || !requestKey) { requestKey = globalThis.crypto.randomUUID(); lastIntent = intent; }
      const job = normalizeJob(await post('/api/research/jobs', { ...values, idempotency_key: requestKey }));
      render(job); await selectJob(job.job_id); await load({ observe: false });
      query('[data-run-progress-title]')?.focus();
    } catch (error) { showError(error); }
    finally { button.disabled = false; }
  });
  for (const [sourceForm, url] of [[sampleForm, '/api/research/snapshots/sample'], [captureForm, '/api/research/snapshots/capture']]) {
    sourceForm?.addEventListener('submit', async (event) => {
      event.preventDefault(); clearError();
      const button = sourceForm.querySelector('button[type="submit"]'); button.disabled = true;
      try {
        const snapshot = await post(url, Object.fromEntries(new FormData(sourceForm)));
        await load({ observe: false }); form.elements.namedItem('snapshot_ref').value = snapshot.snapshot_ref; snapshotDescription(); readiness.invalidate();
      } catch (error) { showError(error); }
      finally { button.disabled = false; }
    });
  }
  query('[data-run-cancel]')?.addEventListener('click', async () => {
    if (!selectedId) return;
    const id = selectedId; const button = query('[data-run-cancel]'); button.disabled = true;
    try { const job = normalizeJob(await post(`/api/research/jobs/${id}/cancel`, {})); if (selectedId === id) { render(job); await selectJob(id); } }
    catch (error) { showError(error); button.disabled = false; }
  });
  query('[data-run-restore]')?.addEventListener('click', () => {
    if (!lastJob || !form) return;
    for (const name of ['snapshot_ref', 'instrument', 'as_of', 'question']) form.elements.namedItem(name).value = lastJob[name] ?? '';
    form.elements.namedItem('max_wall_seconds').value = lastJob.budget?.max_wall_seconds ?? 120;
    form.elements.namedItem('model_config_ref').value = lastJob.model_config_ref ?? 'offline';
    form.elements.namedItem('allow_model_calls').checked = false;
    form.elements.namedItem('materials').value = '';
    requestKey = undefined; lastIntent = undefined; readiness.invalidate(); snapshotDescription();
    text('[data-run-restore-note]', '已填回基础输入，原任务未改变。材料需重新补充并核对；模型授权已取消，检查后再明确提交新任务。');
    form.elements.namedItem('question').focus();
  });
  query('[data-run-new]')?.addEventListener('click', () => { requestKey = undefined; lastIntent = undefined; form?.elements.namedItem('question').focus(); text('[data-snapshot-description]', '已准备新的提交标识；原研究记录会保留。'); });
  query('[data-run-refresh]')?.addEventListener('click', () => load());
  const instance = { stop() { stopped = true; loadGeneration++; readiness.invalidate(); observer.stop(); }, ready: null };
  mounted.set(root, instance);
  globalThis.window?.addEventListener('pagehide', instance.stop);
  globalThis.window?.addEventListener('pageshow', (event) => { if (event.persisted) { stopped = false; load(); } });
  instance.ready = load();
  return instance;
}

export function mountProviderSettings(form, { fetcher = globalThis.fetch } = {}) {
  const status = form.querySelector('[data-provider-status]');
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]'); button.disabled = true;
    try {
      const fields = Object.fromEntries(new FormData(form));
      await readJSON(fetcher, '/api/research/providers/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...fields, _csrf: form.querySelector('[data-provider-csrf]').value }) });
      status.textContent = '配置已保存，尚未调用模型。返回研究任务页选择接口并明确授权。';
      form.reset();
    } catch { status.textContent = '保存未通过校验。请检查标识、模型、HTTPS 地址、凭证引用和角色 JSON；不要填入 Key 本身。'; }
    finally { button.disabled = false; }
  });
}

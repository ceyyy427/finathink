// Reference-only submission and bounded observation; no financial computation.
const TERMINAL = new Set(['completed', 'failed', 'cancelled']);
const STATUSES = new Set(['queued', 'running', 'retryable', ...TERMINAL]);
const REF = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const mounted = new WeakMap();
const LABELS = { queued: '等待运行', running: '运行中', retryable: '等待重试', completed: '运行完成', failed: '运行失败', cancelled: '已取消' };
const STAGES = { RECEIVED: '接收任务', IDENTIFIED: '识别标的', DATA_CHECKED: '校验数据与 PIT', ANALYSTS_RUNNING: '离线分析师流程', ANALYSTS_READY: '汇总角色结果', EVIDENCE_REVIEW: '检查证据', RESEARCH_PLAN_READY: '固定研究计划', QUANT_VALIDATION: '确定性回测', RISK_REVIEW: '纸面风险检查', PAPER_DECISION_READY: '纸面决策', REPORT_PUBLISHED: '生成报告文件', LEARNING_RECORDED: '记录本轮结果' };

export function normalizeJob(payload) {
  if (!payload || payload.schema_version !== 1 || payload.paper_only !== true || payload.execution_mode !== 'local-deterministic') throw new TypeError('Invalid job boundary');
  if (!REF.test(payload.job_id ?? '') || !REF.test(payload.run_id ?? '') || !/^[0-9a-f]{64}$/.test(payload.snapshot_ref ?? '') || !STATUSES.has(payload.status)) throw new TypeError('Invalid job identity');
  if (payload.run_url !== `/research/${payload.run_id}` || (payload.report_url != null && payload.report_url !== `/research/${payload.run_id}/report/complete`)) throw new TypeError('Invalid job report identity');
  if (!payload.metrics || typeof payload.metrics !== 'object' || Array.isArray(payload.metrics) || Object.values(payload.metrics).some((value) => value !== null && (typeof value !== 'number' || !Number.isFinite(value)))) throw new TypeError('Invalid job metrics');
  if (!Array.isArray(payload.stages) || payload.stages.length > 24 || payload.stages.some((stage) => !Object.hasOwn(STAGES, stage.name) || !['COMPLETE', 'RUNNING', 'FAILED', 'PENDING'].includes(stage.status))) throw new TypeError('Invalid job stages');
  return { ...payload, metrics: { ...payload.metrics }, stages: payload.stages.map((stage) => ({ ...stage })) };
}

async function readJSON(fetcher, url, options = {}) {
  const response = await fetcher(url, { credentials: 'same-origin', ...options });
  if (!response.ok) throw new Error('请求未被接受，请检查所选数据、日期、问题和资源上限后重试。');
  return response.json();
}

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
  let selectedId; let snapshots = []; let requestKey; let lastIntent; let stopped = false;
  const text = (selector, value) => { const target = query(selector); if (target) target.textContent = value ?? ''; };
  function showError(error) {
    if (stopped) return;
    root.dataset.workspaceState = 'ERROR';
    if (errorBox) { errorBox.textContent = error.message || '无法读取本地研究服务，请检查服务后重新读取记录。'; errorBox.hidden = false; }
  }
  function clearError() { if (errorBox) errorBox.hidden = true; }
  const post = (url, value) => readJSON(fetcher, url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...value, _csrf: csrf }) });
  function element(tag, value) { const node = root.ownerDocument.createElement(tag); if (value !== undefined) node.textContent = value; return node; }
  function render(job) {
    if (stopped) return;
    root.dataset.jobStatus = job.status;
    root.dataset.workspaceState = 'READY';
    const empty = query('[data-run-empty]'); if (empty) empty.hidden = true;
    text('[data-run-state]', `${LABELS[job.status]} · ${STAGES[job.state] ?? job.state} · 第 ${job.attempts} 次尝试`);
    text('[data-run-identity]', `${job.data_mode ?? 'UNKNOWN'} DATA · ${job.instrument} · ${job.as_of} · ${job.snapshot_ref}`);
    text('[data-run-data-note]', (job.data_limitations ?? []).join(' '));
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
    selectedId = id; clearError();
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
    text('[data-snapshot-description]', selected ? `${selected.mode} · ${selected.record_count} 行 · PIT ${selected.pit_status} · ${selected.snapshot_ref}` : '尚未选择数据，不会自动使用演示数据。');
    if (selected) {
      form.elements.namedItem('instrument').value = selected.instruments[0] ?? '';
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
  async function load({ observe = true } = {}) {
    if (stopped) return;
    clearError(); root.dataset.workspaceState = 'LOADING';
    const loading = query('[data-run-loading]'); if (loading) loading.hidden = false;
    try {
      const [catalog, recent, connections] = await Promise.all([
        readJSON(fetcher, '/api/research/snapshots'), readJSON(fetcher, '/api/research/jobs?limit=20'), readJSON(fetcher, '/api/data/connections'),
      ]);
      if (stopped) return;
      snapshots = catalog.snapshots;
      fillOptions(form?.elements.namedItem('snapshot_ref'), snapshots, (item) => `${item.mode} · ${item.instruments.join(', ')} · ${item.record_count} 行 · ${item.snapshot_ref.slice(0, 12)}`, (item) => item.snapshot_ref, '请选择数据快照');
      fillOptions(captureForm?.elements.namedItem('connection_id'), connections.connections, (item) => item.display_name, (item) => item.connection_id, '请选择已保存的连接');
      snapshotDescription();
      const list = query('[data-run-history]');
      if (list) list.replaceChildren(...recent.jobs.map(normalizeJob).map((job) => {
        const li = element('li'); const button = element('button', `${job.instrument} · ${LABELS[job.status]} · ${job.created_at} · ${job.job_id}`);
        button.type = 'button'; button.className = 'button-secondary'; button.addEventListener('click', () => selectJob(job.job_id)); li.append(button); return li;
      }));
      root.dataset.workspaceState = recent.jobs.length ? 'READY' : 'EMPTY';
      const requested = location?.href ? new URL(location.href).searchParams.get('job_id') : null;
      if (observe && (requested || selectedId || recent.jobs[0])) await selectJob(requested || selectedId || recent.jobs[0].job_id);
    } catch (error) { showError(error); }
    finally { if (loading) loading.hidden = true; }
  }
  form?.elements.namedItem('snapshot_ref').addEventListener('change', snapshotDescription);
  form?.addEventListener('submit', async (event) => {
    event.preventDefault(); clearError();
    const button = form.querySelector('button[type="submit"]'); button.disabled = true;
    try {
      const fields = Object.fromEntries(new FormData(form));
      const values = { snapshot_ref: fields.snapshot_ref, question: fields.question, instrument: fields.instrument, as_of: fields.as_of, model_config_ref: fields.model_config_ref, budget: { max_wall_seconds: Number(fields.max_wall_seconds) } };
      const intent = JSON.stringify(values);
      if (lastIntent !== intent || !requestKey) { requestKey = globalThis.crypto.randomUUID(); lastIntent = intent; }
      const job = normalizeJob(await post('/api/research/jobs', { ...values, idempotency_key: requestKey }));
      render(job); await selectJob(job.job_id); await load({ observe: false });
    } catch (error) { showError(error); }
    finally { button.disabled = false; }
  });
  for (const [sourceForm, url] of [[sampleForm, '/api/research/snapshots/sample'], [captureForm, '/api/research/snapshots/capture']]) {
    sourceForm?.addEventListener('submit', async (event) => {
      event.preventDefault(); clearError();
      const button = sourceForm.querySelector('button[type="submit"]'); button.disabled = true;
      try {
        const snapshot = await post(url, Object.fromEntries(new FormData(sourceForm)));
        await load({ observe: false }); form.elements.namedItem('snapshot_ref').value = snapshot.snapshot_ref; snapshotDescription();
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
  query('[data-run-new]')?.addEventListener('click', () => { requestKey = undefined; lastIntent = undefined; form?.elements.namedItem('question').focus(); text('[data-snapshot-description]', '已准备新的提交标识；原研究记录会保留。'); });
  query('[data-run-refresh]')?.addEventListener('click', () => load());
  const instance = { stop() { stopped = true; observer.stop(); }, ready: null };
  mounted.set(root, instance);
  globalThis.window?.addEventListener('pagehide', instance.stop);
  globalThis.window?.addEventListener('pageshow', (event) => { if (event.persisted) { stopped = false; load(); } });
  instance.ready = load();
  return instance;
}

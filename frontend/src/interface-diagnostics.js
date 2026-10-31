import { readLocalJSON, userErrorMessage } from './local-request.js';

const STATUSES = new Set(['READY', 'UNKNOWN', 'NOT_RUN', 'NOT_CONFIGURED', 'UNAUTHORIZED', 'FORBIDDEN', 'RATE_LIMITED', 'NON_JSON', 'MISSING_FIELDS', 'SCHEMA_ERROR', 'STREAM_INTERRUPTED', 'UNSUPPORTED_STREAM', 'TIMEOUT', 'TRANSPORT', 'PROVIDER_ERROR', 'RESPONSE_TOO_LARGE', 'RESOURCE_LIMIT', 'TARGET_BLOCKED', 'RETRY_EXHAUSTED', 'FAILED', 'RECORD_INVALID', 'RECORD_EXPIRED']);
const STATUS_COPY = { READY: '检查完成', UNKNOWN: '结果未知，请读取原记录', NOT_RUN: '当前模式未发送请求', NOT_CONFIGURED: '配置或凭证尚未就绪', UNAUTHORIZED: '认证失败，请核对所选配置', FORBIDDEN: '接口拒绝访问，请核对权限', RATE_LIMITED: '接口限流，本次不重试', NON_JSON: '响应不是有效 JSON', MISSING_FIELDS: '缺少研究所需字段', SCHEMA_ERROR: '响应结构未通过校验', STREAM_INTERRUPTED: '响应中断，可能已产生用量', UNSUPPORTED_STREAM: '接口返回了流式响应，请配置 JSON 响应', TIMEOUT: '请求超时，请先读取原记录', TRANSPORT: '连接未完成，请核对接口配置', PROVIDER_ERROR: '接口返回错误', RESPONSE_TOO_LARGE: '响应超出大小上限', RESOURCE_LIMIT: '请求达到资源上限', TARGET_BLOCKED: '接口地址未通过检查', RETRY_EXHAUSTED: '请求未完成', FAILED: '存在未通过的检查项', RECORD_INVALID: '记录校验失败', RECORD_EXPIRED: '详细记录已过保存上限，请保留原请求标识' };
const QUALITY_COPY = { EVALUATED: '评估完成', PARTIAL: '部分调用已有记录', NOT_STARTED: '尚无模型调用结果', UNAVAILABLE: '原报告校验未通过，评估不可用' };
const CASE_COPY = { complete: '材料齐全', sparse: '材料不足', conflict: '观点分歧' };
const ROLE_COPY = { technical: '技术', fundamentals: '基本面', sentiment: '情绪', news: '新闻', learning: '学习', research: '研究经理' };
const id = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(value);
const intent = value => JSON.stringify(Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b))));
const rate = value => typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '未知 / 无分母';
const count = value => typeof value === 'number' && Number.isFinite(value) ? String(value) : '未知';

export function normalizeDiagnostic(value) {
  if (!value || value.schema_version !== 'interface-diagnostic.v1' || !/^diag-[a-f0-9]{32}$/.test(value.receipt_id ?? '') || !STATUSES.has(value.status)) throw new TypeError('Invalid diagnostic');
  return value;
}
export function safeEvidenceLink(href, runId) {
  return id(runId) && (href === `/research/${runId}/report/complete#model-research` || href === `/research/${runId}/report/complete#source`);
}

export function createComparisonReader({ request, onChange = () => {} }) {
  let generation = 0;
  return {
    invalidate() { generation++; onChange('STALE'); },
    async read(jobIds) {
      const current = ++generation; onChange('READING');
      try { const value = await request(jobIds); if (current !== generation) return null; onChange('READY'); return value; }
      catch (problem) { if (current !== generation) return null; onChange('ERROR'); throw problem; }
    },
  };
}

// Retain the same request key through ambiguous responses and input edits.
// Only an explicit "next task" action replaces a key for identical inputs.
export function createControlledOperation({ request, onChange = () => {}, keyFactory = () => globalThis.crypto.randomUUID(), keys = new Map(), save = () => {}, keyField = 'request_key' }) {
  let generation = 0; let pending = null; let pendingIntent = null;
  return {
    invalidate() { generation++; onChange('STALE'); },
    newAttempt(values) { if (pending) throw new Error('请先等待当前请求返回，再准备下一次请求。'); keys.delete(intent(values)); save(keys); generation++; },
    run(values) {
      const signature = intent(values);
      if (pending) return signature === pendingIntent ? pending : Promise.reject(new Error('当前请求尚未返回，请先读取原记录。'));
      if (!keys.has(signature)) {
        if (keys.size >= 1000) return Promise.reject(new Error('本页请求标识已达到容量上限，请先保存并核对历史记录。'));
        keys.set(signature, keyFactory());
      }
      try { save(keys); } catch { return Promise.reject(new Error('浏览器无法保存请求标识，请启用本地会话存储后重试。')); }
      const current = ++generation; pendingIntent = signature; onChange('BUSY');
      // Invoke synchronously so a second submit event sees the pending promise.
      let sent; try { sent = request({ ...values, [keyField]: keys.get(signature) }); } catch (error) { sent = Promise.reject(error); }
      pending = Promise.resolve(sent).then(value => { if (current !== generation) return null; onChange('READY', value); return value; }, error => { if (current !== generation) return null; onChange('ERROR'); throw error; }).finally(() => { pending = null; pendingIntent = null; onChange('IDLE'); });
      return pending;
    },
  };
}

const element = (root, tag, text) => { const item = root.ownerDocument.createElement(tag); if (text != null) item.textContent = text; return item; };
const qualityViews = new WeakMap();
const link = (root, text, href) => { const item = element(root, 'a', text); item.href = href; return item; };
function table(root, caption, headings, rows) {
  const wrap = element(root, 'div'); wrap.className = 'table-wrap'; wrap.tabIndex = 0; wrap.setAttribute('role', 'region'); wrap.setAttribute('aria-label', caption+'，可横向滚动');
  const t = element(root, 'table'); t.append(element(root, 'caption', caption)); const head = element(root, 'thead'); const tr = element(root, 'tr');
  headings.forEach(text => { const th = element(root, 'th', text); th.scope = 'col'; tr.append(th); }); head.append(tr); t.append(head);
  const body = element(root, 'tbody'); rows.forEach(row => { const r = element(root, 'tr'); row.forEach(text => r.append(element(root, 'td', text))); body.append(r); }); t.append(body); wrap.append(t); return wrap;
}
function renderDiagnostic(root, value) {
  const report = normalizeDiagnostic(value); const panel = root.querySelector('[data-interface-result]');
  panel.hidden = false; panel.dataset.resultStatus = report.status;
  const local = report.kind === 'LOCAL_COMPATIBILITY';
  panel.replaceChildren(element(root, 'h3', STATUS_COPY[report.status]), element(root, 'p', local ? '本地兼容检查 · 合成样例 · 用户账号与真实 TLS 链路未验证。' : '用户接口单次检查 · 样例响应已记录。凭证与远程原文不进入此记录。'));
  panel.append(element(root, 'p', `请求 ${count(report.request_count)} 次 · 耗时 ${count(report.duration_ms)} ms · 总用量 ${count(report.usage?.total_units)} · 费用未知`));
  panel.append(element(root, 'p', `记录 ${report.receipt_id} · ${report.created_at}`));
  if (report.missing_fields?.length) panel.append(element(root, 'p', `缺失字段：${report.missing_fields.join('、')}`));
  if (report.record_count != null) panel.append(element(root, 'p', `数据 ${count(report.record_count)} 条 · 质量提示 ${count(report.quality_issue_count)} 项 · PIT ${report.pit_available ?? 'UNKNOWN'}`));
  if (report.cases?.length) {
    panel.append(element(root, 'p', `${report.cases.filter(row => row.status === 'PASS').length} / ${report.cases.length} 项兼容检查通过`));
    const details = element(root, 'details'); details.append(element(root, 'summary', '查看协议与故障检查项'));
    details.append(table(root, '本地协议检查项', ['检查项', '预期', '观察', '结果'], report.cases.map(row => [row.case_id, row.expected, row.observed, row.status === 'PASS' ? '通过' : '未通过']))); panel.append(details);
  }
  if (report.quality_demo?.length) panel.append(table(root, '合成评分示例 · 用于验证评分流程', ['固定任务', '调用数', '结构接受率', '引用归属', '缺失识别'], report.quality_demo.map(row => [CASE_COPY[row.case_id], count(row.attempted_calls), rate(row.schema_rate), rate(row.citation_rate), rate(row.insufficient_rate)])));
  const download = root.querySelector('[data-interface-export]'); download.hidden = false; download.href = '/api/diagnostics/interfaces/'+report.receipt_id;
}
function renderQuality(root, value) {
  if (value?.schema_version !== 'research-quality.v1' || !id(value.job_id) || !id(value.run_id) || !QUALITY_COPY[value.status]) throw new TypeError('Invalid quality');
  const panel = root.querySelector('[data-quality-result]'); panel.hidden = false; panel.dataset.qualityState = value.status;
  const signature = JSON.stringify(value); const previous = qualityViews.get(panel);
  if (previous?.signature === signature && panel.dataset.qualityJob === value.job_id) return;
  const same = panel.dataset.qualityJob === value.job_id;
  const opened = new Map(same ? [...panel.querySelectorAll('details')].map(node => [node.dataset.qualitySection, node.open]) : []);
  const active = root.ownerDocument.activeElement;
  const focusedSection = same && active?.tagName === 'SUMMARY' && panel.contains(active) ? active.parentElement.dataset.qualitySection : null;
  const focusedHref = same && panel.contains(active) && active?.tagName === 'A' ? active.getAttribute('href') : null;
  panel.dataset.qualityJob = value.job_id;
  const m = value.metrics; const a = m.schema_acceptance; const c = m.citations; const missing = m.insufficient_data;
  panel.replaceChildren(element(root, 'h3', `${CASE_COPY[value.case_id]} · ${QUALITY_COPY[value.status]}`), element(root, 'p', `SAMPLE · ${value.provider_id} · 任务状态 ${value.job_status}`));
  panel.append(table(root, '本次任务的观察与分母', ['指标', '计数', '结果'], [
    ['结构接受', `${a.accepted} / ${a.attempted}`, rate(a.rate)], ['输入引用归属', `${c.valid} / ${c.observed}`, rate(c.rate)],
    ['缺失材料识别', `${missing.correct} / ${missing.expected}（已观察 ${missing.observed}）`, rate(missing.rate)],
    ['调用结果未知 / 未发送', `${a.unknown} / ${a.not_sent}`, '独立保留'], ['总用量', count(m.usage.total_units), `已知 ${count(m.usage.known_total_units)}，未知 ${count(m.usage.unknown_calls)} 次`],
    ['模型调用耗时合计', count(m.model_duration_ms)+' ms', '各调用耗时之和'], ['费用', '未知', '未推断单价'],
  ]));
  if (m.disagreements?.length) { const d = element(root, 'details'); d.dataset.qualitySection = 'disagreements'; d.append(element(root, 'summary', '查看已记录的分歧')); const ul = element(root, 'ul'); m.disagreements.forEach(text => ul.append(element(root, 'li', text))); d.append(ul); panel.append(d); }
  const details = element(root, 'details'); details.dataset.qualitySection = 'bindings'; details.append(element(root, 'summary', '查看冻结条件、角色模型与观察'));
  details.append(element(root, 'p', `样例 ${value.conditions.case_digest} · 数据 ${value.conditions.snapshot_ref} · 工作流 ${value.conditions.workflow_version}`));
  details.append(element(root, 'p', `规则 ${value.conditions.policy_fingerprint} · 时长 ${value.conditions.budget.max_wall_seconds} 秒 · 最多 ${value.conditions.budget.max_provider_calls} 次调用 · ${value.conditions.budget.max_attempts} 次尝试`));
  details.append(table(root, '角色模型映射', ['角色', '模型'], Object.entries(value.role_models).map(([role, model]) => [ROLE_COPY[role] ?? role, model])));
  (value.roles ?? []).forEach(row => { details.append(element(root, 'h4', `${ROLE_COPY[row.role] ?? row.role} · ${row.status}`)); row.claims?.forEach(text => details.append(element(root, 'p', text))); row.limitations?.forEach(text => details.append(element(root, 'p', text))); }); panel.append(details);
  const actions = element(root, 'div'); actions.className = 'action-row';
  (value.evidence_links ?? []).forEach(href => { if (safeEvidenceLink(href, value.run_id)) { const a = link(root, href.endsWith('#source') ? '核对本次输入' : '查看本次模型观察', href); a.dataset.qualityEvidence = 'true'; actions.append(a); } }); panel.append(actions);
  value.limitations?.forEach(text => panel.append(element(root, 'p', text)));
  [...panel.querySelectorAll('details')].forEach(node => { node.open = opened.get(node.dataset.qualitySection) ?? false; if (node.dataset.qualitySection === focusedSection) node.querySelector('summary')?.focus({ preventScroll: true }); });
  if (focusedHref) [...panel.querySelectorAll('a')].find(node => node.getAttribute('href') === focusedHref)?.focus({ preventScroll: true });
  qualityViews.set(panel, { signature });
  root.querySelector('[data-quality-run]').href = '/research/jobs?job_id='+encodeURIComponent(value.job_id); root.querySelector('[data-quality-run]').hidden = false;
  root.querySelector('[data-quality-export]').href = '/api/research/quality/jobs/'+encodeURIComponent(value.job_id); root.querySelector('[data-quality-export]').hidden = false;
  root.querySelector('[data-quality-cancel]').disabled = !['queued', 'running', 'retryable'].includes(value.job_status);
  root.querySelector('[data-quality-status]').textContent = `${QUALITY_COPY[value.status]}。${['queued', 'running', 'retryable'].includes(value.job_status) ? '正在读取已有任务进度…' : '原研究和调用记录已保留。'}`;
}

export function mountInterfaceDiagnostics(root, { fetcher = globalThis.fetch.bind(globalThis), location = globalThis.location, history = globalThis.history, storage } = {}) {
  if (root._finathinkDiagnostics) return root._finathinkDiagnostics;
  const find = selector => root.querySelector(selector); const csrf = find('[data-interface-csrf]').value;
  const get = path => readLocalJSON(fetcher, path); const post = (path, values) => readLocalJSON(fetcher, path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...values, _csrf: csrf }) });
  const status = text => { find('[data-interface-status]').textContent = text; };
  const error = problem => status(userErrorMessage(problem));
  const probe = find('[data-interface-probe]'); const quality = find('[data-quality-submit]');
  let providers = []; let connections = []; let selectedJob = null; let timer = null; let generation = 0; let disposed = false; let localBusy = false; let receiptGeneration = 0;
  const compared = new Set();
  const comparison = createComparisonReader({ request: jobIds => post('/api/research/quality/compare', { job_ids: jobIds }), onChange: state => {
    const target = find('[data-quality-comparison]'); target.dataset.comparisonState = state;
    if (state === 'READING') { target.hidden = false; target.textContent = '正在读取已有任务并比较冻结条件…'; }
    if (state === 'STALE' && !target.hidden) target.textContent = '比较选择已改变，请重新比较。';
  } });
  const updateURL = (key, value) => { const url = new URL(location.href); url.searchParams.set(key, value); history.replaceState(null, '', url.pathname+url.search); };
  const currentProbe = () => ({ target_kind: probe.elements.target_kind.value, config_ref: probe.elements.config_ref.value, authorize: true, request_timeout_seconds: 5, ...(probe.elements.target_kind.value === 'data' ? Object.fromEntries(['instrument', 'start', 'end', 'as_of'].map(name => [name, probe.elements[name].value])) : {}) });
  const currentQuality = () => ({ case_id: quality.elements.case_id.value, model_config_ref: quality.elements.model_config_ref.value, allow_model_calls: true, budget: { max_wall_seconds: Number(quality.elements.max_wall_seconds.value) } });
  const keys = name => {
    const cache = storage ?? globalThis.sessionStorage;
    const saved = cache.getItem(name); const entries = saved ? JSON.parse(saved) : [];
    if (!Array.isArray(entries) || entries.length > 1000 || entries.some(row => !Array.isArray(row) || row.length !== 2 || typeof row[0] !== 'string' || !id(row[1]))) throw new TypeError('Invalid request keys');
    return { keys: new Map(entries), save: map => cache.setItem(name, JSON.stringify([...map])) };
  };
  const busy = form => state => { if (state === 'BUSY') form.setAttribute('aria-busy', 'true'); if (state === 'IDLE') form.setAttribute('aria-busy', 'false'); form.querySelectorAll('button').forEach(button => { button.disabled = form.getAttribute('aria-busy') === 'true'; }); };
  let probeOp; let qualityOp;
  try {
    probeOp = createControlledOperation({ ...keys('finathink-interface-intents.v1'), request: values => post('/api/diagnostics/interfaces/probe', values), onChange: busy(probe) });
    qualityOp = createControlledOperation({ ...keys('finathink-quality-intents.v1'), request: values => post('/api/research/quality/jobs', values), onChange: busy(quality), keyField: 'idempotency_key' });
  } catch { status('无法读取浏览器请求标识。请启用会话存储后刷新；本页尚未发送请求。'); probe.querySelector('button[type=submit]').disabled = true; quality.querySelector('button[type=submit]').disabled = true; }
  const showReceipt = value => { renderDiagnostic(root, value); updateURL('receipt', value.receipt_id); };
  async function readReceipt(reference) {
    probeOp?.invalidate();
    const current = ++receiptGeneration;
    try { const result = await get('/api/diagnostics/interfaces/'+encodeURIComponent(reference)); if (current === receiptGeneration && !disposed) { showReceipt(result); status('已读取本地检查记录。'); } }
    catch (problem) { if (current === receiptGeneration && !disposed) error(problem); }
  }
  async function loadHistory() {
    const [receipts, jobs] = await Promise.all([get('/api/diagnostics/interfaces'), get('/api/research/quality/jobs')]);
    if (disposed) return;
    const list = find('[data-interface-history]'); list.replaceChildren();
    (receipts.records ?? []).forEach(row => { normalizeDiagnostic(row); const li = element(root, 'li'); const a = link(root, `${row.kind === 'LOCAL_COMPATIBILITY' ? '本地兼容' : row.kind === 'USER_MODEL' ? '模型接口' : '数据接口'} · ${STATUS_COPY[row.status]} · ${row.created_at}`, '?receipt='+row.receipt_id); a.addEventListener('click', event => { event.preventDefault(); readReceipt(row.receipt_id); }); li.append(a); list.append(li); });
    if (!list.hasChildNodes()) list.append(element(root, 'li', '尚无接口检查记录。运行检查后可在这里重新读取。'));
    const historyList = find('[data-quality-history]'); historyList.replaceChildren();
    (jobs.jobs ?? []).forEach(row => {
      if (!id(row.job_id) || !CASE_COPY[row.case_id]) throw new TypeError('Invalid job history');
      const li = element(root, 'li'); const label = element(root, 'label'); const checkbox = element(root, 'input'); checkbox.type = 'checkbox'; checkbox.value = row.job_id; checkbox.checked = compared.has(row.job_id); checkbox.setAttribute('aria-label', '比较 '+CASE_COPY[row.case_id]+' '+row.job_id); checkbox.addEventListener('change', () => { if (checkbox.checked) compared.add(row.job_id); else compared.delete(row.job_id); comparison.invalidate(); }); label.append(checkbox, element(root, 'span', '选择比较'));
      const a = link(root, `${CASE_COPY[row.case_id]} · ${row.job_id}`, '?quality_job='+encodeURIComponent(row.job_id)); a.addEventListener('click', event => { event.preventDefault(); selectJob(row.job_id); }); li.append(label, a); historyList.append(li);
    });
    if (!historyList.hasChildNodes()) historyList.append(element(root, 'li', '尚无固定质量任务。选择模型并授权后可运行。'));
  }
  async function readJob(jobId, current) {
    try {
      const value = await get('/api/research/quality/jobs/'+encodeURIComponent(jobId));
      if (current !== generation || disposed) return;
      renderQuality(root, value);
      if (['queued', 'running', 'retryable'].includes(value.job_status)) timer = setTimeout(() => readJob(jobId, current), 800);
      else { timer = null; await loadHistory(); }
    } catch (problem) { if (current === generation && !disposed) find('[data-quality-status]').textContent = userErrorMessage(problem)+' 可用“读取所选任务状态”继续查看，原任务不会重建。'; }
  }
  function selectJob(jobId) {
    if (!id(jobId)) return;
    qualityOp?.invalidate();
    clearTimeout(timer); selectedJob = jobId; updateURL('quality_job', jobId);
    const panel = find('[data-quality-result]');
    if (panel.dataset.qualityJob !== jobId) {
      panel.hidden = false; panel.dataset.qualityState = 'READING'; delete panel.dataset.qualityJob;
      panel.replaceChildren(element(root, 'p', '正在读取所选任务的原始记录…'));
      find('[data-quality-cancel]').disabled = true; find('[data-quality-run]').hidden = true; find('[data-quality-export]').hidden = true;
    }
    readJob(jobId, ++generation);
  }
  function fillOptions(select, rows, value, label) { const previous = select.value; select.replaceChildren(element(root, 'option', rows.length ? '请选择已保存配置' : '尚无可用配置，请先到接口设置保存')); rows.forEach(row => { const option = element(root, 'option', label(row)); option.value = value(row); select.append(option); }); select.options[0].value = ''; if (rows.some(row => value(row) === previous)) select.value = previous; }
  function probeKind() { const model = probe.elements.target_kind.value === 'model'; fillOptions(probe.elements.config_ref, model ? providers : connections, row => model ? row.provider_id : row.connection_id, row => model ? row.provider_id+' · '+row.model : row.display_name); root.querySelectorAll('[data-interface-data-field]').forEach(label => { label.hidden = model; label.querySelector('input').disabled = model; }); }
  const listen = (target, event, fn) => target.addEventListener(event, fn);
  listen(probe, 'input', event => { probeOp?.invalidate(); if (event.target.name !== 'authorize') probe.elements.authorize.checked = false; });
  listen(quality, 'input', event => { qualityOp?.invalidate(); if (event.target.name !== 'allow_model_calls') quality.elements.allow_model_calls.checked = false; });
  listen(probe.elements.target_kind, 'change', probeKind);
  listen(probe, 'submit', async event => { event.preventDefault(); if (!probeOp || !probe.elements.authorize.checked) return; const current = ++receiptGeneration; status('正在执行本次单次接口检查…'); try { const value = await probeOp.run(currentProbe()); if (value && current === receiptGeneration && !disposed) { showReceipt(value); status('本次接口结果已保存在本地。'); await loadHistory(); } } catch (problem) { if (current === receiptGeneration && !disposed) error(problem); } });
  listen(find('[data-interface-new]'), 'click', () => { try { probeOp?.newAttempt(currentProbe()); probe.elements.authorize.checked = false; status('已准备新的请求标识，请重新授权后检查。'); } catch (problem) { error(problem); } });
  listen(find('[data-quality-new]'), 'click', () => { try { qualityOp?.newAttempt(currentQuality()); quality.elements.allow_model_calls.checked = false; find('[data-quality-status]').textContent = '已准备新的任务标识，请重新授权。'; } catch (problem) { error(problem); } });
  listen(quality, 'submit', async event => { event.preventDefault(); if (!qualityOp || !quality.elements.allow_model_calls.checked) return; find('[data-quality-status]').textContent = '正在提交固定样例研究；不自动重试…'; try { const value = await qualityOp.run(currentQuality()); if (value && !disposed) { selectJob(value.job_id); await loadHistory(); } } catch (problem) { find('[data-quality-status]').textContent = userErrorMessage(problem)+' 重试会使用原任务标识；也可先重新读取历史。'; } });
  listen(find('[data-interface-local]'), 'click', async event => { if (localBusy) return; const current = ++receiptGeneration; localBusy = true; event.currentTarget.disabled = true; status('正在本机检查兼容协议与合成评分流程…'); try { const result = await post('/api/diagnostics/interfaces/local', {}); if (current === receiptGeneration && !disposed) { showReceipt(result); status('本地兼容检查已完成。'); await loadHistory(); } } catch (problem) { if (current === receiptGeneration && !disposed) error(problem); } finally { localBusy = false; find('[data-interface-local]').disabled = false; } });
  listen(find('[data-interface-refresh]'), 'click', () => { Promise.all([loadHistory(), selectedJob ? readJob(selectedJob, ++generation) : Promise.resolve()]).then(() => status('已重新读取本地记录。')).catch(error); });
  listen(find('[data-quality-refresh]'), 'click', () => { if (selectedJob) selectJob(selectedJob); else find('[data-quality-status]').textContent = '请先从本地任务中选择一项。'; });
  listen(find('[data-quality-cancel]'), 'click', async () => { if (!selectedJob) return; try { await post('/api/research/jobs/'+encodeURIComponent(selectedJob)+'/cancel', {}); selectJob(selectedJob); } catch (problem) { error(problem); } });
  listen(find('[data-quality-compare]'), 'click', async () => {
    const target = find('[data-quality-comparison]'); target.hidden = false;
    if (compared.size < 2 || compared.size > 4) { target.textContent = '请选择2至4个不同的固定质量任务。'; return; }
    try { const result = await comparison.read([...compared]); if (!result || disposed) return; target.replaceChildren(element(root, 'h3', result.comparable ? '冻结条件一致，可以对照本次观察' : '冻结条件不同，请分别解释各自结果'));
      target.append(table(root, '固定任务比较 · 无综合分数或收益排名', ['任务 / 配置', '角色模型', '结构接受', '引用归属', '调用用量', '耗时合计'], result.items.map(row => [`${CASE_COPY[row.case_id]} · ${row.provider_id} · ${row.status}`, Object.entries(row.role_models).map(([role, model]) => `${ROLE_COPY[role] ?? role}:${model}`).join('；'), rate(row.metrics.schema_acceptance.rate), rate(row.metrics.citations.rate), count(row.metrics.usage.total_units), count(row.metrics.model_duration_ms)+' ms'])));
    } catch (problem) { target.textContent = userErrorMessage(problem); }
  });
  const ready = Promise.all([get('/api/data/connections'), get('/api/research/providers/config'), get('/api/research/quality/cases'), loadHistory()]).then(([data, configured]) => {
    if (disposed) return; connections = data.connections ?? []; providers = configured.outbound_enabled === false ? [] : (configured.providers ?? []).filter(row => row.enabled && row.adapter_kind !== 'offline' && row.endpoint_configured && row.credential_ref);
    probeKind(); fillOptions(quality.elements.model_config_ref, providers, row => row.provider_id, row => row.provider_id+' · '+row.model);
    root.dataset.interfaceReady = 'true'; status(probeOp && qualityOp ? '本地记录已就绪。运行检查和模型任务都需要主动操作。' : '本地兼容检查可用。启用浏览器会话存储后，可检查用户接口和提交模型任务。');
    const params = new URL(location.href).searchParams; if (params.get('receipt')) readReceipt(params.get('receipt')); if (params.get('quality_job')) selectJob(params.get('quality_job'));
  }).catch(error);
  const destroy = () => { disposed = true; generation++; receiptGeneration++; clearTimeout(timer); };
  globalThis.addEventListener?.('pagehide', destroy, { once: true });
  root._finathinkDiagnostics = { ready, destroy }; return root._finathinkDiagnostics;
}

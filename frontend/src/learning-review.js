// Render approved, server-owned evidence. Never derive outcomes or policy rules.
import { readLocalJSON, userErrorMessage } from './local-request.js';
const mounted = new WeakMap();
const DIGEST = /^[0-9a-f]{64}$/;
const STATES = new Set(['PENDING_REVIEW', 'APPROVED', 'REJECTED', 'KEEP_POLICY']);
const ACTIVE = new Set(['queued', 'running', 'retryable']);
const labels = { PENDING_REVIEW: '待审核', APPROVED: '已批准', REJECTED: '已拒绝', KEEP_POLICY: '保留原配置' };

export function normalizeLearningReview(value) {
  if (!value || !/^learning-[0-9a-f]{32}$/.test(value.review_id ?? '') || !/^[A-Za-z0-9_-]+$/.test(value.run_id ?? '') || value.paper_only !== true || !STATES.has(value.status)) throw new TypeError('复盘身份或状态无效。');
  const monitoring = value.source_kind === 'FACTOR_VALIDATION_MONITORING';
  for (const key of ['parent_policy_ref', 'proposal_digest', 'preview_fingerprint', ...(monitoring ? [] : ['source_snapshot_ref', 'evidence_snapshot_ref', 'observed_snapshot_ref'])]) if (!DIGEST.test(value[key] ?? '')) throw new TypeError('复盘缺少完整证据引用。');
  if (value.preview?.proposal_digest !== value.proposal_digest || value.preview?.policy_fingerprint !== value.preview_fingerprint || (value.status === 'APPROVED' && !DIGEST.test(value.policy_ref ?? ''))) throw new TypeError('复盘结果与审核预览不一致。');
  if (monitoring) {
    const observation = value.observation?.monitoring;
    if (value.settlement !== null || value.proposal?.settlement_digest !== null || Object.keys(value.proposal?.factor_weight_updates ?? {}).length || Object.keys(value.proposal?.risk_rule_updates ?? {}).length || observation?.schema_version !== 'factor-monitoring.v1' || observation.status !== 'REVIEW_RECOMMENDED' || !DIGEST.test(observation.fingerprint ?? '') || !Array.isArray(observation.evidence_refs) || observation.evidence_refs.length < 3 || observation.evidence_refs.some((row) => !DIGEST.test(row.evaluation_fingerprint ?? ''))) throw new TypeError('因子观察不能冒充纸面结算或直接调权。');
  } else if (value.observation?.horizon_observations !== 5 || !Number.isFinite(value.observation?.equity) || !Number.isFinite(value.observation?.net_return)) throw new TypeError('复盘结果与审核预览不一致。');
  return value;
}

export function mountLearningReview(root, { fetcher = globalThis.fetch, interval = 1000 } = {}) {
  if (mounted.has(root)) return mounted.get(root);
  const q = (selector) => root.querySelector(selector);
  const doc = root.ownerDocument;
  const node = (tag, text = '') => { const item = doc.createElement(tag); item.textContent = text; return item; };
  const text = (selector, value) => { q(selector).textContent = value ?? ''; };
  const number = (value) => Number.isFinite(value) ? new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 6 }).format(value) : '未知';
  const percent = (value) => Number.isFinite(value) ? new Intl.NumberFormat('zh-CN', { style: 'percent', maximumFractionDigits: 3 }).format(value) : '未知';
  const form = q('[data-learning-form]'); const reviewForm = q('[data-learning-review-form]'); const rerunForm = q('[data-learning-rerun-form]');
  const originalLabels = Object.fromEntries(['[data-learning-outcome-heading]', '[data-learning-outcome] caption', '[data-learning-decay-heading]', '[data-learning-decay-description]', '[data-learning-decay] th'].map((selector) => [selector, q(selector).textContent]));
  const csrf = q('[data-learning-csrf]').value;
  let current; let snapshots = []; let policies = []; let busy = false; let disposed = false; let generation = 0; let timer; let controller; let activeJob; let rerunKey;
  const request = (url, options) => readLocalJSON(fetcher, url, options);
  const post = (url, values) => request(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...values, _csrf: csrf }) });
  const reportLink = (target, url) => {
    const safe = /^\/research\/[A-Za-z0-9_-]+\/report\/complete$/.test(url ?? ''); target.hidden = !safe;
    if (safe) target.setAttribute('href', url); else target.removeAttribute('href');
  };
  function controls() {
    root.setAttribute('aria-busy', String(busy));
    root.querySelectorAll('button').forEach((button) => { button.disabled = busy; });
    q('[data-learning-approve]').disabled = busy || current?.status !== 'PENDING_REVIEW';
    q('[data-learning-reject]').disabled = busy || current?.status !== 'PENDING_REVIEW';
    q('[data-learning-cancel]').disabled = busy || !ACTIVE.has(activeJob?.status);
  }
  function error(reason) {
    q('[data-learning-status]').setAttribute('role', 'alert');
    text('[data-learning-status]', `需要处理：${userErrorMessage(reason)} 输入已保留；可重新读取记录后重试。`);
  }
  function options(select, items, placeholder) {
    const old = select.value;
    select.replaceChildren(...(placeholder ? [Object.assign(node('option', placeholder), { value: '' })] : []), ...items.map(([value, label]) => Object.assign(node('option', label), { value })));
    if (items.some(([value]) => value === old)) select.value = old;
  }
  function rows(selector, values) {
    q(selector).querySelector('tbody').replaceChildren(...values.map((values) => { const row = node('tr'); for (const value of values) row.append(node('td', value)); return row; }));
  }
  function render(value) {
    current = normalizeLearningReview(value);
    root.dataset.learningState = current.status; q('[data-learning-detail]').hidden = false;
    q('[data-learning-status]').setAttribute('role', 'status');
    text('[data-learning-status]', `${labels[current.status]} · ${current.review_id} · PAPER-ONLY`);
    q('[data-learning-hypotheses]').replaceChildren(...current.hypotheses.map((hypothesis) => node('li', hypothesis)));
    const observed = current.observation;
    const monitoring = current.source_kind === 'FACTOR_VALIDATION_MONITORING';
    const monitoringLabels = ['假设与验证观察', '独立最终测试的描述性变化，非成交估值；没有已实现纸面收益。', '独立窗口变化', '仅比较同定义、同范围且互不重叠的已完成最终测试；描述性变化不等于统计显著衰减。', '独立最终测试窗口'];
    Object.keys(originalLabels).forEach((selector, index) => text(selector, monitoring ? monitoringLabels[index] : originalLabels[selector]));
    text('[data-learning-binding]', monitoring ? `${current.run_id} · 因子最终测试观察，非纸面成交结算 · 截止 ${current.as_of} · ${observed.monitoring.evidence_refs.length} 个独立窗口 · 观察指纹 ${observed.monitoring.fingerprint}` : `${current.run_id} · 原数据 ${current.data_modes?.source ?? 'UNKNOWN'} / 后续数据 ${current.data_modes?.evidence ?? 'UNKNOWN'} / 估值数据 ${current.data_modes?.observed ?? 'UNKNOWN'} · 观察到 ${observed.mark_timestamp} · 证据可用 ${observed.available_at} · 原数据指纹 ${current.source_snapshot_ref} · 估值数据指纹 ${current.observed_snapshot_ref}`);
    rows('[data-learning-outcome]', monitoring ? [['证据类型', '独立最终测试，不代表已实现收益'], ['早期 / 最近描述性 IC', `${number(observed.monitoring.baseline_ic)} / ${number(observed.monitoring.recent_ic)}`], ['适用范围', observed.monitoring.reproduction_scope.join(' / ')], ['动作', '登记研究复核建议，不修改权重或风险规则']] : [['计划目标仓位', percent(observed.planned_weight)], ['实际纸面成交仓位', percent(observed.filled_weight)], ['成交参考价 / 观察末价', `${number(observed.reference_price)} / ${number(observed.mark_price)}`], ['观察末净值 / 纸面净收益', `${number(observed.equity)} / ${percent(observed.net_return)}`], ['费用 / 滑点成本', `${number(observed.fees)} / ${number(observed.slippage)}`], ['现金 / 持有数量', `${number(observed.cash)} / ${number(observed.quantity)}`]]);
    rows('[data-learning-decay]', monitoring ? observed.monitoring.evidence_refs.map((row) => [row.window?.join(' → ') ?? row.run_ref, number(row.ic)]) : observed.factor_decay.horizons.map((horizon, index) => [String(horizon), number(observed.factor_decay.information_coefficients[index])]));
    text('[data-learning-basis]', observed.recommendation_basis);
    rows('[data-learning-preview]', [['动量信号预算乘数', number(current.baseline.factor_weights.momentum_2obs), number(current.preview.factor_weights.momentum_2obs)], ['经验 CVaR 上限', percent(current.baseline.risk_rules.max_cvar), percent(current.preview.risk_rules.max_cvar)]]);
    text('[data-learning-preview-binding]', `原版本 v${current.baseline.version} · ${current.status === 'KEEP_POLICY' ? '本次不创建新版本' : `建议 v${current.preview.next_version}`} · 原配置 ${current.parent_policy_ref} · 预览 ${current.preview_fingerprint}`);
    text('[data-learning-admission]', current.admission ? `${labels[current.status]} · ${current.admission.rationale} · ${current.admission.as_of}${current.policy_ref ? ` · 新配置 ${current.policy_ref}` : ' · 未创建配置版本'}` : current.status === 'KEEP_POLICY' ? '本次没有数值修改，不创建空版本。' : '等待显式审核；尚未改变任何配置。');
    reportLink(q('[data-learning-original-report]'), current.source_report_url);
    rerunForm.hidden = monitoring;
    if (!monitoring) {
      form.elements.run_id.value = current.run_id; rerunForm.elements.run_id.value = current.run_id;
      rerunForm.elements.snapshot_ref.value = current.source_snapshot_ref;
      rerunForm.elements.policy_ref.value = current.policy_ref ?? current.parent_policy_ref;
    }
    reviewForm.elements.rationale.value = current.admission?.rationale ?? '';
    q('[data-learning-ack]').checked = false;
    rerunDate(); controls();
  }
  function rerunDate() {
    const selected = snapshots.find((item) => item.snapshot_ref === rerunForm.elements.snapshot_ref.value);
    const policy = policies.find((item) => item.policy_ref === rerunForm.elements.policy_ref.value);
    if (selected) rerunForm.elements.as_of.value = [selected.as_of.slice(0, 10), policy?.available_as_of ?? ''].sort().at(-1);
  }
  async function refresh() {
    const [reviews, versions, data, jobs] = await Promise.all([request('/api/research/learning'), request('/api/research/learning/policies'), request('/api/research/snapshots'), request('/api/research/jobs')]);
    if (disposed) return;
    snapshots = data.snapshots; policies = versions.policies;
    const completed = jobs.jobs.filter((item) => item.status === 'completed' && item.report_status === 'AVAILABLE' && !item.factor_study);
    const runOptions = completed.map((item) => [item.run_id, `${item.instrument} · ${item.question} · ${item.run_id}`]);
    for (const target of [form.elements.run_id, rerunForm.elements.run_id]) options(target, runOptions, '请选择已有完整报告的研究');
    const dataOptions = snapshots.map((item) => [item.snapshot_ref, `${item.mode} · ${item.instruments.join(', ')} · ${item.record_count}行 · ${item.as_of.slice(0, 10)} · ${item.snapshot_ref.slice(0, 12)}`]);
    options(form.elements.evidence_snapshot_ref, dataOptions, '请选择后续观察数据'); options(rerunForm.elements.snapshot_ref, dataOptions, '请选择复跑数据');
    options(rerunForm.elements.policy_ref, policies.map((item) => [item.policy_ref, `v${item.version} · ${item.version === 0 ? '原始基准' : '已批准'} · ${item.policy_ref.slice(0, 12)}`]));
    const items = reviews.reviews.map((item) => { const li = node('li'); const button = node('button', `${item.observation.instrument} · ${labels[item.status]} · ${item.as_of} · ${item.run_id}`); button.type = 'button'; button.addEventListener('click', () => action(() => select(item.review_id))); li.append(button); return li; });
    q('[data-learning-history]').replaceChildren(...(items.length ? items : [node('li', '尚无复盘。先选择原研究与后续快照，生成第一份修改预览。')]));
    const runs = jobs.jobs.filter((item) => item.policy_lineage?.rerun_of).map((item) => { const li = node('li'); const button = node('button', `${item.run_id} · v${item.policy_lineage.version} · ${item.status}${item.policy_lineage.retrospective ? ' · 回溯比较' : ''}`); button.type = 'button'; button.addEventListener('click', () => watchJob(item)); li.append(button); return li; });
    q('[data-learning-runs]').replaceChildren(...(runs.length ? runs : [node('li', '尚无版本复跑。可选择基准版本或已批准的新版本。')]));
    controls();
  }
  async function select(id) {
    const value = normalizeLearningReview(await request(`/api/research/learning/${encodeURIComponent(id)}`));
    if (value.review_id !== id) throw new Error('返回记录不属于所选复盘。');
    if (!disposed) render(value);
  }
  function stop() { generation++; clearTimeout(timer); controller?.abort(); }
  async function watchJob(job) {
    stop(); const expected = generation; controller = new AbortController(); let polls = 0;
    activeJob = null; root.dataset.learningJobState = 'loading';
    text('[data-learning-job]', `正在读取 ${job.run_id}…`);
    reportLink(q('[data-learning-report]'), null); controls();
    async function poll() {
      try {
        const result = await request(`/api/research/jobs/${encodeURIComponent(job.job_id)}`, { signal: controller.signal });
        if (expected !== generation || disposed) return;
        if (result.job_id !== job.job_id || result.run_id !== job.run_id || result.paper_only !== true) throw new Error('复跑任务身份失配。');
        if (rerunKey?.jobId === result.job_id) rerunKey.status = result.status;
        activeJob = result; root.dataset.learningJobState = result.status;
        text('[data-learning-job]', `${result.run_id} · ${result.status} · v${result.policy_lineage?.version ?? '未知'}${result.policy_lineage?.retrospective ? ' · 回溯比较，不计入新学习' : ''}${result.failure_kind ? ` · ${result.failure_kind}` : ''}`);
        reportLink(q('[data-learning-report]'), result.report_status === 'AVAILABLE' ? result.report_url : null);
        controls(); polls++;
        if (ACTIVE.has(result.status) && polls < 300) timer = setTimeout(poll, interval);
        else if (ACTIVE.has(result.status)) text('[data-learning-job]', `${result.run_id}：已暂停自动读取；后台任务仍运行，可从“最近的版本复跑”重新查看。`);
        else await refresh();
      } catch (reason) { if (expected === generation && !disposed && reason.name !== 'AbortError') error(reason); }
    }
    return poll();
  }
  async function action(operation) {
    if (busy || disposed) return;
    busy = true; controls();
    try { await operation(); }
    catch (reason) { if (!disposed) error(reason); }
    finally { busy = false; if (!disposed) controls(); }
  }
  form.addEventListener('submit', (event) => {
    event.preventDefault(); action(async () => { const value = await post('/api/research/learning', Object.fromEntries(new FormData(form))); await refresh(); render(value); });
  });
  reviewForm.addEventListener('submit', (event) => {
    event.preventDefault(); const decision = event.submitter?.value;
    action(async () => {
      if (!current || current.status !== 'PENDING_REVIEW') throw new Error('请先选择待审核提案。');
      if (decision === 'APPROVED' && !q('[data-learning-ack]').checked) { q('[data-learning-ack]').focus(); throw new Error('请先确认已核对修改预览。'); }
      const value = await post(`/api/research/learning/${current.review_id}/decision`, { decision, expected_preview_fingerprint: current.preview_fingerprint, rationale: reviewForm.elements.rationale.value });
      await refresh(); render(value);
    });
  });
  rerunForm.addEventListener('submit', (event) => {
    event.preventDefault(); action(async () => {
      const values = Object.fromEntries(new FormData(rerunForm)); const signature = JSON.stringify(values);
      if (rerunKey?.signature !== signature || ['cancelled', 'failed'].includes(rerunKey?.status)) rerunKey = { signature, key: crypto.randomUUID() };
      const submission = rerunKey;
      const job = await post('/api/research/learning/rerun', { ...values, idempotency_key: submission.key });
      submission.jobId = job.job_id;
      await watchJob(job); await refresh();
    });
  });
  form.elements.evidence_snapshot_ref.addEventListener('change', () => { const selected = snapshots.find((item) => item.snapshot_ref === form.elements.evidence_snapshot_ref.value); if (selected) form.elements.as_of.value = selected.as_of.slice(0, 10); });
  for (const target of [rerunForm.elements.policy_ref, rerunForm.elements.snapshot_ref]) target.addEventListener('change', rerunDate);
  q('[data-learning-cancel]').addEventListener('click', () => action(async () => { if (activeJob && ACTIVE.has(activeJob.status)) { const job = await post(`/api/research/jobs/${activeJob.job_id}/cancel`, {}); await watchJob(job); } }));
  q('[data-learning-refresh]').addEventListener('click', () => action(async () => { await refresh(); if (current) await select(current.review_id); else text('[data-learning-status]', '本地记录已就绪。请选择原研究与后续观察。'); }));
  busy = true; controls();
  const ready = refresh().then(() => { if (!disposed) text('[data-learning-status]', '本地记录已就绪。请选择原研究与后续观察。'); }).catch((reason) => { if (!disposed) error(reason); }).finally(() => { busy = false; if (!disposed) controls(); });
  const api = { ready, select, dispose() { disposed = true; stop(); } }; mounted.set(root, api);
  doc.defaultView?.addEventListener('pagehide', api.dispose, { once: true }); return api;
}

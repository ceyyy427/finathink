// Server-owned factor evidence: no financial computation or strategy execution.
const STATES = new Set(['DECLARED', 'VALIDATING', 'VALIDATED', 'VALIDATION_FAILED', 'FROZEN', 'TEST_RESERVED', 'TESTING', 'TEST_EVALUATED', 'TEST_FAILED', 'REVIEWED']);
const ACTIVE = new Set(['DECLARED', 'VALIDATING', 'TEST_RESERVED', 'TESTING']);
const mounted = new WeakMap();
const digest = /^[0-9a-f]{64}$/;

export function normalizeFactorStudy(payload) {
  if (!payload || !/^study-[A-Za-z0-9_-]+$/.test(payload.study_id ?? '') || !STATES.has(payload.state) || payload.production_write !== false || !digest.test(payload.spec_ref ?? '') || !digest.test(payload.spec?.snapshot_ref ?? '')) throw new TypeError('实验身份、状态或快照绑定无效。');
  for (const result of [payload.validation, payload.test_result]) {
    if (result && (result.snapshot_ref !== payload.spec.snapshot_ref || result.spec_ref !== payload.spec_ref || !Array.isArray(result.summaries))) throw new TypeError('实验结果不属于所选章程。');
  }
  return payload;
}

export function mountExperimentWorkspace(root, { fetcher = globalThis.fetch, interval = 1000 } = {}) {
  if (mounted.has(root)) return mounted.get(root);
  const query = (selector) => root.querySelector(selector);
  const document = root.ownerDocument;
  const node = (tag, text) => { const item = document.createElement(tag); item.textContent = text ?? ''; return item; };
  const text = (selector, value) => { const target = query(selector); if (target) target.textContent = value ?? ''; };
  const csrf = query('[data-study-csrf]').value;
  const numeric = (value) => typeof value === 'number' && Number.isFinite(value) ? String(Number(value.toPrecision(6))) : '未提供';
  let generation = 0; let timer; let controller; let current; let snapshots = []; let recipes = []; let createKey;
  const request = async (url, options = {}) => {
    const response = await fetcher(url, { credentials: 'same-origin', ...options });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error ?? '本地请求失败，请检查数据与输入后重试。');
    return payload;
  };
  const post = (url, values) => request(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...values, _csrf: csrf }) });
  function stop() { generation++; clearTimeout(timer); controller?.abort(); }
  function error(reason) {
    text('[data-study-status]', `需要处理：${reason.message}`);
    query('[data-study-status]').setAttribute('role', 'alert');
  }
  async function history() {
    const payload = await request('/api/research/experiments');
    query('[data-study-history]').replaceChildren(...payload.studies.map((item) => {
      const li = node('li'); const button = node('button', `${item.question} · ${item.state}`); button.type = 'button';
      button.addEventListener('click', () => select(item.study_id)); li.append(button); return li;
    }));
    if (!payload.studies.length) query('[data-study-history]').append(node('li', '尚无本地实验；先选择快照并固定章程。'));
  }
  async function loadSnapshots(preferred) {
    snapshots = (await request('/api/research/snapshots')).snapshots;
    const select = query('[name="snapshot_ref"]'); const old = preferred ?? select.value;
    const placeholder = node('option', '请选择已冻结数据'); placeholder.value = '';
    select.replaceChildren(placeholder, ...snapshots.map((item) => {
      const option = node('option', `${item.mode} · ${item.instruments.join(', ')} · ${item.record_count} 行 · ${item.snapshot_ref.slice(0, 12)}`); option.value = item.snapshot_ref; return option;
    }));
    select.value = snapshots.some((item) => item.snapshot_ref === old) ? old : snapshots.length === 1 ? snapshots[0].snapshot_ref : '';
    const snapshot = snapshots.find((item) => item.snapshot_ref === select.value);
    if (snapshot) { query('[name="as_of"]').value = snapshot.as_of.slice(0, 10); query('[name="instrument"]').value = snapshot.instruments[0]; }
  }
  function recipeNote() {
    const item = recipes.find((entry) => entry.recipe_id === query('[name="recipe_id"]').value);
    text('[data-study-recipe-note]', item ? `${item.title} · ${item.formula} · 字段 ${item.required_fields.join(', ')} · 单位 ${item.units} · 实现 ${item.implementation_license} · ${item.effect_status}${item.windowed ? '' : ' · 无窗口参数，仅执行一个候选'}` : '');
  }
  function render(study) {
    current = normalizeFactorStudy(study); root.dataset.studyState = study.state;
    root.setAttribute('aria-busy', 'false'); query('[data-study-status]').setAttribute('role', 'status');
    text('[data-study-status]', `${study.state} · 章程 ${study.study_id} · PAPER-ONLY`);
    const spec = study.spec; const result = study.test_result ?? study.validation;
    text('[data-study-binding]', `${spec.question} · ${spec.instrument} · 配方 ${spec.recipe_id} / 窗口 ${spec.windows.join(', ')} / 折数 ${spec.n_splits} / 标签跨度 ${spec.label_horizon} / 间隔 ${spec.embargo} · 快照 ${spec.snapshot_ref} · 费用 ${spec.cost.fee_bps} bps / 滑点 ${spec.cost.slippage_bps} bps · 观察期口径`);
    const stages = ['问题与章程已固定', '受控候选生成', '扩展式验证 / purge / embargo', '依据验证证据冻结', '冻结后一次测试', '验证排名与测试结果分列', '显式研究审核'];
    query('[data-study-stages]').replaceChildren(...stages.map((value) => node('li', value)));
    const body = query('[data-study-candidates] tbody');
    body.replaceChildren(...(result?.experiments ?? []).map((item) => {
      const row = node('tr');
      const values = [`${item.experiment_id} / ${item.parameters.window}`, item.failure_reason ?? item.evaluation.status, numeric(item.ic), item.evaluation.sample_size, numeric(item.adjusted_p_value)];
      for (const value of values) row.append(node('td', value));
      const cell = node('td'); const button = node('button', study.candidate_id === item.experiment_id ? '已冻结' : '冻结候选'); button.type = 'button';
      button.disabled = !['VALIDATED', 'FROZEN'].includes(study.state) || item.failure_reason != null || study.state === 'FROZEN';
      button.addEventListener('click', () => action(async () => {
        if (!query('[data-freeze-ack]').checked) { query('[data-freeze-ack]').focus(); throw new Error('请先确认冻结只依据验证证据。'); }
        return post(`/api/research/experiments/${study.study_id}/freeze`, { candidate_id: item.experiment_id });
      })); cell.append(button); row.append(cell); return row;
    }));
    if (!result?.experiments?.length) { const row = node('tr'); const cell = node('td', ACTIVE.has(study.state) ? '后台计算中；失败实验也将保留。' : '尚无实验结果。'); cell.colSpan = 6; row.append(cell); body.append(row); }
    const groups = (result?.ranking_groups ?? []).map((group) => node('li', `可比分组 ${group.group_id.slice(0, 12)} · 验证顺序 ${group.ranking.join(' → ')}；仅同数据、成本、样本与方法条件可比。`));
    for (const surface of result?.surfaces ?? []) groups.push(node('li', `平台条件 |IC| ≥ ${surface.criteria.min_metric}、邻接差 ≤ ${surface.criteria.max_neighbor_delta}、至少 ${surface.criteria.min_region_size} 点；平台 ${surface.robustness_regions.map((region) => JSON.stringify(region.parameter_bounds)).join('；') || '未形成'}。不代表统计显著或盈利。`));
    query('[data-study-groups]').replaceChildren(...groups);
    const multiple = result?.multiple_testing;
    text('[data-study-multiplicity]', multiple ? `多重检验 ${multiple.method} · 本章程 ${multiple.family_size} 次尝试，失败 ${multiple.failed_trials} 次。未知 p 不按零处理；独立、符号对称区块假设尚未得到证明。` : '验证尚未完成；测试标签保持隔离。');
    text('[data-study-test-result]', result?.test_evaluation ? `冻结候选 ${study.candidate_id} · OOS ${result.test_evaluation.oos_status} · 测试 IC ${numeric(result.test_evaluation.information_coefficient)} · 样本 ${result.test_evaluation.sample_size} · 审核 ${study.review_decision ?? '待决定'}。PASS 不代表盈利。` : '最终测试尚未执行；验证报告不展示测试期价格与绩效。');
    query('[data-study-test]').disabled = study.state !== 'FROZEN';
    query('[data-study-review]').disabled = study.state !== 'TEST_EVALUATED';
    const active = [study.test_job, study.validation_job].find((job) => job && ['queued', 'running', 'retryable'].includes(job.status));
    query('[data-study-cancel]').disabled = !active;
    const report = study.test_job?.report_url ?? study.validation_job?.report_url;
    const link = query('[data-study-report]'); link.hidden = !report;
    if (report && /^\/research\/[A-Za-z0-9_-]+\/report\/complete$/.test(report)) link.setAttribute('href', report); else link.removeAttribute('href');
    const failed = [study.test_job, study.validation_job].find((job) => job?.failure_kind);
    if (failed) text('[data-study-status]', `${study.state} · ${failed.failure_kind} · 失败记录已保留；测试占用不会自动清除。`);
  }
  async function select(id) {
    stop(); const expected = generation; controller = new AbortController(); let polls = 0;
    async function poll() {
      try {
        const study = normalizeFactorStudy(await request(`/api/research/experiments/${encodeURIComponent(id)}`, { signal: controller.signal }));
        if (expected !== generation) return;
        if (study.study_id !== id) throw new Error('读取到了另一项实验。');
        render(study); polls++;
        if (ACTIVE.has(study.state) && polls < 300) timer = setTimeout(poll, interval);
      } catch (reason) { if (expected === generation && reason.name !== 'AbortError') error(reason); }
    }
    return poll();
  }
  async function action(operation) {
    const expected = generation; root.setAttribute('aria-busy', 'true');
    try { const study = await operation(); if (expected === generation && study) await select(study.study_id); await history(); }
    catch (reason) { if (expected === generation) error(reason); }
    finally { if (expected === generation) root.setAttribute('aria-busy', 'false'); }
  }
  query('[data-study-form]').addEventListener('submit', (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget));
    values.windows = values.windows.split(',').map((value) => Number(value.trim()));
    for (const key of ['n_splits', 'label_horizon', 'embargo', 'fee_bps', 'slippage_bps', 'min_abs_ic', 'max_neighbor_delta']) values[key] = Number(values[key]);
    const signature = JSON.stringify(values);
    if (createKey?.signature !== signature) createKey = { signature, key: crypto.randomUUID() };
    action(() => post('/api/research/experiments', { ...values, idempotency_key: createKey.key }));
  });
  query('[data-study-sample]').addEventListener('click', async () => {
    try { const sample = await post('/api/research/experiments/sample', { instrument: query('[name="instrument"]').value }); await loadSnapshots(sample.snapshot_ref); text('[data-study-status]', 'SAMPLE 快照已生成；请固定章程后提交。'); }
    catch (reason) { error(reason); }
  });
  query('[name="snapshot_ref"]').addEventListener('change', () => { const snapshot = snapshots.find((item) => item.snapshot_ref === query('[name="snapshot_ref"]').value); if (snapshot) { query('[name="as_of"]').value = snapshot.as_of.slice(0, 10); query('[name="instrument"]').value = snapshot.instruments[0]; } });
  query('[name="recipe_id"]').addEventListener('change', recipeNote);
  query('[data-study-test]').addEventListener('click', () => { if (current) action(() => post(`/api/research/experiments/${current.study_id}/test`, { idempotency_key: current.test_key ?? crypto.randomUUID() })); });
  query('[data-study-review]').addEventListener('click', () => { if (current) action(() => post(`/api/research/experiments/${current.study_id}/review`, { decision: query('[name="review_decision"]').value, reviewer_id: query('[name="reviewer_id"]').value })); });
  query('[data-study-cancel]').addEventListener('click', () => action(async () => {
    const job = [current?.test_job, current?.validation_job].find((item) => item && ['queued', 'running', 'retryable'].includes(item.status));
    if (job) await post(`/api/research/jobs/${job.job_id}/cancel`, {});
    return current ? request(`/api/research/experiments/${current.study_id}`) : null;
  }));
  const ready = Promise.all([loadSnapshots(), history(), request('/api/research/experiments/catalog').then((payload) => {
    recipes = payload.recipes; query('[name="recipe_id"]').replaceChildren(...recipes.map((item) => { const option = node('option', item.title); option.value = item.recipe_id; return option; })); recipeNote();
  })]).then(() => { text('[data-study-status]', '本地目录已就绪。请选择快照，或显式生成示例。'); }).catch(error);
  const api = { ready, select, dispose: stop }; mounted.set(root, api);
  document.defaultView?.addEventListener('pagehide', stop, { once: true });
  return api;
}

import { readLocalJSON, userErrorMessage } from './local-request.js';
import { renderStrategyWindows } from './strategy-learning-evidence.js';

const BASE = '/api/research/learning/strategy';
const mounted = new WeakMap();
const stateLabels = {
  SETTLED: '已结算', INSUFFICIENT_SAMPLES: '成熟样本不足', DUPLICATE_SOURCE: '重复经济样本',
  UNSETTLED: '交易未结算', UNSETTLED_TRADES: '交易未结算', WINDOW_NOT_MATURE: '观察窗未到期',
  SOURCE_NOT_YET_AVAILABLE: '报告尚不可用', CONFLICTING_ECONOMIC_SAMPLE: '同一经济样本的结果冲突',
  EVIDENCE_CONFLICT: '证据冲突', POLICY_NOT_EFFECTIVE: '政策尚未生效', BENCHMARK_UNAVAILABLE: '缺少可比基准',
  RISK_SUPPRESSED: '风险规则限制成交', DECISION_NEEDS_DATA: '决策缺少数据', RESEARCH_ONLY: '理论研究',
  MISSING_PRICE: '缺少价格', UNKNOWN_TRADEABILITY: '可交易状态未知', LEDGER_MARK_UNAVAILABLE: '账本估值缺失',
  DECISION_UNVERIFIED: '原决策未通过验证',
  LATE_PRICE: '价格迟到', SUSPENDED: '停牌', NO_FILLS: '无成交', INVALID_WINDOW: '窗口不可用',
  MISSING_BENCHMARK: '缺少可比基准', INVALID_BENCHMARK: '基准不可用', NEEDS_DATA: '需要数据',
  READY: '达到试验门槛', KEEP_POLICY: '保留原政策', PENDING_REVIEW: '待明确审核',
  APPROVED: '已批准', REJECTED: '已拒绝', AVAILABLE: '两侧已复算', INCOMPARABLE: '两侧指标不可比',
  EQUITY: '股票', ETF: 'ETF', MIXED: '股票与 ETF', COMPLETE: '完成',
  SAMPLE: '合成样例', CAPTURED: '采集数据', LIVE: '实时快照', PAPER: '纸面数据',
};
const metricLabels = {
  final_equity: '期末权益', net_return: '费用后收益', benchmark_return: '基准收益',
  excess_return: '超额收益', fees: '交易费用', slippage: '滑点成本', fill_count: '成交笔数',
};
const label = value => stateLabels[value] || value || '—';
const fmt = (value, percent = false) => Number.isFinite(value)
  ? (percent ? (value * 100).toFixed(2) + '%' : new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 4 }).format(value)) : '—';
const shortDate = value => value ? value.slice(0, 10) : '—';
const byId = value => encodeURIComponent(value);

export function mountStrategyLearning(root, { fetcher = globalThis.fetch } = {}) {
  if (mounted.has(root)) return mounted.get(root);
  const doc = root.ownerDocument;
  const q = key => root.querySelector('[data-sl-' + key + ']');
  const observe = q('observe'), history = q('history'), review = q('decision'), apply = q('apply');
  const select = observe.elements.run_id, asOf = observe.elements.as_of;
  const csrf = q('csrf').value;
  let revision = 0;
  const stale = Symbol('selection changed');
  async function request(path, options) {
    const owner = revision;
    const value = await readLocalJSON(fetcher, BASE + path, options);
    if (disposed || owner !== revision) throw stale;
    return value;
  }
  const get = path => request(path);
  const post = (path, data) => request(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...data, _csrf: csrf }),
  });
  let sources = [], observations = [], audits = [];
  let source = null, observation = null, audit = null, proposal = null, pair = null;
  let busy = false, disposed = false;
  const node = (tag, text) => { const el = doc.createElement(tag); el.textContent = text; return el; };
  function status(message, error = false) {
    q('status').textContent = message;
    q('status').setAttribute('role', error ? 'alert' : 'status');
  }
  function options(target, prompt, entries, selected = '') {
    const option = (value, title) => { const item = node('option', title); item.value = value; return item; };
    target.replaceChildren(option('', prompt), ...entries.map(([id, title]) => option(id, title)));
    target.value = entries.some(([id]) => id === selected) ? selected : '';
  }
  function rows(target, data) {
    target.replaceChildren(...data.map(values => {
      const row = doc.createElement('tr');
      row.append(...values.map(value => node('td', value)));
      return row;
    }));
  }
  function clearDetails() {
    observation = audit = proposal = pair = null;
    review.reset();
    apply.elements.run_id.replaceChildren();
    for (const key of ['applied', 'admission', 'proposal-text', 'audit-text', 'metrics', 'fingerprints', 'pair-text']) q(key).replaceChildren();
    for (const key of ['run-review', 'baseline', 'candidate']) q(key).removeAttribute('href');
    q('receipt-binding').textContent = '';
    q('raw-receipt').removeAttribute('href');
    q('window-rows').replaceChildren();
    delete q('window-rows').dataset.receipt;
  }
  function resetSelection() {
    revision++;
    busy = false;
    clearDetails();
    paint();
  }
  function selectionMatches() {
    return !!source && observation?.run_id === source.run_id && select.value === source.run_id && shortDate(observation.as_of) === asOf.value;
  }
  function boundReceipt() {
    if (selectionMatches()) return true;
    resetSelection();
    status('来源或截止日已更改，请重新观察，或选择已冻结的收据。');
    return false;
  }
  function controls() {
    root.setAttribute('aria-busy', String(busy));
    for (const input of root.querySelectorAll('button,select,textarea,input:not([type="hidden"])')) input.disabled = busy;
    select.disabled = asOf.disabled = history.disabled = false;
    observe.querySelector('button').disabled = busy || !source;
    q('audit').disabled = busy || !selectionMatches();
    q('propose').disabled = busy || !selectionMatches() || audit?.status !== 'READY' || !!proposal;
    for (const field of review.elements) field.disabled = busy || !selectionMatches() || proposal?.status !== 'PENDING_REVIEW';
    q('pair').disabled = busy || !selectionMatches() || proposal?.status !== 'APPROVED';
    apply.querySelector('button').disabled = busy || !selectionMatches() || !apply.elements.run_id.value;
  }
  function paint() {
    if (disposed) return;
    history.value = observation?.observation_ref || '';
    const detail = {
      receipt: observation, 'audit-detail': audit, proposal, comparison: pair,
      pair: proposal?.status === 'APPROVED', apply: proposal?.status === 'APPROVED',
    };
    for (const [key, visible] of Object.entries(detail)) q(key).hidden = !visible;
    q('original').hidden = !source;
    if (source) q('original').href = '/research/' + byId(source.run_id) + '/report/complete';
    if (observation) {
      const reasons = observation.reasons.map(label).join('、');
      q('receipt-text').textContent = [label(observation.status), label(observation.data_mode), shortDate(observation.as_of),
        '可用窗口 ' + observation.sample_count, '本次新增 ' + observation.new_sample_count,
        '已登记 ' + observation.duplicate_count, reasons].filter(Boolean).join(' · ');
      q('receipt-binding').textContent = '冻结运行 ' + observation.run_id + ' · 请求截止 ' + observation.as_of
        + ' · 实际证据截止 ' + observation.cutoff_at + ' · 收据 ' + observation.observation_ref;
      q('raw-receipt').href = BASE + '/observations/' + byId(observation.observation_ref);
      const body = q('window-rows');
      if (body.dataset.receipt !== observation.observation_ref) {
        renderStrategyWindows(body, observation, { label, fmt,
          readFills: ref => get('/observations/' + byId(ref) + '/fills'), errorMessage: userErrorMessage });
        body.dataset.receipt = observation.observation_ref;
      }
    }
    if (audit) {
      q('audit-text').textContent = [label(audit.status), audit.scope.template_id,
        audit.scope.strategy_assets.join('、'), audit.scope.market_scope, label(audit.scope.data_mode), '有效窗口 ' + audit.sample_count,
        '落后基准 ' + audit.below_benchmark_count + ' 个', '平均超额收益 ' + fmt(audit.mean_excess_return, true)].join(' · ');
    }
    if (proposal) {
      q('proposal-text').textContent = '纸面信号预算从 ' + fmt(proposal.before, true) + ' 调至 ' +
        fmt(proposal.proposed, true) + '。适用模板 ' + proposal.scope.template_id + '，资产 ' + proposal.scope.instruments.join('、') + '。';
      const entries = [['原政策', proposal.parent_policy_ref], ['原执行配置', proposal.baseline_runtime_fingerprint],
        ['政策范围', proposal.scope_fingerprint], ['审核预览', proposal.preview_fingerprint],
        ['原账本', audit.frozen.original_ledger_digest], ['冻结面板', audit.frozen.panel_ref]];
      if (proposal.policy) entries.push(['批准政策', proposal.policy.policy_ref], ['新执行配置', proposal.policy.runtime_fingerprint]);
      q('fingerprints').replaceChildren(...entries.flatMap(([name, value]) => [node('dt', name), node('dd', value)]));
      review.hidden = proposal.status !== 'PENDING_REVIEW';
      const effective = proposal.policy?.effective_at;
      q('admission').textContent = [label(proposal.status), proposal.admission?.rationale,
        effective && '生效时间 ' + effective].filter(Boolean).join(' · ');
      if (effective) {
        const next = sources.filter(row => row.template_id === proposal.scope.template_id && row.run_id !== observation.run_id
          && row.policy_ref === proposal.parent_policy_ref && row.as_of.slice(0, 10) >= effective.slice(0, 10));
        options(apply.elements.run_id, next.length ? '请选择待核验的后续报告' : '尚无符合初筛日期和模板的报告',
          next.map(row => [row.run_id, shortDate(row.as_of) + ' · ' + row.run_id]), apply.elements.run_id.value);
      }
    }
    if (pair) {
      q('pair-text').textContent = [label(pair.status), '同一冻结面板和模板重算',
        pair.fills_changed ? '两侧成交记录有差异' : '两侧成交记录相同', '学习期内回溯对照'].join(' · ');
      rows(q('metrics'), pair.metrics.map(row => [metricLabels[row.metric] || row.metric,
        ...[row.baseline, row.candidate, row.delta].map(value => fmt(value, row.metric.endsWith('_return')))]));
      q('run-review').href = '/research/atlas/runs?pair=' + byId(pair.pair_id);
      for (const side of ['baseline', 'candidate']) q(side).href = BASE + '/pairs/' + byId(pair.pair_id) + '/' + side;
    }
    controls();
    root.dispatchEvent(new CustomEvent('finathink:strategy-learning', { bubbles: true, detail: { source, observation, audit, proposal, pair } }));
  }
  async function action(fn) {
    if (busy || disposed) return;
    const owner = revision;
    busy = true;
    controls();
    try { await fn(); }
    catch (error) { if (!disposed && owner === revision && error !== stale) status(userErrorMessage(error), true); }
    finally { if (!disposed && owner === revision) { busy = false; controls(); } }
  }
  function selectSource(runId) {
    clearDetails();
    select.value = runId || '';
    source = sources.find(row => row.run_id === select.value) || null;
    asOf.value = source ? shortDate(source.observation_date || source.as_of) : '';
    options(history, '请选择观察记录', observations.filter(row => !source || row.run_id === source.run_id).map(row =>
      [row.observation_ref, shortDate(row.as_of) + ' · ' + row.template_id + ' · ' + label(row.status)]));
    paint();
  }
  async function readObservation(ref) {
    clearDetails();
    if (ref) {
      const saved = await get('/observations/' + byId(ref));
      if (source?.run_id !== saved.run_id) selectSource(sources.find(row => row.run_id === saved.run_id)?.run_id || '');
      observation = saved;
      asOf.value = shortDate(saved.as_of);
      const linked = audits.find(row => row.observation_ref === ref);
      if (linked) {
        audit = await get('/audits/' + byId(linked.audit_id));
        if (audit.proposal_id) {
          proposal = await get('/proposals/' + byId(audit.proposal_id));
          if (proposal.pair_id) pair = await get('/pairs/' + byId(proposal.pair_id));
        }
      }
    }
    paint();
  }
  async function loadCatalog(preferRun, preferRef, { restoreLatest = false, draftCutoff = '' } = {}) {
    const [rs, os, as] = await Promise.all([get('/sources'), get('/observations'), get('/audits')]);
    sources = rs.sources; observations = os.observations; audits = as.audits;
    const preferred = observations.find(row => row.observation_ref === preferRef);
    const runId = preferred?.run_id || preferRun || observations[0]?.run_id || sources[0]?.run_id;
    options(select, '请选择策略报告', sources.map(row => [row.run_id,
      [row.template_id, label(row.asset_scope), shortDate(row.as_of), row.run_id.slice(-8)].join(' · ')]));
    selectSource(runId);
    const matching = preferred || (restoreLatest && observations.find(row => row.run_id === runId));
    if (matching) await readObservation(matching.observation_ref);
    else if (draftCutoff) asOf.value = draftCutoff;
    status(source ? '已读取策略账本和审核记录。' : '尚无已发布的策略报告，请先运行策略研究。');
  }
  function changeSource() {
    if (select.value === (source?.run_id || '')) return;
    const chosen = select.value;
    resetSelection();
    selectSource(chosen);
    status('已切换策略来源，请观察账本或选择该运行的冻结收据。');
  }
  select.addEventListener('input', changeSource);
  select.addEventListener('change', changeSource);
  function changeCutoff() {
    resetSelection();
    status('截止日已更改，请重新观察账本。');
  }
  asOf.addEventListener('input', changeCutoff);
  asOf.addEventListener('change', changeCutoff);
  history.addEventListener('change', () => {
    const ref = history.value;
    resetSelection();
    action(async () => {
      await readObservation(ref);
      status(ref ? (source ? '已读取所选报告的冻结观察。' : '原报告暂不可用，可查阅已冻结的观察和审核。') : '请选择已保存的观察。');
    });
  });
  q('refresh').addEventListener('click', () => action(() => loadCatalog(source?.run_id, observation?.observation_ref,
    { draftCutoff: asOf.value })));
  observe.addEventListener('submit', event => {
    event.preventDefault();
    action(async () => {
      const runId = select.value, cutoff = asOf.value;
      const result = await post('/observations', { run_id: runId, as_of: cutoff });
      if (select.value !== runId || asOf.value !== cutoff) { resetSelection(); return; }
      await loadCatalog(runId, result.observation_ref);
      status('观察已保存。可逐窗核对价格、成交和基准，再审核样本。');
    });
  });
  q('audit').addEventListener('click', () => action(async () => {
    if (!boundReceipt()) return;
    audit = await post('/audits', { observation_ref: observation.observation_ref });
    audits = [...audits.filter(row => row.audit_id !== audit.audit_id), audit];
    if (audit.proposal_id) proposal = await get('/proposals/' + byId(audit.proposal_id));
    paint(); status(label(audit.status) + '。样本审核已保存。');
  }));
  q('propose').addEventListener('click', () => action(async () => {
    if (!boundReceipt() || !audit) return;
    proposal = await post('/audits/' + byId(audit.audit_id) + '/propose', {});
    paint(); status('纸面政策提案已保存，请核对调整值和适用范围。');
  }));
  review.addEventListener('submit', event => {
    event.preventDefault();
    const decision = event.submitter?.value;
    if (!['APPROVED', 'REJECTED'].includes(decision)) return;
    action(async () => {
      if (!boundReceipt() || proposal?.status !== 'PENDING_REVIEW') return;
      const data = { decision, rationale: review.elements.rationale.value,
        expected_preview_fingerprint: proposal.preview_fingerprint };
      if (decision === 'APPROVED' && review.elements.effective_on.value) data.effective_on = review.elements.effective_on.value;
      proposal = await post('/proposals/' + byId(proposal.proposal_id) + '/decision', data);
      paint(); status('已保存明确审核决定。');
    });
  });
  q('pair').addEventListener('click', () => action(async () => {
    if (!boundReceipt() || proposal?.status !== 'APPROVED') return;
    status('正在用原策略面板重算两侧的目标权重、成交和账本…');
    pair = await post('/proposals/' + byId(proposal.proposal_id) + '/pair', {});
    paint(); status(label(pair.status) + '。');
  }));
  apply.elements.run_id.addEventListener('change', controls);
  apply.addEventListener('submit', event => {
    event.preventDefault();
    action(async () => {
      if (!boundReceipt() || proposal?.status !== 'APPROVED') return;
      const result = await post('/proposals/' + byId(proposal.proposal_id) + '/apply', { run_id: apply.elements.run_id.value });
      const link = node('a', '读取后续策略的执行证据');
      link.href = BASE + '/applications/' + byId(result.application_id) + '/evidence';
      q('applied').replaceChildren(link);
      status('后续策略运行' + label(result.status) + '。');
    });
  });
  const params = new URLSearchParams(doc.defaultView?.location.search || '');
  const ready = action(() => loadCatalog(params.get('strategy_run') || params.get('run_id'), params.get('observation_ref'), { restoreLatest: true }));
  const api = { ready, dispose() { disposed = true; } };
  mounted.set(root, api);
  doc.defaultView?.addEventListener('pagehide', api.dispose, { once: true });
  return api;
}

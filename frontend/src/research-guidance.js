// Show server-owned readiness and evidence; never infer financial results.
const REPAIRS = new Set(['prepare-data', 'research-instrument', 'research-cutoff', 'data-units', 'research-question', 'research-model', 'research-budget', 'research-materials']);
const REPAIR_PAGES = new Set(['/settings/data-connections', '/settings/providers', '/library/methods', '/research/learning']);
const allowedRepair = href => typeof href === 'string' && (REPAIR_PAGES.has(href) || (href.startsWith('/research/jobs#') && REPAIRS.has(href.split('#')[1])));
export function normalizeReadiness(value) {
  if (!value || value.schema_version !== 'research-readiness.v1' || value.paper_only !== true || !/^[a-f0-9]{64}$/.test(value.input_digest ?? '') || !['READY', 'BLOCKED', 'NEEDS_REVIEW'].includes(value.status) || typeof value.can_submit !== 'boolean') throw new TypeError('Invalid readiness');
  if (!Array.isArray(value.checks) || !value.checks.length || value.checks.length > 32 || value.checks.some(item => !['PASS', 'REVIEW', 'BLOCKED'].includes(item.status) || !allowedRepair(item.action?.href))) throw new TypeError('Invalid readiness checks');
  const blocked = value.checks.some(item => item.status === 'BLOCKED');
  const status = blocked ? 'BLOCKED' : value.checks.some(item => item.status === 'REVIEW') ? 'NEEDS_REVIEW' : 'READY';
  if (value.can_submit !== !blocked || status !== value.status) throw new TypeError('Contradictory readiness');
  return value;
}

export function createReadinessCheck({ request, onChange = () => {} }) {
  let generation = 0;
  return {
    invalidate() { generation++; onChange('STALE'); },
    async check(values) {
      const current = ++generation;
      onChange('CHECKING');
      try {
        const result = normalizeReadiness(await request(values));
        if (current !== generation) return null;
        onChange(result.status, result);
        return result;
      } catch (error) {
        if (current !== generation) return null;
        onChange('ERROR');
        throw error;
      }
    },
  };
}

const node = (root, tag, text) => { const item = root.ownerDocument.createElement(tag); if (text != null) item.textContent = text; return item; };
export function renderReadiness(root, status, result) {
  const panel = root.querySelector('[data-readiness-panel]');
  if (!panel) return;
  panel.dataset.readinessState = status;
  panel.setAttribute('aria-busy', String(status === 'CHECKING'));
  const summary = root.querySelector('[data-readiness-summary]');
  const labels = { STALE: '输入已改变，请重新检查。提交时也会核对当前输入。', CHECKING: '正在检查本地输入，不获取数据或调用模型…', ERROR: '检查未完成，请重试。原输入已保留。', BLOCKED: '请先修复阻断项，再开始研究。', NEEDS_REVIEW: '可以开始纸面研究；以下待核对项会限制解释范围。', READY: '已满足本轮输入检查，可以开始纸面研究。' };
  if (summary) summary.textContent = labels[status] + (result ? ` ${result.data_mode} · ${result.coverage.records} 条 · ${result.coverage.start?.slice(0, 10) ?? '—'} 至 ${result.coverage.end?.slice(0, 10) ?? '—'}` : '');
  const list = root.querySelector('[data-readiness-results]');
  const renderItem = item => {
    const li = node(root, 'li'); li.dataset.checkStatus = item.status;
    li.append(node(root, 'strong', `${{ PASS: '已核对', REVIEW: '待核对', BLOCKED: '需修复' }[item.status]} · ${item.title}`), node(root, 'p', item.message));
    if (item.status !== 'PASS') {
      const link = node(root, 'a', item.action.label); link.href = item.action.href;
      if (item.action.href.startsWith('/research/jobs#')) link.addEventListener('click', event => {
        const target = root.querySelector(`#${item.action.href.split('#')[1]}`);
        if (!target) return;
        event.preventDefault();
        let parent = target.parentElement;
        while (parent && parent !== root) { if (parent.tagName === 'DETAILS') parent.open = true; parent = parent.parentElement; }
        target.scrollIntoView({ block: 'center' }); target.focus({ preventScroll: true });
      });
      else { link.target = '_blank'; link.rel = 'noopener'; link.append('（新标签页）'); }
      li.append(link);
    }
    return li;
  };
  if (list) list.replaceChildren(...(result?.checks ?? []).filter(item => item.status !== 'PASS').map(renderItem));
  const passed = (result?.checks ?? []).filter(item => item.status === 'PASS');
  root.querySelector('[data-readiness-passed]')?.replaceChildren(...passed.map(renderItem));
  const count = root.querySelector('[data-readiness-passed-count]');
  if (count) count.textContent = `查看已核对的 ${passed.length} 项`;
  const passedDetails = root.querySelector('[data-readiness-passed-details]');
  if (passedDetails) passedDetails.hidden = passed.length === 0;
  const step = root.querySelector('[data-guide-step="check"]');
  if (step) step.textContent = `02 核对输入 · ${result?.can_submit ? '已检查' : status === 'BLOCKED' ? '需修复' : '待检查'}`;
}

const renderedExplanations = new WeakMap();
export function renderExplanation(root, job) {
  const panel = root.querySelector('[data-run-explanation]');
  if (!panel) return;
  const signature = JSON.stringify([job.run_id, job.report_status, job.explanation]);
  const previous = renderedExplanations.get(panel);
  if (previous?.signature === signature && panel.hasChildNodes()) return;
  const sameRun = previous?.run === job.run_id;
  const expanded = sameRun && panel.querySelector('details')?.open === true;
  const active = root.ownerDocument.activeElement;
  const focusedSummary = sameRun && active === panel.querySelector('summary');
  const focusedLink = sameRun && panel.contains(active) && active?.tagName === 'A' ? active.getAttribute('href') : null;
  panel.replaceChildren();
  const value = job.explanation;
  if (!value || value.schema_version !== 'research-explanation.v1' || value.run_id !== job.run_id) { panel.append(node(root, 'p', '本轮解释尚未提供。')); return; }
  panel.append(node(root, 'h3', '结论'), node(root, 'p', `${value.headline}。${value.conclusion}`));
  panel.append(node(root, 'h3', '依据'));
  const evidence = node(root, 'ul');
  for (const item of value.evidence ?? []) {
    // Only link to this run's published document and known evidence sections.
    const prefix = `/research/${job.run_id}/report/complete#`;
    if (job.report_status !== 'AVAILABLE' || typeof item.href !== 'string' || !item.href.startsWith(prefix) || !['source', 'quant-metrics', 'risk-portfolio', 'model-research', 'identity', 'factor-study'].includes(item.href.slice(prefix.length))) continue;
    const li = node(root, 'li'); const link = node(root, 'a', item.label); link.href = item.href; li.append(link);
    if (Object.hasOwn(item, 'value')) li.append(node(root, 'span', `：${item.value == null ? '未提供' : item.value}`));
    evidence.append(li);
  }
  if (!evidence.children.length) evidence.append(node(root, 'li', '尚无已发布证据。'));
  panel.append(evidence);
  for (const [title, items] of [['分歧', [value.disagreement_note, ...(value.disagreements ?? [])]], ['适用条件', value.conditions], ['下一步', value.next_steps]]) {
    panel.append(node(root, 'h3', title)); const list = node(root, 'ul');
    list.append(...(items ?? []).map(item => node(root, 'li', item))); panel.append(list);
  }
  const details = node(root, 'details'); details.append(node(root, 'summary', '为什么这样运行：阶段输入与结果'));
  const timeline = node(root, 'ol'); timeline.className = 'research-timeline';
  for (const stage of value.timeline ?? []) {
    const li = node(root, 'li');
    li.append(node(root, 'strong', stage.title), node(root, 'p', `输入：${stage.inputs}；用途：${stage.purpose}。${stage.fact}${stage.blocker ? ` ${stage.blocker}` : ''}`));
    timeline.append(li);
  }
  details.append(timeline); panel.append(details);
  if (value.retry_at) panel.append(node(root, 'p', `最早重试时间：${value.retry_at}；实际运行取决于后台调度。`));
  details.open = expanded;
  renderedExplanations.set(panel, { signature, run: job.run_id });
  if (focusedSummary) details.querySelector('summary').focus({ preventScroll: true });
  else if (focusedLink) [...panel.querySelectorAll('a')].find(link => link.getAttribute('href') === focusedLink)?.focus({ preventScroll: true });
}

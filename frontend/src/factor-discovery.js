import { readLocalJSON, userErrorMessage } from './local-request.js';

const ENDPOINT = '/api/research/factor-suites/discovery';

function renderPlan(target, plan) {
  const doc = target.ownerDocument;
  function element(tag, text, parent) {
    const node = doc.createElement(tag);
    node.textContent = text;
    parent.append(node);
    return node;
  }
  target.replaceChildren();
  if (!plan.candidates) {
    element('h3', '可试用的研究问题', target);
    const examples = element('ul', '', target);
    plan.examples.forEach(question => element('li', question, examples));
    return;
  }
  const cost = plan.evaluation.validation_policy;
  element('h3', plan.question, target);
  element('p', plan.hypothesis + plan.falsification, target);
  element('p', `${plan.evaluation.data_mode} · ${plan.market} · ${plan.evaluation.instrument_ids.join('、')}`, target);
  element('p', `费用 ${cost.fee_bps} bps · 滑点 ${cost.slippage_bps} bps · 登记分母 ${plan.budget.family_size} · 最终测试 ${plan.budget.final_tests} 次`, target);
  element('p', `保留测试期 ${plan.evaluation.test_period.join(' 至 ')}`, target);
  for (const candidate of plan.candidates) {
    const detail = element('details', '', target);
    detail.open = Boolean(candidate.field_issues.length);
    const role = candidate.role === 'PRIMARY' ? '问题对应' : '反证对照';
    const direction = candidate.direction === 1 ? '高值优先' : '低值优先';
    element('summary', `${candidate.title} · ${role} · ${direction}`, detail);
    element('code', candidate.factor_spec.formula, element('p', '', detail));
    element('p', '因子来源 ' + candidate.source_ids.join('、'), detail);
    if (candidate.field_issues.length) {
      const fields = candidate.field_issues.map(item => `${item.field}（要求 ${item.expected_unit}，当前 ${item.declared_unit ?? '未声明'}）`);
      element('p', '需补齐字段或单位 ' + fields.join('、') + '。该候选仍会登记。', detail);
    }
    element('p', '反例 ' + candidate.formula_counterexample, detail);
    const cases = element('ul', '', detail);
    candidate.counterexamples.forEach(example => element('li', example.correction, cases));
    const link = element('a', '核对公式与来源卡', detail);
    link.href = '/research/factor-library?factor=' + encodeURIComponent(candidate.factor_id);
  }
  for (const item of plan.excluded) {
    const reason = item.reason === 'DUPLICATE_FORMULA' ? '等价公式已合并' : '超过候选上限';
    element('p', `${item.factor_id} · ${reason}`, target);
  }
}

export function mountFactorDiscovery(form) {
  const elements = form.elements;
  const previewButton = form.querySelector('[data-factor-preview]');
  const submitButton = form.querySelector('[data-factor-submit]');
  const status = form.querySelector('[data-factor-discovery-status]');
  const result = form.querySelector('[data-factor-discovery-plan]');
  const market = form.querySelector('[data-discovery-market]');
  let revision = 0;
  let reviewed;

  function inputs() {
    const values = { question: elements.discovery_question.value };
    if (elements.mode.value === 'saved') {
      if (!elements.saved_panel_ref.value) throw Error('请先选择一个已保存的面板。');
      values.saved_panel_ref = elements.saved_panel_ref.value;
      const label = elements.market.value.trim().toUpperCase();
      if (label) values.market = label;
    }
    return values;
  }
  function invalidate() {
    revision += 1;
    reviewed = null;
    submitButton.disabled = true;
    market.hidden = elements.mode.value !== 'saved';
    result.replaceChildren();
    status.textContent = '输入已改变，请重新预览。';
  }
  const post = (path, values) => readLocalJSON(fetch, path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...values, _csrf: elements.csrf.value }),
  });
  market.hidden = elements.mode.value !== 'saved';
  previewButton.disabled = false;
  form.addEventListener('input', invalidate);
  form.addEventListener('change', invalidate);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const pending = ++revision;
    reviewed = null;
    submitButton.disabled = true;
    result.replaceChildren();
    status.textContent = '正在登记预览所需的公式和反证…';
    try {
      const selected = inputs();
      const plan = await post(ENDPOINT + '/preview', selected);
      if (pending !== revision || JSON.stringify(inputs()) !== JSON.stringify(selected)) return;
      renderPlan(result, plan);
      if (plan.status === 'READY' && /^[a-f0-9]{64}$/.test(plan.fingerprint ?? '')) {
        reviewed = { selected, ref: plan.fingerprint };
        submitButton.disabled = false;
        status.textContent = '候选计划已就绪。请核对后提交后台验证。';
      } else {
        status.textContent = plan.status === 'SOURCE_REVIEW_REQUIRED' ? '来源版本需要复核。' : '请将问题缩小到一个已收录的因子主题。';
      }
    } catch (error) {
      if (pending === revision) status.textContent = userErrorMessage(error);
    }
  });
  submitButton.addEventListener('click', async () => {
    if (!reviewed) return;
    const pending = revision;
    submitButton.disabled = true;
    status.textContent = '正在登记候选并创建验证任务…';
    try {
      if (JSON.stringify(inputs()) !== JSON.stringify(reviewed.selected)) { invalidate(); return; }
      const study = await post(ENDPOINT, { ...reviewed.selected, authorized: true, preview_ref: reviewed.ref,
        idempotency_key: 'multi-factor-' + reviewed.ref });
      if (!/^study-[a-zA-Z0-9_-]+$/.test(study.study_id ?? '')) throw Error('研究回执异常，请查看已登记研究。');
      if (pending !== revision) { status.textContent = '先前的任务已提交，可从研究历史打开。'; return; }
      location.assign('/research/factor-suites?study=' + encodeURIComponent(study.study_id));
    } catch (error) {
      if (pending === revision) { status.textContent = userErrorMessage(error); submitButton.disabled = false; }
    }
  });
}

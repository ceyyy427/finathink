import { readLocalJSON, userErrorMessage } from './local-request.js';

const PAGE = '/research/workbench';
const API = '/api/research/workbench';
const NAMES = { knowledge: '知识', factor: '因子', strategy: '策略', risk: '风险', case: '反例' };
const LOCAL_PATHS = ['/knowledge/items/', '/research/factor-library?', '/research/strategies', '/research/factor-suites'];

export function latestOperation() {
  let generation = 0;
  let active;
  return {
    invalidate() {
      generation += 1;
      active?.abort();
      active = null;
    },
    next() {
      this.invalidate();
      const id = generation;
      const controller = new AbortController();
      active = controller;
      return { signal: controller.signal, current: () => generation === id };
    },
  };
}

export function safeWorkspaceHref(path) {
  return typeof path === 'string' && LOCAL_PATHS.some(prefix => path.startsWith(prefix)) &&
    !/[\\\x00-\x20]/u.test(path) ? path : null;
}

function element(doc, name, text = '') {
  const node = doc.createElement(name);
  node.textContent = text;
  return node;
}

function link(doc, text, href) {
  const node = element(doc, 'a', text);
  node.setAttribute('href', href);
  return node;
}

function renderDetail(doc, target, row) {
  if (!row) {
    target.replaceChildren(element(doc, 'p', '选择一个对象，查看输入、反例与出处。'));
    return;
  }
  const info = [element(doc, 'h3', row.title), element(doc, 'p', row.summary),
    element(doc, 'p', `版本 ${row.version} · 验证 ${row.evidence.validation} · 用途待审核`)];
  const href = safeWorkspaceHref(row.href);
  if (href) info.push(link(doc, '打开原始详情', href));
  const groups = [
    ['所需字段与适用条件', [...(row.details.fields || []), ...(row.details.conditions || [])]],
    ['反例与边界', row.details.counterexamples || []],
    ['来源与定义摘要', [...row.evidence.source_refs.map(source =>
      [source.title || source.source_id, source.commit?.slice(0, 12)].filter(Boolean).join(' · ')),
    row.evidence.definition_digest]],
  ];
  for (const [label, rows] of groups) {
    const details = element(doc, 'details');
    const list = element(doc, 'ul');
    rows.forEach(item => list.append(element(doc, 'li', item)));
    details.append(element(doc, 'summary', label), list);
    info.push(details);
  }
  target.replaceChildren(...info);
}

function renderCatalogue(root, data, params) {
  if (!Array.isArray(data.items) || !Number.isInteger(data.total)) throw new TypeError('invalid catalogue');
  const doc = root.ownerDocument;
  const status = root.querySelector('[data-qw-status]');
  const results = root.querySelector('[data-qw-results]');
  renderDetail(doc, root.querySelector('[data-qw-detail]'), data.selected);
  const cards = data.items.map(row => {
    if (!Object.hasOwn(NAMES, row.kind) || !/^[a-z0-9-]+:[a-z0-9-]+$/u.test(row.object_id)) throw new TypeError('invalid item');
    const values = new URLSearchParams(params);
    values.set('selected', row.object_id);
    const anchor = link(doc, row.title, PAGE + '?' + values);
    anchor.dataset.qwSelect = row.object_id;
    const card = element(doc, 'article');
    const title = element(doc, 'h3');
    title.append(anchor);
    card.append(element(doc, 'span', NAMES[row.kind] + ' · ' + row.version), title, element(doc, 'p', row.summary));
    return card;
  });
  results.replaceChildren(...cards);
  root.querySelector('[data-qw-empty]').hidden = cards.length !== 0;
  const pagination = root.querySelector('[data-qw-pagination]');
  pagination.replaceChildren();
  for (const [label, offset] of [['上一页', data.offset - data.page_size], ['下一页', data.offset + data.page_size]]) {
    if (offset < 0 || offset >= data.total) continue;
    const values = new URLSearchParams(params);
    values.set('offset', String(offset));
    values.delete('selected');
    const anchor = link(doc, label, PAGE + '?' + values);
    anchor.dataset.qwPage = '';
    pagination.append(anchor);
  }
  status.textContent = `找到 ${data.total} 个对象。`;
}

function mountCatalogue(root, fetcher) {
  const form = root.querySelector('[data-qw-search]');
  const state = latestOperation();
  const status = root.querySelector('[data-qw-status]');
  const results = root.querySelector('[data-qw-results]');
  const detail = root.querySelector('[data-qw-detail]');
  async function search(params, focusDetail = false) {
    const request = state.next();
    status.textContent = '正在读取研究目录…';
    results.setAttribute('aria-busy', 'true');
    detail.replaceChildren();
    try {
      const data = await readLocalJSON(fetcher, API + '?' + params, { signal: request.signal });
      if (!request.current()) return;
      renderCatalogue(root, data, params);
      if (focusDetail) {
        detail.tabIndex = -1;
        detail.focus();
      }
    } catch (error) {
      if (request.current()) status.textContent = userErrorMessage(error) + ' 可再次检索。';
    } finally {
      if (request.current()) results.removeAttribute('aria-busy');
    }
  }
  form.addEventListener('input', () => {
    state.invalidate();
    detail.replaceChildren();
    results.removeAttribute('aria-busy');
    results.replaceChildren();
    root.querySelector('[data-qw-pagination]').replaceChildren();
    root.querySelector('[data-qw-empty]').hidden = true;
    status.textContent = '筛选条件已更改，提交后更新目录。';
  });
  form.addEventListener('submit', event => {
    event.preventDefault();
    search(new URLSearchParams(new FormData(form)));
  });
  root.addEventListener('click', event => {
    const anchor = event.target.closest('[data-qw-select],[data-qw-page]');
    if (!anchor || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    search(new URL(anchor.href).searchParams, Boolean(anchor.dataset.qwSelect));
  });
}

function post(fetcher, path, csrf, body, signal) {
  return readLocalJSON(fetcher, path, { method: 'POST', signal,
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...body, _csrf: csrf }) });
}

function mountDiscovery(root, fetcher) {
  const form = root.querySelector('[data-qw-discovery]');
  const output = root.querySelector('[data-qw-blueprint]');
  const status = root.querySelector('[data-qw-discovery-status]');
  const preview = root.querySelector('[data-qw-preview]');
  const submit = root.querySelector('[data-qw-submit]');
  const doc = root.ownerDocument;
  const state = latestOperation();
  let pending;
  preview.disabled = false;
  function clear() {
    state.invalidate();
    pending = null;
    submit.disabled = true;
    preview.disabled = false;
    output.replaceChildren();
  }
  form.addEventListener('input', () => {
    clear();
    status.textContent = '输入已更改，请重新预览。';
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    clear();
    const values = Object.fromEntries(new FormData(form));
    const request = state.next();
    const body = { question: values.question, profile: values.profile };
    status.textContent = '正在核对反例与数据…';
    preview.disabled = true;
    try {
      if (values.mode === 'saved') {
        body.contract = JSON.parse(values.contract);
        body.attachment_refs = JSON.parse(values.attachment_refs);
      } else if (values.mode === 'sample') {
        const sample = await post(fetcher, '/api/research/discovery/sample', values.csrf, { question: values.question }, request.signal);
        body.contract = sample.contract;
        body.attachment_refs = sample.attachment_refs;
      }
      if (!request.current()) return;
      const plan = await post(fetcher, '/api/research/discovery/preview', values.csrf, body, request.signal);
      if (!request.current()) return;
      if (!['READY', 'NEEDS_DATA', 'NEEDS_CLARIFICATION', 'BUDGET_STOPPED', 'SOURCE_REVIEW_REQUIRED'].includes(plan.status)) throw new TypeError('invalid blueprint');
      const titles = { READY: '可以提交', NEEDS_DATA: '需要补充数据', NEEDS_CLARIFICATION: '请缩小问题范围', BUDGET_STOPPED: '上下文预算不足', SOURCE_REVIEW_REQUIRED: '来源需要复核' };
      status.textContent = titles[plan.status];
      if (plan.hypothesis) output.append(element(doc, 'p', plan.hypothesis), element(doc, 'p', plan.falsification));
      const gaps = [...(plan.missing_fields || []), ...(plan.readiness_gaps || [])];
      if (gaps.length) output.append(element(doc, 'p', gaps.join(' · ')));
      if (plan.status === 'NEEDS_DATA') output.append(link(doc, '打开数据就绪检查', '/settings/data-connections'));
      if (plan.status === 'SOURCE_REVIEW_REQUIRED') output.append(link(doc, '查看来源审阅', '#qw-sources'));
      if (plan.status === 'READY' && /^[a-f0-9]{64}$/u.test(plan.fingerprint)) {
        pending = { request, body, csrf: values.csrf, fingerprint: plan.fingerprint, formIdentity: JSON.stringify(values),
          key: 'discovery-' + crypto.randomUUID() };
        submit.disabled = false;
      }
    } catch (error) {
      if (request.current()) status.textContent = error instanceof SyntaxError ? '请检查面板与附件的 JSON 格式。' : userErrorMessage(error);
    } finally {
      if (request.current()) preview.disabled = false;
    }
  });
  submit.addEventListener('click', async () => {
    if (!pending || submit.disabled) return;
    const bound = pending;
    if (bound.formIdentity !== JSON.stringify(Object.fromEntries(new FormData(form)))) {
      clear();
      status.textContent = '输入已更改，请重新预览。';
      return;
    }
    submit.disabled = true;
    status.textContent = '正在提交冻结研究…';
    try {
      const result = await post(fetcher, '/api/research/discovery', bound.csrf, {
        ...bound.body, authorized: true, blueprint_fingerprint: bound.fingerprint, idempotency_key: bound.key,
      });
      if (!bound.request.current()) return;
      pending = null;
      const run = result.job?.run_id;
      if (!/^research-[a-f0-9]{24}$/u.test(run || '')) throw new TypeError('invalid job');
      status.textContent = '研究已进入后台队列。';
      const study = result.study_id;
      output.append(/^study-[a-f0-9]{24}$/u.test(study || '') ?
        link(doc, '打开因子实验', '/research/experiments?study=' + study) :
        link(doc, '打开研究任务', '/research/' + run));
    } catch (error) {
      if (bound.request.current()) {
        status.textContent = userErrorMessage(error);
        submit.disabled = pending === null;
      }
    }
  });
}

export function mountQuantWorkspace(root, { fetcher = fetch } = {}) {
  if (root.dataset.qwMounted) return;
  root.dataset.qwMounted = 'true';
  mountCatalogue(root, fetcher);
  mountDiscovery(root, fetcher);
}

export function mountQuantPool(form, { fetcher = fetch } = {}) {
  if (form.dataset.poolMounted) return;
  form.dataset.poolMounted = 'true';
  form.addEventListener('submit', event => event.preventDefault());
  const state = latestOperation();
  const buttons = [...form.querySelectorAll('[data-pool-action]')];
  const status = form.querySelector('[data-pool-status]');
  const output = form.querySelector('[data-pool-results]');
  buttons.forEach(button => { button.disabled = false; });
  form.addEventListener('input', () => {
    state.invalidate();
    buttons.forEach(button => { button.disabled = false; });
    status.textContent = '请核对输入后提交。';
    output.replaceChildren();
  });
  form.addEventListener('click', async event => {
    const action = event.target.dataset.poolAction;
    if (!['register', 'freeze', 'observe'].includes(action)) return;
    if (!form.reportValidity()) return;
    const values = Object.fromEntries(new FormData(form));
    const fields = { register: ['pool_ref', 'job_id', 'instrument', 'decision_origin', 'stage'],
      freeze: ['pool_ref', 'decision_at'], observe: ['cohort_ref', 'snapshot_ref'] }[action];
    if (fields.some(key => !values[key]?.trim())) {
      status.textContent = '请填写本操作所需的标识和时点。';
      return;
    }
    const request = state.next();
    buttons.forEach(button => { button.disabled = true; });
    output.replaceChildren();
    status.textContent = '正在核对研究池证据…';
    try {
      const suffix = action === 'register' ? '' : '/' + action;
      const result = await post(fetcher, '/api/research/pools' + suffix, values.csrf,
        Object.fromEntries(fields.map(key => [key, values[key].trim()])));
      if (!request.current()) return;
      if (action === 'freeze' && /^[0-9a-f]{64}$/u.test(result.cohort_ref || '')) {
        form.elements.cohort_ref.value = result.cohort_ref;
      }
      status.textContent = '已返回核验结果。';
      const details = element(form.ownerDocument, 'details');
      details.append(element(form.ownerDocument, 'summary', '查看记录及证据'),
        element(form.ownerDocument, 'pre', JSON.stringify(result, null, 2)));
      output.append(details);
    } catch (error) {
      if (request.current()) status.textContent = userErrorMessage(error);
    } finally {
      if (request.current()) buttons.forEach(button => { button.disabled = false; });
    }
  });
}

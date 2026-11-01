import katex from 'katex';

function requiredText(value, field) {
  if (typeof value !== 'string' || value.trim() === '') throw new TypeError(`${field} is required`);
  return value;
}

export function normalizeKnowledgePayload(payload) {
  if (!payload || payload.schema_version !== 1 || !payload.unit) throw new TypeError('knowledge payload schema is invalid');
  const unit = payload.unit;
  requiredText(unit.unit_id, 'unit id');
  requiredText(unit.title, 'unit title');
  if (!Array.isArray(unit.equations)) throw new TypeError('knowledge equations are invalid');
  const equationIds = new Set();
  const equations = unit.equations.map((equation, index) => {
    requiredText(equation.equation_id, `equation ${index} id`);
    if (equationIds.has(equation.equation_id)) throw new TypeError('knowledge equation ids must be unique');
    equationIds.add(equation.equation_id);
    requiredText(equation.latex, `equation ${index} latex`);
    return { ...equation };
  });
  if (!Array.isArray(unit.code_segments)) throw new TypeError('knowledge code segments are invalid');
  const codeIds = new Set();
  const codeSegments = unit.code_segments.map((segment, index) => {
    requiredText(segment.segment_id, `code segment ${index} id`);
    if (codeIds.has(segment.segment_id)) throw new TypeError('knowledge code segment ids must be unique');
    codeIds.add(segment.segment_id);
    if (!Array.isArray(segment.line_range) || segment.line_range.length !== 2) throw new TypeError('code line range is invalid');
    return { ...segment, line_range: [...segment.line_range] };
  });
  const context = payload.context && typeof payload.context === 'object' ? { ...payload.context } : { status: 'NO_CONTEXT_AVAILABLE', binding: null };
  if (!['AVAILABLE', 'NO_CONTEXT_AVAILABLE'].includes(context.status)) throw new TypeError('knowledge context status is invalid');
  return { ...payload, schema_version: 1, unit: { ...unit, equations, code_segments: codeSegments }, context };
}

export function renderKnowledgeEquation(equation) {
  requiredText(equation?.latex, 'equation latex');
  return katex.renderToString(equation.latex, { displayMode: true, throwOnError: false, output: 'htmlAndMathml' });
}

export function selectCodeSegment(segments, segmentId) {
  const segment = segments.find((item) => item.segment_id === segmentId);
  if (!segment) throw new Error(`knowledge code segment not found: ${segmentId}`);
  return segment;
}

function renderLineButtons(root, segments) {
  const target = root.querySelector('[data-knowledge-code-lines]');
  const inspector = root.querySelector('[data-knowledge-code-inspector]');
  if (!target) return;
  target.textContent = '';
  segments.forEach((segment) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'knowledge-code-line';
    button.dataset.segmentId = segment.segment_id;
    button.textContent = `${segment.line_range[0]}–${segment.line_range[1]}  ${segment.code}`;
    button.addEventListener('click', () => {
      target.querySelectorAll('[aria-current="true"]').forEach((item) => item.removeAttribute('aria-current'));
      button.setAttribute('aria-current', 'true');
      if (inspector) inspector.textContent = `${segment.data_input} → ${segment.data_output}`;
      root.dispatchEvent(new CustomEvent('finathink:code-selected', { bubbles: true, detail: { segmentId: segment.segment_id, equationIds: segment.equation_ids ?? [] } }));
    });
    target.appendChild(button);
  });
}

export function mountKnowledge(root, { payload, payloadUrl } = {}) {
  const load = payload ? Promise.resolve(payload) : fetch(payloadUrl || root.dataset.payloadUrl).then((response) => {
    if (!response.ok) throw new Error(`knowledge payload request failed (${response.status})`);
    return response.json();
  });
  return load.then((raw) => {
    const normalized = normalizeKnowledgePayload(raw);
    const whyNow = root.querySelector('[data-knowledge-why-now]');
    if (whyNow && normalized.context.status === 'AVAILABLE') whyNow.textContent = normalized.context.binding.why_now;
    normalized.unit.equations.forEach((equation) => {
      const target = root.querySelector(`[data-knowledge-equation="${CSS.escape(equation.equation_id)}"]`);
      if (target) target.innerHTML = renderKnowledgeEquation(equation);
    });
    renderLineButtons(root, normalized.unit.code_segments);
    return normalized;
  }).catch((error) => {
    const target = root.querySelector('[data-knowledge-error]') || root;
    target.textContent = `Knowledge rendering unavailable: ${error.message}`;
    target.hidden = false;
    target.setAttribute('role', 'alert');
    throw error;
  });
}

if (typeof document !== 'undefined') {
  document.querySelectorAll('[data-finathink-knowledge]').forEach((root) => { mountKnowledge(root); });
}

export function normalizeKnowledgeSearch(payload) {
  if (!payload || payload.schema_version !== 'knowledge-search.v1' || !Array.isArray(payload.items)
      || payload.items.length > 16 || !Number.isInteger(payload.total) || payload.total < 0
      || !Number.isInteger(payload.offset) || payload.offset < 0 || payload.page_size !== 16
      || typeof payload.query !== 'string' || !payload.filters || typeof payload.filters !== 'object') throw new TypeError('知识检索响应无效');
  const ids = new Set();
  for (const item of payload.items) {
    if (!item || !/^[a-z0-9][a-z0-9._-]{0,127}$/.test(item.canonical_id) || ids.has(item.canonical_id)
        || !['title', 'summary', 'domain', 'kind', 'evidence_level'].every(key => typeof item[key] === 'string')
        || !Array.isArray(item.aliases) || item.aliases.some(alias => typeof alias !== 'string')) throw new TypeError('知识词条响应无效');
    ids.add(item.canonical_id);
  }
  return payload;
}

// Generation checks remain necessary even when a mocked/cached transport ignores abort.
export function createKnowledgeSearch({ fetcher = fetch, update }) {
  let generation = 0;
  let active;
  return {
    async run(params) {
      const current = ++generation;
      active?.abort();
      active = new AbortController();
      update({ state: 'loading' });
      try {
        const response = await fetcher(`/api/knowledge/search?${new URLSearchParams(params)}`, { signal: active.signal, credentials: 'same-origin' });
        if (!response.ok) throw new Error('request failed');
        const payload = normalizeKnowledgeSearch(await response.json());
        if (current === generation) update({ state: payload.items.length ? 'ready' : 'empty', payload });
      } catch (error) {
        if (current === generation && error.name !== 'AbortError') update({ state: 'error', message: '暂时无法读取检索结果。请检查本地应用状态后重新检索。' });
      }
    },
    dispose() { generation++; active?.abort(); },
  };
}

const knowledgeDomains = { 'data-pit': '数据与时点', 'returns-market': '收益与市场', statistics: '统计方法', 'technical-volume': '技术与价量', fundamentals: '财务分析', 'factor-validation': '因子检验', 'strategy-portfolio': '策略与组合', 'risk-review': '风险与复盘' };
const knowledgeKinds = { CONCEPT: '基础概念', METHOD: '研究方法', FACTOR: '因子假设', STRATEGY: '组合规则', RISK: '风险检查', CASE: '案例' };
const knowledgeLevels = { DEFINITION_CHECKED: '定义已核对', SOURCE_REPORTED: '来源中的研究假设' };

export function mountQuantKnowledge(root) {
  if (root.dataset.knowledgeMounted === 'true') return;
  root.dataset.knowledgeMounted = 'true';
  const form = root.querySelector('[data-knowledge-search]');
  const results = root.querySelector('[data-knowledge-results]');
  const status = root.querySelector('[data-knowledge-status]');
  const empty = root.querySelector('[data-knowledge-empty]');
  const retry = root.querySelector('[data-knowledge-retry]');
  const pages = root.querySelector('[data-knowledge-pagination]');
  const node = (tag, text, className) => {
    const el = document.createElement(tag);
    if (text != null) el.textContent = text;
    if (className) el.className = className;
    return el;
  };
  const controller = createKnowledgeSearch({ update({ state, payload, message }) {
    root.dataset.knowledgeState = state;
    results.setAttribute('aria-busy', String(state === 'loading'));
    results.hidden = state === 'loading' || state === 'error';
    retry.hidden = state !== 'error';
    empty.hidden = state !== 'empty';
    pages.hidden = state === 'loading' || state === 'error';
    status.textContent = state === 'loading' ? '正在检索本地知识…' : state === 'error' ? message : `找到 ${payload.total} 个词条。`;
    if (!payload) return;
    results.replaceChildren();
    for (const item of payload.items) {
      const card = node('article', null, 'knowledge-tile');
      const heading = node('h2');
      const link = node('a', item.title);
      link.href = `/knowledge/items/${encodeURIComponent(item.canonical_id)}`;
      heading.append(link);
      card.append(node('span', `${knowledgeDomains[item.domain] || item.domain} · ${knowledgeKinds[item.kind] || item.kind}`, 'knowledge-tag'), heading,
        node('p', item.summary), node('p', item.aliases.slice(0, 3).join(' · '), 'knowledge-alias'), node('p', knowledgeLevels[item.evidence_level] || item.evidence_level, 'meta'));
      results.append(card);
    }
    const nav = node('nav', null, 'knowledge-pagination');
    nav.setAttribute('aria-label', '知识结果分页');
    for (const [label, offset] of [['上一页', payload.offset - 16], ['下一页', payload.offset + 16]]) {
      if (offset < 0 || offset >= payload.total) continue;
      const link = node('a', label);
      link.href = `/knowledge?${new URLSearchParams({ q: payload.query, ...payload.filters, offset })}`;
      nav.append(link);
    }
    pages.replaceChildren(nav);
  } });
  const run = () => controller.run(Object.fromEntries(new FormData(form)));
  form.addEventListener('submit', event => { event.preventDefault(); run(); });
  retry.addEventListener('click', run);
  // The first page is server-rendered; no duplicate initialization request.
  return controller;
}

if (typeof document !== 'undefined') document.querySelectorAll('[data-quant-knowledge]').forEach(mountQuantKnowledge);

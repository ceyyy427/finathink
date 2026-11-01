import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeKnowledgePayload, renderKnowledgeEquation, selectCodeSegment } from '../src/knowledge.js';

const payload = {
  schema_version: 1,
  unit: {
    unit_id: 'sharpe',
    title: 'Sharpe Ratio',
    equations: [{ equation_id: 'sharpe-formula', latex: '\\frac{r-rf}{sigma}', mathml: '<math></math>' }],
    code_segments: [{ segment_id: 'sharpe-code', line_range: [1, 1], code: 'sharpe = mean_excess / volatility' }],
  },
  context: { status: 'NO_CONTEXT_AVAILABLE', binding: null },
};

test('normalizes knowledge payload and rejects duplicate equations', () => {
  const normalized = normalizeKnowledgePayload(payload);
  assert.equal(normalized.unit.unit_id, 'sharpe');
  assert.equal(selectCodeSegment(normalized.unit.code_segments, 'sharpe-code').segment_id, 'sharpe-code');
  assert.throws(() => normalizeKnowledgePayload({ ...payload, unit: { ...payload.unit, equations: [...payload.unit.equations, payload.unit.equations[0]] } }), /equation/);
});

test('renders local equation markup and rejects unknown code selections', () => {
  assert.match(renderKnowledgeEquation(payload.unit.equations[0]), /frac|math/);
  assert.throws(() => selectCodeSegment(payload.unit.code_segments, 'missing'), /code segment/);
});

const { createKnowledgeSearch, normalizeKnowledgeSearch } = await import('../src/knowledge.js');
const searchPayload = (id = 'return') => ({ schema_version: 'knowledge-search.v1', total: 1, offset: 0, page_size: 16, query: id, filters: {}, items: [{ canonical_id: id, title: '简单收益率', summary: '同期间比例变化', domain: 'returns-market', kind: 'CONCEPT', evidence_level: 'DEFINITION_CHECKED', aliases: ['Return'] }] });

test('search rejects malformed shape and unsafe identifiers', () => {
  assert.equal(normalizeKnowledgeSearch(searchPayload()).items[0].canonical_id, 'return');
  assert.throws(() => normalizeKnowledgeSearch({ ...searchPayload(), total: -1 }));
  assert.throws(() => normalizeKnowledgeSearch(searchPayload('../private')));
  assert.throws(() => normalizeKnowledgeSearch({ ...searchPayload(), items: [searchPayload().items[0], searchPayload().items[0]] }));
});

test('search ignores stale success and stale error even if abort is ignored', async () => {
  const requests = [], updates = [];
  const search = createKnowledgeSearch({ fetcher: (url, options) => new Promise((resolve, reject) => requests.push({ url, options, resolve, reject })), update: value => updates.push(value) });
  const old = search.run({ q: '旧查询' });
  const fresh = search.run({ q: '新查询' });
  assert.equal(requests[0].options.signal.aborted, true);
  requests[1].resolve({ ok: true, json: async () => searchPayload('drawdown') });
  await fresh;
  requests[0].resolve({ ok: true, json: async () => searchPayload('return') });
  await old;
  assert.equal(updates.at(-1).payload.items[0].canonical_id, 'drawdown');
  const staleError = search.run({ q: '再试' });
  const newer = search.run({ q: '最新' });
  requests[3].resolve({ ok: true, json: async () => searchPayload('volatility') });
  await newer;
  requests[2].reject(new Error('old failure'));
  await staleError;
  assert.equal(updates.at(-1).payload.items[0].canonical_id, 'volatility');
});

test('search has empty, recoverable error, retry and disposed request states', async () => {
  const states = [];
  let response = { ok: false };
  const search = createKnowledgeSearch({ fetcher: async () => response, update: value => states.push(value) });
  await search.run({ q: '问题' });
  assert.equal(states.at(-1).state, 'error');
  assert.match(states.at(-1).message, /重新检索/);
  response = { ok: true, json: async () => ({ ...searchPayload(), total: 0, items: [] }) };
  await search.run({ q: '问题' });
  assert.equal(states.at(-1).state, 'empty');
  let finish;
  const disposed = createKnowledgeSearch({ fetcher: () => new Promise(resolve => { finish = resolve; }), update: value => states.push(value) });
  const pending = disposed.run({ q: 'pending' });
  disposed.dispose();
  const before = states.length;
  finish({ ok: true, json: async () => searchPayload() });
  await pending;
  assert.equal(states.length, before);
});

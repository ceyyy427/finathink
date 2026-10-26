import test from 'node:test';
import assert from 'node:assert/strict';

import { mountOverview, normalizeOverviewPayload, overviewStatus, renderOverview, renderOverviewVisuals } from '../src/overview.js';

const payload = {
  schema_version: 1,
  mode: 'OFFLINE',
  paper_only: true,
  as_of: '2026-10-10',
  metrics: { sample_events: 1, saved_items: 2, learning_items: 1, research_runs: 0 },
  activity: [
    { timestamp: '2026-10-02', label: 'learning', status: 'RECORDED' },
    { timestamp: '2026-10-01', label: 'event', status: 'RECORDED' },
  ],
  topics: [],
  learning_activity: [],
  research_status: { count: 0, states: [] },
  limitations: ['fixture only'],
  provenance: { data_mode: 'SAMPLE', source_state: 'CAPTURED', source_id: 'bls' },
  activity_series: [{ date: '2026-10-01', count: 2, learning: 1, research: 1 }],
  research_comparison: [],
};

test('normalizeOverviewPayload preserves server metrics and chronological activity', () => {
  const normalized = normalizeOverviewPayload(payload);
  assert.equal(normalized.metrics.saved_items, 2);
  assert.deepEqual(normalized.activity.map((item) => item.timestamp), ['2026-10-01', '2026-10-02']);
  assert.deepEqual(normalized.topics, []);
  assert.equal(normalized.paper_only, true);
  assert.deepEqual(normalized.activity_series[0].count, 2);
});

test('normalizeOverviewPayload rejects invalid boundaries and public secrets or paths', () => {
  assert.throws(() => normalizeOverviewPayload({ ...payload, paper_only: false }), /boundary/i);
  assert.throws(() => normalizeOverviewPayload({ ...payload, provenance: { api_key: 'hidden' } }), /unsafe/i);
  assert.throws(() => normalizeOverviewPayload({ ...payload, limitations: ['/Users/mac/private.sqlite3'] }), /unsafe/i);
  assert.throws(() => normalizeOverviewPayload({ ...payload, topics: [{ topic: 'quant', count: -1 }] }), /count/i);
});

test('overviewStatus distinguishes loading, empty, ready, blocked, and error states', () => {
  assert.equal(overviewStatus(null), 'LOADING');
  assert.equal(overviewStatus(normalizeOverviewPayload({ ...payload, metrics: { sample_events: 0, saved_items: 0, learning_items: 0, research_runs: 0 }, activity: [], activity_series: [], topics: [], learning_activity: [], research_comparison: [] })), 'EMPTY');
  assert.equal(overviewStatus(normalizeOverviewPayload(payload)), 'READY');
  assert.equal(overviewStatus(null, new Error('offline')), 'ERROR');
});

test('renderOverview exposes reduced-motion and keyboard text-summary affordances', () => {
  const chart = { dataset: {}, setAttribute(name, value) { this[name] = value; } };
  const root = { dataset: {}, querySelector: (selector) => selector === '[data-overview-chart]' ? chart : { dataset: {} } };
  globalThis.window = { matchMedia: () => ({ matches: true }) };
  renderOverview(root, payload);
  assert.equal(root.dataset.animation, 'reduced');
  assert.equal(chart.tabindex, '0');
  assert.match(chart['aria-label'], /text summary/i);
  delete globalThis.window;
});

test('renderOverviewVisuals marks real chart, heatmap, and comparison regions ready', () => {
  const chart = { dataset: {}, querySelector: () => null };
  const topics = { dataset: {} };
  const heatmap = { dataset: {} };
  const compare = { dataset: {} };
  const root = {
    dataset: {},
    querySelector: (selector) => ({
      '[data-overview-chart]': chart,
      '[data-overview-topics]': topics,
      '[data-overview-heatmap]': heatmap,
      '[data-overview-comparison]': compare,
    }[selector] || null),
  };
  renderOverviewVisuals(root, normalizeOverviewPayload(payload));
  assert.equal(chart.dataset.chartReady, 'ready');
  assert.equal(topics.dataset.heatmapReady, 'empty');
  assert.equal(heatmap.dataset.heatmapReady, 'empty');
  assert.equal(compare.dataset.comparisonReady, 'empty');
});

test('mountOverview exposes loading and actionable error states', async () => {
  const loading = { hidden: true };
  const error = { hidden: true, textContent: '' };
  const root = {
    dataset: {},
    setAttribute(name, value) { this[name] = value; },
    querySelector: (selector) => selector === '[data-overview-loading]' ? loading : selector === '[data-overview-error]' ? error : null,
  };
  await mountOverview(root, { payload });
  assert.equal(root.dataset.overviewStatus, 'READY');
  assert.equal(root['aria-busy'], 'false');
  assert.equal(loading.hidden, true);
  const failed = { dataset: {}, setAttribute(name, value) { this[name] = value; }, querySelector: (selector) => selector === '[data-overview-loading]' ? { hidden: true } : selector === '[data-overview-error]' ? error : null };
  await assert.rejects(() => mountOverview(failed, { payload: { ...payload, schema_version: 99 } }), /schema/);
  assert.equal(failed.dataset.overviewStatus, 'ERROR');
  assert.match(error.textContent, /Check the local service/);
});

test('mountOverview coalesces repeated calls into one request and render', async () => {
  let requests = 0;
  let finish;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = () => {
    requests += 1;
    return new Promise((resolve) => { finish = () => resolve({ ok: true, json: async () => payload }); });
  };
  try {
    const root = { dataset: { overviewPayloadUrl: '/api/overview' }, querySelector: () => null };
    const first = mountOverview(root);
    const second = mountOverview(root);
    assert.equal(requests, 1);
    finish();
    assert.equal(await first, await second);
    await mountOverview(root);
    assert.equal(requests, 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

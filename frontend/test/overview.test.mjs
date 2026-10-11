import test from 'node:test';
import assert from 'node:assert/strict';

import { normalizeOverviewPayload, overviewStatus, renderOverview } from '../src/overview.js';

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
};

test('normalizeOverviewPayload preserves server metrics and chronological activity', () => {
  const normalized = normalizeOverviewPayload(payload);
  assert.equal(normalized.metrics.saved_items, 2);
  assert.deepEqual(normalized.activity.map((item) => item.timestamp), ['2026-10-01', '2026-10-02']);
  assert.deepEqual(normalized.topics, []);
  assert.equal(normalized.paper_only, true);
});

test('normalizeOverviewPayload rejects invalid boundaries and public secrets or paths', () => {
  assert.throws(() => normalizeOverviewPayload({ ...payload, paper_only: false }), /boundary/i);
  assert.throws(() => normalizeOverviewPayload({ ...payload, provenance: { api_key: 'hidden' } }), /unsafe/i);
  assert.throws(() => normalizeOverviewPayload({ ...payload, limitations: ['/Users/mac/private.sqlite3'] }), /unsafe/i);
});

test('overviewStatus distinguishes loading, empty, ready, blocked, and error states', () => {
  assert.equal(overviewStatus(null), 'LOADING');
  assert.equal(overviewStatus(normalizeOverviewPayload({ ...payload, metrics: { sample_events: 0, saved_items: 0, learning_items: 0, research_runs: 0 }, activity: [], topics: [], learning_activity: [] })), 'EMPTY');
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

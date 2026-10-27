import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeFactorStudy } from '../src/experiment-workspace.js';
import * as workspace from '../src/experiment-workspace.js';

const study = { study_id: 'study-abc', state: 'VALIDATING', production_write: false, spec_ref: 'a'.repeat(64), spec: { snapshot_ref: 'b'.repeat(64), question: '研究窗口稳定性', cost: { fee_bps: 5, slippage_bps: 5 } } };

test('factor study normalization keeps server conditions and audit state', () => {
  assert.deepEqual(normalizeFactorStudy(study), study);
});

test('unsupported execution and missing snapshot identity are rejected', () => {
  assert.throws(() => normalizeFactorStudy({ ...study, production_write: true }));
  assert.throws(() => normalizeFactorStudy({ ...study, state: 'LIVE' }));
  assert.throws(() => normalizeFactorStudy({ ...study, spec: {} }));
});

test('nine-step track keeps blocking and not-run states bound to the same run', () => {
  assert.equal(typeof workspace.normalizeFactorValidationTrack, 'function');
  const ids = ['scope_data_audit', 'semantic_check', 'information_coefficient', 'deduplication', 'portfolio_backtest', 'oos_stability', 'incremental_value', 'capacity', 'archive_decision'];
  const run = { schema_version: 'factor-validation-run.v1', fingerprint: 'a'.repeat(64), binding_digest: 'b'.repeat(64),
    steps: ids.map((stage_id, index) => ({ stage_id, status: index === 0 ? 'BLOCKED_DATA' : 'NOT_RUN', binding_digest: 'b'.repeat(64) })) };
  assert.deepEqual(workspace.normalizeFactorValidationTrack(run), run.steps);
  assert.throws(() => workspace.normalizeFactorValidationTrack({ ...run, steps: run.steps.slice(1) }));
  assert.throws(() => workspace.normalizeFactorValidationTrack({ ...run, binding_digest: 'c'.repeat(64) }));
});

test('switching a method study to ordinary or pending study clears prior provenance', async () => {
  class Element {
    constructor() { this.value = ''; this.textContent = ''; this.children = []; this.dataset = {}; }
    addEventListener() {}
    setAttribute() {}
    removeAttribute() {}
    append(...items) { this.children.push(...items); }
    replaceChildren(...items) { this.children = items; }
  }
  const elements = new Map(); const get = (selector) => { if (!elements.has(selector)) elements.set(selector, new Element()); return elements.get(selector); };
  const root = { dataset: {}, setAttribute() {}, querySelector: get, ownerDocument: { createElement: () => new Element() } };
  const ids = ['scope_data_audit', 'semantic_check', 'information_coefficient', 'deduplication', 'portfolio_backtest', 'oos_stability', 'incremental_value', 'capacity', 'archive_decision'];
  const spec = { ...study.spec, instrument: 'AAA', recipe_id: 'turnover_mean', windows: [5], n_splits: 2, label_horizon: 1, embargo: 1, method_binding: { methods_digest: 'c'.repeat(64) } };
  const method = { ...study, study_id: 'study-method', state: 'VALIDATED', spec, validation: { snapshot_ref: spec.snapshot_ref, spec_ref: study.spec_ref, summaries: [], validation_run: { schema_version: 'factor-validation-run.v1', fingerprint: 'd'.repeat(64), binding_digest: 'e'.repeat(64), reproduction_scope: 'FORMULA_FIXTURE', steps: ids.map((stage_id) => ({ stage_id, status: 'COMPLETE', binding_digest: 'e'.repeat(64) })) } } };
  const records = { 'study-method': method, 'study-ordinary': { ...study, study_id: 'study-ordinary', state: 'VALIDATED', spec: { ...spec, method_binding: undefined } }, 'study-pending': { ...study, study_id: 'study-pending', spec } };
  const fetcher = async (url) => ({ ok: true, json: async () => url.endsWith('/catalog') ? { recipes: [] } : url.endsWith('/snapshots') ? { snapshots: [] } : url.endsWith('/experiments') ? { studies: [] } : records[url.split('/').pop()] });
  const api = workspace.mountExperimentWorkspace(root, { fetcher, interval: 100000 });
  try {
    await api.ready;
    for (const next of ['study-ordinary', 'study-pending']) {
      await api.select('study-method');
      assert.match(get('[data-study-method-binding]').textContent, /cccccccccccc/);
      assert.match(get('[data-study-method-binding]').textContent, /dddddddddddd/);
      await api.select(next);
      assert.doesNotMatch(get('[data-study-method-binding]').textContent, /cccccccccccc|dddddddddddd|FORMULA_FIXTURE/);
    }
  } finally { api.dispose(); }
});

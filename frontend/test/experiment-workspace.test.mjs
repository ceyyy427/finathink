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

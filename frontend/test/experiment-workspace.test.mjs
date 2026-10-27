import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeFactorStudy } from '../src/experiment-workspace.js';

const study = { study_id: 'study-abc', state: 'VALIDATING', production_write: false, spec_ref: 'a'.repeat(64), spec: { snapshot_ref: 'b'.repeat(64), question: '研究窗口稳定性', cost: { fee_bps: 5, slippage_bps: 5 } } };

test('factor study normalization keeps server conditions and audit state', () => {
  assert.deepEqual(normalizeFactorStudy(study), study);
});

test('unsupported execution and missing snapshot identity are rejected', () => {
  assert.throws(() => normalizeFactorStudy({ ...study, production_write: true }));
  assert.throws(() => normalizeFactorStudy({ ...study, state: 'LIVE' }));
  assert.throws(() => normalizeFactorStudy({ ...study, spec: {} }));
});

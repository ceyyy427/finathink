import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeLearningReview } from '../src/learning-review.js';

const digest = 'a'.repeat(64);
const review = { review_id: `learning-${'b'.repeat(32)}`, run_id: 'research-example', status: 'PENDING_REVIEW', paper_only: true, source_snapshot_ref: digest, evidence_snapshot_ref: digest, observed_snapshot_ref: digest, parent_policy_ref: digest, proposal_digest: digest, preview_fingerprint: digest, preview: { proposal_digest: digest, policy_fingerprint: digest }, observation: { equity: 9900, net_return: -0.01, horizon_observations: 5 } };

test('review normalization preserves server values and evidence bindings', () => {
  assert.deepEqual(normalizeLearningReview(review), review);
});
test('a changed preview, missing evidence, fabricated outcome or execution is rejected', () => {
  for (const patch of [{ paper_only: false }, { status: 'LIVE' }, { observed_snapshot_ref: '' }, { preview: { ...review.preview, proposal_digest: 'c'.repeat(64) } }, { observation: { ...review.observation, net_return: NaN } }, { observation: { ...review.observation, horizon_observations: 2 } }]) {
    assert.throws(() => normalizeLearningReview({ ...review, ...patch }));
  }
});

test('factor monitoring is labeled separately and cannot impersonate a settlement or weight update', () => {
  const value = { ...review, source_kind: 'FACTOR_VALIDATION_MONITORING', settlement: null,
    source_snapshot_ref: null, evidence_snapshot_ref: null, observed_snapshot_ref: null,
    proposal: { settlement_digest: null, factor_weight_updates: {}, risk_rule_updates: {} },
    observation: { monitoring: { schema_version: 'factor-monitoring.v1', status: 'REVIEW_RECOMMENDED',
      fingerprint: digest, evidence_refs: [1, 2, 3].map(() => ({ evaluation_fingerprint: digest })) } } };
  assert.equal(normalizeLearningReview(value), value);
  assert.throws(() => normalizeLearningReview({ ...value, settlement: { invented: true } }));
  assert.throws(() => normalizeLearningReview({ ...value, proposal: { ...value.proposal, factor_weight_updates: { foo: 1 } } }));
});

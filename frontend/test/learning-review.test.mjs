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

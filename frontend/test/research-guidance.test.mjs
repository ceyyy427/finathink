import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeReadiness, createReadinessCheck } from '../src/research-guidance.js';

const ready = { schema_version: 'research-readiness.v1', paper_only: true, status: 'READY', can_submit: true, input_digest: 'a'.repeat(64), checks: [{ code: 'snapshot', title: '数据', status: 'PASS', message: '已验证', action: { label: '核对', href: '/research/jobs#prepare-data' } }], coverage: { records: 4 } };

test('readiness rejects contradictory permission and arbitrary repair URLs', () => {
  assert.equal(normalizeReadiness(ready).can_submit, true);
  for (const change of [{ input_digest: 'unknown' }, { can_submit: false }, { status: 'BLOCKED' }, { checks: [{ ...ready.checks[0], status: 'BLOCKED' }] }, { checks: [{ ...ready.checks[0], action: { href: 'javascript:alert(1)' } }] }]) assert.throws(() => normalizeReadiness({ ...ready, ...change }));
});

test('edited input invalidates a pending result and its submit permission', async () => {
  let finish; const states = [];
  const checker = createReadinessCheck({ request: () => new Promise(resolve => { finish = resolve; }), onChange: (state) => states.push(state) });
  const pending = checker.check({ question: 'old' });
  checker.invalidate();
  finish(ready);
  assert.equal(await pending, null);
  assert.equal(states.at(-1), 'STALE');
  assert.equal(states.includes('READY'), false);
});

test('newer check wins and a late failure cannot replace it', async () => {
  let rejectOld; const states = [];
  const checker = createReadinessCheck({ request: value => value.question === 'old' ? new Promise((_, reject) => { rejectOld = reject; }) : Promise.resolve(ready), onChange: state => states.push(state) });
  const old = checker.check({ question: 'old' });
  assert.equal((await checker.check({ question: 'new' })).can_submit, true);
  rejectOld(new Error('old error'));
  assert.equal(await old, null);
  assert.equal(states.at(-1), 'READY');
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { latestOperation, displayValue } from '../src/research-reproduction.js';

test('late result does not replace a new selection; repeated clicks coalesce', async () => {
  let resolve; let calls = 0;
  const operation = latestOperation(() => { calls++; return new Promise(done => { resolve = done; }); });
  const first = operation.run('a');
  const duplicate = operation.run('a');
  assert.equal(calls, 1);
  operation.invalidate();
  resolve('old');
  assert.equal(await first, null);
  assert.equal(await duplicate, null);
});

test('zero is displayed while missing and nonfinite metrics remain unknown', () => {
  assert.equal(displayValue(0), '0');
  for (const item of [null, undefined, NaN, Infinity]) assert.equal(displayValue(item), '未知');
});

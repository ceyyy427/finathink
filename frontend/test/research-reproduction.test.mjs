import test from 'node:test';
import assert from 'node:assert/strict';
import { latestOperation, displayValue, exportRequest } from '../src/research-reproduction.js';

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

test('data export scope is explicit and tied to the selected run or learning pair', () => {
  assert.deepEqual(exportRequest('r', false, 'workflow', 'p'), {run_id: 'r', include_data: false});
  assert.deepEqual(exportRequest('r', true, 'prices', 'p'), {run_id: 'r', include_data: true});
  assert.deepEqual(exportRequest('r', true, 'workflow', ''), {run_id: 'r', include_data: true, data_scope: 'workflow'});
  assert.deepEqual(exportRequest('r', true, 'workflow', 'p'), {pair_id: 'p', include_data: true, data_scope: 'workflow'});
});

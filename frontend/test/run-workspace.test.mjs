import test from 'node:test';
import assert from 'node:assert/strict';
import { createJobObserver, modelCallSummary, mountRunWorkspace, normalizeJob } from '../src/run-workspace.js';

const job = { schema_version: 1, paper_only: true, execution_mode: 'local-deterministic', job_id: 'job-1', run_id: 'research-1', snapshot_ref: 'a'.repeat(64), status: 'running', state: 'QUANT_VALIDATION', attempts: 1, stages: [], metrics: {}, report_url: null, run_url: '/research/research-1' };
const response = (value) => ({ ok: true, json: async () => value });

test('an interrupted dispatched attempt is displayed as unknown usage, not zero calls', () => {
  const text = modelCallSummary({ ...job, execution_mode: 'provider-grounded', model_config_ref: 'desk', status: 'cancelled', model_research: { calls: [{ dispatched: true, status: 'UNKNOWN', usage: null }], usage_status: 'UNKNOWN' } });
  assert.match(text, /1 次调用/);
  assert.match(text, /未知/);
  assert.doesNotMatch(text, /0 次调用/);
});

test('job contract preserves server metrics and verifies report identity', () => {
  const value = normalizeJob({ ...job, status: 'completed', metrics: { total_return: 0.125, sharpe_ratio: null }, report_url: '/research/research-1/report/complete' });
  assert.equal(value.metrics.total_return, 0.125);
  assert.equal(value.metrics.sharpe_ratio, null);
  for (const change of [{ paper_only: false }, { snapshot_ref: 'unknown' }, { report_url: 'javascript:alert(1)' }, { report_url: '/research/other/report/complete' }, { metrics: { total_return: Infinity } }]) assert.throws(() => normalizeJob({ ...job, ...change }));
});

test('grounded jobs retain model provenance without admitting other execution modes', () => {
  const value = normalizeJob({ ...job, execution_mode: 'provider-grounded', model_config_ref: 'desk', model_research: { calls: [], roles: [] } });
  assert.equal(value.execution_mode, 'provider-grounded');
  assert.equal(value.model_config_ref, 'desk');
  assert.throws(() => normalizeJob({ ...job, execution_mode: 'live-trading' }));
});

test('observer stops on terminal result and never schedules another poll', async () => {
  const timers = new Map(); let serial = 0; let calls = 0; const updates = [];
  const observer = createJobObserver({ fetcher: async () => response({ ...job, status: ++calls === 1 ? 'running' : 'completed' }), setTimer: (fn) => { timers.set(++serial, fn); return serial; }, clearTimer: (id) => timers.delete(id) });
  await observer.watch('job-1', (value) => updates.push(value.status));
  const next = timers.values().next().value; timers.clear(); await next();
  assert.deepEqual(updates, ['running', 'completed']);
  assert.equal(timers.size, 0);
  observer.stop();
});

test('polling is bounded and stopping aborts the active request', async () => {
  const timers = []; const errors = []; let signal;
  const observer = createJobObserver({ maxPolls: 2, fetcher: async (_url, options) => { signal = options.signal; return response(job); }, setTimer: (fn) => { timers.push(fn); return fn; }, clearTimer: () => {} });
  await observer.watch('job-1', () => {}, (error) => errors.push(error.message));
  await timers.shift()();
  assert.equal(timers.length, 0);
  assert.equal(errors.length, 1);
  assert.match(errors[0], /暂停/);
  observer.stop();
  assert.equal(signal.aborted, true);
});

test('a late response from an old selection cannot replace the new job', async () => {
  let resolveOld; const updates = [];
  const observer = createJobObserver({ fetcher: async (url) => url.endsWith('job-old') ? new Promise((resolve) => { resolveOld = resolve; }) : response({ ...job, job_id: 'job-new', status: 'completed' }) });
  const old = observer.watch('job-old', (value) => updates.push(value.job_id));
  await observer.watch('job-new', (value) => updates.push(value.job_id));
  resolveOld(response({ ...job, job_id: 'job-old' })); await old;
  assert.deepEqual(updates, ['job-new']);
  observer.stop();
});

test('mounting one workspace twice coalesces its initial requests', async () => {
  let calls = 0;
  const root = { dataset: {}, querySelector: () => null };
  const fetcher = async () => { calls++; return response({ snapshots: [], jobs: [], connections: [] }); };
  const first = mountRunWorkspace(root, { fetcher });
  const second = mountRunWorkspace(root, { fetcher });
  assert.equal(first, second);
  await first.ready;
  assert.equal(calls, 4);
  first.stop();
});
import { parseMethodResponse } from '../src/method-handoff.js';

test('method callback accepts bounded bound JSON, never an executable blob', () => {
  const value = { schema_version: 'method-result.v1', package_digest: 'a'.repeat(64) };
  assert.deepEqual(parseMethodResponse(JSON.stringify(value)), value);
  assert.throws(() => parseMethodResponse('run_python()'));
  assert.throws(() => parseMethodResponse(JSON.stringify({ ...value, package_digest: 'other' })));
  assert.throws(() => parseMethodResponse('x'.repeat(131073)));
});

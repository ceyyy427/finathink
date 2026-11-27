import assert from 'node:assert/strict';
import { test } from 'node:test';

import { boundResponse, workerCommand } from '../src/model-workbench.js';
import { citationRows, jobApi, jobUrl, usageText } from '../src/model-workbench-view.js';

test('a manual return belongs to the displayed packet and stays within its byte bound', () => {
  const digest = 'a'.repeat(64);
  const good = { schema_version: 'method-result.v1', package_digest: digest, result: { evidence_refs: [] } };
  assert.deepEqual(boundResponse(JSON.stringify(good), digest), good);
  assert.throws(() => boundResponse(JSON.stringify(good), 'b'.repeat(64)), /包指纹/);
  assert.throws(() => boundResponse('{', digest), /有效/);
  assert.doesNotThrow(() => boundResponse(JSON.stringify({ ...good, pad: '字'.repeat(44000) }), digest));
  assert.throws(() => boundResponse(JSON.stringify({ ...good, pad: '字'.repeat(88000) }), digest), /256 KiB/);
});

test('receipt usage preserves unavailable, partially reported and genuine zero counts', () => {
  assert.equal(usageText(null), '未知');
  assert.equal(usageText({}), '未知');
  assert.equal(usageText({ total_units: null, generated_units: 0 }), '输出 0');
  assert.equal(usageText({ generated_units: 2, total_units: 10, input_units: 8 }), '输入 8 / 输出 2 / 合计 10');
});

test('native navigation preserves the complete selected packet reference', () => {
  const link = new URL(jobUrl('local & job', 'method-handoff:abc'), 'http://localhost:8123');
  assert.equal(link.searchParams.get('job'), 'local & job');
  assert.equal(link.searchParams.get('handoff'), 'method-handoff:abc');
  assert.equal(link.hash, '#mw-codex');
  assert.equal(jobApi('job/name'), '/api/research/model-workbench/jobs/job%2Fname');
});

test('operator connection command uses the actual origin and only shell-safe identifiers', () => {
  assert.equal(workerCommand('http://[::1]:8824', 'job-a', 'codex:operation-1'), 'finathink-codex --run --url http://[::1]:8824 --job job-a --operation codex:operation-1');
  for (const bad of ['id;echo', 'foo bar', null, undefined]) assert.throws(() => workerCommand('http://localhost:9000', bad, 'op-2'));
});

test('citation rows distinguish a conflicting price from a matching note and preserve legacy receipts', () => {
  const checked = { claim_index: 0, status: 'REVIEW_REQUIRED', evidence_refs: ['note', 'price'], citation_checks: [
    { evidence_ref: 'note', status: 'SOURCE_MATCHED' }, { evidence_ref: 'price', status: 'SOURCE_MISMATCH' },
  ] };
  assert.deepEqual(citationRows([checked]), [
    ['1', '待人工核对', 'note', '字面表述与来源相符'], ['1', '待人工核对', 'price', '与所引来源不符'],
  ]);
  const legacy = { claim_index: 1, status: 'SOURCE_MATCHED', evidence_refs: ['legacy'] };
  assert.deepEqual(citationRows([legacy]), [['2', '字面表述与来源相符', 'legacy', '未记录逐条结果']]);
});

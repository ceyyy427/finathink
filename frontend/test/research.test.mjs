import test from 'node:test';
import assert from 'node:assert/strict';

import {
  formatResearchPoint,
  normalizeResearchRun,
  normalizePayload,
  normalizeWorkbenchPayload,
  researchRunStatus,
  selectPoint,
  selectWorkbenchPoint,
  previewWorkbenchParameter,
  normalizeResearchStatusWall,
  renderResearchStatusWall,
  normalizeResearchRuntime,
  renderResearchRuntime,
} from '../src/research.js';

const payload = {
  schema_version: 1,
  dataset: { id: 'fixture', fingerprint: 'abc123', mode: 'SAMPLE' },
  points: [
    {
      id: 'p-1', time: '2026-01-01T00:00:00Z', open: 10, high: 12, low: 9,
      close: 11, volume: 100, features: { momentum: 0.2 }, events: ['cpi'], signal: 'WATCH',
    },
  ],
  features: [{ id: 'momentum', label: 'Momentum' }],
  events: [{ id: 'cpi', label: 'CPI release' }],
  limitations: ['fixture only'],
};

test('normalizePayload preserves provenance and rejects malformed chart records', () => {
  const normalized = normalizePayload(payload);
  assert.equal(normalized.dataset.fingerprint, 'abc123');
  assert.equal(normalized.points[0].id, 'p-1');
  assert.throws(() => normalizePayload({ ...payload, points: [{ ...payload.points[0], close: NaN }] }), /finite|invalid/i);
  assert.throws(() => normalizePayload({ ...payload, dataset: { id: 'fixture' } }), /fingerprint/i);
});

test('normalizePayload preserves bounded factor research evidence without recomputing it', () => {
  const normalized = normalizePayload({ ...payload, factor_research: { state: 'CANDIDATE_POOL', rounds: [{ candidate: { expression: 'rank(close)' }, evaluation: { status: 'VALID' }, admission: { status: 'ADMITTED' } }] } });
  assert.equal(normalized.factor_research.state, 'CANDIDATE_POOL');
  assert.equal(normalized.factor_research.rounds[0].admission.status, 'ADMITTED');
  assert.deepEqual(normalized.factor_candidates, []);
  assert.equal(normalized.provider_status, null);
});

test('normalizePayload rejects duplicate point identity or timestamps', () => {
  const duplicateId = { ...payload.points[0], time: '2026-01-02T00:00:00Z' };
  assert.throws(() => normalizePayload({ ...payload, points: [payload.points[0], duplicateId] }), /unique/i);
  const duplicateTime = { ...payload.points[0], id: 'p-2' };
  assert.throws(() => normalizePayload({ ...payload, points: [payload.points[0], duplicateTime] }), /unique/i);
});

test('selectPoint returns the exact canonical point without recomputing values', () => {
  const normalized = normalizePayload(payload);
  assert.equal(selectPoint(normalized.points, 'p-1').close, 11);
  assert.equal(selectPoint(normalized.points, 'missing'), null);
});

test('formatResearchPoint exposes a keyboard/table-friendly research summary', () => {
  const point = normalizePayload(payload).points[0];
  const text = formatResearchPoint(point, { cpi: 'CPI release' });
  assert.match(text, /2026-01-01/);
  assert.match(text, /Close 11/);
  assert.match(text, /Momentum 0\.2/);
  assert.match(text, /CPI release/);
});

const runPayload = {
  schema_version: 1,
  run_id: 'run-ui',
  state: 'LEARNING_RECORDED',
  mode: 'OFFLINE',
  paper_only: true,
  as_of: '2026-10-01',
  decision_eligible: true,
  analysts: [{ role: 'technical', status: 'READY' }],
  missing_analysts: [],
  report_links: { complete: '/research/run-ui/report/complete' },
};

test('research run status covers loading, success, partial, blocked, and error', () => {
  assert.equal(researchRunStatus(null), 'LOADING');
  const ready = normalizeResearchRun(runPayload);
  assert.equal(researchRunStatus(ready), 'READY');
  assert.equal(researchRunStatus(normalizeResearchRun({ ...runPayload, missing_analysts: ['news'] })), 'PARTIAL');
  assert.equal(researchRunStatus(normalizeResearchRun({ ...runPayload, state: 'VALIDATION_FAILED', decision_eligible: false })), 'BLOCKED');
  assert.equal(researchRunStatus(ready, new Error('offline failure')), 'ERROR');
});

test('research run payload rejects non-offline or non-paper boundaries', () => {
  assert.throws(() => normalizeResearchRun({ ...runPayload, paper_only: false }), /boundary/i);
  assert.throws(() => normalizeResearchRun({ ...runPayload, state: 'UNKNOWN' }), /state/i);
});

test('normalizeResearchStatusWall preserves server-owned status facts and does not calculate metrics', () => {
  const wall = normalizeResearchStatusWall({
    schema_version: 1,
    run_id: 'wall-ui',
    state: 'ANALYSTS_RUNNING',
    role_status: { technical: 'READY', fundamentals: 'PARTIAL' },
    stage_status: { analysts: 'PARTIAL', evidence: 'BLOCKED' },
    missing_evidence: ['technical:price'],
    checkpoint_status: { state: 'SAVED' },
    factor_proposals: [{ proposal_id: 'p-1', status: 'VALIDATED', digest: 'd' }],
    provider_readiness: { status: 'CONFIGURED', credential_ref: 'USER_KEY' },
  });
  assert.deepEqual(Object.keys(wall.role_status), ['fundamentals', 'technical']);
  assert.equal(wall.stage_status.evidence, 'BLOCKED');
  assert.deepEqual(wall.missing_evidence, ['technical:price']);
  assert.equal(wall.factor_proposals[0].digest, 'd');
  assert.equal(wall.provider_readiness.status, 'CONFIGURED');
  assert.equal(Object.hasOwn(wall, 'metrics'), false);
});

test('renderResearchStatusWall is read-only and supports no-JS fallback', () => {
  const root = {
    dataset: {},
    querySelector: () => ({ textContent: '' }),
  };
  const normalized = renderResearchStatusWall(root, {
    schema_version: 1,
    run_id: 'wall-ui',
    state: 'CANCELLED',
    role_status: { technical: 'READY' },
    stage_status: { cancel: 'CANCELLED' },
    missing_evidence: [],
    checkpoint_status: { state: 'CANCELLED' },
    factor_proposals: [],
    provider_readiness: { status: 'NOT_CONFIGURED' },
  });
  assert.equal(root.dataset.statusWall, 'READY');
  assert.equal(normalized.state, 'CANCELLED');
});

test('normalizeResearchRuntime keeps server-owned stream facts and rejects public secrets', () => {
  const normalized = normalizeResearchRuntime({
    schema_version: 2,
    run_id: 'stream-ui',
    state: 'ANALYSTS_RUNNING',
    mode: 'OFFLINE',
    paper_only: true,
    stage_status: { analysts: 'CURRENT' },
    role_status: { technical: 'READY' },
    tool_summaries: [{ name: 'factor_scan', status: 'COMPLETE' }],
    retries: { count: 1 },
    checkpoint: { state: 'SAVED' },
    learning_proposal: { status: 'NO_LEARNING_UPDATE' },
    manifest_digest: 'a'.repeat(64),
    limitations: ['fixture'],
  });
  assert.equal(normalized.schema_version, 2);
  assert.equal(normalized.run_id, 'stream-ui');
  assert.equal(normalized.tool_summaries[0].name, 'factor_scan');
  assert.throws(() => normalizeResearchRuntime({ ...normalized, checkpoint: { prompt: 'hidden' } }), /unsafe|invalid/i);
});

test('online runtime accepts the same read-only contract without weakening mode checks', () => {
  const payload = { schema_version: 3, run_id: 'online-runtime', state: 'LEARNING_RECORDED', mode: 'USER_API', paper_only: true, manifest_digest: 'a'.repeat(64) };
  assert.equal(normalizeResearchRuntime(payload).mode, 'USER_API');
  assert.throws(() => normalizeResearchRuntime({ ...payload, mode: 'LIVE' }));
});

test('renderResearchRuntime is render-only and supports no-JS fallback', () => {
  const root = { dataset: {}, querySelector: () => ({ textContent: '' }) };
  const rendered = renderResearchRuntime(root, {
    schema_version: 2,
    run_id: 'stream-ui',
    state: 'CANCELLED',
    mode: 'OFFLINE',
    paper_only: true,
    stage_status: { cancel: 'CANCELLED' },
    role_status: {},
    tool_summaries: [],
    retries: { count: 0 },
    checkpoint: { state: 'CANCELLED' },
    learning_proposal: { status: 'NO_LEARNING_UPDATE' },
    manifest_digest: 'a'.repeat(64),
    limitations: [],
  });
  assert.equal(root.dataset.runtimeStatus, 'READY');
  assert.equal(rendered.state, 'CANCELLED');
});

test('normalizeResearchRuntime preserves server-owned report tree stages without calculating metrics', () => {
  const normalized = normalizeResearchRuntime({
    schema_version: 3,
    run_id: 'tree-ui',
    state: 'RISK_REVIEW',
    mode: 'OFFLINE',
    paper_only: true,
    stage_status: { quant: 'COMPLETE', risk: 'CURRENT', learning: 'PENDING' },
    role_status: {},
    experiments: [{ experiment_id: 'exp-1', status: 'COMPLETE', metrics: { ic: 0.1 } }],
    risk_attribution: { status: 'CURRENT', factors: [{ name: 'volatility', contribution: 0.2 }] },
    learning_history: [],
    manifest_digest: 'a'.repeat(64),
  });
  assert.equal(normalized.schema_version, 3);
  assert.equal(normalized.experiments[0].experiment_id, 'exp-1');
  assert.equal(normalized.risk_attribution.status, 'CURRENT');
  assert.throws(() => normalizeResearchRuntime({ ...normalized, experiments: [{ prompt: 'hidden' }] }), /unsafe/i);
});

test('renderResearchRuntime uses server stage status for array report data', () => {
  const state = { textContent: '' };
  const stages = { textContent: '' };
  const root = {
    dataset: {},
    querySelector: (selector) => selector === '[data-research-runtime-state]' ? state : selector === '[data-research-runtime-report-stages]' ? stages : { textContent: '' },
  };
  renderResearchRuntime(root, {
    schema_version: 3,
    run_id: 'tree-ui',
    state: 'RISK_REVIEW',
    mode: 'OFFLINE',
    paper_only: true,
    stage_status: { quant: 'COMPLETE', risk: 'CURRENT', learning: 'PENDING' },
    role_status: {},
    experiments: [{ experiment_id: 'exp-1' }],
    risk_attribution: [],
    learning_history: [],
    manifest_digest: 'a'.repeat(64),
  });
  assert.equal(stages.textContent, 'experiments: COMPLETE · risk_attribution: CURRENT · learning_history: PENDING');
});

const workbench = {
  schema_version: 1,
  view: 'factor-strategy-workbench',
  run_id: 'fixture-workbench-v1',
  paper_only: true,
  points: [
    { point_id: 'p-1', time: '2026-01-01T00:00:00Z', instrument: 'AAA', score: 0.2, signal: true, raw_weight: 1, risk_scale: 1, final_weight: 1, held_weight: 0, exposure: 1, cash: 0, risk_state: 'NORMAL', trade_weight: 0, fees: 0, slippage: 0, gross_return: 0, net_return: 0, equity: 1, drawdown: 0, fault_events: [] },
  ],
  metrics: { total_return: 0 },
  provenance: { dataset_fingerprint: 'dataset', policy_fingerprint: 'policy' },
  factor_observations: [], signals: [], raw_weights: [], risk_scales: [], final_weights: [], exposure: [], cash: [], risk_states: [], trades: [], costs: [], slippage: [], fault_events: [], explanation_refs: [], baseline_variant_refs: [], limitations: [],
};

test('normalizeWorkbenchPayload validates paper-only server-owned layers', () => {
  const normalized = normalizeWorkbenchPayload(workbench);
  assert.equal(normalized.points[0].held_weight, 0);
  assert.equal(normalized.provenance.policy_fingerprint, 'policy');
  assert.throws(() => normalizeWorkbenchPayload({ ...workbench, paper_only: false }), /paper/i);
  assert.throws(() => normalizeWorkbenchPayload({ ...workbench, points: [{ ...workbench.points[0], net_return: NaN }] }), /finite/i);
});

test('selectWorkbenchPoint and previewWorkbenchParameter keep interaction render-only', () => {
  const normalized = normalizeWorkbenchPayload(workbench);
  assert.equal(selectWorkbenchPoint(normalized, 'p-1').risk_state, 'NORMAL');
  assert.equal(selectWorkbenchPoint(normalized, 'missing'), null);
  const root = { dataset: {} };
  const preview = previewWorkbenchParameter(root, 'lookback', 40);
  assert.deepEqual(preview, { parameter: 'lookback', value: 40, saved: false });
  assert.equal(root.dataset.workbenchPreview, JSON.stringify(preview));
});

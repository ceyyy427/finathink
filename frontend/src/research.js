import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
} from 'lightweight-charts';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import './knowledge.js';
import { mountOverview } from './overview.js';
import { mountProviderSettings, mountRunWorkspace } from './run-workspace.js';
import { mountExperimentWorkspace } from './experiment-workspace.js';
import { mountLearningReview } from './learning-review.js';

echarts.use([LineChart, GridComponent, TooltipComponent, CanvasRenderer]);

const MAX_POINTS = 10_000;
const RESEARCH_RUN_STATES = new Set(['RECEIVED', 'IDENTIFIED', 'DATA_CHECKED', 'ANALYSTS_RUNNING', 'ANALYSTS_READY', 'EVIDENCE_REVIEW', 'RESEARCH_PLAN_READY', 'QUANT_VALIDATION', 'RISK_REVIEW', 'PAPER_DECISION_READY', 'REPORT_PUBLISHED', 'LEARNING_RECORDED', 'REJECTED', 'NO_DATA_AVAILABLE', 'DATA_UNAVAILABLE', 'PROVIDER_NOT_CONFIGURED', 'VALIDATION_FAILED', 'CANCELLED', 'FAILED']);

function humanizeKey(value) {
  const text = String(value).replaceAll('_', ' ');
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

function finite(value, field) {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new TypeError(`${field} must be finite`);
  }
  return value;
}

function requiredText(value, field) {
  if (typeof value !== 'string' || value.trim() === '') throw new TypeError(`${field} is required`);
  return value;
}

export function normalizeResearchRun(payload) {
  if (!payload || payload.schema_version !== 1) throw new TypeError('research run payload schema is invalid');
  if (typeof payload.run_id !== 'string' || payload.run_id.trim() === '') throw new TypeError('research run id is invalid');
  if (!RESEARCH_RUN_STATES.has(payload.state)) throw new TypeError('research run state is invalid');
  if (!['OFFLINE', 'USER_API'].includes(payload.mode) || payload.paper_only !== true) throw new TypeError('research run boundary is invalid');
  if (!Array.isArray(payload.analysts) || payload.analysts.some((item) => !item || typeof item.role !== 'string' || typeof item.status !== 'string')) throw new TypeError('research analyst status is invalid');
  return {
    ...payload,
    analysts: [...payload.analysts].sort((left, right) => left.role.localeCompare(right.role)).map((item) => ({ ...item, limitations: Array.isArray(item.limitations) ? [...item.limitations] : [] })),
    missing_analysts: Array.isArray(payload.missing_analysts) ? [...payload.missing_analysts].sort() : [],
    report_links: payload.report_links && typeof payload.report_links === 'object' ? { ...payload.report_links } : {},
  };
}

export function researchRunStatus(payload, error = null) {
  if (error) return 'ERROR';
  if (!payload) return 'LOADING';
  if (['FAILED', 'VALIDATION_FAILED', 'NO_DATA_AVAILABLE', 'DATA_UNAVAILABLE', 'PROVIDER_NOT_CONFIGURED', 'REJECTED', 'CANCELLED'].includes(payload.state)) return 'BLOCKED';
  if ((payload.missing_analysts ?? []).length > 0) return 'PARTIAL';
  return 'READY';
}

const STATUS_WALL_FORBIDDEN = /(?:api[_-]?key|token|secret|password|credential(?![_-]?ref)|authorization|prompt|raw[_-]?(?:provider[_-]?)?response|endpoint|absolute[_-]?path|file[_-]?path)/i;

function normalizePublicMap(value, field) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError(`${field} is invalid`);
  return Object.fromEntries(Object.entries(value).sort(([left], [right]) => left.localeCompare(right)).map(([key, status]) => {
    if (STATUS_WALL_FORBIDDEN.test(key) || typeof status !== 'string') throw new TypeError(`${field} contains unsafe data`);
    return [key, status];
  }));
}

export function normalizeResearchStatusWall(payload) {
  if (!payload || payload.schema_version !== 1 || typeof payload.run_id !== 'string' || payload.run_id.trim() === '') throw new TypeError('research status wall schema is invalid');
  if (!RESEARCH_RUN_STATES.has(payload.state)) throw new TypeError('research status wall state is invalid');
  const role_status = normalizePublicMap(payload.role_status ?? {}, 'role_status');
  const stage_status = normalizePublicMap(payload.stage_status ?? {}, 'stage_status');
  const checkpoint_status = normalizePublicMap(payload.checkpoint_status ?? {}, 'checkpoint_status');
  const provider_readiness = normalizePublicMap(payload.provider_readiness ?? {}, 'provider_readiness');
  const missing_evidence = Array.isArray(payload.missing_evidence) ? payload.missing_evidence.map((item) => {
    if (typeof item !== 'string' || STATUS_WALL_FORBIDDEN.test(item)) throw new TypeError('missing evidence contains unsafe data');
    return item;
  }) : [];
  const factor_proposals = Array.isArray(payload.factor_proposals) ? payload.factor_proposals.map((item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) throw new TypeError('factor proposals are invalid');
    const summary = {};
    for (const key of ['proposal_id', 'status', 'digest', 'family']) {
      if (Object.hasOwn(item, key)) {
        if (STATUS_WALL_FORBIDDEN.test(key) || typeof item[key] !== 'string') throw new TypeError('factor proposal contains unsafe data');
        summary[key] = item[key];
      }
    }
    return summary;
  }) : [];
  return {
    schema_version: 1,
    run_id: payload.run_id,
    state: payload.state,
    role_status,
    stage_status,
    missing_evidence,
    checkpoint_status,
    factor_proposals,
    provider_readiness,
  };
}

export function renderResearchStatusWall(root, payload) {
  const normalized = normalizeResearchStatusWall(payload);
  if (root?.dataset) {
    root.dataset.statusWall = 'READY';
    root.dataset.status = normalized.state;
  }
  const status = root?.querySelector?.('[data-research-status-wall-state]');
  if (status) status.textContent = `${normalized.state} · READ-ONLY · PAPER-ONLY`;
  const roles = root?.querySelector?.('[data-research-status-wall-roles]');
  if (roles) roles.textContent = Object.entries(normalized.role_status).map(([role, value]) => `${role}: ${value}`).join(' · ');
  const stages = root?.querySelector?.('[data-research-status-wall-stages]');
  if (stages) stages.textContent = Object.entries(normalized.stage_status).map(([stage, value]) => `${stage}: ${value}`).join(' · ');
  return normalized;
}

const RUNTIME_STATES = RESEARCH_RUN_STATES;

function normalizeRuntimeRecord(value, field) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError(`${field} is invalid`);
  const output = {};
  for (const [key, item] of Object.entries(value)) {
    if (STATUS_WALL_FORBIDDEN.test(key) || STATUS_WALL_FORBIDDEN.test(String(item)) || (typeof item !== 'string' && typeof item !== 'number')) throw new TypeError(`${field} contains unsafe data`);
    output[key] = item;
  }
  return output;
}

export function normalizeResearchRuntime(payload) {
  if (!payload || ![2, 3].includes(payload.schema_version) || typeof payload.run_id !== 'string' || payload.run_id.trim() === '') throw new TypeError('research runtime schema is invalid');
  if (!RUNTIME_STATES.has(payload.state)) throw new TypeError('research runtime state is invalid');
  if (!['OFFLINE', 'USER_API'].includes(payload.mode) || payload.paper_only !== true) throw new TypeError('research runtime boundary is invalid');
  const stage_status = normalizePublicMap(payload.stage_status ?? {}, 'stage_status');
  const role_status = normalizePublicMap(payload.role_status ?? {}, 'role_status');
  const checkpoint = normalizeRuntimeRecord(payload.checkpoint ?? { state: 'NOT_ATTACHED' }, 'checkpoint');
  const learning_proposal = normalizeRuntimeRecord(payload.learning_proposal ?? { status: 'NO_LEARNING_UPDATE' }, 'learning proposal');
  const retries = normalizeRuntimeRecord(payload.retries ?? { count: 0 }, 'retries');
  if (!Number.isInteger(retries.count) || retries.count < 0) throw new TypeError('retry count is invalid');
  if (typeof payload.manifest_digest !== 'string' || !/^[a-f0-9]{64}$/i.test(payload.manifest_digest)) throw new TypeError('manifest digest is invalid');
  const tool_summaries = Array.isArray(payload.tool_summaries) ? payload.tool_summaries.map((item) => normalizeRuntimeRecord(item, 'tool summary')) : [];
  const limitations = Array.isArray(payload.limitations) ? payload.limitations.map((item) => {
    if (typeof item !== 'string' || STATUS_WALL_FORBIDDEN.test(item)) throw new TypeError('runtime limitation is unsafe');
    return item;
  }) : [];
  const normalizeStageData = (value, field) => {
    const visit = (item) => {
      if (item === null || typeof item === 'number' || typeof item === 'boolean') return item;
      if (typeof item === 'string') {
        if (STATUS_WALL_FORBIDDEN.test(item)) throw new TypeError(`${field} contains unsafe data`);
        return item;
      }
      if (Array.isArray(item)) return item.map(visit);
      if (!item || typeof item !== 'object') throw new TypeError(`${field} is invalid`);
      return Object.fromEntries(Object.entries(item).map(([key, nested]) => {
        if (STATUS_WALL_FORBIDDEN.test(key)) throw new TypeError(`${field} contains unsafe data`);
        return [key, visit(nested)];
      }));
    };
    return visit(value ?? []);
  };
  const experiments = normalizeStageData(payload.experiments, 'experiments');
  const risk_attribution = normalizeStageData(payload.risk_attribution, 'risk attribution');
  const learning_history = normalizeStageData(payload.learning_history, 'learning history');
  return {
    schema_version: payload.schema_version,
    run_id: payload.run_id,
    state: payload.state,
    mode: payload.mode,
    paper_only: true,
    stage_status,
    role_status,
    checkpoint,
    learning_proposal,
    retries,
    manifest_digest: payload.manifest_digest,
    tool_summaries,
    limitations,
    experiments,
    risk_attribution,
    learning_history,
  };
}

export function renderResearchRuntime(root, payload) {
  const normalized = normalizeResearchRuntime(payload);
  if (root?.dataset) {
    root.dataset.runtimeStatus = 'READY';
    root.dataset.runtimeState = normalized.state;
  }
  const state = root?.querySelector?.('[data-research-runtime-state]');
  if (state) state.textContent = `${normalized.state} · READ-ONLY · PAPER-ONLY`;
  const stage = root?.querySelector?.('[data-research-runtime-stages]');
  if (stage) stage.textContent = Object.entries(normalized.stage_status).map(([name, value]) => `${name}: ${value}`).join(' · ');
  const tools = root?.querySelector?.('[data-research-runtime-tools]');
  if (tools) tools.textContent = normalized.tool_summaries.map((item) => `${item.name ?? item.tool ?? 'tool'}: ${item.status ?? 'RECORDED'}`).join(' · ') || 'No tool summary recorded.';
  const stages = root?.querySelector?.('[data-research-runtime-report-stages]');
  if (stages) {
    const stageNames = { experiments: 'quant', risk_attribution: 'risk', learning_history: 'learning' };
    stages.textContent = ['experiments', 'risk_attribution', 'learning_history'].map((name) => {
      const stageData = normalized[name];
      const dataStatus = stageData && !Array.isArray(stageData) && typeof stageData === 'object' ? stageData.status : null;
      const status = normalized.stage_status[name] ?? normalized.stage_status[stageNames[name]] ?? dataStatus ?? 'PENDING';
      return `${name}: ${status}`;
    }).join(' · ');
  }
  return normalized;
}

export function mountResearchRuntime(root, { payload, payloadUrl } = {}) {
  if (root) root.dataset.runtimeStatus = 'LOADING';
  const load = payload ? Promise.resolve(payload) : fetch(payloadUrl || root.dataset.payloadUrl).then((response) => {
    if (!response.ok) throw new Error(`research runtime request failed (${response.status})`);
    return response.json();
  });
  return load.then((raw) => renderResearchRuntime(root, raw)).catch((error) => {
    if (root) root.dataset.runtimeStatus = 'ERROR';
    const target = root?.querySelector('[data-research-runtime-error]');
    if (target) { target.textContent = `Research runtime unavailable: ${error.message}`; target.hidden = false; }
    throw error;
  });
}

export function renderResearchRun(root, payload) {
  const normalized = normalizeResearchRun(payload);
  root.dataset.state = normalized.state;
  root.dataset.status = researchRunStatus(normalized);
  const status = root.querySelector('[data-research-run-status]');
  if (status) status.textContent = `${researchRunStatus(normalized)} · ${normalized.state} · ${normalized.mode} · PAPER-ONLY`;
  const summary = root.querySelector('[data-research-run-summary]');
  if (summary) summary.textContent = `As-of ${normalized.as_of ?? 'not attached'} · ${normalized.analysts.length} analyst report(s) · ${normalized.decision_eligible ? 'decision eligible' : 'decision blocked'}`;
  if (normalized.role_status || normalized.stage_status) {
    const wall = root.querySelector('[data-research-status-wall]');
    if (wall) renderResearchStatusWall(wall, normalized);
  }
  return normalized;
}

export function mountResearchRun(root, { payload, payloadUrl } = {}) {
  if (root) root.dataset.status = 'LOADING';
  const load = payload ? Promise.resolve(payload) : fetch(payloadUrl || root.dataset.payloadUrl).then((response) => {
    if (!response.ok) throw new Error(`research run request failed (${response.status})`);
    return response.json();
  });
  return load.then((raw) => renderResearchRun(root, raw)).catch((error) => {
    if (root) root.dataset.status = 'ERROR';
    const target = root?.querySelector('[data-research-run-error]');
    if (target) { target.textContent = `Research run unavailable: ${error.message}`; target.hidden = false; }
    throw error;
  });
}

export function normalizePayload(payload) {
  if (!payload || payload.schema_version !== 1) throw new TypeError('chart payload schema is invalid');
  const dataset = payload.dataset;
  if (!dataset || !requiredText(dataset.id, 'dataset id') || !requiredText(dataset.fingerprint, 'dataset fingerprint')) {
    throw new TypeError('dataset provenance is invalid');
  }
  if (!Array.isArray(payload.points) || payload.points.length > MAX_POINTS) throw new TypeError('chart points are invalid or too large');
  const pointIds = new Set();
  const pointTimes = new Set();
  const points = payload.points.map((point, index) => {
    if (!point || !requiredText(point.id, `point ${index} id`) || !requiredText(point.time, `point ${index} time`)) throw new TypeError('chart point identity is invalid');
    const parsed = Date.parse(point.time);
    if (!Number.isFinite(parsed)) throw new TypeError(`point ${index} time is invalid`);
    if (pointIds.has(point.id) || pointTimes.has(parsed)) throw new TypeError('chart point ids and times must be unique');
    pointIds.add(point.id);
    pointTimes.add(parsed);
    for (const field of ['open', 'high', 'low', 'close', 'volume']) finite(point[field], `point ${index} ${field}`);
    if (!point.features || typeof point.features !== 'object' || Array.isArray(point.features)) throw new TypeError('point features are invalid');
    for (const [feature, value] of Object.entries(point.features)) finite(value, `point ${index} feature ${feature}`);
    return { ...point, events: Array.isArray(point.events) ? [...point.events] : [], features: { ...point.features } };
  });
  for (let index = 1; index < points.length; index += 1) {
    if (Date.parse(points[index - 1].time) > Date.parse(points[index].time)) throw new TypeError('chart points must be chronological');
  }
  return {
    schema_version: 1,
    dataset: { ...dataset },
    points,
    features: Array.isArray(payload.features) ? payload.features.map((item) => ({ ...item })) : [],
    events: Array.isArray(payload.events) ? payload.events.map((item) => ({ ...item })) : [],
    sweep: payload.sweep && typeof payload.sweep === 'object' ? { ...payload.sweep } : null,
    factor_research: payload.factor_research && typeof payload.factor_research === 'object' ? { ...payload.factor_research } : null,
    factor_candidates: Array.isArray(payload.factor_candidates) ? payload.factor_candidates.map((item) => ({ ...item })) : [],
    factor_evaluations: Array.isArray(payload.factor_evaluations) ? payload.factor_evaluations.map((item) => ({ ...item })) : [],
    factor_decay: Array.isArray(payload.factor_decay) ? payload.factor_decay.map((item) => ({ ...item })) : [],
    factor_admission: Array.isArray(payload.factor_admission) ? payload.factor_admission.map((item) => ({ ...item })) : [],
    research_rounds: Array.isArray(payload.research_rounds) ? payload.research_rounds.map((item) => ({ ...item })) : [],
    provider_status: payload.provider_status && typeof payload.provider_status === 'object' ? { ...payload.provider_status } : null,
    limitations: Array.isArray(payload.limitations) ? [...payload.limitations] : [],
  };
}

const WORKBENCH_NUMERIC_FIELDS = ['score', 'raw_weight', 'risk_scale', 'final_weight', 'held_weight', 'exposure', 'cash', 'trade_weight', 'fees', 'slippage', 'gross_return', 'net_return', 'equity', 'drawdown'];

export function normalizeWorkbenchPayload(payload) {
  if (!payload || payload.schema_version !== 1 || payload.view !== 'factor-strategy-workbench') throw new TypeError('workbench payload schema is invalid');
  if (typeof payload.run_id !== 'string' || payload.run_id.trim() === '') throw new TypeError('workbench run id is invalid');
  if (payload.paper_only !== true) throw new TypeError('workbench boundary is not paper-only');
  if (!Array.isArray(payload.points) || payload.points.length > MAX_POINTS) throw new TypeError('workbench points are invalid or too large');
  const seen = new Set();
  const points = payload.points.map((point, index) => {
    if (!point || typeof point.point_id !== 'string' || point.point_id.trim() === '' || typeof point.instrument !== 'string') throw new TypeError(`workbench point ${index} identity is invalid`);
    if (seen.has(point.point_id)) throw new TypeError('workbench point ids must be unique');
    seen.add(point.point_id);
    if (!Number.isFinite(Date.parse(point.time))) throw new TypeError(`workbench point ${index} time is invalid`);
    for (const field of WORKBENCH_NUMERIC_FIELDS) finite(point[field], `workbench point ${index} ${field}`);
    if (!Array.isArray(point.fault_events)) throw new TypeError(`workbench point ${index} faults are invalid`);
    return { ...point, fault_events: point.fault_events.map((event) => ({ ...event })) };
  });
  for (let index = 1; index < points.length; index += 1) {
    if (Date.parse(points[index - 1].time) > Date.parse(points[index].time)) throw new TypeError('workbench points must be chronological');
  }
  const layers = ['factor_observations', 'signals', 'raw_weights', 'risk_scales', 'final_weights', 'exposure', 'cash', 'risk_states', 'trades', 'costs', 'slippage', 'fault_events', 'explanation_refs', 'baseline_variant_refs'];
  return {
    ...payload,
    points,
    metrics: payload.metrics && typeof payload.metrics === 'object' ? { ...payload.metrics } : {},
    provenance: payload.provenance && typeof payload.provenance === 'object' ? { ...payload.provenance } : {},
    limitations: Array.isArray(payload.limitations) ? [...payload.limitations] : [],
    ...Object.fromEntries(layers.map((layer) => [layer, Array.isArray(payload[layer]) ? payload[layer].map((item) => ({ ...item })) : []])),
  };
}

export function selectWorkbenchPoint(payload, pointId) {
  return payload?.points?.find((point) => point.point_id === pointId) ?? null;
}

export function previewWorkbenchParameter(root, parameter, value) {
  requiredText(parameter, 'workbench parameter');
  finite(value, 'workbench preview value');
  const preview = { parameter, value, saved: false };
  if (root?.dataset) root.dataset.workbenchPreview = JSON.stringify(preview);
  const target = root?.querySelector?.('[data-workbench-preview]');
  if (target) target.textContent = `Preview only · ${parameter} = ${value} · not saved`;
  return preview;
}

export function renderWorkbench(root, payload) {
  const normalized = normalizeWorkbenchPayload(payload);
  root.dataset.workbenchStatus = 'READY';
  root.setAttribute?.('aria-busy', 'false');
  const loading = root.querySelector('[data-workbench-loading]');
  if (loading) loading.hidden = true;
  root.dataset.workbenchRunId = normalized.run_id;
  const status = root.querySelector('[data-workbench-status]');
  if (status) status.textContent = `PAPER-ONLY · ${normalized.points.length} point(s) · render-only`;
  for (const [key, value] of Object.entries(normalized.metrics)) {
    const target = root.querySelector(`[data-workbench-metric="${CSS.escape(key)}"]`);
    if (target) target.textContent = String(value);
  }
  const announce = (point) => {
    const target = root.querySelector('[data-workbench-inspector]');
    if (target) target.textContent = `${point.time} · ${point.instrument} · score ${point.score} · target ${point.final_weight} · held ${point.held_weight} · risk ${point.risk_state} · net ${point.net_return}`;
    root.querySelectorAll('[data-workbench-row][aria-current="true"]').forEach((row) => row.removeAttribute('aria-current'));
    const row = [...root.querySelectorAll('[data-workbench-row]')].find((candidate) => candidate.dataset.pointId === point.point_id);
    if (row) row.setAttribute('aria-current', 'true');
    root.dispatchEvent(new CustomEvent('finathink:workbench-point-selected', { bubbles: true, detail: { pointId: point.point_id } }));
  };
  root.querySelectorAll('[data-workbench-row]').forEach((row) => {
    const point = selectWorkbenchPoint(normalized, row.dataset.pointId);
    if (!point) return;
    row.addEventListener('click', () => announce(point));
    row.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); announce(point); } });
  });
  root.querySelectorAll('[data-workbench-parameter]').forEach((input) => {
    input.addEventListener('input', () => previewWorkbenchParameter(root, input.dataset.workbenchParameter, Number(input.value)));
  });
  if (normalized.points[0]) announce(normalized.points[0]);
  return normalized;
}

export function mountWorkbench(root, { payload, payloadUrl } = {}) {
  if (root) {
    root.dataset.workbenchStatus = 'LOADING';
    root.setAttribute?.('aria-busy', 'true');
    const loading = root.querySelector('[data-workbench-loading]');
    if (loading) loading.hidden = false;
  }
  const load = payload ? Promise.resolve(payload) : fetch(payloadUrl || root.dataset.payloadUrl).then((response) => {
    if (!response.ok) throw new Error(`workbench request failed (${response.status})`);
    return response.json();
  });
  return load.then((raw) => {
    const result = renderWorkbench(root, raw);
    root.setAttribute?.('aria-busy', 'false');
    const loading = root.querySelector('[data-workbench-loading]');
    if (loading) loading.hidden = true;
    return result;
  }).catch((error) => {
    if (root) root.dataset.workbenchStatus = 'ERROR';
    root?.setAttribute?.('aria-busy', 'false');
    const loading = root?.querySelector('[data-workbench-loading]');
    if (loading) loading.hidden = true;
    const target = root?.querySelector('[data-workbench-error]');
    if (target) { target.textContent = `Workbench unavailable: ${error.message}. Check the local service and retry.`; target.hidden = false; }
    throw error;
  });
}

export function selectPoint(points, pointId) {
  return points.find((point) => point.id === pointId) ?? null;
}

export function formatResearchPoint(point, eventLabels = {}) {
  const featureText = Object.entries(point.features ?? {}).map(([key, value]) => `${humanizeKey(key)} ${value}`).join(', ') || 'none';
  const events = (point.events ?? []).map((event) => eventLabels[event] ?? event).join(', ') || 'none';
  return `${point.time} · Open ${point.open} · High ${point.high} · Low ${point.low} · Close ${point.close} · Volume ${point.volume} · Features ${featureText} · Events ${events}`;
}

function announcePoint(root, point, eventLabels) {
  const summary = formatResearchPoint(point, eventLabels);
  const inspector = root.querySelector('[data-research-inspector]');
  const tooltip = root.querySelector('[data-research-tooltip]');
  if (inspector) inspector.textContent = summary;
  if (tooltip) tooltip.textContent = summary;
  const link = root.querySelector('[data-knowledge-context-link]');
  const status = root.querySelector('[data-knowledge-context-status]');
  if (link) link.href = `/knowledge/volatility?context_type=research_point&context_id=${encodeURIComponent(point.id)}`;
  if (status) status.textContent = `Point-in-time values and evidence are available for ${point.time}.`;
}

function pointTable(root, payload, selectedId = null) {
  const existing = root.querySelector('[data-research-point-table]');
  if (existing) existing.remove();
  const target = root.querySelector('[data-research-table-anchor]') || root;
  const wrapper = document.createElement('div');
  wrapper.dataset.researchPointTable = 'true';
  wrapper.className = 'table-wrap';
  const table = document.createElement('table');
  table.setAttribute('aria-label', 'Research observations');
  const head = document.createElement('thead');
  const headerRow = document.createElement('tr');
  ['Time', 'Close', 'Volume', 'Features', 'Events'].forEach((label) => {
    const cell = document.createElement('th');
    cell.textContent = label;
    headerRow.appendChild(cell);
  });
  head.appendChild(headerRow);
  table.appendChild(head);
  const body = document.createElement('tbody');
  const eventLabels = Object.fromEntries(payload.events.map((event) => [event.id, event.label ?? event.title ?? event.id]));
  payload.points.forEach((point) => {
    const row = document.createElement('tr');
    row.dataset.pointId = point.id;
    row.tabIndex = 0;
    row.setAttribute('role', 'button');
    row.setAttribute('aria-label', formatResearchPoint(point, eventLabels));
    [
      point.time,
      point.close,
      point.volume,
      Object.entries(point.features).map(([key, value]) => `${humanizeKey(key)}: ${value}`).join(', ') || '—',
      point.events.map((event) => eventLabels[event] ?? event).join(', ') || '—',
    ].forEach((value) => {
      const cell = document.createElement('td');
      cell.textContent = String(value);
      row.appendChild(cell);
    });
    row.addEventListener('click', () => selectAndAnnounce(root, payload, point.id));
    row.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectAndAnnounce(root, payload, point.id); }
    });
    if (point.id === selectedId) row.setAttribute('aria-current', 'true');
    body.appendChild(row);
  });
  table.appendChild(body);
  wrapper.appendChild(table);
  target.appendChild(wrapper);
}

function selectAndAnnounce(root, payload, pointId) {
  const point = selectPoint(payload.points, pointId);
  if (!point) return;
  root.querySelectorAll('[data-point-id][aria-current="true"]').forEach((row) => row.removeAttribute('aria-current'));
  const row = [...root.querySelectorAll('[data-point-id]')].find((candidate) => candidate.dataset.pointId === pointId);
  if (row) row.setAttribute('aria-current', 'true');
  const eventLabels = Object.fromEntries(payload.events.map((event) => [event.id, event.label ?? event.title ?? event.id]));
  announcePoint(root, point, eventLabels);
  root.dispatchEvent(new CustomEvent('finathink:point-selected', { bubbles: true, detail: { pointId } }));
}

function renderSweep(root, sweep) {
  if (!sweep || !Array.isArray(sweep.experiments) || !sweep.experiments.length) return;
  const target = root.querySelector('[data-research-sweep]');
  if (!target) return;
  const chart = echarts.init(target, null, { renderer: 'canvas' });
  const rows = sweep.experiments.map((item) => {
    const score = Number(item.oos?.score ?? item.metrics?.oos ?? item.metrics?.score ?? 0);
    return [String(item.parameters?.window ?? item.experiment_id ?? item.index), Number.isFinite(score) ? score : 0];
  });
  chart.setOption({
    animation: !(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches),
    grid: { left: 48, right: 16, top: 20, bottom: 32 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: rows.map((item) => item[0]), name: 'Parameter' },
    yAxis: { type: 'value', name: 'OOS metric' },
    series: [{ type: 'line', data: rows.map((item) => item[1]), smooth: false, lineStyle: { color: '#0b6670' }, itemStyle: { color: '#0b6670' } }],
  });
  const onResize = () => chart.resize();
  window.addEventListener('resize', onResize, { passive: true });
  return () => {
    window.removeEventListener('resize', onResize);
    chart.dispose();
  };
}

export function mountResearch(root, { payload, payloadUrl } = {}) {
  if (root._finathinkCleanup) root._finathinkCleanup();
  root.dataset.researchStatus = 'LOADING';
  root.setAttribute?.('aria-busy', 'true');
  const loading = root.querySelector('[data-research-loading]');
  if (loading) loading.hidden = false;
  const load = payload ? Promise.resolve(payload) : fetch(payloadUrl || root.dataset.payloadUrl).then((response) => {
    if (!response.ok) throw new Error(`research payload request failed (${response.status})`);
    return response.json();
  });
  return load.then((raw) => {
    const normalized = normalizePayload(raw);
    root.dataset.researchStatus = 'READY';
    root.setAttribute?.('aria-busy', 'false');
    if (loading) loading.hidden = true;
    const workbenchRoot = root.querySelector('[data-finathink-workbench]');
    if (workbenchRoot && normalized.workbench) renderWorkbench(workbenchRoot, normalized.workbench);
    const chartContainer = root.querySelector('[data-research-chart]');
    if (chartContainer && normalized.points.length) {
      const chart = createChart(chartContainer, { autoSize: true, layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#40545d' }, grid: { vertLines: { color: '#e3e9e8' }, horzLines: { color: '#e3e9e8' } }, rightPriceScale: { borderColor: '#b5c4c4' }, timeScale: { borderColor: '#b5c4c4', rightOffset: 3 } });
      const candles = chart.addSeries(CandlestickSeries, { upColor: '#2f6b57', downColor: '#a5413e', borderVisible: false, wickUpColor: '#2f6b57', wickDownColor: '#a5413e' });
      candles.setData(normalized.points.map((point) => ({ time: Math.floor(Date.parse(point.time) / 1000), open: point.open, high: point.high, low: point.low, close: point.close })));
      const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: '' });
      volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
      volume.setData(normalized.points.map((point) => ({ time: Math.floor(Date.parse(point.time) / 1000), value: point.volume, color: point.close >= point.open ? '#9cc9b4' : '#e8b0ad' })));
      for (const [featureName, color] of [['return_1d', '#8a5a17'], ['range_pct', '#63528a']]) {
        if (!normalized.points.some((point) => Object.prototype.hasOwnProperty.call(point.features, featureName))) continue;
        const series = chart.addSeries(LineSeries, { title: featureName, color, lineWidth: 1, priceScaleId: featureName });
        series.priceScale().applyOptions({ scaleMargins: { top: 0.72, bottom: 0.05 } });
        series.setData(normalized.points.map((point) => ({ time: Math.floor(Date.parse(point.time) / 1000), value: point.features[featureName] })));
      }
      const markers = normalized.points.flatMap((point) => (point.events ?? []).map((event) => ({ time: Math.floor(Date.parse(point.time) / 1000), position: 'aboveBar', color: '#8a5a17', shape: 'arrowDown', text: event })));
      if (markers.length) createSeriesMarkers(candles, markers);
      chart.subscribeCrosshairMove((param) => {
        const timestamp = param.time;
        const point = normalized.points.find((candidate) => Math.floor(Date.parse(candidate.time) / 1000) === timestamp);
        if (point) selectAndAnnounce(root, normalized, point.id);
      });
      chart.subscribeClick((param) => {
        const timestamp = param.time;
        const point = normalized.points.find((candidate) => Math.floor(Date.parse(candidate.time) / 1000) === timestamp);
        if (point) selectAndAnnounce(root, normalized, point.id);
      });
      root._finathinkChart = chart;
      root._finathinkCleanup = () => chart.remove();
    }
    pointTable(root, normalized, normalized.points[0]?.id ?? null);
    if (normalized.points[0]) selectAndAnnounce(root, normalized, normalized.points[0].id);
    const sweepCleanup = renderSweep(root, normalized.sweep);
    const priorCleanup = root._finathinkCleanup;
    root._finathinkCleanup = () => {
      if (priorCleanup) priorCleanup();
      if (sweepCleanup) sweepCleanup();
    };
    return normalized;
  }).catch((error) => {
    root.dataset.researchStatus = 'ERROR';
    root.setAttribute?.('aria-busy', 'false');
    if (loading) loading.hidden = true;
    const target = root.querySelector('[data-research-error]') || root;
    target.textContent = `Research visualization unavailable: ${error.message}. Check the local service and retry.`;
    target.hidden = false;
    target.setAttribute('role', 'alert');
    throw error;
  });
}

if (typeof document !== 'undefined') {
  document.querySelectorAll('[data-experiment-workspace]').forEach((root) => { mountExperimentWorkspace(root); });
  document.querySelectorAll('[data-learning-workspace]').forEach((root) => { mountLearningReview(root); });
  document.querySelectorAll('[data-provider-form]').forEach((form) => { mountProviderSettings(form); });
  document.querySelectorAll('[data-run-workspace]').forEach((root) => { mountRunWorkspace(root); });
  document.querySelectorAll('[data-finathink-overview]').forEach((root) => { mountOverview(root).catch(() => {}); });
  document.querySelectorAll('[data-finathink-research]').forEach((root) => { mountResearch(root); });
  document.querySelectorAll('[data-research-run]').forEach((root) => { mountResearchRun(root).catch(() => {}); });
  document.querySelectorAll('[data-research-runtime]').forEach((root) => { mountResearchRuntime(root).catch(() => {}); });
  document.querySelectorAll('[data-finathink-workbench]').forEach((root) => { if (!root.closest('[data-finathink-research]')) mountWorkbench(root).catch(() => {}); });
}

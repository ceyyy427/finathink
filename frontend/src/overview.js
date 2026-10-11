import * as echarts from 'echarts/core';
import { BarChart } from 'echarts/charts';
import { GridComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';

echarts.use([BarChart, GridComponent, TooltipComponent, CanvasRenderer]);

const UNSAFE_PUBLIC_FIELD = /(?:api[_-]?key|token|secret|password|credential(?![_-]?ref)|authorization|prompt|raw[_-]?(?:provider[_-]?)?response|endpoint|absolute[_-]?path|file[_-]?path)/i;
const ABSOLUTE_PATH = /^(?:[A-Za-z]:[\\/]|\\\\|\/Users\/|\/home\/|\/tmp\/)/;

function assertPublic(value, field = 'payload') {
  if (typeof value === 'string') {
    if (UNSAFE_PUBLIC_FIELD.test(value) || ABSOLUTE_PATH.test(value)) throw new TypeError(`${field} contains unsafe data`);
    return value;
  }
  if (Array.isArray(value)) return value.map((item, index) => assertPublic(item, `${field}[${index}]`));
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => {
      if (UNSAFE_PUBLIC_FIELD.test(key)) throw new TypeError(`${field} contains unsafe data`);
      return [key, assertPublic(item, `${field}.${key}`)];
    }));
  }
  return value;
}

function nonNegativeInteger(value, field) {
  if (!Number.isInteger(value) || value < 0) throw new TypeError(`${field} must be a non-negative integer`);
  return value;
}

export function normalizeOverviewPayload(payload) {
  if (!payload || payload.schema_version !== 1) throw new TypeError('overview payload schema is invalid');
  if (!['OFFLINE', 'LOCAL'].includes(payload.mode) || payload.paper_only !== true) throw new TypeError('overview boundary is invalid');
  assertPublic(payload);
  if (typeof payload.as_of !== 'string' || payload.as_of.trim() === '') throw new TypeError('overview as_of is invalid');
  const metrics = payload.metrics;
  if (!metrics || typeof metrics !== 'object' || Array.isArray(metrics)) throw new TypeError('overview metrics are invalid');
  const normalizedMetrics = Object.fromEntries(Object.entries(metrics).map(([key, value]) => [key, nonNegativeInteger(value, `metrics.${key}`)]));
  const normalizeRows = (value, field) => {
    if (!Array.isArray(value)) throw new TypeError(`${field} is invalid`);
    return value.map((item, index) => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) throw new TypeError(`${field}[${index}] is invalid`);
      return { ...item };
    });
  };
  const activity = normalizeRows(payload.activity ?? [], 'activity').sort((left, right) => String(left.timestamp ?? '').localeCompare(String(right.timestamp ?? '')));
  const topics = normalizeRows(payload.topics ?? [], 'topics').map((item, index) => {
    if (Object.hasOwn(item, 'count') && (!Number.isInteger(item.count) || item.count < 0)) throw new TypeError(`topics[${index}].count is invalid`);
    return { ...item };
  });
  const learning_activity = normalizeRows(payload.learning_activity ?? [], 'learning_activity');
  const normalizeSeries = (value, field) => normalizeRows(value ?? [], field).map((item, index) => {
    for (const key of ['count', 'learning', 'research']) {
      if (Object.hasOwn(item, key) && (!Number.isInteger(item[key]) || item[key] < 0)) throw new TypeError(`${field}[${index}].${key} is invalid`);
    }
    return { ...item };
  });
  const activity_series = normalizeSeries(payload.activity_series, 'activity_series').sort((left, right) => String(left.date ?? '').localeCompare(String(right.date ?? '')));
  const topic_heatmap = normalizeRows(payload.topic_heatmap ?? topics, 'topic_heatmap').map((item, index) => {
    if (Object.hasOwn(item, 'count') && (!Number.isInteger(item.count) || item.count < 0)) throw new TypeError(`topic_heatmap[${index}].count is invalid`);
    return { ...item };
  });
  const research_comparison = normalizeRows(payload.research_comparison ?? [], 'research_comparison');
  return {
    schema_version: 1,
    mode: payload.mode,
    paper_only: true,
    as_of: payload.as_of,
    metrics: normalizedMetrics,
    activity,
    activity_series,
    topics,
    topic_heatmap,
    learning_activity,
    research_comparison,
    research_status: assertPublic(payload.research_status ?? {}, 'research_status'),
    limitations: assertPublic(Array.isArray(payload.limitations) ? payload.limitations : [], 'limitations'),
    provenance: assertPublic(payload.provenance ?? {}, 'provenance'),
  };
}

export function overviewStatus(payload, error = null) {
  if (error) return 'ERROR';
  if (!payload) return 'LOADING';
  const values = Object.values(payload.metrics ?? {});
  const empty = values.length === 0 || values.every((value) => value === 0);
  if (empty && !(payload.activity?.length || payload.activity_series?.length || payload.topics?.length || payload.learning_activity?.length || payload.research_comparison?.length)) return 'EMPTY';
  return 'READY';
}

function reducedMotion() {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function setHidden(target, hidden) {
  if (target) target.hidden = hidden;
}

function bindOverviewTooltips(root) {
  const live = root?.querySelector?.('[data-overview-tooltip]');
  if (!live) return;
  root.querySelectorAll?.('[data-tooltip]').forEach((target) => {
    if (target.dataset.tooltipBound === 'true') return;
    const announce = () => { live.textContent = target.dataset.tooltip || ''; };
    target.addEventListener('mouseenter', announce);
    target.addEventListener('focus', announce);
    target.dataset.tooltipBound = 'true';
  });
}

export function renderOverview(root, payload) {
  const normalized = normalizeOverviewPayload(payload);
  if (root?.dataset) {
    root.dataset.overviewStatus = overviewStatus(normalized);
    root.dataset.animation = reducedMotion() ? 'reduced' : 'enabled';
    root.setAttribute?.('aria-busy', 'false');
  }
  setHidden(root?.querySelector?.('[data-overview-loading]'), true);
  setHidden(root?.querySelector?.('[data-overview-error]'), true);
  const chart = root?.querySelector?.('[data-overview-chart]');
  if (chart?.dataset) {
    chart.dataset.chartReady = normalized.activity.length ? 'ready' : 'empty';
    chart.setAttribute?.('tabindex', '0');
    chart.setAttribute?.('aria-label', normalized.activity.length ? `${normalized.activity.length} recorded activity item(s); use the text summary below` : 'No recorded activity; use the text summary below');
  }
  const topics = root?.querySelector?.('[data-overview-topics]');
  if (topics?.dataset) topics.dataset.topicCount = String(normalized.topics.length);
  renderOverviewVisuals(root, normalized);
  bindOverviewTooltips(root);
  return normalized;
}

export function renderOverviewVisuals(root, normalized) {
  const chart = root?.querySelector?.('[data-overview-chart]');
  if (chart?.dataset) chart.dataset.chartReady = normalized.activity_series.length ? 'ready' : 'empty';
  const topics = root?.querySelector?.('[data-overview-topics]');
  if (topics?.dataset) topics.dataset.heatmapReady = normalized.topic_heatmap.length ? 'ready' : 'empty';
  const learning = root?.querySelector?.('[data-overview-heatmap]');
  if (learning?.dataset) learning.dataset.heatmapReady = normalized.learning_activity.length ? 'ready' : 'empty';
  const comparison = root?.querySelector?.('[data-overview-comparison]');
  if (comparison?.dataset) comparison.dataset.comparisonReady = normalized.research_comparison.length ? 'ready' : 'empty';
  if (typeof document === 'undefined' || !chart) return normalized;
  const chartCanvas = chart.querySelector?.('.overview-chart');
  if (chartCanvas && normalized.activity_series.length) {
    chartCanvas.querySelector?.('.overview-chart-bars')?.setAttribute('hidden', 'hidden');
    const instance = echarts.init(chartCanvas, null, { renderer: 'canvas' });
    instance.setOption({
      animation: !reducedMotion(),
      grid: { top: 20, right: 16, bottom: 32, left: 42 },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: normalized.activity_series.map((item) => item.date ?? '—'), axisLabel: { color: '#71819A' } },
      yAxis: { type: 'value', minInterval: 1, axisLabel: { color: '#71819A' }, splitLine: { lineStyle: { color: 'rgba(167, 139, 250, .16)' } } },
      series: [{ type: 'bar', name: 'Recorded activity', data: normalized.activity_series.map((item) => item.count ?? 0), itemStyle: { color: '#38BDF8', borderRadius: [4, 4, 0, 0] } }],
    });
    root._finathinkOverviewChart = instance;
  }
  const bars = chart.querySelector?.('[data-overview-activity-series]');
  if (bars && normalized.activity_series.length) {
    bars.setAttribute('data-point-count', String(normalized.activity_series.length));
    bars.setAttribute('aria-label', normalized.activity_series.map((item) => `${item.date ?? 'unknown'}: ${item.count ?? 0} recorded`).join('; '));
  }
  return normalized;
}

export function mountOverview(root, { payload, payloadUrl } = {}) {
  if (!root) return Promise.reject(new TypeError('overview root is required'));
  root.dataset.overviewStatus = 'LOADING';
  root.setAttribute?.('aria-busy', 'true');
  setHidden(root.querySelector?.('[data-overview-loading]'), false);
  setHidden(root.querySelector?.('[data-overview-error]'), true);
  const load = payload ? Promise.resolve(payload) : fetch(payloadUrl || root.dataset.overviewPayloadUrl || root.dataset.payloadUrl).then((response) => {
    if (!response.ok) throw new Error(`overview request failed (${response.status})`);
    return response.json();
  });
  return load.then((raw) => renderOverview(root, raw)).catch((error) => {
    root.dataset.overviewStatus = 'ERROR';
    root.setAttribute?.('aria-busy', 'false');
    setHidden(root.querySelector?.('[data-overview-loading]'), true);
    const target = root.querySelector?.('[data-overview-error]');
    if (target) { target.textContent = `Overview unavailable: ${error.message}. Check the local service and retry.`; target.hidden = false; }
    throw error;
  });
}

if (typeof document !== 'undefined') {
  document.querySelectorAll('[data-finathink-overview]').forEach((root) => { mountOverview(root).catch(() => {}); });
}

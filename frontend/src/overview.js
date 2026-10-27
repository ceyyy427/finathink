import * as echarts from 'echarts/core';
import { BarChart } from 'echarts/charts';
import { GridComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { readLocalJSON, userErrorMessage } from './local-request.js';

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
  root?._finathinkOverviewCleanup?.();
  if (root?.dataset) {
    root.dataset.overviewStatus = overviewStatus(normalized);
    root.dataset.animation = reducedMotion() ? 'reduced' : 'enabled';
    root.setAttribute?.('aria-busy', 'false');
  }
  setHidden(root?.querySelector?.('[data-overview-loading]'), true);
  setHidden(root?.querySelector?.('[data-overview-error]'), true);
  setHidden(root?.querySelector?.('[data-overview-retry]'), true);
  root?.querySelectorAll?.('[data-overview-metric]')?.forEach((target) => {
    const key = target.dataset.overviewMetric;
    target.textContent = Object.hasOwn(normalized.metrics, key) ? String(normalized.metrics[key]) : '—';
  });
  const tooltip = root?.querySelector?.('[data-overview-tooltip]');
  if (tooltip) tooltip.textContent = '聚焦日期或主题，查看对应的记录数量。';
  const history = root?.querySelector?.('[data-overview-activity]');
  if (history && typeof document !== 'undefined') {
    const records = normalized.activity.length ? normalized.activity : [null];
    history.replaceChildren(...records.map((item) => {
      const row = document.createElement('li');
      const description = document.createElement('span');
      const status = document.createElement('strong');
      if (item) {
        description.textContent = `${item.timestamp ?? '—'} · ${item.label ?? '研究活动'}`;
        status.textContent = item.status ?? 'RECORDED';
      } else {
        row.className = 'empty-state';
        description.textContent = '还没有保存的研究活动';
        status.textContent = '暂无记录';
      }
      row.append(description, status);
      return row;
    }));
  }
  const chart = root?.querySelector?.('[data-overview-chart]');
  if (chart?.dataset) {
    chart.dataset.chartReady = normalized.activity.length ? 'ready' : 'empty';
    chart.setAttribute?.('tabindex', '0');
    chart.setAttribute?.('aria-label', normalized.activity.length ? `${normalized.activity.length} 条活动；可查看下方文字记录` : '暂无活动；可查看下方文字记录');
  }
  const topics = root?.querySelector?.('[data-overview-topics]');
  if (topics?.dataset) topics.dataset.topicCount = String(normalized.topics.length);
  const tableBody = root?.querySelector?.('[data-overview-daily-rows]');
  if (tableBody && typeof document !== 'undefined') {
    const rows = normalized.activity_series.map((item) => {
      const row = document.createElement('tr');
      const heading = document.createElement('th');
      heading.scope = 'row';
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'overview-day';
      button.dataset.overviewDay = '';
      button.dataset.date = item.date;
      button.dataset.tooltip = `${item.date}：${item.count} 条记录 · ${item.learning} 条学习 · ${item.research} 条研究`;
      button.setAttribute('aria-describedby', 'overview-tooltip');
      button.textContent = item.date;
      heading.appendChild(button);
      row.appendChild(heading);
      for (const key of ['count', 'learning', 'research']) {
        const cell = document.createElement('td');
        cell.textContent = String(item[key]);
        row.appendChild(cell);
      }
      return row;
    });
    if (!rows.length) {
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 4;
      cell.textContent = '还没有带日期的活动记录。可先保存研究，或完成一次学习自查。';
      row.appendChild(cell);
      rows.push(row);
    }
    tableBody.replaceChildren(...rows);
  }
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
  const chartCanvas = chart.querySelector?.('[data-overview-canvas]');
  const bars = chart.querySelector?.('[data-overview-activity-series]');
  if (!normalized.activity_series.length) {
    setHidden(chartCanvas, true);
    setHidden(bars, false);
    if (bars) bars.textContent = '还没有带日期的活动记录。';
  }
  if (chartCanvas && normalized.activity_series.length) {
    chartCanvas.hidden = false;
    const instance = echarts.init(chartCanvas, null, { renderer: 'canvas' });
    instance.setOption({
      animation: !reducedMotion(),
      grid: { top: 20, right: 16, bottom: 32, left: 42 },
      tooltip: { trigger: 'axis', confine: true },
      xAxis: { type: 'category', data: normalized.activity_series.map((item) => item.date ?? '—'), axisLabel: { color: '#62728a' } },
      yAxis: { type: 'value', minInterval: 1, axisLabel: { color: '#62728a' }, splitLine: { lineStyle: { color: '#e6ecf4' } } },
      series: [{ type: 'bar', name: '记录数量', data: normalized.activity_series.map((item) => item.count ?? 0), itemStyle: { color: '#2563eb', borderRadius: [4, 4, 0, 0] } }],
    });
    setHidden(chart.querySelector?.('[data-overview-activity-series]'), true);
    root._finathinkOverviewChart = instance;
    const resize = () => instance.resize();
    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(resize) : null;
    observer?.observe(chartCanvas);
    window.addEventListener('resize', resize, { passive: true });
    const motion = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    const updateMotion = () => {
      root.dataset.animation = motion.matches ? 'reduced' : 'enabled';
      instance.setOption({ animation: !motion.matches });
    };
    motion?.addEventListener('change', updateMotion);
    const cleanup = (event) => {
      if (event?.persisted) return;
      observer?.disconnect();
      window.removeEventListener('resize', resize);
      window.removeEventListener('pagehide', cleanup);
      motion?.removeEventListener('change', updateMotion);
      instance.dispose();
      root._finathinkOverviewChart = null;
      root._finathinkOverviewCleanup = null;
    };
    root._finathinkOverviewCleanup = cleanup;
    window.addEventListener('pagehide', cleanup);
  }
  if (bars && normalized.activity_series.length) {
    bars.setAttribute('data-point-count', String(normalized.activity_series.length));
    bars.setAttribute('aria-label', normalized.activity_series.map((item) => `${item.date ?? '未知日期'}：${item.count ?? 0} 条记录`).join('；'));
  }
  return normalized;
}

export function mountOverview(root, { payload, payloadUrl } = {}) {
  if (!root) return Promise.reject(new TypeError('overview root is required'));
  if (root._finathinkOverviewMount) return root._finathinkOverviewMount;
  root.dataset.overviewStatus = 'LOADING';
  root.setAttribute?.('aria-busy', 'true');
  setHidden(root.querySelector?.('[data-overview-loading]'), false);
  setHidden(root.querySelector?.('[data-overview-error]'), true);
  setHidden(root.querySelector?.('[data-overview-retry]'), true);
  const load = payload ? Promise.resolve(payload) : readLocalJSON((url, options) => fetch(url, options), payloadUrl || root.dataset.overviewPayloadUrl || root.dataset.payloadUrl);
  root._finathinkOverviewMount = load.then((raw) => renderOverview(root, raw)).catch((error) => {
    root._finathinkOverviewMount = null;
    root.dataset.overviewStatus = 'ERROR';
    root.setAttribute?.('aria-busy', 'false');
    setHidden(root.querySelector?.('[data-overview-loading]'), true);
    const target = root.querySelector?.('[data-overview-error]');
    if (target) { target.textContent = `总览暂时无法更新。${userErrorMessage(error)}`; target.hidden = false; }
    setHidden(root.querySelector?.('[data-overview-retry]'), false);
    throw error;
  });
  return root._finathinkOverviewMount;
}

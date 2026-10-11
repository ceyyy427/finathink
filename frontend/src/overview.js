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
  const topics = normalizeRows(payload.topics ?? [], 'topics');
  const learning_activity = normalizeRows(payload.learning_activity ?? [], 'learning_activity');
  return {
    schema_version: 1,
    mode: payload.mode,
    paper_only: true,
    as_of: payload.as_of,
    metrics: normalizedMetrics,
    activity,
    topics,
    learning_activity,
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
  if (empty && !(payload.activity?.length || payload.topics?.length || payload.learning_activity?.length)) return 'EMPTY';
  return 'READY';
}

function reducedMotion() {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export function renderOverview(root, payload) {
  const normalized = normalizeOverviewPayload(payload);
  if (root?.dataset) {
    root.dataset.overviewStatus = overviewStatus(normalized);
    root.dataset.animation = reducedMotion() ? 'reduced' : 'enabled';
  }
  const chart = root?.querySelector?.('[data-overview-chart]');
  if (chart?.dataset) {
    chart.dataset.chartReady = normalized.activity.length ? 'ready' : 'empty';
    chart.setAttribute?.('tabindex', '0');
    chart.setAttribute?.('aria-label', normalized.activity.length ? `${normalized.activity.length} recorded activity item(s); use the text summary below` : 'No recorded activity; use the text summary below');
  }
  const topics = root?.querySelector?.('[data-overview-topics]');
  if (topics?.dataset) topics.dataset.topicCount = String(normalized.topics.length);
  return normalized;
}

export function mountOverview(root, { payload, payloadUrl } = {}) {
  if (!root) return Promise.reject(new TypeError('overview root is required'));
  root.dataset.overviewStatus = 'LOADING';
  const load = payload ? Promise.resolve(payload) : fetch(payloadUrl || root.dataset.overviewPayloadUrl || root.dataset.payloadUrl).then((response) => {
    if (!response.ok) throw new Error(`overview request failed (${response.status})`);
    return response.json();
  });
  return load.then((raw) => renderOverview(root, raw)).catch((error) => {
    root.dataset.overviewStatus = 'ERROR';
    const target = root.querySelector?.('[data-overview-error]');
    if (target) { target.textContent = `Overview unavailable: ${error.message}`; target.hidden = false; }
    throw error;
  });
}

if (typeof document !== 'undefined') {
  document.querySelectorAll('[data-finathink-overview]').forEach((root) => { mountOverview(root).catch(() => {}); });
}

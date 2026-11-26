// The atlas navigates server-computed values. All formula and validation work stays in Python.
import { readLocalJSON, userErrorMessage } from './local-request.js';

const mounted = new WeakSet();
const studyStates = {
  DECLARED: '章程已保存，等待验证',
  VALIDATING: '章程已保存，正在进行样本内验证',
  VALIDATED: '样本内验证已完成',
  VALIDATION_FAILED: '样本内验证未完成',
  FROZEN: '候选已冻结',
  TEST_RESERVED: '已预留样本外检验',
  TESTING: '样本外检验进行中',
  TEST_EVALUATED: '样本外检验已完成',
  TEST_FAILED: '样本外检验未完成',
  REVIEWED: '研究记录已复核',
};

function mountEvidence(root) {
  const graph = [...root.querySelectorAll('[data-atlas-node]')];
  const inspector = root.querySelector('[data-atlas-inspector]');
  const viewport = root.querySelector('[data-atlas-viewport]');
  if (!graph.length || !inspector || !viewport) return;
  const win = root.ownerDocument.defaultView;
  const stage = root.querySelector('[data-atlas-stage]');
  const zoomButtons = [...root.querySelectorAll('[data-atlas-zoom]')];
  let scale = 100;
  function select(link, moveFocus = false) {
    const key = link.dataset.atlasNode;
    const template = root.querySelector(`template[data-atlas-inspector-for="${key}"]`);
    if (!template) return;
    for (const anchor of graph) anchor.setAttribute('aria-current', String(anchor.dataset.atlasNode === key));
    inspector.replaceChildren(template.content.cloneNode(true));
    root.querySelectorAll('input[name="node"]').forEach((field) => { field.value = key; });
    win.history.replaceState(win.history.state, '', link.getAttribute('href'));
    if (moveFocus) link.focus({ preventScroll: true });
  }
  const next = {
    snapshot: { ArrowRight: 'close', ArrowDown: 'close' },
    close: { ArrowLeft: 'snapshot', ArrowRight: 'factor', ArrowDown: 'lag', ArrowUp: 'snapshot' },
    lag: { ArrowLeft: 'snapshot', ArrowRight: 'factor', ArrowUp: 'close', ArrowDown: 'factor' },
    factor: { ArrowLeft: 'close', ArrowUp: 'lag', ArrowRight: 'hypothesis', ArrowDown: 'hypothesis' },
    hypothesis: { ArrowLeft: 'factor', ArrowUp: 'factor' },
  };
  graph.forEach((link) => {
    link.addEventListener('click', (event) => {
      if (event.button || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
      event.preventDefault();
      select(link);
    });
    link.addEventListener('keydown', (event) => {
      if (event.key === ' ') { event.preventDefault(); select(link); return; }
      const target = next[link.dataset.atlasNode][event.key]
        || (event.key === 'Home' ? 'snapshot' : event.key === 'End' ? 'hypothesis' : null);
      if (!target) return;
      event.preventDefault();
      select(link.closest('svg').querySelector(`[data-atlas-node="${target}"]`), true);
    });
  });
  zoomButtons.forEach((button) => button.addEventListener('click', () => {
    const before = stage.getBoundingClientRect().width;
    const centerX = viewport.scrollLeft + viewport.clientWidth / 2;
    const centerY = viewport.scrollTop + viewport.clientHeight / 2;
    scale = Math.max(70, Math.min(160, scale + Number(button.dataset.atlasZoom) * 15));
    stage.style.width = `${scale}%`;
    const ratio = before ? stage.getBoundingClientRect().width / before : 1;
    viewport.scrollLeft = centerX * ratio - viewport.clientWidth / 2;
    viewport.scrollTop = centerY * ratio - viewport.clientHeight / 2;
    root.querySelector('[data-atlas-zoom-label]').textContent = `${scale}%`;
    for (const item of zoomButtons) item.disabled = Number(item.dataset.atlasZoom) < 0 ? scale === 70 : scale === 160;
  }));
}

function mountChart(root) {
  const chart = root.querySelector('[data-atlas-chart]');
  if (!chart) return;
  const points = JSON.parse(chart.dataset.points);
  const plots = [...chart.querySelectorAll('[data-atlas-plot]')];
  const evidence = root.querySelector('[data-atlas-evidence-link]');
  let selected = Number(chart.dataset.initial);
  function show(index) {
    selected = Math.max(0, Math.min(points.length - 1, index));
    const [stamp, first, second] = points[selected];
    const text = `${stamp.slice(0, 10)}，${chart.dataset.short} 期动量 ${first.toFixed(4)}，${chart.dataset.long} 期动量 ${second.toFixed(4)}`;
    for (const plot of plots) {
      const { left, right, top, bottom, low, high, tooltipWidth } = plot.dataset;
      const x = Number(left) + selected / Math.max(1, points.length - 1) * (right - left);
      const toY = (value) => bottom - (value - low) / (high - low) * (bottom - top);
      const line = plot.querySelector('[data-cursor-line]');
      line.setAttribute('x1', x); line.setAttribute('x2', x);
      for (const [selector, value] of [['[data-short-marker]', first], ['[data-long-marker]', second]]) {
        const marker = plot.querySelector(selector);
        marker.setAttribute('cx', x); marker.setAttribute('cy', toY(value));
      }
      const tw = Number(tooltipWidth);
      const tx = Math.max(Number(left) + 4, Math.min(right - tw - 4, x + tw + 16 < right ? x + 12 : x - tw - 12));
      plot.querySelector('[data-tooltip]').setAttribute('transform', `translate(${tx},${Number(top) + 3})`);
      plot.querySelector('[data-tooltip-date]').textContent = stamp.slice(0, 10);
      plot.querySelector('[data-tooltip-short]').textContent = first.toFixed(3);
      plot.querySelector('[data-tooltip-long]').textContent = second.toFixed(3);
      plot.setAttribute('aria-valuenow', selected + 1);
      plot.setAttribute('aria-valuetext', text);
    }
    const url = new URL(evidence.getAttribute('href'), root.ownerDocument.baseURI);
    url.searchParams.set('at', stamp);
    evidence.setAttribute('href', url.pathname + url.search);
  }
  plots.forEach((plot) => {
    function pointAt(event) {
      const bounds = plot.getBoundingClientRect();
      if (!bounds.width) return;
      const x = (event.clientX - bounds.left) * plot.viewBox.baseVal.width / bounds.width;
      const index = Math.round((x - plot.dataset.left) / (plot.dataset.right - plot.dataset.left) * (points.length - 1));
      if (index !== selected) show(index);
    }
    plot.addEventListener('pointermove', pointAt);
    plot.addEventListener('pointerdown', pointAt);
    plot.addEventListener('keydown', (event) => {
      const positions = { ArrowLeft: selected - 1, ArrowRight: selected + 1, ArrowUp: selected + 1,
        ArrowDown: selected - 1, PageUp: selected + 10, PageDown: selected - 10, Home: 0, End: points.length - 1 };
      if (!Object.hasOwn(positions, event.key)) return;
      event.preventDefault();
      show(positions[event.key]);
    });
  });
}

function mountFactors(root, fetcher, interval) {
  const doc = root.ownerDocument;
  const win = doc.defaultView;
  const form = root.querySelector('[data-atlas-parameters]');
  const run = form.querySelector('[data-atlas-run]');
  const refresh = form.querySelector('[data-atlas-refresh]');
  const preview = root.querySelector('[data-atlas-preview]');
  const result = root.querySelector('[data-atlas-result]');
  const sourceNote = root.querySelector('[data-atlas-source-note]');
  const fields = [...form.querySelectorAll('input,select')];
  const values = () => Object.fromEntries(fields.map((field) => [field.name, field.value]));
  let current = new URLSearchParams(values()).toString();
  let revision = 0;
  let previewTimer;
  let pollTimer;
  let controller;
  let saving = false;
  refresh.hidden = true;
  mountChart(root);

  function stop() {
    revision++;
    win.clearTimeout(previewTimer);
    win.clearTimeout(pollTimer);
    controller?.abort();
  }
  function announce(message, error = false) {
    result.setAttribute('role', error ? 'alert' : 'status');
    result.textContent = message;
  }
  function resetControls() {
    form.elements.long.setCustomValidity(Number(form.elements.short.value) >= Number(form.elements.long.value)
      ? '长窗口须大于短窗口。' : '');
    run.disabled = saving || !form.checkValidity() || !root.querySelector('[data-atlas-chart]');
  }
  async function updatePreview() {
    stop();
    const version = revision;
    resetControls();
    if (!form.reportValidity()) return false;
    const parameters = new URLSearchParams(values());
    const next = parameters.toString();
    if (next === current && root.querySelector('[data-atlas-chart]')) return true;
    controller = new AbortController();
    preview.setAttribute('aria-busy', 'true');
    run.disabled = true;
    announce('正在按所选参数重新计算图表…');
    try {
      const target = `/research/atlas/factors?${parameters}`;
      const response = await fetcher(target, { credentials: 'same-origin', signal: controller.signal });
      if (!response.ok) throw new Error('暂时无法读取图表，请检查所选输入后重新预览。');
      const page = new win.DOMParser().parseFromString(await response.text(), 'text/html');
      if (version !== revision) return false;
      const fragment = page.querySelector('[data-atlas-preview]');
      const note = page.querySelector('[data-atlas-source-note]');
      if (!fragment || !note) throw new Error('本地图表内容不完整，请重新读取。');
      preview.replaceChildren(...[...fragment.childNodes].map((node) => doc.importNode(node, true)));
      sourceNote.textContent = note.textContent;
      current = next;
      win.history.replaceState(win.history.state, '', target);
      refresh.hidden = true;
      mountChart(root);
      const valid = Boolean(root.querySelector('[data-atlas-chart]'));
      announce(valid ? '图表已按当前参数更新。' : '请调整输入后重新计算。', !valid);
      return valid;
    } catch (error) {
      if (version === revision && error.name !== 'AbortError') {
        refresh.hidden = false;
        announce(userErrorMessage(error), true);
      }
      return false;
    } finally {
      if (version === revision) { preview.removeAttribute('aria-busy'); resetControls(); }
    }
  }
  function edited() {
    if (saving) return;
    stop();
    preview.removeAttribute('aria-busy');
    resetControls();
    if (new URLSearchParams(values()).toString() === current && root.querySelector('[data-atlas-chart]')) {
      announce('');
      return;
    }
    run.disabled = true;
    announce('参数已改变，正在更新图表。');
    if (form.checkValidity()) previewTimer = win.setTimeout(updatePreview, 180);
    else announce('请检查回看窗口及费用。', true);
  }
  form.addEventListener('input', edited);
  form.addEventListener('change', edited);

  function showStudy(studyId, state, summary = '') {
    const link = doc.createElement('a');
    link.href = `/research/experiments?study_id=${encodeURIComponent(studyId)}`;
    link.textContent = '查看验证记录 ↗';
    announce(`${studyStates[state] || '已保存研究记录'}。${summary}`);
    result.append(link);
  }
  async function watch(studyId, snapshotRef, version) {
    try {
      const study = await readLocalJSON(fetcher, `/api/research/experiments/${studyId}`);
      if (version !== revision) return;
      if (study.study_id !== studyId || study.spec?.snapshot_ref !== snapshotRef || study.production_write !== false) {
        throw new Error('返回的实验绑定与当前记录不一致。');
      }
      const metrics = (study.validation?.summaries || []).filter((item) => !item.failure_reason
        && typeof item.metrics?.information_coefficient === 'number');
      const summary = metrics.map((item) => `${item.parameters.window} 期 IC ${item.metrics.information_coefficient.toFixed(3)}`).join('，');
      showStudy(studyId, study.state, summary);
      if (['DECLARED', 'VALIDATING'].includes(study.state)) pollTimer = win.setTimeout(() => watch(studyId, snapshotRef, version), interval);
    } catch (error) {
      if (version === revision) showStudy(studyId, '', `读取进度时遇到问题。${userErrorMessage(error)}`);
    }
  }
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (saving) return;
    const submitStudy = event.submitter === run;
    if (!await updatePreview() || !submitStudy) return;
    stop();
    const version = revision;
    const input = values();
    saving = true;
    fields.forEach((field) => { field.disabled = true; });
    run.disabled = true;
    announce('正在保存当前参数并提交样本内验证…');
    try {
      const study = await readLocalJSON(fetcher, run.dataset.atlasRun, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...input, _csrf: run.value }),
      });
      if (version !== revision) return;
      if (!/^study-[a-f0-9]{24}$/.test(study.study_id) || study.snapshot_ref !== input.dataset.split('~')[0]) {
        throw new Error('返回的实验与所选快照不一致，请打开实验记录核对。');
      }
      showStudy(study.study_id, study.state);
      pollTimer = win.setTimeout(() => watch(study.study_id, study.snapshot_ref, version), interval);
    } catch (error) {
      if (version === revision) announce(userErrorMessage(error), true);
    } finally {
      saving = false;
      fields.forEach((field) => { field.disabled = false; });
      resetControls();
    }
  });
  resetControls();
  win.addEventListener('pagehide', stop);
}

export function mountAtlasLabs(root, { fetcher = globalThis.fetch, interval = 1400 } = {}) {
  if (!root || mounted.has(root)) return;
  mounted.add(root);
  root.dataset.atlasEnhanced = 'true';
  if (root.dataset.atlasLab === 'evidence') mountEvidence(root);
  if (root.dataset.atlasLab === 'factors') mountFactors(root, fetcher, interval);
}

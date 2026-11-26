const studioPath = '/research/strategy-validation';
const terminalStatuses = new Set(['completed', 'failed', 'cancelled']);

function watchReceipt(root, pending) {
  if (!pending || pending.dataset.refreshMounted) return;
  const receipt = new URL(pending.dataset.svRefresh, window.location.href);
  const keys = Array.from(receipt.searchParams.keys());
  if (receipt.origin !== window.location.origin || receipt.pathname !== studioPath ||
      keys.length !== 1 || !['study', 'source_job'].includes(keys[0])) return;
  const progress = new URL('/api' + studioPath + '/progress', receipt.origin);
  progress.search = receipt.search;
  pending.dataset.refreshMounted = 'true';
  let timer;
  let request;
  let active = true;
  const phase = pending.querySelector('[data-sv-phase]');

  function canRead() {
    return active && document.visibilityState === 'visible' && !root.querySelector('.sv-reconfigure[open]');
  }

  function schedule() {
    clearTimeout(timer);
    if (active && !request && document.visibilityState === 'visible') timer = setTimeout(check, 4000);
  }

  async function check() {
    if (!canRead()) {
      schedule();
      return;
    }
    const controller = new AbortController();
    request = controller;
    const deadline = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(progress, {cache: 'no-store', signal: controller.signal});
      if (!response.ok) return;
      const {status} = await response.json();
      if (controller.signal.aborted || !canRead()) return;
      if (typeof status === 'string' && phase && Object.hasOwn(phase.dataset, status)) {
        const label = phase.dataset[status];
        if (phase.textContent !== label) phase.textContent = label;
      }
      if (terminalStatuses.has(status)) {
        active = false;
        window.location.replace(receipt.href);
      }
    } catch {
      // The visible receipt and manual link stay usable through a transport failure.
    } finally {
      clearTimeout(deadline);
      request = undefined;
      schedule();
    }
  }

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible') request?.abort();
    schedule();
  });
  window.addEventListener('pagehide', () => {
    active = false;
    clearTimeout(timer);
    request?.abort();
  });
  window.addEventListener('pageshow', () => {
    active = true;
    schedule();
  });
  schedule();
}

// The HTML table remains the source for the keyboard and pointer readout.
export function mountStrategyValidation(root) {
  watchReceipt(root, root.querySelector('[data-sv-refresh]'));
  for (const figure of root.querySelectorAll('[data-sv-curve]')) {
    const controls = figure.querySelector('[data-sv-scrub]');
    if (!controls || !controls.hidden) continue;
    const input = controls.querySelector('input');
    const output = controls.querySelector('output');
    const cursor = figure.querySelector('[data-sv-cursor]');
    const plot = figure.querySelector('svg');
    const rows = Array.from(figure.querySelectorAll('tbody tr'));
    if (!rows.length) continue;

    function inspect(index) {
      const [date, equity, benchmark] = Array.from(rows[index].cells, cell => cell.textContent);
      const reading = date + ' · 策略 ' + equity + (benchmark === '—' ? '' : ' · 基准 ' + benchmark);
      const x = index / Math.max(1, rows.length - 1) * 800;
      cursor.setAttribute('x1', x);
      cursor.setAttribute('x2', x);
      cursor.removeAttribute('hidden');
      input.value = String(index);
      input.setAttribute('aria-valuetext', reading);
      output.textContent = reading;
    }

    function inspectPointer(event) {
      const rect = plot.getBoundingClientRect();
      if (rect.width <= 0) return;
      const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
      inspect(Math.round(ratio * (rows.length - 1)));
    }

    input.addEventListener('input', () => inspect(input.valueAsNumber));
    plot.addEventListener('pointermove', event => {
      if (event.pointerType !== 'touch') inspectPointer(event);
    });
    plot.addEventListener('click', inspectPointer);
    controls.hidden = false;
    inspect(rows.length - 1);
  }
}

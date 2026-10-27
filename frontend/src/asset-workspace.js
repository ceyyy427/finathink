import { readLocalJSON, userErrorMessage } from './local-request.js';
const mounted = new WeakMap();
const KINDS = new Set(['methods', 'behavior', 'lessons']);
const AVAILABILITY = new Set(['PACKAGED', 'PARTIAL', 'CORRUPT', 'MISSING']);

export function normalizeAssetPage(value) {
  if (value?.schema_version !== 'asset-workspace.v1' || !KINDS.has(value.kind) || !Number.isInteger(value.total) || value.total < 0 || !Number.isInteger(value.limit) || value.limit < 1 || value.limit > 100 || !Number.isInteger(value.offset) || value.offset < 0 || !Array.isArray(value.items) || value.items.length > value.limit || value.total < value.items.length) throw new TypeError('资料目录响应无效，请重新读取。');
  for (const item of value.items) if (!/^[a-z0-9][a-z0-9._-]{0,159}$/.test(item?.asset_id ?? '') || typeof item.title !== 'string' || !item.title || item.title.length > 500 || item.kind !== value.kind || !AVAILABILITY.has(item.availability) || item.loaded !== false || !Array.isArray(item.tags) || item.tags.some((tag) => typeof tag !== 'string' || tag.length > 64)) throw new TypeError('资料身份或状态不一致，不能显示为已加载。');
  return value;
}

export function assetQuery(values) {
  if (!values || Object.keys(values).some((key) => !['kind', 'q', 'offset', 'limit', 'tag'].includes(key)) || !KINDS.has(values.kind)) throw new TypeError('资料检索只接受有界查询条件。');
  return new URLSearchParams(Object.entries(values).map(([key, value]) => [key, String(value)])).toString();
}

export function mountAssetWorkspace(root, { fetcher = globalThis.fetch } = {}) {
  if (mounted.has(root)) return mounted.get(root);
  const form = root.querySelector('[data-asset-form]'); const status = root.querySelector('[data-asset-status]');
  const results = root.querySelector('[data-asset-results]'); const retry = root.querySelector('[data-asset-retry]');
  const doc = root.ownerDocument; let controller; let generation = 0; let disposed = false;
  const node = (tag, text) => { const item = doc.createElement(tag); if (text !== undefined) item.textContent = text; return item; };
  async function load() {
    const current = ++generation; controller?.abort(); controller = new AbortController();
    root.dataset.assetState = 'loading'; root.setAttribute('aria-busy', 'true'); status.setAttribute('role', 'status'); status.textContent = '正在读取本地资料…'; retry.hidden = true;
    try {
      const query = assetQuery({ kind: root.dataset.assetKind, q: form.elements.q.value, offset: 0 });
      const data = normalizeAssetPage(await readLocalJSON(fetcher, `/api/research/assets?${query}`, { signal: controller.signal }));
      if (disposed || current !== generation) return;
      const entries = data.items.map((item) => {
        const article = node('article'); article.className = 'asset-entry';
        const state = node('span', { PACKAGED: '方法资料已打包', PARTIAL: '资料待补齐', CORRUPT: '资料校验失败', MISSING: '资料缺失' }[item.availability]); state.className = 'asset-state';
        const heading = node('h2'); const link = node('a', item.title); link.href = `/library/${item.kind}?asset=${encodeURIComponent(item.asset_id)}`; heading.append(link);
        const meta = node('p', `v${item.version} · ${item.tags.join(' / ')} · `); meta.append(state);
        article.append(heading, node('p', item.summary), meta);
        const details = node('details'); details.append(node('summary', '输入、来源与依赖'), node('p', `所需输入：${(item.required_inputs ?? []).join('、') || '反例适用的研究上下文'}`), node('p', `资料缺口：${item.missing_dependencies?.join('；') || '包内资料可读，用户输入仍需绑定。'}`)); article.append(details);
        if (item.kind !== 'lessons') { const enter = node('a', '带入研究问题'); enter.href = `/research/jobs?${new URLSearchParams({ question: `使用「${item.title}」提出可验证的研究假设；先检查所需输入与来源。` })}`; article.append(enter); }
        return article;
      });
      results.replaceChildren(...(entries.length ? entries : [node('p', '没有匹配资料。可清空条件后重试。')]));
      const pagination = root.querySelector('[data-asset-pagination]'); pagination.replaceChildren();
      if (data.offset + data.items.length < data.total) { const next = node('a', '下一页'); next.href = `/library/${data.kind}?${new URLSearchParams({ q: form.elements.q.value, offset: data.offset + data.limit, limit: data.limit })}`; pagination.append(next); }
      status.textContent = `找到 ${data.total} 项，当前显示 ${data.items.length} 项。`; root.dataset.assetState = entries.length ? 'ready' : 'empty';
    } catch (error) {
      if (disposed || current !== generation || error.name === 'AbortError') return;
      root.dataset.assetState = 'error'; status.setAttribute('role', 'alert'); status.textContent = `${userErrorMessage(error)} 检索条件和原结果已保留。`; retry.hidden = false;
    } finally { if (current === generation && !disposed) root.setAttribute('aria-busy', 'false'); }
  }
  form.addEventListener('submit', (event) => { event.preventDefault(); load(); }); retry.addEventListener('click', load);
  const result = { dispose() { disposed = true; generation++; controller?.abort(); mounted.delete(root); } };
  mounted.set(root, result); return result;
}

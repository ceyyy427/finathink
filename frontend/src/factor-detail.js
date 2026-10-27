import { readLocalJSON, userErrorMessage } from './local-request.js';
const mounted = new WeakMap();

export function mountFactorDetail(root, { fetcher = globalThis.fetch } = {}) {
  if (mounted.has(root)) return mounted.get(root);
  const status = root.querySelector('[data-factor-action-status]'); const csrf = root.dataset.factorCsrf;
  let busy = false;
  const send = async (kind, values) => {
    if (busy) return;
    busy = true; root.setAttribute('aria-busy', 'true'); status.setAttribute('role', 'status'); status.textContent = '正在校验本地证据并记录…';
    try {
      const result = await readLocalJSON(fetcher, `/api/research/factors/${encodeURIComponent(root.dataset.factorId)}/${kind}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...values, _csrf: csrf }) });
      status.textContent = kind === 'decisions' ? `已记录 ${result.event_ref}。重新读取页面可查看不可变历史；没有生产写入。` : `观察状态：${result.proposal.status}。${result.learning_review ? '已创建待审核研究注记，请前往复盘与学习检查。' : '没有创建调权或风险修改。'}`;
    } catch (error) { status.setAttribute('role', 'alert'); status.textContent = `${userErrorMessage(error)} 输入已保留，请核对原报告和范围后重试。`; }
    finally { busy = false; root.setAttribute('aria-busy', 'false'); }
  };
  root.querySelector('[data-factor-review-form]')?.addEventListener('submit', (event) => {
    event.preventDefault(); if (busy) return; const form = event.currentTarget;
    const intent = JSON.stringify([form.elements.scope.value, form.elements.reviewer.value, form.elements.decision.value]);
    let key = form.dataset.reviewKey;
    if (!key || form.dataset.reviewIntent !== intent) { key = `factor-review-${globalThis.crypto.randomUUID()}`; form.dataset.reviewKey = key; form.dataset.reviewIntent = intent; }
    send('decisions', { ...JSON.parse(form.elements.scope.value), reviewer: form.elements.reviewer.value, decision: form.elements.decision.value, idempotency_key: key });
  });
  root.querySelector('[data-factor-monitor-form]')?.addEventListener('submit', (event) => { event.preventDefault(); send('monitor', { as_of: event.currentTarget.elements.as_of.value }); });
  const result = { dispose() { mounted.delete(root); } }; mounted.set(root, result); return result;
}

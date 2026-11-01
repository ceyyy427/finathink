// Submission only: financial evidence is computed by the existing local worker.
import { readLocalJSON, userErrorMessage } from './local-request.js';
const mounted = new WeakSet();
export function mountFactorLibrary(form, { fetcher = globalThis.fetch } = {}) {
  if (mounted.has(form)) return;
  mounted.add(form);
  const status = form.parentElement.querySelector('[data-library-status]');
  const button = form.querySelector('button[type=submit]');
  let requestKey; let requestSignature;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (button.disabled) return;
    button.disabled = true;
    status.textContent = '正在校验固定输入并提交…';
    try {
      const fields = new FormData(form);
      const signature = JSON.stringify([...fields]);
      if (signature !== requestSignature) {
        requestSignature = signature;
        requestKey = 'library-' + crypto.randomUUID();
      }
      const data = await readLocalJSON(fetcher, '/api/research/factor-library/studies', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ _csrf: fields.get('csrf'), factor_id: fields.get('factor_id'),
          factor_version: fields.get('factor_version'), question: fields.get('question'),
          contract: JSON.parse(fields.get('contract')), attachment_refs: JSON.parse(fields.get('attachment_refs')),
          idempotency_key: requestKey }),
      });
      if (!/^study-[a-zA-Z0-9_-]+$/.test(data.study_id ?? '') || data.production_write !== false) throw new Error('研究回执格式有误，请在因子实验历史中检查提交结果。');
      const document = form.ownerDocument;
      status.replaceChildren(document.createTextNode('研究已提交。'));
      const link = document.createElement('a');
      link.href = '/research/experiments';
      link.textContent = '进入因子实验历史查看任务与报告';
      status.append(link);
    } catch (error) {
      status.textContent = error instanceof SyntaxError ? 'JSON 格式有误，请检查面板合同和附件引用数组。' : userErrorMessage(error);
    } finally { button.disabled = false; }
  });
}

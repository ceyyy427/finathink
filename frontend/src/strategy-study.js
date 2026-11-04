import { readLocalJSON, userErrorMessage } from './local-request.js';

const mounted = new WeakSet();

export function mountStrategyStudy(form) {
  if (mounted.has(form)) return;
  mounted.add(form);
  const button = form.querySelector('button[type=submit]');
  const status = form.querySelector('[data-strategy-status]');
  let signature;
  let requestKey;

  const submit = (url, body) => readLocalJSON(fetch, url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (button.disabled) return;
    button.disabled = true;
    status.textContent = '正在校验策略输入…';
    try {
      const values = new FormData(form);
      const next = JSON.stringify([...values]);
      const csrf = values.get('csrf');
      const parameters = JSON.parse(values.get('parameters'));
      if (next !== signature) {
        signature = next;
        requestKey = 'strategy-' + crypto.randomUUID();
      }
      const data = values.get('mode') === 'sample'
        ? await submit('/api/research/strategies/sample', { _csrf: csrf })
        : { contract: JSON.parse(values.get('contract')), attachment_refs: JSON.parse(values.get('attachment_refs')) };
      if (JSON.stringify([...new FormData(form)]) !== next) {
        status.textContent = '输入已改变，请核对后重新提交。';
        return;
      }
      const result = await submit('/api/research/strategies', {
        _csrf: csrf,
        idempotency_key: requestKey,
        template_id: values.get('template_id'),
        contract: data.contract,
        attachment_refs: data.attachment_refs,
        parameters,
      });
      if (typeof result.job_id !== 'string' || !/^[a-zA-Z0-9_-]+$/.test(result.job_id)) {
        throw new Error('任务回执无效，请检查后台研究。');
      }
      status.textContent = '任务已提交。';
      const link = form.ownerDocument.createElement('a');
      link.href = '/research/jobs?job=' + encodeURIComponent(result.job_id);
      link.textContent = '打开后台任务';
      status.append(' ', link);
    } catch (error) {
      status.textContent = error instanceof SyntaxError ? 'JSON 格式有误，请检查合同、附件或参数。' : userErrorMessage(error);
    } finally {
      button.disabled = false;
    }
  });
}

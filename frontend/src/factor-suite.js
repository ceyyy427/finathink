import { readLocalJSON, userErrorMessage } from './local-request.js';
const mounted = new WeakSet();
export function mountFactorSuite(form) {
  if (mounted.has(form)) return;
  mounted.add(form);
  const root = form.closest('.suite-page');
  const style = form.ownerDocument.createElement('style');
  style.textContent = '.suite-choices label{display:flex;align-items:flex-start;gap:8px;line-height:1.4;font-weight:500}.suite-choices input[type=checkbox]{width:18px;min-width:18px;max-width:18px;height:18px;min-height:18px;margin:1px 0;flex:none}';
  root.prepend(style);
  const csrf = form.querySelector('[name=csrf]').value;
  let key; let signature;
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('[type=submit]');
    if (button.disabled) return;
    const status = form.querySelector('[data-suite-status]');
    button.disabled = true; status.textContent = '正在登记与校验输入…';
    try {
      const fields = new FormData(form);
      const ids = fields.getAll('factor');
      if (ids.length < 1 || ids.length > 24) throw Error('请选1至24个候选，其他候选可在同一试验家族后续提交。');
      const next = JSON.stringify([...fields]);
      if (next !== signature) { signature = next; key = 'suite-' + crypto.randomUUID(); }
      const data = { _csrf: csrf, idempotency_key: key, family_key: fields.get('family_key'),
        factor_ids: ids, question: fields.get('question') };
      if (fields.get('mode') === 'sample') data.sample_spec = JSON.parse(root.querySelector('[data-suite-default]').textContent);
      else { data.contract = JSON.parse(fields.get('contract')); data.attachment_refs = JSON.parse(fields.get('attachments')); }
      const result = await readLocalJSON(fetch, '/api/research/factor-suites', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
      if (!/^study-[a-zA-Z0-9_-]+$/.test(result.study_id ?? '')) throw Error('研究回执无效，请检查实验历史。');
      if (JSON.stringify([...new FormData(form)]) !== next) { status.textContent = '输入已改变，已提交任务可在实验历史查看。'; return; }
      location.assign('/research/factor-suites?study=' + encodeURIComponent(result.study_id));
    } catch (error) { status.textContent = error instanceof SyntaxError ? 'JSON 格式有误，请检查面板合同与附件引用。' : userErrorMessage(error); }
    finally { button.disabled = false; }
  });
  root.querySelector('[data-suite-refresh]')?.addEventListener('click', () => location.reload());
  root.querySelectorAll('[data-suite-action]').forEach(action => {
    let actionKey;
    action.addEventListener('submit', async event => {
      event.preventDefault(); const button = action.querySelector('button'); if (button.disabled) return;
      button.disabled = true; const status = root.querySelector('[data-suite-action-status]');
      status.textContent = '正在提交研究操作…';
      try {
        const study = new URL(location.href).searchParams.get('study');
        const kind = action.dataset.suiteAction;
        const body = { _csrf: csrf };
        if (kind === 'freeze') body.candidate_id = new FormData(action).get('candidate_id');
        if (kind === 'test') { actionKey ||= 'test-' + crypto.randomUUID(); body.idempotency_key = actionKey; }
        const url = kind === 'cancel' ? '/api/research/jobs/' + encodeURIComponent(action.dataset.job) + '/cancel' : '/api/research/experiments/' + encodeURIComponent(study) + '/' + kind;
        await readLocalJSON(fetch, url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        location.reload();
      } catch (error) { status.textContent = userErrorMessage(error); button.disabled = false; }
    });
  });
}

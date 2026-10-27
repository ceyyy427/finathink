import { readLocalJSON, userErrorMessage } from './local-request.js';

export function parseMethodResponse(text) {
  if (typeof text !== 'string' || new TextEncoder().encode(text).length > 131072) throw new TypeError('回传 JSON 最多128 KiB。');
  const value = JSON.parse(text);
  if (value?.schema_version !== 'method-result.v1' || !/^[0-9a-f]{64}$/.test(value.package_digest ?? '')) throw new TypeError('请提供完整的 method-result.v1 绑定结果。');
  return value;
}

export function mountMethodHandoff(root, { fetcher = globalThis.fetch, csrf } = {}) {
  const q = (selector) => root.querySelector(selector); const status = q('[data-method-status]');
  let job; let handoff; let attachment; let busy = false; let studyIntent; let studyKey;
  const post = (url, values) => readLocalJSON(fetcher, url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...values, _csrf: csrf }) });
  function controls() {
    root.setAttribute('aria-busy', String(busy)); q('[data-method-export]').disabled = busy || !job;
    q('[data-method-accept]').disabled = busy || !handoff; q('[data-method-fields-save]').disabled = busy || !job;
    q('[data-method-study-create]').disabled = busy || !handoff || !q('[name="artifact_ref"]').value;
  }
  async function action(operation) {
    if (busy || !job) return; busy = true; controls(); status.setAttribute('role', 'status');
    const current = job.job_id;
    try { await operation(current); }
    catch (error) { status.setAttribute('role', 'alert'); status.textContent = `${userErrorMessage(error)} 草稿已保留；请核对任务与包指纹后重试。`; }
    finally { busy = false; controls(); }
  }
  q('[data-method-export]').addEventListener('click', () => action(async (id) => {
    const result = await post(`/api/research/jobs/${encodeURIComponent(id)}/methods`, { purposes: ['factor-mining'],
      market: job.instrument?.includes(':') ? job.instrument.split(':')[0] : 'UNSPECIFIED',
      available_fields: ['open', 'high', 'low', 'close', 'volume'] });
    if (job.job_id !== id) return;
    handoff = result.handoff_ref; q('[data-method-package]').value = JSON.stringify(result.package, null, 2);
    status.textContent = `交接包已冻结 · 方法 ${result.package.manifest.methods_digest} · 自动 Codex 驱动：未验证；尚未执行提案。`;
  }));
  q('[data-method-result-form]').addEventListener('submit', (event) => {
    event.preventDefault(); action(async (id) => {
      const response = parseMethodResponse(event.currentTarget.elements.response.value);
      const result = await post(`/api/research/jobs/${encodeURIComponent(id)}/methods/${encodeURIComponent(handoff)}/result`, { response });
      if (job.job_id !== id) return;
      const select = q('[name="artifact_ref"]');
      select.replaceChildren(...result.artifacts.map((artifact) => { const option = root.ownerDocument.createElement('option'); option.value = artifact.artifact_ref; option.textContent = `${artifact.artifact_type} · ${artifact.content_digest}`; return option; }));
      status.textContent = `回传已校验并保存 · ${result.outcome.status} · ${result.artifacts.length} 个类型化产物；还没有运行回测。`;
    });
  });
  q('[data-method-fields-form]').addEventListener('submit', (event) => {
    event.preventDefault(); const file = event.currentTarget.elements.attachment.files[0];
    action(async (id) => {
      if (!file || file.size > 10 * 1024 * 1024) throw new TypeError('字段附件需要10 MiB以内的 JSON 文件。');
      const payload = JSON.parse(await file.text());
      const result = await post(`/api/research/jobs/${encodeURIComponent(id)}/research-fields`, { attachment: payload });
      if (job.job_id !== id) return;
      attachment = result.attachment_ref; status.textContent = `已保存原快照的字段附件 ${attachment}；状态 ${result.status}，没有补造数据。`;
    });
  });
  q('[data-method-study-form]').addEventListener('submit', (event) => {
    event.preventDefault(); action(async (id) => {
      const artifact = event.currentTarget.elements.artifact_ref.value;
      const intent = JSON.stringify([id, handoff, artifact, attachment]);
      if (intent !== studyIntent) { studyIntent = intent; studyKey = `method-study-${globalThis.crypto.randomUUID()}`; }
      const result = await post(`/api/research/jobs/${encodeURIComponent(id)}/method-studies`, {
        handoff_ref: handoff, artifact_ref: artifact, attachment_ref: attachment ?? null, idempotency_key: studyKey });
      if (job.job_id !== id) return;
      const link = q('[data-method-study-link]'); link.hidden = false; link.href = `/research/experiments?study_id=${encodeURIComponent(result.study_id)}`;
      status.textContent = `已固定章程 ${result.study_id} · ${result.state}。真实阶段和数据阻断在因子实验中查看。`;
    });
  });
  q('[name="artifact_ref"]').addEventListener('change', controls);
  controls();
  return { setJob(value) {
    if (job?.job_id !== value.job_id) {
      handoff = undefined; attachment = undefined; studyIntent = undefined; studyKey = undefined;
      q('[data-method-package]').value = ''; q('[name="artifact_ref"]').replaceChildren(); q('[data-method-study-link]').hidden = true;
      status.textContent = `${value.run_id} 已选定。回传草稿保留，但引用需要重新导出并校验。`;
    }
    job = value; controls();
  } };
}

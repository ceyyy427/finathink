import { readLocalJSON, userErrorMessage } from './local-request.js';
import { parseMethodResponse } from './method-handoff.js';
import {
  API, jobApi, jobUrl, label, node, renderHandoffList, renderHistory, renderJob,
  renderPacket, renderPreview,
} from './model-workbench-view.js';

const activeJobs = new Set(['queued', 'running', 'external_waiting', 'retryable']);
const activeOperations = new Set(['AUTHORIZED', 'DISPATCHED']);

export function workerCommand(origin, job, operation) {
  const local = new URL(origin);
  if (!['http:', 'https:'].includes(local.protocol) || !/^[a-z0-9.[\]:-]+$/i.test(local.host)
    || ![job, operation].every(v => typeof v === 'string' && /^[\w.:-]+$/.test(v))) {
    throw new Error('执行器任务标识无效，请重新读取交接包。');
  }
  return 'finathink-codex --run --url ' + local.origin + ' --job ' + job + ' --operation ' + operation;
}

export function boundResponse(text, digest) {
  let response;
  try { response = parseMethodResponse(text, 256); }
  catch { throw new Error('请粘贴有效的 method-result.v1 JSON，最多 256 KiB，并保留完整的包指纹。'); }
  if (response.package_digest !== digest) throw new Error('回传中的包指纹与所选交接包不同，请核对后再提交。');
  return response;
}

export function mountModelWorkbench(root, { fetcher = globalThis.fetch } = {}) {
  if (root.dataset.mwReady === 'true') return;
  const q = name => root.querySelector('[data-mw-' + name + ']');
  const form = q('compose');
  const draft = form.elements;
  const submit = q('submit');
  const consent = submit.elements.authorized;
  const worker = q('worker');
  const workerConsent = worker.elements.authorized;
  const manual = q('manual');
  const csrf = draft._csrf.value;
  const params = new URLSearchParams(window.location.search);
  let jobId = params.get('job') || '';
  let handoffRef = params.get('handoff') || '';
  let job, packet, preview, draftKey;
  let busy = false, consumed = false, generation = 0, timer;
  let materialCount = 0, lastProviderWasOffline = true;
  const read = path => readLocalJSON(fetcher, path);
  const post = (path, body) => readLocalJSON(fetcher, path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...body, _csrf: csrf }),
  });
  const operationsApi = () => jobApi(jobId) + '/executions';
  const packetApi = ref => jobApi(jobId) + '/handoffs/' + encodeURIComponent(ref);
  const isActive = () => activeJobs.has(String(job?.status || '').toLowerCase());
  const canManual = () => packet && !packet.operation_id && packet.status === 'AWAITING_AUTHORIZATION';

  function message(channel, text, error = false) {
    const target = q(channel + '-status');
    target.setAttribute('role', error ? 'alert' : 'status');
    target.textContent = text;
  }

  function controls() {
    root.setAttribute('aria-busy', String(busy));
    q('draft-fields').disabled = busy;
    q('preview-button').disabled = busy || !draft.snapshot_ref.value;
    q('add').disabled = busy || materialCount >= 16 || draft.model_config_ref.value === 'offline';
    const ready = preview?.status === 'READY' && !consumed && !busy;
    consent.disabled = !ready;
    submit.querySelector('button').disabled = !ready || !consent.checked;
    q('refresh').disabled = busy;
    q('cancel').disabled = busy || !isActive();
    q('freeze').querySelector('button').disabled = busy || !job;
    q('freeze').elements.purpose.disabled = busy || !job;
    q('download').disabled = q('copy').disabled = busy || !packet;
    manual.elements.response.disabled = busy || !canManual();
    manual.querySelector('button').disabled = busy || !canManual() || !manual.elements.response.value.trim();
    const canAuthorize = !busy && canManual() && packet.eligible === true;
    workerConsent.disabled = !canAuthorize;
    worker.querySelector('button[type=submit]').disabled = !canAuthorize || !workerConsent.checked;
    q('worker-cancel').disabled = busy || !activeOperations.has(packet?.status);
  }

  function invalidate() {
    const had = Boolean(preview);
    preview = null; consumed = false; draftKey = null; consent.checked = false;
    q('preview').hidden = true;
    if (had) message('compose', '研究设置已修改，请重新预览并核对材料。');
    controls();
  }

  function selectSnapshot() {
    const option = draft.snapshot_ref.selectedOptions[0];
    const instruments = JSON.parse(option?.dataset.instruments || '[]');
    const choices = instruments.map(symbol => {
      const item = node('option', symbol); item.value = symbol; return item;
    });
    if (instruments.length !== 1) {
      const empty = node('option', '请选择标的'); empty.value = ''; choices.unshift(empty);
    }
    draft.instrument.replaceChildren(...choices);
    draft.as_of.value = option?.dataset.asOf || '';
  }

  function selectProvider(resetBudget) {
    const selected = draft.model_config_ref.selectedOptions[0];
    const offline = selected.value === 'offline';
    const models = JSON.parse(selected.dataset.models || '{}');
    if (resetBudget && lastProviderWasOffline !== offline) draft.max_provider_calls.value = offline ? '1' : '6';
    lastProviderWasOffline = offline;
    q('material-note').textContent = offline ?
      '选择用户模型后可补充带来源的摘要。离线研究只使用本地行情快照。' :
      '可按角色添加最多 16 条带来源的摘要。可获得时间须在研究截止日以内。';
    q('mode').textContent = offline ? '离线' : '用户模型';
    q('provider-note').textContent = offline ? '本地规则处理所选快照。研究经理汇总各分析师引用的材料。' :
      '接口配置' + (selected.dataset.status ? ' · ' + label(selected.dataset.status) : '') + '。预览会核对每个角色的材料与预算。';
    for (const el of root.querySelectorAll('[data-mw-role]')) {
      el.textContent = offline ? '本地规则' : models[el.dataset.mwRole] || selected.dataset.model || '未配置';
    }
  }

  function requestBody() {
    if (draft.model_config_ref.value === 'offline' && materialCount) {
      throw new Error('已有角色摘要。请选择用户模型，或移除摘要后再预览离线研究。');
    }
    const materials = [...q('materials').querySelectorAll('[data-mw-material]')].map(row => {
      const fields = row.elements;
      if (fields.material_instrument.value.trim() !== draft.instrument.value) {
        throw new Error('材料标的与研究标的不同，请核对来源或移除这条材料。');
      }
      const observed = fields.observed_at.value + 'Z', available = fields.available_at.value + 'Z';
      if (Date.parse(observed) > Date.parse(available) || fields.available_at.value.slice(0, 10) > draft.as_of.value) {
        throw new Error('观测时间须早于或等于可获得时间，两者均须在研究截止日以内。');
      }
      return {
        role: fields.material_role.value, instrument: draft.instrument.value, source_label: fields.source_label.value.trim(),
        summary: fields.summary.value.trim(), observed_at: observed, available_at: available, authorized: fields.material_authorized.checked,
      };
    });
    draftKey ||= 'model-workbench-' + globalThis.crypto.randomUUID();
    return {
      snapshot_ref: draft.snapshot_ref.value, instrument: draft.instrument.value, as_of: draft.as_of.value,
      question: draft.question.value.trim(), model_config_ref: draft.model_config_ref.value, materials,
      budget: { max_wall_seconds: Number(draft.max_wall_seconds.value), max_provider_calls: Number(draft.max_provider_calls.value) },
      idempotency_key: draftKey,
    };
  }

  function displayJob(value) {
    job = value;
    renderJob(q('job'), value);
    q('job-actions').hidden = false;
    q('job-json').href = jobApi(jobId);
    const report = q('report');
    report.hidden = !(typeof value.report_url === 'string' && /^\/research\/[\w/%.?=&:-]+$/.test(value.report_url));
    if (!report.hidden) report.href = value.report_url;
  }

  function displayPacket(value) {
    if (packet?.package_digest !== value?.package_digest || packet?.status !== value?.status) {
      workerConsent.checked = false;
    }
    if (packet?.package_digest !== value?.package_digest) manual.elements.response.value = '';
    packet = value;
    q('handoff').hidden = !packet;
    if (!packet) return;
    renderPacket(q('packet-facts'), q('worker-state'), packet);
    q('manual-note').textContent = canManual()
      ? '粘贴 Codex 生成的 method-result.v1 结果。服务器会核对任务、包指纹、证据归属与产物类型。'
      : (packet.status === 'MANUAL' ? '该包的手动回传已保存。可在上方查看产物与证据。' :
        '此包已锁定手动回传。请查看交接状态与执行器记录。');
    if (typeof packet.packet_json !== 'string') throw new Error('未读到冻结包的原始文本，请刷新任务记录。');
    q('packet').value = packet.packet_json;
    const showCommand = packet.status === 'AUTHORIZED' && Boolean(packet.operation_id);
    q('command-block').hidden = !showCommand;
    q('command').value = showCommand ? workerCommand(window.location.origin, jobId, packet.operation_id) : '';
  }

  async function loadSelected(current, requestedRef = handoffRef) {
    if (!jobId) return;
    const [result, list] = await Promise.all([read(jobApi(jobId)), read(jobApi(jobId) + '/handoffs')]);
    if (current !== generation) return;
    displayJob(result);
    const records = list.handoffs;
    const target = requestedRef || records[0]?.handoff_ref || '';
    renderHandoffList(q('handoffs'), jobId, records, target);
    if (target) {
      const value = await read(packetApi(target));
      if (current !== generation) return;
      handoffRef = target; displayPacket(value);
    } else {
      handoffRef = ''; displayPacket(null);
      message('handoff', '已选中研究任务，可按用途冻结交接包。');
    }
  }

  async function loadHistory(current) {
    const result = await read(API);
    if (current === generation) renderHistory(q('history'), result.history, jobId);
  }

  function remember(ref = '') {
    const url = new URL(jobUrl(jobId, ref), window.location.origin);
    window.history.replaceState(null, '', url.pathname + url.search + url.hash);
  }

  function schedule() {
    clearTimeout(timer);
    if (busy || document.hidden || !jobId || (!isActive() && !activeOperations.has(packet?.status))) return;
    timer = setTimeout(async () => {
      const current = generation;
      try {
        if (isActive()) {
          const result = await read(jobApi(jobId));
          if (current !== generation) return;
          if (JSON.stringify(result) !== JSON.stringify(job)) {
            displayJob(result);
            message('job', '当前研究' + label(result.status) + '。');
            if (!isActive()) {
              await loadHistory(current);
              if (current === generation && packet?.status === 'AWAITING_AUTHORIZATION') {
                const refreshed = await read(packetApi(handoffRef));
                if (current === generation) displayPacket(refreshed);
              }
            }
          }
        }
        if (current === generation && activeOperations.has(packet?.status)) {
          const status = await read(operationsApi() + '/' + encodeURIComponent(packet.operation_id));
          if (current !== generation) return;
          if (status.status !== packet.status) {
            await loadSelected(current);
            if (current === generation) message('handoff', '执行器任务状态已更新 · ' + label(status.status) + '。');
          }
        }
        if (current === generation) { controls(); schedule(); }
      } catch (error) {
        if (current === generation) message('job', userErrorMessage(error) + ' 可使用“刷新任务记录”重新读取。', true);
      }
    }, 1800);
  }

  async function action(channel, starting, operation) {
    if (busy) return;
    const current = ++generation;
    const trigger = root.contains(document.activeElement) ? document.activeElement : null;
    clearTimeout(timer);
    busy = true; message(channel, starting); controls();
    try { await operation(current); }
    catch (error) { message(channel, userErrorMessage(error), true); }
    finally {
      busy = false; controls(); schedule();
      if (trigger && document.activeElement === document.body) {
        if (trigger.isConnected && !trigger.matches(':disabled')) trigger.focus({ preventScroll: true });
        else if (channel === 'handoff' && !q('command-block').hidden) q('command').focus();
        else root.querySelector(channel === 'job' ? '#mw-record-heading' : channel === 'handoff' ? '#mw-codex-heading' : '#mw-compose-heading')?.focus();
      }
    }
  }

  form.addEventListener('input', invalidate);
  form.addEventListener('change', event => {
    if (event.target === draft.snapshot_ref) selectSnapshot();
    if (event.target === draft.model_config_ref) selectProvider(true);
    invalidate();
  });
  consent.addEventListener('change', controls);
  workerConsent.addEventListener('change', controls);
  manual.elements.response.addEventListener('input', controls);
  q('add').addEventListener('click', () => {
    if (materialCount >= 16) return;
    const row = q('material-template').content.firstElementChild.cloneNode(true);
    row.elements.material_instrument.value = draft.instrument.value;
    q('materials').append(row); materialCount++; invalidate();
    row.querySelector('[data-mw-remove]').addEventListener('click', () => {
      row.remove(); materialCount--; invalidate(); q('add').focus();
    });
    row.elements.material_role.focus();
  });
  form.addEventListener('submit', event => {
    event.preventDefault();
    action('compose', '正在冻结角色材料与预算…', async () => {
      if (consumed) draftKey = null;
      consent.checked = false; preview = null; consumed = false; q('preview').hidden = true;
      const value = await post(API + '/preview', requestBody());
      renderPreview(q('preview-content'), value);
      preview = value;
      q('consent-label').textContent = value.selection.model_config_ref === 'offline' ?
        '我确认用以上材料指纹和预算在本地运行本次研究。' :
        '我授权将以上材料发送给所选角色模型，按冻结预算使用我的接口额度。';
      root.querySelector('#mw-preview-heading').textContent = value.status === 'READY' ? '冻结材料预览' : '研究预览尚未冻结';
      q('preview').hidden = false;
      message('compose', value.status === 'READY' ? '材料已冻结。核对角色输入后可提交研究。' : '本次研究暂不能提交，请查看预览中的原因。');
      root.querySelector('#mw-preview-heading').focus();
    });
  });
  submit.addEventListener('submit', event => {
    event.preventDefault();
    if (!consent.checked || preview?.status !== 'READY' || consumed) return;
    action('compose', '正在提交已核对的研究…', async current => {
      const value = await post(API + '/authorize', { preview_ref: preview.preview_ref, preview_digest: preview.preview_digest, authorized: true });
      jobId = value.job_id; handoffRef = ''; job = null; displayPacket(null); consumed = true; consent.checked = false; remember();
      message('compose', '本次预览已提交，可在任务记录查看进度。');
      await Promise.all([loadSelected(current, ''), loadHistory(current)]);
      message('job', '任务已提交 · ' + label(job.status) + '。');
      root.querySelector('#mw-record-heading').focus();
    });
  });
  q('refresh').addEventListener('click', () => action('job', '正在读取本地记录…', async current => {
    await Promise.all([loadHistory(current), loadSelected(current)]);
    message('job', job ? '当前研究' + label(job.status) + '。' : '已读取本地任务历史。');
    if (packet) message('handoff', '已读取交接包 · ' + label(packet.status) + '。');
  }));
  q('cancel').addEventListener('click', () => action('job', '正在取消研究任务…', async current => {
    await post(jobApi(jobId) + '/cancel', {});
    await Promise.all([loadSelected(current), loadHistory(current)]);
    message('job', '当前研究' + label(job.status) + '。');
  }));
  q('freeze').addEventListener('submit', event => {
    event.preventDefault();
    if (!job) return;
    action('handoff', '正在冻结研究方法与上下文…', async current => {
      const result = await post(jobApi(jobId) + '/handoffs', { purposes: [q('freeze').elements.purpose.value] });
      handoffRef = result.handoff_ref; remember(handoffRef);
      await loadSelected(current, handoffRef);
      message('handoff', '交接包已冻结，可下载、复制或提交回传。'); q('packet-heading').focus();
    });
  });
  q('download').addEventListener('click', () => {
    if (!packet) return;
    const url = URL.createObjectURL(new Blob([q('packet').value], { type: 'application/json' }));
    const a = node('a'); a.href = url; a.download = 'finathink-handoff-' + packet.package_digest.slice(0, 12) + '.json';
    root.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    message('handoff', '已导出当前指纹的交接包。');
  });
  function copy(value, title) {
    return action('handoff', '正在复制' + title + '…', async () => {
      try {
        await navigator.clipboard.writeText(value.value);
        message('handoff', title + '已复制。');
      } catch {
        if (value === q('packet')) q('packet-details').open = true;
        value.focus(); value.select();
        message('handoff', '已选中' + title + '，可按系统复制快捷键。');
      }
    });
  }
  q('copy').addEventListener('click', () => copy(q('packet'), '交接包'));
  q('command-copy').addEventListener('click', () => copy(q('command'), '执行器命令'));
  worker.addEventListener('submit', event => {
    event.preventDefault();
    if (!workerConsent.checked || !canManual() || !packet.eligible) return;
    action('handoff', '正在登记执行器授权…', async current => {
      await post(operationsApi(), { handoff_ref: handoffRef, package_digest: packet.package_digest, limits: packet.limits, authorized: true });
      workerConsent.checked = false;
      await loadSelected(current);
      message('handoff', '已授权此包领取一次。可在本机终端运行下方命令，启动只读 Codex 研究。');
    });
  });
  q('worker-cancel').addEventListener('click', () => action('handoff', '正在取消执行器任务…', async current => {
    await post(operationsApi() + '/' + encodeURIComponent(packet.operation_id) + '/cancel', {});
    await loadSelected(current);
    message('handoff', '执行器任务' + label(packet.status) + '。');
  }));
  manual.addEventListener('submit', event => {
    event.preventDefault();
    if (!canManual()) return;
    action('handoff', '正在核对回传的绑定与证据…', async current => {
      const raw = manual.elements.response.value;
      boundResponse(raw, packet.package_digest);
      // Preserve hashed numbers such as 1.0 exactly as the external tool wrote them.
      const result = await post(packetApi(handoffRef) + '/result', { response_json: raw });
      await loadSelected(current);
      const status = result.outcome?.status || packet.protocol_status;
      if (status === 'REJECTED') {
        message('handoff', '回传未通过校验 · ' + label(result.outcome?.failure_kind) + '。请核对包与证据引用后再提交。', true);
      } else {
        message('handoff', '回传已保存 · ' + label(status) + '。请分别核对协议与语义证据。');
        q('packet-heading').focus();
      }
    });
  });
  document.addEventListener('visibilitychange', schedule);
  window.addEventListener('pagehide', () => { clearTimeout(timer); generation++; }, { once: true });
  selectProvider(false);
  controls();
  root.dataset.mwReady = 'true';
  if (jobId) action('job', '正在读取所选任务…', async current => {
    await loadSelected(current);
    message('job', '已读取所选研究 · ' + label(job.status) + '。');
    if (packet) message('handoff', '已恢复交接包 · ' + label(packet.status) + '。');
  });
}

const BASE = '/research/model-workbench';
export const API = '/api/research/model-workbench';
export const roleNames = {
  fundamentals: '基本面分析师', technical: '技术分析师', sentiment: '市场情绪分析师',
  news: '新闻分析师', learning: '学习分析师', research: '研究经理',
};
const labels = {
  QUEUED: '已排队', RUNNING: '研究中', COMPLETED: '已完成', CANCELLED: '已取消', FAILED: '失败',
  EXTERNAL_WAITING: '等待外部处理', RETRYABLE: '等待重试', REJECTED: '已拒绝', READY: '就绪',
  ANALYZED: '已分析', BLOCKED: '有待解决的问题', BLOCKED_DATA: '数据不足', AVAILABLE: '有材料',
  INSUFFICIENT_DATA: '材料不足', MISSING: '缺失', UNAVAILABLE: '不可用', NOT_REQUESTED: '未请求',
  OFFLINE: '离线', SAMPLE: '合成样例（SAMPLE）', USER_API: '用户模型接口', CAPTURED: '已保存快照',
  AUTHORIZED: '已授权，等待领取', DISPATCHED: '已领取', AWAITING_AUTHORIZATION: '等待授权',
  TIMED_OUT: '已超时', STALE: '绑定已失效', MANUAL: '手动交接', ACCEPTED: '已接受',
  TYPED_EVIDENCE_VERIFIED: '类型与证据绑定已核验', INSUFFICIENT_EVIDENCE: '证据不足',
  NOT_EVALUATED: '尚未评估', UNKNOWN: '未知', KNOWN: '已记录', REPORTED: '已报告',
  NOT_APPLICABLE: '不适用', NOT_CALLED: '未调用', NOT_DISPATCHED: '未派发', NOT_SENT: '未发送',
  PENDING: '等待处理', PARTIAL: '部分完成', PASSED: '通过', VERIFIED: '已校验',
  UNVERIFIED: '尚未验证', VALIDATED: '已验证', NOT_VERIFIED: '尚未验证', NOT_RUN: '未运行',
  NOT_AVAILABLE: '尚无记录', NOT_CONFIGURED: '未配置', CONFIGURED: '已配置', REQUESTED: '已请求',
  PUBLISHED: '已发布', UNPUBLISHED: '尚未发布', DISABLED: '未启用', UNKNOWN_AFTER_DISPATCH: '发送后结果未知',
  VALIDATION_FAILED: '校验失败', PROTOCOL_ACCEPTED: '协议已接受', PROTOCOL_REJECTED: '协议未通过',
  EXECUTOR_FAILED: '执行器失败', INVALID_EXECUTOR_RESPONSE: '执行器返回格式有误', EXECUTOR_TIMEOUT: '执行器超时',
  'provider-grounded': '用户模型研究', 'local-deterministic': '本地离线研究', LOCAL_ONLY: '本地计算',
  OUTBOUND_DISABLED: '工作区仅限离线', CREDENTIAL_MISSING: '未找到配置凭证', ENDPOINT_MISSING: '接口地址未配置',
  SENT: '已发送', RECORDED: '已记录', CONFLICT: '记录不一致', REVIEW_REQUIRED: '待人工核对',
  NO_EVIDENCE: '尚无证据', SOURCE_MATCHED: '字面表述与来源相符', EXTERNAL_HANDOFF_REQUIRED: '等待回传',
  NOT_PUBLISHED: '尚未发布', PREPARED: '已准备', SUCCESS: '成功', SUCCEEDED: '成功',
  SOURCE_MISMATCH: '与所引来源不符', FROZEN_PREVIEW: '已授权冻结预览', LEGACY_SUBMISSION: '较早版本的提交',
  PUBLICATION_INVALID: '已发布文件未通过完整性校验', FROZEN_INPUT_INVALID: '冻结输入未通过校验',
  CALL_BINDING_CONFLICT: '调用与冻结输入不一致', CALL_JOURNAL_CONFLICT: '调用日志与归档不一致',
  CALL_JOURNAL_INCOMPLETE: '调用日志缺少本次尝试的回执', CALL_JOURNAL_UNAVAILABLE: '调用日志无法读取',
  MODEL_PUBLICATION_MISSING: '已发布报告缺少模型回执', ROLE_EVIDENCE_CONFLICT: '角色引用与报告不一致',
  LEGACY_DISCOVERY_CONTEXT: '早期发现任务使用其他研究上下文', SCHEMA_ERROR: '返回结构有误', NON_JSON: '返回格式有误',
  HTTP_ERROR: '远端请求失败', TRANSPORT: '传输失败', RESOURCE_LIMIT: '超出预算',
  CAPABILITY_REJECTED: '接口能力不匹配', STREAM_INTERRUPTED: '返回中断', UNSUPPORTED_STREAM: '不支持流式响应',
  RESPONSE_TOO_LARGE: '返回超出大小限制', TIMEOUT: '超时', INVALID_RESPONSE: '返回内容无效', AUTHENTICATION: '认证失败', RATE_LIMITED: '触发限流',
  ResearchPlan: '研究计划', FactorProposal: '因子提案',
};

export function label(value, empty = '尚无记录') {
  if (value === null || value === undefined || value === '') return empty;
  return labels[value] ?? labels[String(value).toUpperCase()] ?? String(value);
}

export function jobUrl(jobId, handoffRef = '') {
  const query = new URLSearchParams({ job: jobId });
  if (handoffRef) query.set('handoff', handoffRef);
  return BASE + '?' + query + (handoffRef ? '#mw-codex' : '#mw-records');
}

export function jobApi(jobId) {
  return API + '/jobs/' + encodeURIComponent(jobId);
}

export function node(tag, value, cls) {
  const el = document.createElement(tag);
  if (value !== undefined && value !== null) el.textContent = String(value);
  if (cls) el.className = cls;
  return el;
}

function link(title, href) {
  const el = node('a', title);
  el.href = href;
  return el;
}

function detail(title, ...contents) {
  const el = node('details', null, 'mw-fold');
  el.dataset.mwDisclosure = title;
  const body = node('div', null, 'mw-roll');
  body.append(...contents);
  el.append(node('summary', title), body);
  return el;
}

function note(text) {
  return node('p', text, 'mw-note');
}

function digest(title, value) {
  const el = node('p', title, 'mw-digest');
  el.append(node('code', value));
  return el;
}

function facts(entries) {
  const dl = node('dl', null, 'mw-facts');
  for (const [title, value] of entries) {
    if (value === undefined || value === null || value === '') continue;
    const div = node('div');
    div.append(node('dt', title), node('dd', value));
    dl.append(div);
  }
  return dl;
}

function bullets(items) {
  const ul = node('ul', null, 'mw-bullets');
  for (const item of items) ul.append(node('li', item));
  return ul;
}

function table(caption, headings, rows) {
  const wrap = node('div', null, 'mw-scroll');
  wrap.tabIndex = 0;
  wrap.setAttribute('role', 'region');
  wrap.setAttribute('aria-label', caption);
  const el = node('table');
  el.append(node('caption', caption, 'mw-sr'));
  const tr = el.createTHead().insertRow();
  for (const title of headings) {
    const th = node('th', title);
    th.scope = 'col';
    tr.append(th);
  }
  const tbody = el.createTBody();
  for (const values of rows) {
    const row = tbody.insertRow();
    values.forEach((value, i) => {
      const cell = node(i === 0 ? 'th' : 'td', value ?? '—');
      if (i === 0) cell.scope = 'row';
      row.append(cell);
    });
  }
  wrap.append(el);
  return wrap;
}

function source(value) {
  return value === 'Selected market snapshot' ? '所选行情快照' : value || '来源未记录';
}

function materials(role) {
  const items = [];
  for (const item of role.materials || []) {
    items.push(node('h4', source(item.source_label)));
    if (typeof item.summary === 'string') items.push(node('p', item.summary));
    else if (item.summary) {
      const rows = item.summary.observations ?? [];
      items.push(note('快照共 ' + item.summary.record_count + ' 条观测，本次上下文含 ' + rows.length + ' 条。'));
      if (rows.length) items.push(table('纳入上下文的价格与成交量', ['观测时间', '可获得时间', '收盘价', '成交量'],
        rows.map(row => [row.timestamp, row.available_at, row.close, row.volume])));
    }
    items.push(facts([['观测时间', item.observed_at], ['可获得时间', item.available_at]]));
    if (item.evidence_ref) items.push(digest('输入证据', item.evidence_ref));
  }
  if (!items.length) items.push(note(role.material_status === 'LOCAL_ONLY' ?
    '此角色使用本地规则，本次没有发送模型上下文。' : '此角色没有收到可用的研究摘要，跳过模型调用。'));
  if (role.dynamic_observations) items.push(note('任务执行时，研究经理还会读取通过证据引用校验的分析师观察。'));
  if (role.context_fingerprint) items.push(digest('角色上下文指纹', role.context_fingerprint));
  if (role.evidence_refs?.length) items.push(detail('核对证据引用', bullets(role.evidence_refs)));
  if (role.context) {
    const raw = node('pre', JSON.stringify({ system_instructions: role.system_instructions, context: role.context,
      response_format: role.response_format, output_limit: role.output_limit }, null, 2), 'mw-projection');
    items.push(detail('查看冻结的指令与完整角色上下文', raw));
  }
  if (role.wire_fingerprint) items.push(digest('请求指纹', role.wire_fingerprint));
  return items;
}

function budgetFacts(budget = {}) {
  return facts([
    ['最长运行时间', budget.max_wall_seconds == null ? null : budget.max_wall_seconds + ' 秒'],
    ['调用上限', budget.max_provider_calls == null ? null : budget.max_provider_calls + ' 次'],
    ['尝试上限', budget.max_attempts], ['字节上限', budget.max_bytes], ['实验数上限', budget.max_experiments],
  ]);
}

export function renderPreview(container, preview) {
  const { selection, budget } = preview;
  const content = [];
  if (selection.snapshot_ref) {
    content.push(node('p', selection.question, 'mw-lede'),
      facts([['标的', selection.instrument], ['研究截止日', selection.as_of], ['数据模式', label(selection.data_mode)],
        ['快照观测数', selection.record_count], ['执行配置', selection.model_config_ref === 'offline' ? '本地离线研究' : selection.model_config_ref],
        ['预计外部调用', preview.expected_calls == null ? '未知' : preview.expected_calls + ' 次']]),
      digest('行情快照指纹', selection.snapshot_ref));
  }
  if (preview.preview_digest) content.push(digest('预览指纹', preview.preview_digest));
  if (selection.policy_fingerprint) content.push(digest('研究政策指纹', selection.policy_fingerprint));
  if (Object.keys(budget).length) content.push(detail('核对完整预算', budgetFacts(budget)));
  const roles = preview.roles || [];
  if (roles.length) content.push(node('h3', selection.model_config_ref === 'offline' ? '各角色的本地处理方式' : '各角色收到的材料', 'mw-subheading'));
  for (const role of roles) {
    content.push(detail([roleNames[role.role] || role.role, label(role.material_status), role.model || '本地规则'].join(' · '), ...materials(role)));
  }
  if (preview.blockers?.length) content.push(node('h3', '提交前需处理', 'mw-subheading'), bullets(preview.blockers.map(b => b.message || b.code)));
  container.replaceChildren(...content);
}

export function usageText(usage) {
  if (usage == null) return '未知';
  if (typeof usage === 'number') return String(usage);
  const keys = { input_units: '输入', generated_units: '输出', total_units: '合计' };
  const counts = Object.keys(keys).filter(key => Number.isFinite(usage[key]));
  return counts.length ? counts.map(key => keys[key] + ' ' + usage[key]).join(' / ') : '未知';
}

export function dispatchText(dispatched) {
  if (dispatched === true) return '已发送';
  if (dispatched === false) return '未发送';
  return '未知';
}

export function citationRows(checks) {
  return checks.flatMap(check => {
    const claim = String(check.claim_index + 1);
    const verdict = label(check.status);
    if (check.citation_checks?.length) {
      return check.citation_checks.map(citation => [claim, verdict, citation.evidence_ref, label(citation.status)]);
    }
    return [[claim, verdict, (check.evidence_refs || []).join('、'), '未记录逐条结果']];
  });
}

function reportRows(roles) {
  const results = [];
  for (const role of roles) {
    const content = [];
    const refs = role.evidence_refs || [];
    content.push(facts([['角色模型', role.model], ['材料状态', label(role.material_status)],
      ['报告中的角色状态', label(role.role_status)], ['实际派发', label(role.dispatch_status)], ['协议状态', label(role.protocol_status)], ['语义证据', label(role.semantic_status)]]));
    if (role.claims?.length) content.push(node('h4', '模型观察'), bullets(role.claims.map(c => typeof c === 'string' ? c : c.text || c.claim)));
    if (role.semantic_checks?.length) content.push(table('逐条观察的证据核对', ['观察', '观察判定', '证据引用', '引用核对'],
      citationRows(role.semantic_checks)));
    if (role.call_ids?.length) content.push(detail('关联调用编号', bullets(role.call_ids)));
    if (role.limitations?.length) content.push(node('h4', '限制'), bullets(role.limitations));
    if (refs.length) content.push(node('h4', '已引用的证据'), bullets(refs));
    if (role.context_fingerprint) content.push(digest('上下文指纹', role.context_fingerprint));
    const row = detail((roleNames[role.role] || role.role) + ' · ' + label(role.protocol_status || role.material_status), ...content);
    row.dataset.mwDisclosure = 'role:' + role.role;
    results.push(row);
  }
  return results;
}

export function renderJob(container, job) {
  const selected = job.selection || {};
  const content = [
    node('h3', selected.question || '所选研究'), digest('任务编号', job.job_id),
    facts([['任务状态', label(job.status)], ['执行方式', label(job.execution_mode)], ['行情来源', label(job.data_mode)],
      ['标的', selected.instrument], ['研究截止日', selected.as_of], ['模型配置', selected.model_config_ref],
      ['实际派发', label(job.dispatch_status)], ['用量记录', label(job.usage_status, '未知')],
      ['协议校验', label(job.protocol_status)], ['语义证据', label(job.semantic_status)],
      ['预期调用', job.expected_calls], ['实际发送次数', job.dispatched_calls ?? '未知'],
      ['已确认发送次数', job.dispatched_calls == null ? job.known_dispatched_calls : null],
      ['授权依据', label(job.consent_status)], ['报告状态', job.report_status === 'AVAILABLE' ? '已发布' : label(job.report_status)], ['失败原因', job.failure_kind ? label(job.failure_kind) : null]]),
  ];
  if (selected.snapshot_ref) content.push(digest('行情快照指纹', selected.snapshot_ref));
  if (job.preview_digest) content.push(digest('已授权的预览指纹', job.preview_digest));
  if (job.run_id) content.push(digest('研究编号', job.run_id));
  content.push(note('协议校验检查返回格式。语义证据记录模型观察能否关联到本次研究输入。投资表现须另行验证。'));
  if (job.semantic_summary?.checked_claims) content.push(facts([['已核对观察', job.semantic_summary.checked_claims],
    ['与来源字面相符', job.semantic_summary.matched_claims]]));
  if (job.evidence_issues?.length) content.push(node('h4', '证据记录需核对'), bullets(job.evidence_issues.map(issue => label(issue))));
  if (job.synthesis?.hypotheses?.length) content.push(detail('研究经理的待验证假设', bullets(job.synthesis.hypotheses)));
  if (job.synthesis?.disagreements?.length) content.push(detail('研究经理记录的分歧', bullets(job.synthesis.disagreements)));
  if (job.usage) content.push(facts([['总用量（单位）', job.usage.total_units ?? '未知'],
    ['已记录单位', job.usage.known_total_units], ['用量未知的调用', job.usage.unknown_calls ?? '未知']]));
  if (job.budget) content.push(detail('本次任务的冻结预算', budgetFacts(job.budget)));
  if (job.roles?.length) content.push(node('h3', '角色证据', 'mw-subheading'), ...reportRows(job.roles));
  content.push(node('h3', '模型调用回执', 'mw-subheading'));
  if (job.calls?.length) {
    content.push(table('逐次调用回执', ['研究角色', '角色模型', '派发', '协议结果', '用量（单位）', '耗时'],
      job.calls.map(call => [roleNames[call.role] || call.role, call.model, dispatchText(call.dispatched), call.status === 'READY' ? '格式已接受' : label(call.status),
        usageText(call.usage), call.duration_ms == null ? '未知' : call.duration_ms + ' ms'])));
    for (const call of job.calls) {
      content.push(detail('调用详情 · ' + (roleNames[call.role] || call.role),
        digest('调用编号', call.call_id), digest('上下文指纹', call.context_fingerprint),
        facts([['开始时间', call.started_at], ['尝试序号', call.attempt], ['日志留存状态', call.observed_status ? label(call.observed_status) : null],
          ['请求字节', call.request_bytes], ['上下文字节', call.context_bytes]])));
    }
  } else {
    content.push(note(job.dispatch_status === 'OFFLINE' ? '本地离线研究没有外部模型调用。' : '当前尚无模型调用回执。'));
  }
  const same = container.dataset.job === job.job_id;
  const opened = same ? new Set([...container.querySelectorAll('details[open]')].map(el => el.dataset.mwDisclosure)) : new Set();
  const focused = same && container.contains(document.activeElement) ? document.activeElement.closest('details')?.dataset.mwDisclosure : null;
  container.replaceChildren(...content);
  container.dataset.job = job.job_id;
  container.hidden = false;
  for (const fold of container.querySelectorAll('details')) {
    if (opened.has(fold.dataset.mwDisclosure)) fold.open = true;
    if (focused === fold.dataset.mwDisclosure) fold.querySelector('summary').focus({ preventScroll: true });
  }
}

export function renderHistory(container, records, currentId) {
  const items = records.map(job => {
    const li = node('li');
    const selected = job.selection || {};
    const a = link(job.question || selected.question || job.instrument || job.run_id || job.job_id, jobUrl(job.job_id));
    if (job.job_id === currentId) a.setAttribute('aria-current', 'true');
    const download = link('下载回执 JSON', jobApi(job.job_id));
    download.className = 'mw-json'; download.download = 'finathink-model-receipt.json';
    li.append(a, node('code', job.job_id.slice(-12)), node('span', label(job.status)), download);
    return li;
  });
  container.replaceChildren(...(items.length ? items : [node('li', '此工作区还没有研究任务。')]));
}

export function renderHandoffList(container, jobId, rows, selectedRef) {
  const items = rows.map(row => {
    const li = node('li');
    const title = '交接包 · ' + row.package_digest.slice(0, 12);
    const a = link(title, jobUrl(jobId, row.handoff_ref));
    if (row.handoff_ref === selectedRef) a.setAttribute('aria-current', 'true');
    li.append(a, node('span', label(row.status)));
    return li;
  });
  container.replaceChildren(...(items.length ? items : [node('li', '此任务还没有冻结的交接包。')]));
}

function returnedArtifact(artifact) {
  const body = artifact.payload;
  const rows = [];
  if (artifact.artifact_type === 'ResearchPlan') {
    if (body.hypotheses.length) rows.push(node('h4', '研究假设'), bullets(body.hypotheses));
    const validation = body.validation_spec;
    rows.push(facts([['验证方法', validation.validation], ['否证条件', validation.falsification]]));
    if (Array.isArray(validation.limitations)) rows.push(node('h4', '研究局限'), bullets(validation.limitations));
    if (Array.isArray(validation.evidence_refs)) rows.push(node('h4', '方案引用'), bullets(validation.evidence_refs));
  } else if (artifact.artifact_type === 'FactorProposal') {
    rows.push(facts([['候选公式', body.expression], ['来源假设', body.source_hypothesis],
      ['所需字段', body.required_fields?.join('、')]]));
  }
  if (artifact.evidence_refs?.length) {
    const title = artifact.evidence_scope === 'ARTIFACT' ? '产物所引证据' : '本次回传所引证据';
    rows.push(detail(title, bullets(artifact.evidence_refs)));
  }
  rows.push(detail('查看全部产物字段', node('pre', JSON.stringify(body, null, 2), 'mw-projection')));
  return detail('阅读' + label(artifact.artifact_type), ...rows);
}

export function renderPacket(container, worker, packet) {
  const { package: pkg, limits = {} } = packet;
  const content = [digest('交接包指纹', packet.package_digest),
    facts([['包大小', packet.packet_bytes == null ? null : packet.packet_bytes + ' 字节'],
      ['研究标的', pkg?.context?.instrument], ['研究截止日', pkg?.context?.as_of],
      ['包内证据数', pkg?.context?.evidence_index ? Object.keys(pkg.context.evidence_index).length : null],
      ['允许的回传类型', pkg?.manifest?.allowed_output_types?.map(kind => label(kind)).join('、')],
      ['交接状态', label(packet.status)], ['协议校验', label(packet.protocol_status, '尚未回传')],
      ['语义证据', label(packet.semantic_status, '尚未评估')],
      ['已回传产物', packet.artifact_types?.map(kind => label(kind)).join('、')]])];
  if (pkg?.manifest) content.push(digest('研究方法指纹', pkg.manifest.methods_digest));
  const refs = packet.evidence_refs || [];
  if (refs.length) content.push(detail('回传引用的证据', bullets(refs)));
  if (packet.artifact_refs?.length) content.push(detail('已保存的产物引用', bullets(packet.artifact_refs)));
  if (packet.artifact_review_status === 'UNAVAILABLE') {
    content.push(note(packet.artifact_review_failure === 'ARTIFACT_PREVIEW_LIMIT'
      ? '已接受的产物正文超出页面预览上限。' : '产物完整性校验未通过，暂时无法打开正文。'));
  }
  for (const artifact of packet.artifacts || []) content.push(returnedArtifact(artifact));
  container.replaceChildren(...content);
  if (packet.budget_status === 'USAGE_UNAVAILABLE') {
    container.append(note('以往调用的用量无法核实，目前无法授权新的执行额度。'));
  }
  const limitsView = facts([['执行器领取上限', limits.max_provider_calls == null ? null : limits.max_provider_calls + ' 次'],
    ['字节上限', limits.max_bytes], ['时长上限', limits.max_wall_seconds == null ? null : limits.max_wall_seconds + ' 秒']]);
  const state = [limitsView];
  if (packet.operation_id) {
    state.push(digest('执行器任务编号', packet.operation_id), facts([['实际领取次数', packet.execution_accounting?.worker_claims ?? packet.usage?.provider_calls],
      ['已用字节', packet.usage?.bytes_used], ['模型用量', label(packet.usage?.usage_state, '未知')]]));
    if (Number.isFinite(packet.lease_until)) state.push(note('领取或回传截止于 ' + new Date(packet.lease_until * 1000).toISOString() + '。'));
  }
  if (packet.failure_kind) state.push(note(label(packet.failure_kind)));
  if (packet.reason) state.push(note(label(packet.reason)));
  worker.replaceChildren(...state);
}

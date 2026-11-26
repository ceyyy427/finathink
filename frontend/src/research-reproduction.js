import { readLocalJSON, userErrorMessage } from './local-request.js';

const BASE = '/api/research/reproduction';
const COPY = { COMPATIBLE: '条件一致', INCOMPATIBLE: '条件存在差异', UNKNOWN: '条件尚不完整', SAME: '一致', CHANGED: '变化', READY: '证据已计算', BLOCKED: '待补充条件', MATCHED: '已覆盖产物一致', PARTIAL: '部分产物已复现', DIFFERENT: '复算产物存在差异', INCLUDED: '已纳入复算', REQUIRES_ORIGINAL_WORKFLOW: '需通过原研究流程', ESTIMATED: '区块重采样区间', INSUFFICIENT_SAMPLE: '共同样本不足', NOT_COMPUTED: '尚未计算', DISCONTINUOUS_SAMPLE: '样本区间不连续' };
const NEEDS = { NORMALIZED_DATA: '需要携带标准化数据的研究包', CODE_MISMATCH: '需要原代码内容版本', DEPENDENCY_MISMATCH: '需要匹配 Python / numpy / pandas 版本', POLICY_UNSUPPORTED: '需要原风险政策对应的计算内核', FACTOR_WORKFLOW_DEPENDENCIES: '因子方法、字段附件和冻结测试授权需在原研究流程恢复', STRATEGY_VALIDATION_WORKFLOW_DEPENDENCIES: '策略验证需要原纸面账本、冻结方案及对应的时点面板', STRATEGY_WORKFLOW_DEPENDENCIES: '策略账本需要原工作流中的面板合同、附件与政策', DISCOVERY_BLUEPRINT_DEPENDENCIES: '研究计划与分析师证据见原报告和冻结来源', MODEL_MATERIALS_AND_AUTHORIZATION: '模型文字需原材料、模型配置和用户授权', INPUT_UNSUPPORTED: '输入未通过当前内核检查', UNITS_REQUIRED: '补充同币种价格/现金和 shares 成交量单位', ASSET_CLASS_REQUIRED: '补充每个资产的股票或 ETF 类型', MULTI_ASSET_REQUIRED: '需要至少两个资产', PIT_UNAVAILABLE: '补充截至研究日可用的数据时间', HISTORICAL_AVAILABILITY: '历史数据在执行边界尚不可用', EXPOSURE_AVAILABILITY: '共同截止点的暴露数据尚不可用', COMMON_SAMPLE_REQUIRED: '需要更多起止时间一致的收益区间', LOW_FREQUENCY_REQUIRED: '需要每个 UTC 日期最多一条观测', RESOURCE_LIMIT: '缩小输入至声明的资产和记录上限', NONFINITE_RETURN: '价格变化超出可计算范围' };
export function displayValue(value) { return value == null || (typeof value === 'number' && !Number.isFinite(value)) ? '未知' : typeof value === 'object' ? JSON.stringify(value) : String(value); }
export function latestOperation(request) {
  let generation = 0; let pending = null; let signature = null;
  return { invalidate() { generation++; }, run(input) {
    const key = JSON.stringify(input);
    if (pending) return key === signature ? pending : Promise.reject(new Error('当前操作尚未完成，请稍后重试。'));
    const current = ++generation; signature = key;
    let sent; try { sent = request(input); } catch (error) { sent = Promise.reject(error); }
    pending = Promise.resolve(sent).then(result => current === generation ? result : null,
      error => { if (current === generation) throw error; return null; }).finally(() => { pending = null; signature = null; });
    return pending;
  } };
}
const el = (root, tag, text) => { const node = root.ownerDocument.createElement(tag); if (text != null) node.textContent = text; return node; };
function table(root, caption, headings, rows) {
  const wrap = el(root, 'div'); wrap.className = 'table-wrap'; wrap.tabIndex = 0; wrap.setAttribute('role', 'region'); wrap.setAttribute('aria-label', caption+'，可横向滚动');
  const t = el(root, 'table'); t.append(el(root, 'caption', caption)); const head = el(root, 'thead'); const tr = el(root, 'tr');
  headings.forEach(text => { const th = el(root, 'th', text); th.scope = 'col'; tr.append(th); }); head.append(tr); t.append(head);
  const body = el(root, 'tbody'); rows.forEach(row => { const r = el(root, 'tr'); row.forEach(text => r.append(el(root, 'td', displayValue(text)))); body.append(r); }); t.append(body); wrap.append(t); return wrap;
}
function details(root, title, value) {
  const d = el(root, 'details'); d.append(el(root, 'summary', title), el(root, 'pre', JSON.stringify(value, null, 2))); return d;
}
function evidence(root, side, label) {
  const block = el(root, 'section'); block.className = 'section'; block.append(el(root, 'h3', label+' · '+side.run_id));
  if ('strategy_validation' in side) {
    const marker = side.strategy_validation || {};
    block.append(el(root, 'p', '策略验证 · ' + displayValue(marker.template_id) + ' · 原运行 ' + displayValue(marker.run_id)));
    const link = el(root, 'a', '查看完整策略验证报告'); link.href = side.report_url; block.append(link);
    return block;
  }
  const e = side.quant_evidence;
  if (!e) { block.append(el(root, 'p', '原报告没有多资产证据，请从完整输入创建新研究。')); return block; }
  block.append(el(root, 'p', `${COPY[e.status] || e.status} · ${side.data_mode} · ${e.coverage.asset_count ?? 0} 个资产 / ${e.coverage.common_intervals ?? 0} 个共同收益区间`));
  const reasons = el(root, 'ul'); e.blocking_reasons.forEach(code => reasons.append(el(root, 'li', NEEDS[code] || code))); block.append(reasons);
  if (e.variance) block.append(table(root, '样本方差贡献（可为负）', ['资产', '等权研究权重', '方差贡献'], Object.entries(e.weights).map(([asset, weight]) => [asset, weight, e.variance.contributions[asset]])));
  if (e.exposure) block.append(table(root, '行业与动量暴露', ['类型', '名称', '暴露'], [...Object.entries(e.exposure.industry).map(([name, value]) => ['行业', name, value]), ...Object.entries(e.exposure.momentum_contributions).map(([name, value]) => ['两期动量', name, value])]));
  if (e.sensitivity) block.append(table(root, '压力敏感度 · 已测稳定点', ['市场冲击', '组合损失', '未超声明上限'], e.sensitivity.points.map(p => [p.shock, p.loss, p.within_limit ? '是' : '否'])));
  if (e.stress) block.append(details(root, '逐项压力情景与归因', e.stress.scenarios));
  const u = e.uncertainty;
  block.append(el(root, 'p', `均值不确定性：${COPY[u.status] || u.status}；样本 ${displayValue(u.sample_size)}；95% 区间 ${displayValue(u.interval)}；区块长度 ${displayValue(u.block_length)}。`));
  block.append(el(root, 'p', '固定等权历史研究，未扣成本；95% 区间不是未来盈利概率。来源研究主张独立保留为未验证。'));
  if (side.factor_surfaces.length) block.append(details(root, '原因子参数网格、稳定区域与阈值', side.factor_surfaces));
  block.append(details(root, '口径、覆盖与限制', { coverage: e.coverage, variance: e.variance, tail_risk: e.tail_risk, uncertainty: u, limitations: e.limitations }));
  return block;
}
function renderComparison(root, value) {
  const panel = root.querySelector('[data-repro-comparison]'); panel.replaceChildren(el(root, 'h3', COPY[value.comparison.status]), el(root, 'p', value.comparison.basis));
  panel.append(table(root, '六类冻结条件', ['条件', '状态', '解释'], value.comparison.categories.map(row => [row.label, COPY[row.status], row.explanation])));
  value.comparison.categories.forEach(row => panel.append(details(root, row.label+'：查看左右条件', { '左侧': row.left, '右侧': row.right })));
  if (Object.keys(value.comparison.metric_differences).length) panel.append(details(root, '同条件基线指标差值', value.comparison.metric_differences));
  root.querySelector('[data-repro-evidence]').replaceChildren(evidence(root, value.left, '左侧证据'), evidence(root, value.right, '右侧证据'));
}
const PRODUCTS = {
  factor_review: '因子审核记录', historical_reports: '历史报告原件', baseline: '基线回测与风险', quant_evidence: '多资产量化证据', factor_study: '冻结因子测试',
  strategy_study: '策略纸面账本', strategy_validation: '策略滚动验证与组合归因',
  strategy_ledger: '纸面策略账本', strategy_benchmark: '策略基准', strategy_decisions: '策略决策记录',
  strategy_result: '策略运行结果', strategy_validation_plan: '策略验证方案', strategy_walkforward: '滚动样本外验证',
  strategy_portfolio: '组合归因', model_text: '模型文字与调用记录', discovery: '研究计划与分析师证据',
  learning_approval: '学习审核与政策', learning_baseline: '原政策复跑', learning_candidate: '候选政策复跑',
  learning_comparison: '成对差异',
};
const resultLabel = status => ({
  REPRODUCED: '已复现', AVAILABLE: '已就绪', INCOMPLETE: '等待两侧复跑', RUNNING: '计算中', ARCHIVED: '已归档原证据', SOURCE_WITHDRAWN: '来源已撤回',
}[status] || COPY[status] || status);
const needsLabel = code => ({
  SOURCE_WITHDRAWN: '冻结来源已撤回或需重新审核', FACTOR_TEST_NOT_PUBLISHED: '需要已发表的最终测试回执',
  FACTOR_FORMULA_VERSION_UNAVAILABLE: '缺少相同版本的因子公式',
  FACTOR_SOURCE_VERSION_UNAVAILABLE: '因子来源版本不一致',
  STRATEGY_DEFINITION_MISMATCH: '策略或风控规则的定义已改变',
  STRATEGY_VERSION_MISMATCH: '策略运行内核版本不同',
}[code] || NEEDS[code] || code);

export function exportRequest(runId, includeData, scope, pairId) {
  if (includeData && scope === 'workflow') {
    return { ...(pairId ? { pair_id: pairId } : { run_id: runId }), include_data: true, data_scope: 'workflow' };
  }
  return { run_id: runId, include_data: includeData };
}

function download(root, name, blob) {
  const url = URL.createObjectURL(blob);
  const anchor = el(root, 'a');
  anchor.href = url;
  anchor.download = name;
  root.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function renderReceipt(root, saved) {
  const panel = root.querySelector('[data-repro-receipt]');
  panel.replaceChildren();
  root.querySelector('[data-repro-history-section]').hidden = !(saved?.history_count > 0);
  root.querySelector('[data-repro-history-list]').replaceChildren();
  if (!saved) { panel.append(el(root, 'p', '尚无恢复记录。')); return; }
  const r = saved.receipt;
  panel.append(el(root, 'h4', r ? resultLabel(r.status) : '已恢复，等待显式复算'));
  const products = r?.products || {};
  const rows = Object.entries(saved.product_coverage).map(([key, coverage]) => {
    const result = products[key];
    const status = typeof result === 'string' ? result : result?.status || (r && coverage === 'INCLUDED' ? 'BLOCKED' : coverage);
    return [PRODUCTS[key] || key, resultLabel(status)];
  });
  panel.append(table(root, '每项产物的复现范围', ['产物', '结果'], rows));
  for (const [key, result] of Object.entries(products)) {
    if (!result || typeof result !== 'object' || !result.expected_digest && !result.actual_digest) continue;
    const section = el(root, 'details');
    section.append(el(root, 'summary', (PRODUCTS[key] || key) + ' · 查看摘要与差异'));
    section.append(table(root, '计算与原始证据', ['项目', '值'], [
      ['原产物摘要', result.expected_digest], ['复算摘要', result.actual_digest],
      ['已核对字段', result.fields_checked], ['差异数量', result.difference_count],
      ['绝对容差', result.abs_tolerance], ['相对容差', result.rel_tolerance],
    ]));
    if (result.differences?.length) {
      section.append(table(root, '存在差异的字段', ['字段路径', '原值', '复算值'],
        result.differences.map(item => [item.path, item.expected, item.actual])));
    }
    panel.append(section);
  }
  const missing = r?.missing_dependencies || saved.missing_dependencies || [];
  if (missing.length) {
    const list = el(root, 'ul');
    missing.forEach(code => list.append(el(root, 'li', needsLabel(code))));
    panel.append(list);
  }
  if (r?.version_differences && Object.keys(r.version_differences).length) {
    const names = { dependencies: '运行依赖', source_digest: '代码内容', engine_version: '量化引擎', strategy_version: '策略内核' };
    panel.append(table(root, '版本差异', ['组件', '原版本', '当前版本'],
      Object.entries(r.version_differences).map(([key, diff]) => [names[key] || key, diff.expected, diff.actual])));
  }
  if (r) panel.append(details(root, '执行版本与复算摘要', r));
}
export function mountResearchReproduction(root) {
  if (root.dataset.reproductionReady) return;
  const q = selector => root.querySelector(selector);
  const csrf = q('[data-repro-csrf]').value;
  const post = (path, values) => readLocalJSON(globalThis.fetch, BASE+path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ _csrf: csrf, ...values }) });
  const error = q('[data-repro-error]'); const busy = new Set(); let packages = []; let historyVersion = 0;
  const compare = latestOperation(({left,right}) => readLocalJSON(globalThis.fetch, BASE+'/compare?'+new URLSearchParams({left,right})));
  const exportOp = latestOperation(values => post('/export', values));
  const restore = latestOperation(value => post('/restore', {package:value}));
  const recompute = latestOperation(id => post('/'+id+'/recompute', {}));
  const workflow = () => q('[data-repro-data]').checked && q('[data-repro-scope]').value === 'workflow';
  const archived = latestOperation(id => readLocalJSON(globalThis.fetch, BASE+'/'+id+'/history'));
  let pairListLoaded = false;
  const sync = () => {
    const hasData = q('[data-repro-data]').checked;
    const pair = workflow() && q('[data-repro-pair]').value;
    q('[data-repro-scopes]').hidden = !hasData;
    q('[data-repro-pair-field]').hidden = !workflow();
    q('[data-repro-scope-note]').textContent = workflow()
      ? '携带所选研究已发表的输入、版本化方法、审核和报告。模型调用保留原记录。'
      : '携带已发表的标准化行情，用于复算基础量化产物。';
    q('[data-repro-export]').textContent = pair ? '导出学习成对研究包' : '导出左侧研究包';
    q('[data-repro-load-history]').disabled = busy.has('artifacts');
    q('[data-repro-compare]').disabled = busy.has('compare') || !q('[data-repro-left]').value || !q('[data-repro-right]').value;
    q('[data-repro-export]').disabled = busy.has('export') || (!q('[data-repro-left]').value && !pair);
    q('[data-repro-restore]').disabled = busy.has('restore');
    q('[data-repro-recompute]').disabled = busy.has('recompute') || !q('[data-repro-packages]').value;
  };
  async function action(name, work) {
    if (busy.has(name)) return;
    busy.add(name); error.hidden = true; sync(); root.setAttribute('aria-busy','true');
    try { await work(); } catch (problem) { error.textContent = userErrorMessage(problem); error.hidden = false; if (name === 'compare') q('[data-repro-comparison]').textContent = '本次读取未完成，检查提示后可重新比较。'; }
    finally { busy.delete(name); sync(); root.setAttribute('aria-busy', String(busy.size > 0)); }
  }
  async function history(selected) {
    const current = ++historyVersion;
    const value = await readLocalJSON(globalThis.fetch, BASE);
    if (current !== historyVersion) return;
    packages = value.packages;
    const select = q('[data-repro-packages]'); const oldSelection = select.value; const previous = selected || oldSelection;
    select.replaceChildren();
    if (!packages.length) { const o = el(root,'option','暂无研究包'); o.value=''; select.append(o); }
    packages.forEach(p => { const o=el(root,'option',(p.pair_id || p.run_id || '研究包')+' · '+p.package_id.slice(0,8)); o.value=p.package_id; select.append(o); });
    if (packages.some(p=>p.package_id===previous)) select.value=previous;
    if (select.value !== oldSelection) { recompute.invalidate(); archived.invalidate(); }
    renderReceipt(root, packages.find(p=>p.package_id===select.value)); sync();
  }
  q('[data-repro-compare-form]').addEventListener('submit', event => { event.preventDefault(); action('compare', async()=>{
    q('[data-repro-comparison]').textContent='正在读取已验证的研究条件…';
    const value = await compare.run({left:q('[data-repro-left]').value,right:q('[data-repro-right]').value}); if (value) renderComparison(root,value);
  }); });
  for (const select of [q('[data-repro-left]'),q('[data-repro-right]')]) select.addEventListener('change',()=>{
    compare.invalidate(); exportOp.invalidate(); q('[data-repro-data]').checked=false;
    q('[data-repro-scope]').value = 'prices'; q('[data-repro-pair]').value = '';
    q('[data-repro-comparison]').textContent='选择已变化，请重新比较研究条件。'; q('[data-repro-evidence]').replaceChildren(); sync();
  });
  async function loadPairs() {
    if (!workflow() || pairListLoaded) return;
    const {pairs} = await readLocalJSON(globalThis.fetch, BASE+'/pairs');
    const select = q('[data-repro-pair]');
    for (const pair of pairs) {
      const label = pair.kind === 'strategy_pair' ? '策略学习对照' : '研究政策对照';
      const option = el(root, 'option', label+' · '+pair.pair_id.slice(-12)+' · '+resultLabel(pair.status));
      option.value = pair.pair_id;
      select.append(option);
    }
    if (!pairs.length) {
      const option = el(root, 'option', '暂无学习对照');
      option.disabled = true;
      select.append(option);
    }
    pairListLoaded = true;
  }
  q('[data-repro-export]').addEventListener('click',()=>action('export',async()=>{
    const body = exportRequest(q('[data-repro-left]').value, q('[data-repro-data]').checked,
      q('[data-repro-scope]').value, q('[data-repro-pair]').value);
    const value = await exportOp.run(body);
    if (value) download(root, 'finathink-research-'+value.package_id.slice(0,12)+'.json',
      new Blob([JSON.stringify(value)], {type:'application/json'}));
  }));
  for (const selector of ['[data-repro-data]', '[data-repro-scope]', '[data-repro-pair]']) {
    q(selector).addEventListener('change', () => {
      exportOp.invalidate();
      if (!workflow()) q('[data-repro-pair]').value = '';
      sync();
      if (workflow() && !pairListLoaded) action('pairs', loadPairs);
    });
  }
  q('[data-repro-file]').addEventListener('change',()=>restore.invalidate());
  q('[data-repro-import]').addEventListener('submit',event=>{ event.preventDefault(); action('restore',async()=>{
    const file=q('[data-repro-file]').files[0]; if (!file || file.size>32*1024*1024) throw new Error('请选择不超过 32 MiB 的 JSON 研究包。');
    let value; try { value=JSON.parse(await file.text()); } catch { throw new Error('文件不是有效的 JSON，请重新选择导出的研究包。'); }
    if (q('[data-repro-file]').files[0] !== file) return;
    const saved=await restore.run(value); if (!saved) return;
    q('[data-repro-import-status]').textContent='研究包已恢复。可查看范围并复算。'; await history(saved.package_id);
  }); });
  q('[data-repro-packages]').addEventListener('change',()=>{ historyVersion++; recompute.invalidate(); archived.invalidate(); renderReceipt(root,packages.find(p=>p.package_id===q('[data-repro-packages]').value)); sync(); });
  q('[data-repro-recompute]').addEventListener('click',()=>action('recompute',async()=>{
    const id=q('[data-repro-packages]').value; const result=await recompute.run(id); if (!result) return;
    const saved=packages.find(p=>p.package_id===id); if (saved) saved.receipt=result;
    if (q('[data-repro-packages]').value === id) renderReceipt(root,saved);
  }));
  q('[data-repro-load-history]').addEventListener('click', () => action('artifacts', async () => {
    const id = q('[data-repro-packages]').value;
    const result = await archived.run(id);
    if (!result || q('[data-repro-packages]').value !== id) return;
    const list = q('[data-repro-history-list]');
    list.replaceChildren();
    if (result.evidence && Object.keys(result.evidence).length) {
      for (const [key, value] of Object.entries(result.evidence)) {
        list.append(details(root, PRODUCTS[key] || key, value));
      }
      const downloadEvidence = el(root, 'button', '下载归档证据 JSON');
      downloadEvidence.type = 'button';
      downloadEvidence.className = 'button-secondary';
      downloadEvidence.addEventListener('click', () => download(root, 'evidence-'+id.slice(0,12)+'.json',
        new Blob([JSON.stringify(result.evidence,null,2)], {type:'application/octet-stream'})));
      list.append(downloadEvidence);
    }
    for (const artifact of result.artifacts) {
      const name = artifact.name.split('/').pop();
      const item = el(root, 'p');
      const button = el(root, 'button', '下载 '+artifact.name);
      button.type = 'button';
      button.className = 'button-secondary';
      button.addEventListener('click', () => action('download', async () => {
        const bytes = Uint8Array.from(atob(artifact.content_base64), char => char.charCodeAt(0));
        const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
        const hex = Array.from(digest, byte => byte.toString(16).padStart(2, '0')).join('');
        if (hex !== artifact.sha256) throw new Error('历史文件摘要不一致，请重新导入原研究包。');
        if (q('[data-repro-packages]').value === id) download(root, name, new Blob([bytes], {type:'application/octet-stream'}));
      }));
      item.append(button);
      list.append(item, el(root, 'code', artifact.sha256));
    }
  }));
  root.dataset.reproductionReady='true'; sync(); action('history',()=>history());
}

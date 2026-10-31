import { readLocalJSON, userErrorMessage } from './local-request.js';

const BASE = '/api/research/reproduction';
const COPY = { COMPATIBLE: '条件一致', INCOMPATIBLE: '条件存在差异', UNKNOWN: '条件尚不完整', SAME: '一致', CHANGED: '变化', READY: '证据已计算', BLOCKED: '待补充条件', MATCHED: '已覆盖产物一致', PARTIAL: '部分产物已复现', DIFFERENT: '复算产物存在差异', INCLUDED: '已纳入复算', REQUIRES_ORIGINAL_WORKFLOW: '需通过原研究流程', ESTIMATED: '区块重采样区间', INSUFFICIENT_SAMPLE: '共同样本不足', NOT_COMPUTED: '尚未计算', DISCONTINUOUS_SAMPLE: '样本区间不连续' };
const NEEDS = { NORMALIZED_DATA: '需要携带标准化数据的研究包', CODE_MISMATCH: '需要原代码内容版本', DEPENDENCY_MISMATCH: '需要匹配 Python / numpy / pandas 版本', POLICY_UNSUPPORTED: '需要原风险政策对应的计算内核', FACTOR_WORKFLOW_DEPENDENCIES: '因子方法、字段附件和冻结测试授权需在原研究流程恢复', MODEL_MATERIALS_AND_AUTHORIZATION: '模型文字需原材料、模型配置和用户授权', INPUT_UNSUPPORTED: '输入未通过当前内核检查', UNITS_REQUIRED: '补充同币种价格/现金和 shares 成交量单位', ASSET_CLASS_REQUIRED: '补充每个资产的股票或 ETF 类型', MULTI_ASSET_REQUIRED: '需要至少两个资产', PIT_UNAVAILABLE: '补充截至研究日可用的数据时间', HISTORICAL_AVAILABILITY: '历史数据在执行边界尚不可用', EXPOSURE_AVAILABILITY: '共同截止点的暴露数据尚不可用', COMMON_SAMPLE_REQUIRED: '需要更多起止时间一致的收益区间', LOW_FREQUENCY_REQUIRED: '需要每个 UTC 日期最多一条观测', RESOURCE_LIMIT: '缩小输入至声明的资产和记录上限', NONFINITE_RETURN: '价格变化超出可计算范围' };
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
function renderReceipt(root, saved) {
  const panel = root.querySelector('[data-repro-receipt]'); panel.replaceChildren();
  if (!saved) { panel.append(el(root, 'p', '尚无恢复记录。')); return; }
  const r = saved.receipt;
  panel.append(el(root, 'h4', r ? COPY[r.status] || r.status : '已恢复，等待显式复算'));
  panel.append(table(root, '每项产物的复现范围', ['产物', '结果'], Object.entries(r?.products || saved.product_coverage).map(([key, status]) => [{ baseline: '基线回测与风险', quant_evidence: '多资产量化证据', factor_study: '因子研究', model_text: '模型文字' }[key], COPY[status] || status])));
  const list = el(root, 'ul'); (r?.missing_dependencies || saved.missing_dependencies).forEach(code => list.append(el(root, 'li', NEEDS[code] || code))); panel.append(list);
  panel.append(el(root, 'p', '比对数值产物；原出处、运行标识与生成时间分别保留。内容摘要校验不等于第三方来源认证。'));
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
  const sync = () => {
    q('[data-repro-compare]').disabled = busy.has('compare') || !q('[data-repro-left]').value || !q('[data-repro-right]').value;
    q('[data-repro-export]').disabled = busy.has('export') || !q('[data-repro-left]').value;
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
    const select = q('[data-repro-packages]'); const previous = selected || select.value;
    select.replaceChildren();
    if (!packages.length) { const o = el(root,'option','暂无研究包'); o.value=''; select.append(o); }
    packages.forEach(p => { const o=el(root,'option',p.run_id+' · '+p.package_id.slice(0,8)); o.value=p.package_id; select.append(o); });
    if (packages.some(p=>p.package_id===previous)) select.value=previous;
    renderReceipt(root, packages.find(p=>p.package_id===select.value)); sync();
  }
  q('[data-repro-compare-form]').addEventListener('submit', event => { event.preventDefault(); action('compare', async()=>{
    q('[data-repro-comparison]').textContent='正在读取已验证的研究条件…';
    const value = await compare.run({left:q('[data-repro-left]').value,right:q('[data-repro-right]').value}); if (value) renderComparison(root,value);
  }); });
  for (const select of [q('[data-repro-left]'),q('[data-repro-right]')]) select.addEventListener('change',()=>{
    compare.invalidate(); exportOp.invalidate(); q('[data-repro-data]').checked=false;
    q('[data-repro-comparison]').textContent='选择已变化，请重新比较研究条件。'; q('[data-repro-evidence]').replaceChildren(); sync();
  });
  q('[data-repro-export]').addEventListener('click',()=>action('export',async()=>{
    const value=await exportOp.run({run_id:q('[data-repro-left]').value,include_data:q('[data-repro-data]').checked}); if (!value) return;
    const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'})); const a=el(root,'a'); a.href=url; a.download='finathink-research-'+value.package_id.slice(0,12)+'.json'; root.append(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(url),1000);
  }));
  q('[data-repro-data]').addEventListener('change',()=>exportOp.invalidate());
  q('[data-repro-file]').addEventListener('change',()=>restore.invalidate());
  q('[data-repro-import]').addEventListener('submit',event=>{ event.preventDefault(); action('restore',async()=>{
    const file=q('[data-repro-file]').files[0]; if (!file || file.size>8*1024*1024) throw new Error('请选择不超过 8 MiB 的 JSON 研究包。');
    let value; try { value=JSON.parse(await file.text()); } catch { throw new Error('文件不是有效的 JSON，请重新选择导出的研究包。'); }
    if (q('[data-repro-file]').files[0] !== file) return;
    const saved=await restore.run(value); if (!saved) return;
    q('[data-repro-import-status]').textContent='研究包已恢复；可以检查范围后复算。'; await history(saved.package_id);
  }); });
  q('[data-repro-packages]').addEventListener('change',()=>{ historyVersion++; recompute.invalidate(); renderReceipt(root,packages.find(p=>p.package_id===q('[data-repro-packages]').value)); sync(); });
  q('[data-repro-recompute]').addEventListener('click',()=>action('recompute',async()=>{
    const id=q('[data-repro-packages]').value; const result=await recompute.run(id); if (!result) return;
    const saved=packages.find(p=>p.package_id===id); saved.receipt=result; renderReceipt(root,saved);
  }));
  root.dataset.reproductionReady='true'; sync(); action('history',()=>history());
}

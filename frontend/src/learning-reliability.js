import { readLocalJSON, userErrorMessage } from './local-request.js';
const catalogueFlights = new WeakMap();
export function readLearningCatalogue(fetcher, url, options = {}) {
  if (!['/api/research/learning', '/api/research/snapshots'].includes(url) || Object.keys(options).length) return readLocalJSON(fetcher, url, options);
  let flights = catalogueFlights.get(fetcher);
  if (!flights) { flights = new Map(); catalogueFlights.set(fetcher, flights); }
  if (!flights.has(url)) { const pending = readLocalJSON(fetcher, url).finally(() => flights.delete(url)); flights.set(url, pending); }
  return flights.get(url);
}
const mounted = new WeakMap();
const BASE = '/api/research/learning';
const labels = { OBSERVE:'继续积累样本', RECOMMENDED:'达到固定建议门槛', KEEP_POLICY:'保留原配置', INCOMPLETE:'尚有任务未排队', RUNNING:'后台运行中', AVAILABLE:'成对结果已校验', BLOCKED:'需要检查子任务或报告' };
const exclusionLabels = { INVALID_PROOF:'证据校验失败', UNVERIFIED:'证据未验证', SCOPE_MISMATCH:'配置或数据模式不同', UNKNOWN_IDENTITY:'身份缺失', OUTSIDE_WINDOW:'不在所选窗口', LATE_EVIDENCE:'截止时尚不可用', INVALID_WINDOW:'观察期无效', UNKNOWN_OUTCOME:'收益未知', UNFILLED:'未成交', DUPLICATE:'重复经济信号或结算', OVERLAPPING_WINDOW:'观察期重叠' };
const unresolvedEvidence = value => value.exclusions.some(row=>['INVALID_PROOF','UNVERIFIED'].includes(row.reason));
export function selectionGuard(read) {
  let revision = 0;
  return { invalidate() { revision++; }, begin() { const current = ++revision; const key = read(); return () => current === revision && key === read(); } };
}
export function normalizeAssessment(value) {
  if (!/^assessment-[a-f0-9]{64}$/.test(value?.assessment_id ?? '') || value.paper_only !== true || !['OBSERVE','RECOMMENDED','KEEP_POLICY'].includes(value.status) || !Number.isInteger(value.sample_count) || value.sample_count < 0 || value.sample_count > 200 || value.minimum_samples !== 20 || value.selected_review_ids?.length !== value.sample_count || !Array.isArray(value.exclusions) || !Array.isArray(value.counterexamples) || !Array.isArray(value.limits) || !value.statistics || Object.values(value.statistics).flat().some(item => item !== null && (typeof item !== 'number' || !Number.isFinite(item))) || (value.status === 'RECOMMENDED' && value.sample_count < 20)) throw new TypeError('样本评估的证据结构不完整，请重新读取。');
  if (value.status === 'RECOMMENDED' && unresolvedEvidence(value)) throw new TypeError('候选证据尚未完整校验，请修复后重新评估。');
  return value;
}
export function normalizePair(value) {
  if (!/^pair-[a-f0-9]{64}$/.test(value?.pair_id ?? '') || value.paper_only !== true || !['INCOMPLETE','RUNNING','AVAILABLE','BLOCKED'].includes(value.status) || !value.children || !Array.isArray(value.metrics) || value.metrics.some(row => ['left','right','delta'].some(key=>!Number.isFinite(row[key]))) || (value.status === 'AVAILABLE' && (!value.children.left || !value.children.right || Object.values(value.children).some(child => child.status !== 'completed' || child.report_status !== 'AVAILABLE')))) throw new TypeError('成对复跑尚无完整的已校验结果，请重新读取。');
  return value;
}
export function mountLearningReliability(root, {fetcher=globalThis.fetch}={}) {
  if (mounted.has(root)) return mounted.get(root);
  const doc=root.ownerDocument, q=selector=>root.querySelector(selector);
  const node=(tag,text='')=>{const value=doc.createElement(tag);value.textContent=text;return value;};
  const form=q('[data-cohort-form]'), pairForm=q('[data-pair-form]'), saved=q('[data-cohort-saved]'), savedPairs=q('[data-pair-saved]');
  const fields=target=>Object.fromEntries(new FormData(target));
  const csrf=q('[data-cohort-csrf]').value;
  const request=url=>readLearningCatalogue(fetcher,url);
  const post=(url,body)=>readLocalJSON(fetcher,url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,_csrf:csrf})});
  let assessment=null, pair=null, busy=false, disposed=false, reviews=[], pendingAssessment=false, pendingPair=false;
  const assessGuard=selectionGuard(()=>JSON.stringify([fields(form),saved.value]));
  const pairGuard=selectionGuard(()=>JSON.stringify([fields(pairForm),savedPairs.value]));
  const number=value=>Number.isFinite(value)?new Intl.NumberFormat('zh-CN',{maximumFractionDigits:6}).format(value):'未知';
  const percent=value=>Number.isFinite(value)?new Intl.NumberFormat('zh-CN',{style:'percent',maximumFractionDigits:3}).format(value):'未知';
  function status(message,error=false) { q('[data-cohort-status]').textContent=message;q('[data-cohort-status]').setAttribute('role',error?'alert':'status'); }
  function controls() {
    root.setAttribute('aria-busy',String(busy));
    root.querySelectorAll('button').forEach(button=>{button.disabled=busy;});
    q('[data-cohort-assess]').disabled=busy||!form.elements.anchor_review_id.value;
    q('[data-cohort-propose]').disabled=busy||assessment?.status!=='RECOMMENDED'||assessment.assessment_id!==saved.value;
    q('[data-pair-create]').disabled=busy||!pairForm.elements.review_id.value||!pairForm.elements.snapshot_ref.value;
    q('[data-pair-refresh]').disabled=busy||!savedPairs.value;
    q('[data-pair-resume]').disabled=busy||pair?.status!=='INCOMPLETE'||pair.pair_id!==savedPairs.value;
  }
  function table(caption,headers,rows) {
    const wrap=node('div');wrap.className='table-wrap';wrap.tabIndex=0;wrap.setAttribute('role','region');wrap.setAttribute('aria-label',caption+'，可横向滚动');
    const table=node('table');table.append(node('caption',caption));const head=node('thead'),tr=node('tr');
    headers.forEach(text=>{const th=node('th',text);th.scope='col';tr.append(th);});head.append(tr);table.append(head);
    const body=node('tbody');rows.forEach(values=>{const row=node('tr');values.forEach(value=>row.append(node('td',String(value))));body.append(row);});table.append(body);wrap.append(table);return wrap;
  }
  function renderAssessment(value) {
    assessment=normalizeAssessment(value);
    const stats=value.statistics;
    const target=q('[data-cohort-result]');target.replaceChildren(node('h4',`${unresolvedEvidence(value)?'候选证据待修复':labels[value.status]} · ${value.sample_count} / 20 个合格窗口`),node('p',`${value.start} 至 ${value.as_of} · ${value.scope.instrument} · ${Object.values(value.scope.data_modes).join(' / ')}`));
    if(unresolvedEvidence(value)) target.append(node('p','候选证据校验失败或尚未验证，已暂停生成建议。下方统计只描述仍可验证的样本；请修复证据后重新评估。'));
    target.append(table('历史纸面收益描述',['样本数','亏损 / 非亏损 / 零收益','均值','中位数','亏损比例95%区间'],[[value.sample_count,`${stats.losses} / ${stats.non_losses} / ${stats.zero}`,percent(stats.mean),percent(stats.median),stats.loss_interval_95?.map(percent).join(' 至 ')??'未知']]));
    target.append(node('h4','反例与排除记录'),node('p',value.counterexamples.length?`${stats.non_losses} 个非亏损反例，最多展示20个。`:'合格样本中尚无非亏损反例；这不证明未来仍会亏损。'));
    if(value.counterexamples.length) target.append(table('非亏损反例',['复盘引用','净收益'],value.counterexamples.map(row=>[row.review_id,percent(row.net_return)])));
    const details=node('details');details.append(node('summary',`${value.exclusions.length} 条排除记录与固定规则`));
    const list=node('ul');value.exclusions.forEach(row=>list.append(node('li',`${exclusionLabels[row.reason]??'未知排除原因'} · ${row.review_id}`)));details.append(list,...value.limits.map(text=>node('p',text)));target.append(details);
    target.append(node('p','仅当至少20个窗口、净收益均值小于零且亏损比例区间下界超过0.5，才建议预算和风险上限各收缩10%；仍需显式审核。'));
  }
  function renderPair(value) {
    pair=normalizePair(value);const target=q('[data-pair-result]');
    target.replaceChildren(node('h4',labels[value.status]),node('p',`${value.comparison_kind==='RETROSPECTIVE'?'回溯比较':'较晚窗口的描述性比较'} · ${value.pair_id}`));
    target.append(node('p',`共同数据 ${value.binding?.snapshot_ref??'未知'} · 截止 ${value.binding?.as_of??'未知'} · 父配置 ${value.binding?.left_policy_ref??'未知'} · 新配置 ${value.binding?.right_policy_ref??'未知'}`));
    const list=node('ul');for(const [side,child] of Object.entries(value.children)) {
      const li=node('li',`${side==='left'?'父版本':'新版本'} · ${child.status} · `);
      const link=node('a','打开任务详情');link.href=`/research/jobs?job_id=${encodeURIComponent(child.job_id)}`;li.append(link);
      if(child.report_status==='AVAILABLE'&&child.report_url===`/research/${child.run_id}/report/complete`) {const report=node('a','阅读报告');report.href=child.report_url;li.append(doc.createTextNode(' · '),report);}list.append(li);
    }target.append(list);
    if(value.status==='AVAILABLE') target.append(table('右侧新版本减去左侧父版本；有限数值指标',['指标','父版本','新版本','差值'],value.metrics.map(row=>[row.metric,number(row.left),number(row.right),number(row.delta)])));
    target.append(...(value.reasons??[]).map(text=>node('p',text)),...(value.limits??[]).map(text=>node('p',text)));
  }
  function options(select,items,empty) {
    const old=select.value;select.replaceChildren(Object.assign(node('option',empty),{value:''}),...items.map(([value,label])=>Object.assign(node('option',label),{value})));
    if(items.some(([value])=>value===old)) select.value=old;
  }
  function invalidateAssessment() {assessGuard.invalidate();assessment=null;q('[data-cohort-result]').replaceChildren(node('p','选择已变化，请重新评估或读取已保存记录。'));controls();}
  function invalidatePair() {pairGuard.invalidate();pair=null;q('[data-pair-result]').replaceChildren(node('p','选择已变化，请创建或读取对应成对任务。'));controls();}
  async function readAssessment() { const id=saved.value;if(!id){assessment=null;q('[data-cohort-result]').replaceChildren(node('p','选择已结算复盘和日期，查看样本覆盖、收益分布与反例。'));return;}invalidateAssessment();const current=assessGuard.begin();const value=await request(`${BASE}/assessments/${id}`);if(disposed||!current())return;form.elements.anchor_review_id.value=value.anchor_review_id;form.elements.start.value=value.start;form.elements.as_of.value=value.as_of;assessGuard.invalidate();renderAssessment(value); }
  async function readPair() {const id=savedPairs.value;if(!id){pair=null;q('[data-pair-result]').replaceChildren(node('p','批准配置后，可在同一份冻结输入上比较两个版本。'));return;}invalidatePair();const current=pairGuard.begin();const value=await request(`${BASE}/pairs/${id}`);if(disposed||!current())return;renderPair(value);}
  async function reload({restore=false}={}) {
    const [records,data,assessments,pairs]=await Promise.all([request(BASE),request('/api/research/snapshots'),request(`${BASE}/assessments`),request(`${BASE}/pairs`)]);
    if(disposed)return;reviews=records.reviews;
    const beforeAssessment=JSON.stringify([fields(form),saved.value]),beforePair=JSON.stringify([fields(pairForm),savedPairs.value]);
    options(form.elements.anchor_review_id,reviews.filter(row=>!row.source_kind&&row.settlement).map(row=>[row.review_id,`${row.observation.instrument} · ${row.as_of} · ${row.review_id}`]),'请选择普通纸面复盘');
    options(pairForm.elements.review_id,reviews.filter(row=>row.status==='APPROVED'&&row.source_kind!=='FACTOR_VALIDATION_MONITORING').map(row=>[row.review_id,`${row.source_kind==='LEARNING_COHORT'?'样本建议':'探索性试验'} · ${row.review_id}`]),'请选择已批准配置');
    options(pairForm.elements.snapshot_ref,data.snapshots.map(row=>[row.snapshot_ref,`${row.mode} · ${row.as_of.slice(0,10)} · ${row.snapshot_ref.slice(0,12)}`]),'请选择共同冻结数据');
    options(saved,assessments.assessments.map(row=>[row.assessment_id,`${labels[row.status]} · ${row.sample_count} 个窗口 · ${row.as_of}`]),'请选择已保存评估');
    options(savedPairs,pairs.pairs.map(row=>[row.pair_id,`${labels[row.status]} · ${row.pair_id.slice(-12)}`]),'请选择已保存成对任务');
    if(!form.elements.as_of.value) form.elements.as_of.value=new Date().toISOString().slice(0,10);
    if(!pairForm.elements.as_of.value) pairForm.elements.as_of.value=new Date().toISOString().slice(0,10);
    if(restore) {if(!saved.value&&assessments.assessments[0])saved.value=assessments.assessments[0].assessment_id;if(!savedPairs.value&&pairs.pairs[0])savedPairs.value=pairs.pairs[0].pair_id;await readAssessment();await readPair();}
    if(!restore&&beforeAssessment!==JSON.stringify([fields(form),saved.value]))invalidateAssessment();
    if(!restore&&beforePair!==JSON.stringify([fields(pairForm),savedPairs.value]))invalidatePair();
    root.dataset.cohortReady='true';status('本地记录已就绪；提交前可继续核对样本范围与版本。');controls();
  }
  async function readPendingSelections() {
    while(!disposed&&(pendingAssessment||pendingPair)) {
      if(pendingAssessment){pendingAssessment=false;await readAssessment();}
      if(pendingPair){pendingPair=false;await readPair();}
    }
  }
  async function action(callback) {if(busy||disposed)return;busy=true;controls();try{await callback();}catch(error){if(!disposed)status(`需要处理：${userErrorMessage(error)} 输入已保留，请重新读取后重试。`,true);}finally{busy=false;if(!disposed){controls();if(pendingAssessment||pendingPair)await action(readPendingSelections);}}}
  form.addEventListener('input',invalidateAssessment);form.addEventListener('change',invalidateAssessment);pairForm.addEventListener('input',invalidatePair);pairForm.addEventListener('change',invalidatePair);
  form.elements.anchor_review_id.addEventListener('change',()=>{const row=reviews.find(row=>row.review_id===form.elements.anchor_review_id.value);if(row)form.elements.start.value=row.observation.decision_cutoff.slice(0,10);assessGuard.invalidate();});
  form.addEventListener('submit',event=>{event.preventDefault();action(async()=>{const current=assessGuard.begin();const value=await post(`${BASE}/assessments`,fields(form));if(disposed||!current())return;await reload();if(disposed||!current())return;saved.value=value.assessment_id;renderAssessment(value);status('评估已保存；请检查反例、排除原因和固定规则。');});});
  saved.addEventListener('change',()=>{invalidateAssessment();pendingAssessment=true;action(readPendingSelections);});
  savedPairs.addEventListener('change',()=>{invalidatePair();pendingPair=true;action(readPendingSelections);});
  q('[data-cohort-propose]').addEventListener('click',()=>action(async()=>{if(!assessment||assessment.assessment_id!==saved.value){invalidateAssessment();return;}const current=assessGuard.begin();const value=await post(`${BASE}/assessments/${assessment.assessment_id}/propose`,{});if(disposed||!current())return;doc.dispatchEvent(new CustomEvent('finathink:learning-review',{detail:value.review_id}));status('建议已生成并打开上方审核；批准前配置不变。');}));
  pairForm.addEventListener('submit',event=>{event.preventDefault();action(async()=>{const current=pairGuard.begin();const value=await post(`${BASE}/pairs`,fields(pairForm));if(disposed||!current())return;await reload();if(disposed||!current())return;savedPairs.value=value.pair_id;renderPair(value);status('成对任务已保存，可刷新状态或从任务页查看进度。');});});
  q('[data-pair-refresh]').addEventListener('click',()=>action(readPair));
  q('[data-pair-resume]').addEventListener('click',()=>action(async()=>{if(!pair||pair.pair_id!==savedPairs.value){invalidatePair();return;}const current=pairGuard.begin();const value=await post(`${BASE}/pairs/${pair.pair_id}/resume`,{});if(!disposed&&current())renderPair(value);}));
  q('[data-cohort-refresh]').addEventListener('click',()=>action(()=>reload({restore:true})));
  const changed=()=>action(()=>reload());doc.addEventListener('finathink:learning-changed',changed);
  const ready=action(()=>reload({restore:true}));
  const api={ready,dispose(){disposed=true;assessGuard.invalidate();pairGuard.invalidate();doc.removeEventListener('finathink:learning-changed',changed);}};mounted.set(root,api);doc.defaultView?.addEventListener('pagehide',api.dispose,{once:true});return api;
}

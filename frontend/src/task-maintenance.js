import { readLocalJSON, userErrorMessage } from './local-request.js';
const mounted = new WeakMap();
const states = { queued:'等待运行',running:'运行中',retryable:'等待重试',completed:'运行完成',failed:'运行失败',cancelled:'已取消',external_waiting:'等待外部回传' };
export function mountTaskMaintenance(root,{fetcher=globalThis.fetch}={}) {
  if(mounted.has(root))return mounted.get(root);
  const doc=root.ownerDocument,q=selector=>root.querySelector(selector);
  const node=(tag,text='')=>{const item=doc.createElement(tag);item.textContent=text;return item;};
  let cursor=null,next=null,busy=false,disposed=false;
  const number=value=>Number.isFinite(value)?new Intl.NumberFormat('zh-CN',{maximumFractionDigits:3}).format(value):'未知';
  const stamp=value=>{const date=new Date(value);return Number.isFinite(date.getTime())?new Intl.DateTimeFormat('zh-CN',{timeZone:'UTC',dateStyle:'short',timeStyle:'short'}).format(date)+' UTC':'时间未知';};
  const status=(message,error=false)=>{q('[data-maintenance-status]').textContent=message;q('[data-maintenance-status]').setAttribute('role',error?'alert':'status');};
  function controls(){root.setAttribute('aria-busy',String(busy));root.querySelectorAll('button').forEach(button=>{button.disabled=busy;});q('[data-maintenance-next]').disabled=busy||!next;q('[data-maintenance-first]').disabled=busy||!cursor;}
  async function load(){
    if(busy||disposed)return;busy=true;controls();status('正在读取队列与已保存指标…');
    try {
      const [value,page]=await Promise.all([readLocalJSON(fetcher,'/api/research/maintenance'),readLocalJSON(fetcher,'/api/research/jobs/summaries?limit=20'+(cursor?'&cursor='+encodeURIComponent(cursor):''))]);
      if(disposed)return;
      const facts=q('[data-maintenance-facts]');facts.replaceChildren();
      const rows=[...Object.entries(value.counts).map(([state,count])=>[states[state]??state,number(count)]),['已过期租约',number(value.expired_leases)],['退避等待 / 取消请求',`${number(value.backoff_waiting)} / ${number(value.cancel_pending)}`],['磁盘可用空间（MiB）',number(value.resources.disk_free_bytes===null?null:value.resources.disk_free_bytes/1048576)],['数据库 / WAL（KiB）',`${number(value.resources.database_bytes===null?null:value.resources.database_bytes/1024)} / ${number(value.resources.wal_bytes===null?null:value.resources.wal_bytes/1024)}`],['运行进程内存 / CPU','未知（未采集）'],['报告校验缓存命中 / 完整校验',`${number(value.cache.hits)} / ${number(value.cache.misses)}`]];
      rows.forEach(([key,text])=>facts.append(node('dt',key),node('dd',text)));
      const stages=q('[data-maintenance-stages]');stages.replaceChildren();const list=node('ul');
      value.slow_stages.forEach(row=>list.append(node('li',`${row.stage} · ${number(row.seconds)} 秒 · ${row.job_id}`)));
      stages.append(value.slow_stages.length?list:node('p','尚无已记录的阶段耗时；任务完成后可重新读取。'));
      q('[data-maintenance-jobs]').replaceChildren(...page.jobs.map(job=>{const li=node('li');const a=node('a',`${job.instrument} · ${job.question} · ${states[job.status]??job.status} · ${stamp(job.created_at)} · ${job.job_id.slice(-8)} · 报告尚未校验`);a.href=`/research/jobs?job_id=${encodeURIComponent(job.job_id)}`;li.append(a);return li;}));
      if(!page.jobs.length)q('[data-maintenance-jobs]').append(node('li','当前工作区尚无任务，可从研究任务页开始。'));
      next=page.next_cursor;q('[data-maintenance-page]').textContent=`本次分页范围共 ${page.total} 项 · 本页 ${page.jobs.length} 项`;
      q('[data-maintenance-limits]').textContent=value.limits.join(' ');
      root.dataset.maintenanceReady='true';status(`已读取 ${stamp(value.as_of)} 的本地状态；未调度或恢复任何任务。`);
    }catch(error){if(!disposed)status(`需要处理：${userErrorMessage(error)} 请重新读取维护信息。`,true);}
    finally{busy=false;if(!disposed)controls();}
  }
  q('[data-maintenance-refresh]').addEventListener('click',()=>{cursor=null;load();});
  q('[data-maintenance-first]').addEventListener('click',()=>{cursor=null;load();});
  q('[data-maintenance-next]').addEventListener('click',()=>{if(next){cursor=next;load();}});
  const api={ready:load(),dispose(){disposed=true;}};mounted.set(root,api);doc.defaultView?.addEventListener('pagehide',api.dispose,{once:true});return api;
}

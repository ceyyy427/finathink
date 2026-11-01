"""Read-only scorecards and explicit actions on existing durable studies."""
import html
import json
from urllib.parse import quote

from .asset_catalog import ResearchAssetCatalog
from .factor_library import FactorLibrary
from .factor_validation_sample import SAMPLE_SPEC


def esc(value):
    return html.escape(str(value), quote=True)


def suite_page(service, csrf, study_id=None):
    library=FactorLibrary()
    statuses={"PASS":"通过登记条件","FALSIFIED":"阈值不符","NEEDS_DATA":"资料或有效样本不足","EXECUTION_FAILED":"执行失败"}
    rows=''.join(f'<label><input type="checkbox" name="factor" value="{esc(r["factor_id"])}">{esc(r["title"])}</label>' for r in library.entries())
    body=f'''<section class="suite-page"><header><p class="eyebrow">FACTOR VALIDATION</p><h1>因子检验与评分卡</h1><p>先登记试验，再看结果；根据验证集冻结候选，显式读取最终测试。</p></header>
<p><a href="/research/factor-library">阅读因子定义与数据要求</a> · <a href="/research/experiments">因子实验历史</a></p>
<section class="panel"><h2>登记一批研究</h2><form data-suite-form method="post" action="/api/research/factor-suites"><input type="hidden" name="csrf" value="{esc(csrf)}">
<label>研究问题<textarea name="question" maxlength="2000" required>检验当前因子的关联、成本敏感性与稳定性</textarea></label>
<label>试验家族标识<input name="family_key" required maxlength="128" value="factor-research"></label>
<fieldset><legend>选择1至24个候选</legend><div class="suite-choices">{rows}</div></fieldset>
<label>研究数据<select name="mode"><option value="sample">固定合成 SAMPLE（64标的×756观察点）</option><option value="user">已保存的用户面板</option></select></label>
<p>合成样例用于工程复现。评分是滞后分层诊断，实际成交账本通过既有因子研究流程核验。</p>
<details><summary>用户面板合同与附件</summary><label>面板合同 JSON<textarea name="contract" spellcheck="false"></textarea></label><label>字段附件引用 JSON 数组<textarea name="attachments" spellcheck="false">[]</textarea></label></details>
<button type="submit">登记候选并提交后台检验</button><p role="status" aria-live="polite" data-suite-status>读取页面不会创建研究任务。</p></form></section>'''
    if study_id:
        study=service.factor_study(study_id)
        if study['spec'].get('version')!='factor-workbench.suite.v1':
            raise ValueError('研究不是因子评分套件')
        result=study.get('test_result') or study.get('validation') or {}
        suite=result.get('suite_validation') or {}
        trials=suite.get('trials',[])
        body+=f'<section class="panel section" data-suite-record data-state="{esc(study["state"])}"><h2>研究状态：{esc(study["state"])}</h2><p>研究标识 <code>{esc(study_id)}</code></p><p>数据模式：{esc(study["spec"]["data_mode"])}</p>'
        if not trials:body+='<p class="empty-state">研究尚未发布结果，可稍后刷新。失败与取消记录会保留。</p>'
        for trial in trials:
            metrics=trial.get('metrics') or {}
            numbers=metrics.get('metrics', {})
            labels=(('mean_ic','截面秩IC'),('icir','ICIR（未年化）'),('n_dates','有效IC日期数'),
                    ('mean_net_top_return','平均净分层收益（小数）'),('mean_turnover','平均单边换手（小数）'),
                    ('mean_cost','平均逐腿成本（小数）'),('coverage','有效覆盖率（小数）'),('n_pairs','有效资产日期对'),
                    ('monotonicity','分层单调性'))
            table='<table><caption>本次验证的关键指标</caption><tbody>'+''.join('<tr><th scope="row">'+esc(label)+'</th><td>'+esc('无法计算' if numbers.get(key) is None else format(numbers[key],'.6g'))+'</td></tr>' for key,label in labels)+'</tbody></table>'
            body+=f'<details data-suite-trial><summary>{esc(trial["factor_id"])} · {esc(statuses.get(trial["status"],trial["status"]))}</summary><p>{esc(trial.get("failure_reason") or "已完成本次指标计算")}</p><h3>评分、有效样本与计算范围</h3>{table}<pre>{esc(json.dumps(metrics,ensure_ascii=False,indent=2))}</pre></details>'
        if suite:
            body+=f'<details><summary>试验家族与多重检验</summary><pre>{esc(json.dumps(suite.get("multiple_testing",{}),ensure_ascii=False,indent=2))}</pre></details><p>金融用途仍需显式审核。成交账本：{esc(suite.get("execution_ledger_status","NOT_RUN"))}</p>'
        if study['state']=='VALIDATED':
            options=''.join(f'<option value="{esc(r["experiment_id"])}">{esc(r["experiment_id"])}</option>' for r in result.get('experiments',[]) if not r.get('failure_reason'))
            if options:body+=f'<form data-suite-action="freeze"><label>冻结候选以进行最终复验<select name="candidate_id">{options}</select></label><button>冻结候选</button></form>'
        if study['state']=='FROZEN':body+='<form data-suite-action="test"><p>读取后该区间会登记为已消费测试。</p><button>运行一次最终测试</button></form>'
        job=study.get('test_job') or study.get('validation_job') or {}
        if job.get('status') in {'queued','running','retry_wait'}:
            body+=f'<form data-suite-action="cancel" data-job="{esc(job["job_id"])}"><button>取消当前任务</button></form>'
        for key in ('validation_job','test_job'):
            job=study.get(key) or {}
            if job.get('report_status')=='AVAILABLE' and str(job.get('report_url','')).startswith('/research/'):
                body+=f'<p><a href="{esc(job["report_url"])}">打开离线研究报告</a></p>'
        body+='<p role="status" aria-live="polite" data-suite-action-status></p><button data-suite-refresh>刷新研究状态</button></section>'
    else:body+='<section class="panel section"><h2>已登记研究</h2><ul>'+''.join(f'<li><a href="/research/factor-suites?study={quote(r["study_id"],safe="")}">{esc(r["spec"]["question"])} · {esc(r["state"])}</a></li>' for r in service.factor_studies.list() if r['spec'].get('version')=='factor-workbench.suite.v1')+'</ul></section>'
    cases=json.loads(ResearchAssetCatalog.packaged().read_text('factor-counterexamples', max_bytes=1048576))['cases']
    body+='<section class="panel section"><h2>反例与研究边界</h2><p>每轮发布记录绑定反例的实际复现摘要；坏数据与执行错误分别保留原因。</p>'+''.join('<details data-suite-counterexample><summary>'+esc(c['title'])+'</summary><p>'+esc(c['correction'])+'</p><p>'+esc(c['scope'])+'</p></details>' for c in cases)+'</section>'
    return '''<style>.suite-page{max-width:1100px;margin:auto}.suite-page form{display:grid;gap:14px}.suite-page textarea{width:100%;min-height:90px;box-sizing:border-box}.suite-page input,.suite-page select{max-width:100%}.suite-choices{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,230px),1fr));gap:8px;max-height:320px;overflow:auto}.suite-page pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:540px;overflow:auto}.suite-page code{overflow-wrap:anywhere}.suite-page details{margin:16px 0}.suite-page :focus-visible{outline:3px solid #2563eb;outline-offset:3px}.suite-page table{width:100%;border-collapse:collapse}.suite-page th,.suite-page td{text-align:left;padding:8px;border-bottom:1px solid #dce5ee;overflow-wrap:anywhere}.suite-page fieldset{min-width:0;border:1px solid #dce5ee;border-radius:10px}</style>'''+body+f'<script type="application/json" data-suite-default>{json.dumps(SAMPLE_SPEC)}</script></section>'

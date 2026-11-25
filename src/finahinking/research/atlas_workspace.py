"""Read-only projections for the research atlas. Source reports own all measured values."""

from functools import lru_cache
from html import escape
from math import isfinite
from numbers import Real
from urllib.parse import quote, urlencode

from .atlas_graphics import equity_chart, observation_labels, process_map, sparkline
from .contracts import stable_digest
from .strategy_catalog import StrategyCatalog

VIEWS = {"/": "总览", "/research/atlas/evidence": "证据", "/research/atlas/factors": "因子实验",
         "/research/atlas/runs": "研究记录"}
RUNS = "/research/atlas/runs"


def masthead(page, menu_icon, search_icon):
    tabs = []
    for href, label in VIEWS.items():
        current = ' aria-current="page"' if href == page else ''
        tabs.append(f'<a href="{href}"{current}>{label}</a>')
    return f'''<header class="topbar atlas-masthead"><div class="topbar-context"><a href="/" class="atlas-wordmark" aria-label="Finathink 首页"><i aria-hidden="true"></i><span>Finathink</span></a></div>
<nav class="atlas-tabs" aria-label="研究视图">{''.join(tabs)}</nav>
<div class="topbar-actions"><span class="atlas-local">本地工作区</span>
<button type="button" class="shell-search-open" data-shell-search-open aria-controls="shell-search" aria-label="搜索功能入口" hidden>{search_icon}</button>
<button type="button" class="shell-icon-button shell-menu" data-shell-menu aria-controls="shell-navigation" aria-label="展开导航" aria-expanded="false" hidden>{menu_icon}<span>工具目录</span></button>
<a class="atlas-directory-fallback" href="#shell-navigation">工具目录</a></div></header>'''


@lru_cache(maxsize=1)
def _titles():
    return {t['template_id']: t['title'] for t in StrategyCatalog().templates()}


def _strategy(entry):
    if entry['report_status'] != 'AVAILABLE':
        return {}
    return entry['manifest'].get('source_snapshot', {}).get('strategy_study', {})


def _data_mode(entry):
    if entry['report_status'] != 'AVAILABLE':
        return None
    source = entry['manifest'].get('source_snapshot', {})
    return (_strategy(entry) or {}).get('data_mode') or source.get('data_mode')


def _mode_label(mode):
    if mode in {'SAMPLE', 'SYNTHETIC'}:
        return '合成样例'
    return {'PAPER': '纸面快照', 'CAPTURED': '用户快照', 'LIVE': '用户快照'}.get(mode, '')


def _title(entry):
    study = _strategy(entry)
    if study:
        return _titles().get(study.get('binding', {}).get('template_id'), '纸面策略研究')
    if entry['report_status'] == 'AVAILABLE':
        source = entry['manifest'].get('source_snapshot', {})
        if source.get('factor_study'):
            return '因子检验'
        if source.get('discovery'):
            return '研究问题与假设'
    return '本地研究'


def _run_url(run_id):
    return RUNS + '?' + urlencode({'run': run_id})


def _amount(value):
    return isinstance(value, Real) and not isinstance(value, bool) and isfinite(value) and value > 0


def normalized_marks(result):
    """Only compare a complete matching calendar, never match observations by position."""
    marks = (result.get('ledger') or {}).get('marks', [])
    if not marks or not all(isinstance(m, dict) and isinstance(m.get('timestamp'), str)
                            and _amount(m.get('equity')) for m in marks):
        return []
    dates = [m['timestamp'] for m in marks]
    if dates != sorted(set(dates)):
        return []
    benchmark = result.get('benchmark') or {}
    other = benchmark.get('marks', [])
    comparable = (benchmark.get('status') == 'COMPLETE' and len(other) == len(marks)
                  and all(isinstance(m, dict) and m.get('timestamp') == d and _amount(m.get('equity'))
                          for m, d in zip(other, dates)))
    return [(d, m['equity']/marks[0]['equity'], other[i]['equity']/other[0]['equity'] if comparable else None)
            for i, (d, m) in enumerate(zip(dates, marks))]


def _status(entry):
    if entry['report_status'] != 'AVAILABLE':
        return '报告缺失' if entry['report_status'] == 'MISSING' else '校验异常'
    result = _strategy(entry).get('result', {})
    return {'COMPLETE': '已发布', 'NEEDS_DATA': '待补数据', 'RESEARCH_ONLY': '理论研究',
            'BLOCKED_RISK': '风控拦截'}.get(result.get('status'), '已归档')


def recent_rows(entries):
    rows = []
    for entry in entries:
        points = normalized_marks(_strategy(entry).get('result', {}))
        provenance = _mode_label(_data_mode(entry))
        stamp = (entry.get('finished_at') or entry['created_at']).replace('T', ' ')[:16]
        run_id = entry['run_id']
        rows.append(f'''<tr><th scope="row"><a href="{_run_url(run_id)}">{escape(_title(entry))}</a>
<small>{escape(run_id[-10:])}{" · " + provenance if provenance else ""}</small></th>
<td>{sparkline([r[1] for r in points])}</td><td><span class="atlas-state">{_status(entry)}</span></td><td><time>{escape(stamp)}</time></td></tr>''')
    if not rows:
        return '''<div class="atlas-inbox-empty"><p>还没有已发布的研究。</p><span>提交一次纸面策略后，净值和报告会出现在这里。</span>
<a href="/research/strategies">创建纸面策略 <b aria-hidden="true">↗</b></a></div>'''
    return ('''<div class="atlas-table-scroll"><table class="atlas-table"><thead><tr><th>研究名称</th><th>净值轨迹</th><th>报告状态</th><th>更新时间 · UTC</th></tr></thead><tbody>'''
            + ''.join(rows) + '</tbody></table></div>')


def overview_plate(repo):
    entries = repo.list_runs(limit=3)
    if entries:
        last = entries[0]
        href, action = _run_url(last['run_id']), '继续研究'
        question = f"{_title(last)} · 复盘"
        subtitle = ("沿着这份账本，核对成交、净值与可比基准。"
                    if _strategy(last) else "回到归档，核对输入、证据与报告状态。")
    else:
        href, action = '/research/atlas/factors', '打开因子实验'
        question, subtitle = '价格动量能持续多久？', '从一组价格观测，追踪到一次样本外检验。'
    return f'''<header class="overview-header atlas-cover">
<div class="atlas-lead"><h1>研究总览</h1><h2>{escape(question)}</h2><p>{escape(subtitle)}</p>
<a class="atlas-action" href="{href}">{action}<span aria-hidden="true">⟶</span></a></div>{process_map()}</header>
<section class="atlas-recent" aria-labelledby="atlas-recent-title"><div class="atlas-section-heading"><h2 id="atlas-recent-title">最近研究</h2>
<a href="{RUNS}">查看研究记录 <span aria-hidden="true">↗</span></a></div>{recent_rows(entries)}</section>'''


@lru_cache(maxsize=1)
def _example_result():
    # Explicit read-only example. No snapshots, jobs or learning receipts are written.
    from finahinking.data.research_panel import build_research_panel

    from .snapshot_tools import local_research_policy
    from .strategy_runner import execute_strategy
    from .strategy_sample import strategy_sample

    snapshot, attachments, contract, classes = strategy_sample()
    panel = build_research_panel(snapshot, attachments, contract)
    return execute_strategy('equal-weight', panel, {}, asset_classes=classes, policy=local_research_policy())


def _selector(entries, selected, example):
    options = [f'<option value="">{"选择一份报告" if entries else "尚无归档报告"}</option>']
    for item in entries:
        run = item['run_id']
        attr = ' selected' if run == selected else ''
        options.append(f'<option value="{escape(run, quote=True)}"{attr}>{escape(_title(item))} · {escape(run[-10:])}</option>')
    return f'''<div class="atlas-run-controls"><form action="{RUNS}" method="get"><label for="atlas-run">归档报告</label>
<select id="atlas-run" name="run" required>{''.join(options)}</select><button type="submit"{"" if entries else " disabled"}>读取</button></form>
<a href="{RUNS}?example=equal-weight"{' aria-current="true"' if example else ''}>查看内置账本示例 ↗</a></div>'''


def _metrics(rows):
    if not rows:
        return [('累计收益', '—'), ('最大回撤', '—'), ('相对基准收益', '—')]
    peak, drawdown = rows[0][1], 0.
    for _, nav, _ in rows:
        peak = max(peak, nav)
        drawdown = min(drawdown, nav / peak - 1)
    relative = rows[-1][1] - rows[-1][2] if rows[-1][2] is not None else None
    def percent(value):
        return f'{value:+.2%}' if value else '0.00%'
    return [('累计收益', percent(rows[-1][1] - 1)), ('最大回撤', percent(drawdown)),
            ('相对基准收益', '—' if relative is None else f'{relative * 100:+.2f} 个百分点')]


def _comparison(rows, metrics, *, names, title):
    table = ''
    present = [i for i in range(len(names)) if rows and rows[0][i+1] is not None]
    if rows:
        parts = []
        for row, stamp in zip(rows, observation_labels(rows)):
            cells = ''.join(f'<td>{row[i+1]:.6f}</td>' for i in present)
            parts.append(f'<tr><th scope="row"><time datetime="{escape(row[0], quote=True)}">{escape(stamp)}</time></th>{cells}</tr>')
        headers = ''.join(f'<th>{names[i]}</th>' for i in present)
        table = f'''<details class="atlas-observations"><summary>查看逐期净值 · {len(rows)} 个观察点</summary>
<div class="atlas-table-scroll"><table class="atlas-table"><thead><tr><th>观察时间 · UTC</th>{headers}</tr></thead><tbody data-atlas-values>{''.join(parts)}</tbody></table></div></details>'''
    legend = ''.join(f'<span>{names[i]}</span>' for i in present)
    scrubber = (f'<label class="atlas-scrubber">逐期核对 <input type="range" min="0" max="{len(rows)-1}" value="{len(rows)-1}" data-atlas-scrub><output aria-live="polite"></output></label>'
                if len(rows) > 1 else '')
    return f'''<div class="atlas-run-grid"><figure class="atlas-performance"><figcaption>净值 <span>首期 = 1.00</span></figcaption>
<div class="atlas-legend">{legend}</div>{equity_chart(rows)}{scrubber}</figure>
<aside class="atlas-run-metrics"><h2>{title}</h2><dl>{''.join(f'<div><dt>{k}</dt><dd>{v}</dd></div>' for k,v in metrics)}</dl></aside></div>{table}'''


def render_runs(repo, query):
    run = (query.get('run') or [''])[0]
    example = query.get('example') == ['equal-weight']
    entries = repo.list_runs(limit=30)
    if run:
        entry = repo.get_run(run)
        if not any(item['run_id'] == run for item in entries):
            entries.append(entry)
    else:
        entry = entries[0] if entries and not example else None
    selected = entry['run_id'] if entry else None
    result = (_strategy(entry).get('result', {}) if entry else _example_result() if example else {})
    rows = normalized_marks(result)
    if entry:
        label = _title(entry)
        notice = '仅展示已发布纸面账本的记录区间。'
        if entry['report_status'] != 'AVAILABLE':
            notice = '原报告缺失或校验失败，请从备份恢复后再核对净值。'
        elif not result:
            notice = '此研究没有策略账本，可打开完整报告查看其证据。'
        elif result.get('status') != 'COMPLETE':
            notice += ' 这次策略的状态为' + _status(entry) + '，已有数值仅覆盖记录到的部分。'
        provenance = _mode_label(_data_mode(entry))
        if provenance:
            notice = provenance + ' · ' + notice
    elif example:
        label, notice = '股票与 ETF 等权组合', '内置合成样例 · 现场读取固定输入的模拟账本，指标覆盖图中全部观测。'
    else:
        label, notice = '', '选择本地归档报告后，可逐日核对净值、费用和原始证据。'
    actions = ['<a href="/research/strategies">纸面策略</a>']
    if entry and entry['report_status'] == 'AVAILABLE':
        if _strategy(entry):
            actions.append(f'<a href="/research/learning?{urlencode({"strategy_run": selected})}#strategy-learning">观察与结算</a>')
        if 'complete_report.html' in entry['manifest'].get('files', {}):
            actions.append(f'<a class="atlas-report" download="Finathink-{escape(selected)}.html" href="/research/{quote(selected,safe="")}/report/complete">导出离线报告 ↓</a>')
    return f'''<article class="atlas-run" data-atlas-review><header class="atlas-title"><h1>运行复盘</h1>{f'<span>{escape(label)}</span>' if label else ''}</header>
{_selector(entries, selected, example)}<p class="atlas-context">{escape(notice)}</p>
{_comparison(rows, _metrics(rows), names=('策略净值','可比基准'), title='账本指标')}
<nav class="atlas-run-actions" aria-label="复盘操作">{''.join(actions)}</nav></article>'''


def render_pair(pair, original):
    series = pair['series']
    names = ('原政策', '已批准政策', '共同基准')
    fields = ('baseline', 'candidate', 'benchmark')
    if series and not all(_amount(mark.get(field)) for mark in series for field in fields):
        raise ValueError('复跑净值未通过校验')
    rows = [(mark['timestamp'], *(mark[field]/series[0][field] for field in fields)) for mark in series]
    metrics = [
        ('原政策累计收益', f'{rows[-1][1]-1:+.2%}' if rows else '—'),
        ('新政策累计收益', f'{rows[-1][2]-1:+.2%}' if rows else '—'),
        ('收益变化', f'{(rows[-1][2]-rows[-1][1])*100:+.2f} 个百分点' if rows else '—'),
    ]
    run = pair['run_id']
    report = f'/research/{quote(run, safe="")}/report/complete'
    learning = '/research/learning?' + urlencode({'strategy_run': run}) + '#strategy-learning'
    original_ready = (original and original['report_status'] == 'AVAILABLE'
                      and stable_digest(original['manifest']) == pair['frozen']['report_digest']
                      and 'complete_report.html' in original['manifest'].get('files', {}))
    download = (f'<a class="atlas-report" href="{report}" download="Finathink-{escape(run)}.html">导出原始报告 ↓</a>'
                if original_ready else '<span>原报告待恢复，可从已保存的对照证据核对本次复跑。</span>')
    status = '本次两侧账本缺少可比较的净值序列。' if not rows else ''
    provenance = _mode_label(pair['scope'].get('data_mode'))
    provenance = provenance + ' · ' if provenance else ''
    return f'''<article class="atlas-run" data-atlas-review><header class="atlas-title"><h1>运行复盘</h1><span>政策版本对照</span></header>
<p class="atlas-context">{provenance}回溯比较 · 两侧使用同一份冻结面板、模板、成本口径和基准。观察期为原账本区间。{status}</p>
{_comparison(rows, metrics, names=names, title='政策对照')}
<nav class="atlas-run-actions" aria-label="复盘操作"><a href="{_run_url(run)}">查看原账本</a><a href="{learning}">返回策略审核</a>
{download}</nav></article>'''

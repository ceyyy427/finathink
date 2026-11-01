"""Read-only Chinese knowledge and preparation, using the canonical index."""
from __future__ import annotations

import html
import json
import sqlite3
from urllib.parse import urlencode

from .asset_contracts import AssetLoadError
from .knowledge_contracts import DOMAINS
from .knowledge_index import KnowledgeIndexUnavailable

KINDS = {'CONCEPT': '基础概念', 'METHOD': '研究方法', 'FACTOR': '因子假设', 'STRATEGY': '组合规则', 'RISK': '风险检查', 'CASE': '案例'}
CLAIMS = {'DEFINITION': '定义解释', 'MATHEMATICAL': '数学关系', 'EMPIRICAL': '经验假设', 'ENGINEERING': '工程流程'}
LEVELS = {'DEFINITION_CHECKED': '定义已核对', 'SOURCE_REPORTED': '来源中的研究假设'}
CSS = '''
.quant-knowledge{max-width:1120px;margin:0 auto}.knowledge-hero{padding:20px 0 24px;border-bottom:1px solid var(--border)}
.knowledge-hero h1{margin:8px 0}.knowledge-metrics{display:flex;gap:24px;flex-wrap:wrap;font-size:.9rem;color:var(--text-secondary)}
.knowledge-filter{display:grid;grid-template-columns:2fr 1fr 1fr;gap:14px;align-items:end}.knowledge-filter .knowledge-query{grid-column:1/-1}
.knowledge-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin:20px 0}
.knowledge-tile{border:1px solid var(--border);background:var(--surface);border-radius:12px;padding:22px;min-width:0;transition:box-shadow .18s,border-color .18s}
.knowledge-tile:hover{border-color:var(--focus);box-shadow:var(--shadow-sm)}.knowledge-tile h2,.knowledge-tile h3{margin:8px 0 10px;font-size:1.1rem}
.knowledge-tile p{margin:8px 0}.knowledge-tag{font-size:.78rem;color:var(--information);font-weight:650}.knowledge-alias{font-size:.8rem;color:var(--text-muted);overflow-wrap:anywhere}
.knowledge-domain-nav{display:flex;flex-wrap:wrap;gap:8px;margin:18px 0}.knowledge-domain-nav a{padding:7px 12px;border:1px solid var(--border);border-radius:20px;text-decoration:none;min-height:40px}
.knowledge-domain-nav a[aria-current=true]{background:var(--information);color:white}.knowledge-pagination{display:flex;gap:16px;align-items:center;margin:18px 0}
.knowledge-detail{max-width:850px;margin:auto}.knowledge-detail section{margin:22px 0}.knowledge-detail details{border-top:1px solid var(--border);padding:14px 0}.knowledge-detail summary{cursor:pointer;font-weight:650}
.knowledge-detail ol li{margin:10px 0}.knowledge-detail pre,.knowledge-detail code{overflow-wrap:anywhere;white-space:pre-wrap}.knowledge-source{overflow-wrap:anywhere;margin:16px 0}
.knowledge-formula{background:var(--surface-elevated);border-left:3px solid var(--information);padding:18px}.knowledge-status{min-height:28px;margin:14px 0}
@media(max-width:720px){.knowledge-grid,.knowledge-filter{grid-template-columns:1fr}.knowledge-tile{padding:18px}.knowledge-metrics{gap:14px}.quant-knowledge{width:100%}}
@media(prefers-reduced-motion:reduce){.knowledge-tile{transition:none}}
'''


def esc(value):
    return html.escape(str(value), quote=True)


def _link(item, title=None):
    return f'<a href="/knowledge/items/{esc(item["canonical_id"])}">{esc(title or item["title"])}</a>'


def search_payload(index, query):
    q = query.get('q', [''])[0]
    filters = {k: query.get(k, [''])[0] for k in ('domain', 'kind', 'claim_kind', 'source_id')}
    offset = int(query.get('offset', ['0'])[0])
    if not 0 <= offset <= 2000:
        raise ValueError('知识页码无效。')
    results = index.search(q, filters, limit=100)
    return {'schema_version': 'knowledge-search.v1', 'fingerprint': index.fingerprint, 'query': q, 'filters': filters,
            'total': len(results), 'offset': offset, 'page_size': 16,
            'items': [{k: i[k] for k in ('canonical_id', 'title', 'summary', 'domain', 'kind', 'claim_kind', 'evidence_level', 'aliases', 'matched_fields', 'source_refs', 'evidence_refs')} for i in results[offset:offset + 16]]}


def cards(items):
    return ''.join(f'<article class="knowledge-tile"><span class="knowledge-tag">{esc(DOMAINS[i["domain"]])} · {esc(KINDS[i["kind"]])}</span><h2>{_link(i)}</h2><p>{esc(i["summary"])}</p><p class="knowledge-alias">{esc(" · ".join(i["aliases"][:3]))}</p><p class="meta">{esc(LEVELS[i["evidence_level"]])}</p></article>' for i in items)


def pagination(payload):
    base = {'q': payload['query'], **payload['filters']}
    links = []
    for label, offset in (('上一页', payload['offset'] - 16), ('下一页', payload['offset'] + 16)):
        if 0 <= offset < payload['total']:
            links.append(f'<a href="/knowledge?{esc(urlencode({**base, "offset": offset}))}">{label}</a>')
    return '<nav class="knowledge-pagination" aria-label="知识结果分页">' + ''.join(links) + '</nav>'


def catalog_page(index, query):
    payload = search_payload(index, query)
    def select(name, label, options):
        selected = payload['filters'][name]
        return f'<label>{label}<select name="{name}"><option value="">全部</option>' + ''.join(f'<option value="{esc(k)}" {"selected" if k == selected else ""}>{esc(v)}</option>' for k, v in options.items()) + '</select></label>'
    exported = index.export()
    metrics = {"items": len(exported["items"]), "domains": len(DOMAINS), "paths": sum(not p.get("legacy") for p in exported["paths"]),
               "examples": len({e["example_id"] for i in exported["items"] for e in i["examples"]})}
    source_options = {s['source_ref']: f'{s["title"]} · L{s["start_line"]}' for s in exported['sources']}
    domains = '<a href="/knowledge"' + (' aria-current="true"' if not payload['filters']['domain'] else '') + '>全部主题</a>' + ''.join(f'<a href="/knowledge?domain={key}" {"aria-current=true" if payload["filters"]["domain"] == key else ""}>{esc(label)}</a>' for key, label in DOMAINS.items())
    paths = ''.join(f'<article class="knowledge-tile"><span class="knowledge-tag">{len(p["item_ids"])} 个学习节点</span><h3><a href="/knowledge/paths/{esc(p["path_id"])}">{esc(p["title"])}</a></h3><p>{esc(p["description"])}</p></article>' for p in index.paths() if not p.get('legacy'))
    legacy = ''.join(f'<li><a href="/knowledge/paths/{esc(p["path_id"])}">{esc(p["title"])}</a></li>' for p in index.paths() if p.get('legacy'))
    return (f'<style>{CSS}</style><div class="quant-knowledge" data-quant-knowledge data-knowledge-state="ready">'
            '<header class="knowledge-hero"><p class="eyebrow">概念 · 方法 · 可核对的来源</p><h1>量化知识库</h1><p class="lede">从一个问题开始，理解公式、核对条件，再把知识带入研究。</p>'
            f'<div class="knowledge-metrics"><span>{metrics["items"]} 个中文词条</span><span>{metrics["domains"]} 个主题域</span><span>{metrics["paths"]} 条学习路径</span><span>{metrics["examples"]} 个手算示例</span></div></header>'
            f'<nav class="knowledge-domain-nav" aria-label="知识主题">{domains}</nav>'
            '<form class="knowledge-filter" data-knowledge-search method="get" action="/knowledge">'
            f'<label class="knowledge-query">搜索中文概念、英文术语或研究问题<input name="q" maxlength="200" value="{esc(payload["query"])}" placeholder="例如：回撤、Rank IC、公告之后何时可用" autocomplete="off"></label>'
            + select('domain', '主题域', DOMAINS) + select('kind', '内容类型', KINDS) + select('claim_kind', '主张类型', CLAIMS) + select('source_id', '参考来源', source_options)
            + '<button type="submit">检索知识</button><a href="#learning-paths">按路径学习</a></form>'
            f'<p class="knowledge-status" data-knowledge-status role="status" aria-live="polite">找到 {payload["total"]} 个词条。</p>'
            '<button type="button" data-knowledge-retry hidden>重新检索</button>'
            f'<div class="knowledge-grid" data-knowledge-results>{cards(payload["items"])}</div><p data-knowledge-empty {"hidden" if payload["items"] else ""}>暂未找到匹配词条。试试缩短关键词，或清除筛选条件。</p>'
            f'<div data-knowledge-pagination>{pagination(payload)}</div><noscript><p>当前可直接阅读；提交检索表单将刷新结果。</p></noscript>'
            f'<section id="learning-paths"><h2>选择一条学习路径</h2><p>按顺序理解概念，随时回到前置知识；阅读与个人掌握记录分别保存。</p><div class="knowledge-grid">{paths}</div><details><summary>原有课程入口</summary><ul>{legacy}</ul></details></section>'
            '<p><a href="/api/knowledge/export">导出公开知识与来源（JSON）</a></p></div>')


def source_list(item):
    return '<ul>' + ''.join(f'<li class="knowledge-source"><a href="{esc(s["url"])}" target="_blank" rel="noreferrer">{esc(s["title"])} · L{s["start_line"]}–{s["end_line"]}</a><p>{esc(s["scope_note"])}</p><p class="meta">提交 {esc(s["commit"])} · 捕获 {esc(s["capture_date"])} · 许可引用 {esc(s["license_resource_id"])}</p><code>sha256:{esc(s["sha256"])}</code></li>' for s in item['source_refs']) + '</ul>'


def preparation(index, item_id):
    item = index.resolve(item_id)
    return {'schema_version': 'knowledge-preparation.v1', 'status': 'NEEDS_DATA', 'item_id': item['canonical_id'],
            'item_version': item['version'], 'catalog_fingerprint': index.fingerprint, 'required_fields': item['required_fields'],
            'source_refs': item['source_refs'], 'method_refs': item['method_refs'], 'snapshot_ref': None,
            'missing_fields': item['required_fields'], 'title': item['title'], 'application': item['application']}


def preparation_panel(index, item_id, *, continuation=True):
    data = preparation(index, item_id)
    question = f'研究「{data["title"]}」的适用条件。知识:{data["item_id"]}@{data["item_version"]}；目录:{data["catalog_fingerprint"]}；字段:{",".join(data["required_fields"])}；方法:{",".join(data["method_refs"])}。先核对数据与来源。'
    enter = '/research/jobs?' + urlencode({'knowledge': data['item_id'], 'question': question})
    return (f'<section class="card knowledge-detail" data-knowledge-preparation><h2>{esc(data["title"])} · 研究准备</h2>'
            '<p><strong>NEEDS_DATA · 待绑定数据快照</strong></p><p>尚未选择用于本次研究的数据。先核对下列字段及其可用时点，再选择已有快照或接入自己的数据接口。</p>'
            f'<p>{esc(data["application"])}</p><h3>所需字段</h3><p><code>{esc(" · ".join(data["required_fields"]))}</code></p>'
            f'<h3>方法引用</h3><p>{esc(" · ".join(data["method_refs"]))}</p><details><summary>随研究保留的来源与版本</summary>{source_list(data)}<p>知识版本 {esc(data["item_version"])}</p><code>{esc(data["catalog_fingerprint"])}</code></details>'
            f'<p><a href="/knowledge/items/{esc(data["item_id"])}">返回完整解释</a> · <a href="/settings/data-connections">配置数据接口</a></p>'
            + (f'<a class="button-secondary" href="{esc(enter)}">带入研究问题</a><p class="meta">进入后可核对数据与任务设置，明确提交时才开始研究。</p>' if continuation else '') + '</section>')


def detail_page(index, item):
    prerequisites = ''.join(f'<li>{_link(index.resolve(key))}</li>' for key in item['prerequisites']) or '<li>可以从本词条开始。</li>'
    formulas = ''.join(f'<div class="knowledge-formula"><pre>{esc(f["expression"])}</pre><p>{esc(f["meaning"])}</p></div>' for f in item['formulas'])
    examples = ''.join(f'<details><summary>{esc(e["title"])}</summary><p>{esc(e["derivation"])}</p><p>独立预期：<code>{esc(json.dumps(e["expected"], ensure_ascii=False))}</code></p><pre>{esc(json.dumps(e["inputs"], ensure_ascii=False, indent=2))}</pre></details>' for e in item['examples'])
    misconceptions = ''.join(f'<article><h3>{esc(c["claim"])}</h3><p>{esc(c["correction"])}</p><p class="meta">例子：{esc(c["scenario"])}</p></article>' for c in item['counterexamples'])
    legacy = ''.join(f'<li><a href="/knowledge/{esc(r["item_id"])}">原有讲解与个人自测 · {esc(r["item_id"])}</a></li>' for r in item['legacy_refs'])
    return (f'<style>{CSS}</style><article class="knowledge-detail"><p><a href="/knowledge">量化知识库</a> / {esc(DOMAINS[item["domain"]])}</p>'
            f'<h1>{esc(item["title"])}</h1><p class="lede">{esc(item["summary"])}</p><p class="knowledge-alias">{esc(" · ".join(item["aliases"]))}</p><p class="knowledge-tag">{esc(LEVELS[item["evidence_level"]])} · {esc(CLAIMS[item["claim_kind"]])}</p>'
            f'<section><h2>先理解它</h2><p>{esc(item["explanation"])}</p></section><section><h2>公式与口径</h2>{formulas}</section>'
            f'<section><h2>适用条件</h2><ul>{"".join(f"<li>{esc(a)}</li>" for a in item["assumptions"])}</ul><p>需要字段：<code>{esc(" · ".join(item["required_fields"]))}</code></p></section>'
            f'<section><h2>用一个例子核对</h2><p>{esc(item["example_text"])}</p>{examples}</section><section><h2>容易误解的地方</h2>{misconceptions}</section>'
            f'<section><h2>带入研究</h2><p>{esc(item["application"])}</p><a class="button-secondary" href="/knowledge/prepare?item={esc(item["canonical_id"])}">检查研究所需数据</a></section>'
            f'<details><summary>前置知识</summary><ul>{prerequisites}</ul></details><details><summary>来源、代码与版本</summary>{source_list(item)}<p>规范ID：{esc(item["canonical_id"])} · 版本 {esc(item["version"])}</p><code>{esc(index.fingerprint)}</code></details>'
            + (f'<details><summary>保留的原有学习入口</summary><ul>{legacy}</ul></details>' if legacy else '') + '</article>')


def path_page(index, path_id):
    path = index.path(path_id)
    steps = ''.join(f'<li>{_link(index.resolve(key))}<p>{esc(index.resolve(key)["summary"])}</p></li>' for key in path['item_ids'])
    return f'<style>{CSS}</style><article class="knowledge-detail"><p><a href="/knowledge#learning-paths">全部学习路径</a></p><h1>{esc(path["title"])}</h1><p class="lede">{esc(path["description"])}</p><ol>{steps}</ol><section class="card"><h2>完成后的练习</h2><p>{esc(path.get("practice", "逐项核对定义与前置知识，并通过原有入口保留个人自测记录。"))}</p></section></article>'


def route(app, method, clean, query):
    recognized = clean in {'/knowledge', '/knowledge/prepare', '/api/knowledge/search', '/api/knowledge/paths', '/api/knowledge/export', '/api/knowledge/preparation'} or clean.startswith(('/knowledge/items/', '/knowledge/paths/', '/api/knowledge/items/'))
    if not recognized:
        return None
    api = clean.startswith('/api/')
    if method != 'GET':
        return 405, 'application/json', {'error': '知识与研究准备只支持读取；请通过明确的研究提交入口开始任务。'}
    try:
        index = app.quant_knowledge
        if clean == '/api/knowledge/search':
            return 200, 'application/json', search_payload(index, query)
        if clean == '/api/knowledge/paths':
            return 200, 'application/json', {'paths': index.paths(), 'fingerprint': index.fingerprint}
        if clean == '/api/knowledge/export':
            return 200, 'application/json', index.export()
        if clean == '/api/knowledge/preparation':
            return 200, 'application/json', preparation(index, query.get('item', [''])[0])
        if clean.startswith('/api/knowledge/items/'):
            return 200, 'application/json', index.resolve(clean.removeprefix('/api/knowledge/items/'))
        if clean == '/knowledge':
            title, body = '量化知识库', catalog_page(index, query)
        elif clean == '/knowledge/prepare':
            title, body = '知识研究准备', f'<style>{CSS}</style><h1>把知识带入研究</h1>' + preparation_panel(index, query.get('item', [''])[0])
        elif clean.startswith('/knowledge/paths/'):
            title, body = '学习路径', path_page(index, clean.removeprefix('/knowledge/paths/'))
        else:
            item = index.resolve(clean.removeprefix('/knowledge/items/'))
            title, body = item['title'], detail_page(index, item)
        return 200, 'text/html; charset=utf-8', app.render_shell('/knowledge', title, body, eyebrow='知识与研究', inspector=app._inspector('知识上下文', {'内容': '本地中文知识与固定来源', '学习': '解释 → 手算 → 条件 → 研究准备', '版本': index.fingerprint[:16]}, status='已审核目录'), scripts=('/assets/finathink-research.js',))
    except KeyError:
        status, message = 404, '没有找到这个知识词条或学习路径。请返回知识库重新选择。'
    except (ValueError, TypeError):
        status, message = 400, '检索条件无效。请使用200字以内的关键词，并重新选择主题和筛选条件。'
    except (KnowledgeIndexUnavailable, AssetLoadError, OSError, sqlite3.Error) as exc:
        status = 503
        message = str(exc) if isinstance(exc, KnowledgeIndexUnavailable) else '本地知识资料暂时无法读取。请检查安装文件和磁盘状态后重试。'
    if api:
        return status, 'application/json', {'error': message}
    return status, 'text/html; charset=utf-8', app.render_error_page('知识库需要处理', message, '原有研究记录已保留。', links=(('/knowledge', '返回知识库'),))

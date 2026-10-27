"""A read-only, source-backed library inside the existing blue-white shell."""

from __future__ import annotations

import html
import re
from dataclasses import asdict
from urllib.parse import urlencode, urlsplit

from .asset_contracts import AssetLoadError

KINDS = {"methods": "研究方法", "behavior": "公开行为档案", "lessons": "反例与复盘"}
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,159}$")
_STATES = {"PACKAGED": "方法资料已打包", "PARTIAL": "资料待补齐", "CORRUPT": "资料校验失败", "MISSING": "资料缺失"}


def safe_source_url(value):
    parsed = urlsplit(value or "")
    return value if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password else None


class ResearchAssetWorkspace:
    def __init__(self, catalog):
        self.catalog = catalog

    def _sources(self, references):
        result = []
        for identifier in dict.fromkeys(references):
            source = self.catalog.get_resource(identifier)
            result.append({"resource_id": source.resource_id, "availability": source.availability,
                "author": source.author, "version": source.version, "license_id": source.license_id,
                "sha256": source.sha256, "source_url": safe_source_url(source.source_url)})
        return result

    def _method(self, skill, kind):
        sources = self._sources(resource.resource_id for resource in self.catalog.resources_for(skill.skill_id))
        required = {skill.main_resource, skill.policy_ref, *skill.required_resources}
        gaps = [source["resource_id"] for source in sources if source["availability"] != "PACKAGED"]
        state = "PARTIAL" if any(source["resource_id"] in required and source["availability"] != "PACKAGED" for source in sources) else "PACKAGED"
        body = ""
        try:
            body = self.catalog.read_text(skill.main_resource, max_bytes=32768)
            for source in sources:
                if source["resource_id"] in required and source["availability"] == "PACKAGED" and source["resource_id"] != skill.main_resource:
                    self.catalog.read_text(source["resource_id"], max_bytes=65536)
        except AssetLoadError as exc:
            state = "CORRUPT" if exc.code in {"HASH_MISMATCH", "INVALID_JSON"} else "MISSING"
        return {"asset_id": skill.main_resource if kind == "methods" else "behavior-" + skill.skill_id,
            "skill_id": skill.skill_id, "kind": kind, "title": skill.name, "version": skill.version,
            "tags": list(skill.tags), "availability": state, "loaded": False,
            "summary": "检查所需输入、方法边界与来源后，再带入研究。" if kind == "methods" else "公开行为研究协议；真实材料与身份置信度仍由用户提供，不是人物持仓或收益记录。",
            "required_inputs": list(skill.required_inputs), "output_types": list(skill.output_types),
            "boundaries": list(skill.boundaries), "source_resources": sources, "missing_dependencies": gaps,
            "evidence_status": "METHOD_DOCUMENTATION" if kind == "methods" else "REQUIRES_PUBLIC_EVIDENCE",
            "body": body}

    def _items(self, kind):
        if kind in {"methods", "behavior"}:
            skills = self.catalog.skills()
            if kind == "behavior":
                skills = tuple(skill for skill in skills if "public-behavior" in skill.tags
                               and "identity_confidence" in skill.required_inputs and skill.skill_id != "niu-san-distillation")
            return [self._method(skill, kind) for skill in skills]
        return [{"asset_id": lesson.lesson_id, "kind": "lessons", "title": lesson.title,
            "version": self.catalog.get_resource("lessons").version, "tags": list(lesson.tags),
            "availability": "PACKAGED", "loaded": False, "summary": lesson.summary,
            "evidence_status": lesson.evidence_status, "required_inputs": [], "output_types": [],
            "missing_dependencies": [], "source_resources": self._sources(lesson.source_resource_ids),
            "body": "\n\n".join((lesson.summary, "修正方向：" + lesson.mitigation,
                "回归检查：" + lesson.regression_check, "重访条件：" + lesson.revisit_when)),
            "lesson": asdict(lesson)} for lesson in self.catalog.lessons_for(())]

    def list(self, *, kind="methods", tags=(), query="", limit=20, offset=0):
        if (kind not in KINDS or type(limit) is not int or not 1 <= limit <= 100
                or type(offset) is not int or not 0 <= offset <= 10000
                or not isinstance(tags, (tuple, list)) or len(tags) > 16
                or any(not isinstance(tag, str) or not tag or len(tag) > 64 for tag in tags)
                or not isinstance(query, str) or len(query) > 200):
            raise ValueError("资料检索参数无效：每页最多100项，标签最多16项，检索文字最多200字。")
        items = self._items(kind)
        available_tags = sorted({tag for item in items for tag in item["tags"]})
        selected = [item for item in items if set(tags) <= set(item["tags"])
            and query.strip().casefold() in " ".join((item["title"], item["summary"], *item["tags"])).casefold()]
        return {"schema_version": "asset-workspace.v1", "kind": kind, "total": len(selected),
            "catalog_total": len(items), "limit": limit, "offset": offset, "available_tags": available_tags,
            "items": [{key: value for key, value in item.items() if key != "body"} for item in selected[offset:offset + limit]]}

    def detail(self, identifier):
        if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
            raise ValueError("资料身份无效")
        for kind in KINDS:
            for item in self._items(kind):
                if item["asset_id"] == identifier:
                    return item
        raise KeyError(identifier)


def _source_links(sources):
    esc = html.escape
    return "".join(f'<li>{esc(source["resource_id"])} · {esc(source["availability"])} · {esc(source["version"])}'
        + (f' · <a href="{esc(source["source_url"], quote=True)}" rel="noreferrer">查看原来源</a>' if source["source_url"] else "")
        + '</li>' for source in sources)


def render_asset_items(items):
    esc = html.escape
    if not items:
        return '<p class="empty-state">没有匹配资料。可清空检索条件，或检查已安装资产。</p>'
    result = []
    for item in items:
        href = f'/library/{item["kind"]}?' + urlencode({"asset": item["asset_id"]})
        enter = '/research/jobs?' + urlencode({"question": f'使用「{item["title"]}」提出可验证的研究假设；先检查所需输入与来源。'})
        gaps = '；'.join(item["missing_dependencies"]) or '包内资料可读；用户数据与授权仍需绑定。'
        result.append(f'<article class="asset-entry"><div><h2><a href="{esc(href, quote=True)}">{esc(item["title"])}</a></h2><p>{esc(item["summary"])}</p>'
            f'<p class="field-help">v{esc(item["version"])} · {esc(" / ".join(item["tags"]))} · <span class="asset-state">{esc(_STATES[item["availability"]])}</span></p></div>'
            f'<details><summary>输入、来源与依赖</summary><p>所需输入：{esc("、".join(item["required_inputs"])) or "反例适用的研究上下文"}</p>'
            f'<p>资料状态：{esc(gaps)}</p><ul>{_source_links(item["source_resources"])}</ul></details>'
            + (f'<a class="asset-action" href="{esc(enter, quote=True)}">带入研究问题</a>' if item["kind"] != "lessons" else '') + '</article>')
    return "".join(result)


def asset_workspace_page(service, kind, query):
    esc = html.escape
    search = query.get("q", [""])[0]
    tags = tuple(query.get("tag", ()))
    limit, offset = int(query.get("limit", ["20"])[0]), int(query.get("offset", ["0"])[0])
    data = service.list(kind=kind, query=search, tags=tags, limit=limit, offset=offset)
    tabs = ''.join(f'<a href="/library/{key}"' + (' aria-current="page"' if kind == key else '')
                   + f'>{esc(value)}</a>' for key, value in KINDS.items())
    detail = ''
    if query.get("asset"):
        selected = service.detail(query["asset"][0])
        detail = (f'<section class="asset-document" aria-labelledby="asset-document-title"><h2 id="asset-document-title">{esc(selected["title"])}</h2>'
            f'<p>v{esc(selected["version"])} · {esc(_STATES[selected["availability"]])} · 资料展示不代表已加载执行</p>'
            + ('<p>来源事实：尚待用户公开材料。转述：需逐条核验来源。研究假设：以下协议只定义分析视角，不证明人物真实交易与收益。</p>' if selected['kind'] == 'behavior' else '')
            +
            f'<pre>{esc(selected["body"])}</pre><ul>{_source_links(selected["source_resources"])}</ul></section>')
    def page_link(delta, label):
        values = {"q": search, "limit": limit, "offset": max(0, offset + delta), "tag": tags}
        return f'<a href="/library/{kind}?{esc(urlencode(values, doseq=True), quote=True)}">{label}</a>'
    pagination = (page_link(-limit, "上一页") if offset else '') + (page_link(limit, "下一页") if offset + len(data["items"]) < data["total"] else '')
    return (f'<section data-asset-workspace data-asset-kind="{kind}" data-asset-state="ready"><header class="overview-header"><div><h1>{KINDS[kind]}</h1>'
        '<p class="lede">找到方法，检查来源，再进入可验证的研究。</p></div></header>'
        f'<nav class="asset-tabs" aria-label="资料分类">{tabs}</nav><form class="asset-search" method="get" action="/library/{kind}" data-asset-form>'
        f'<label>检索资料<input name="q" value="{esc(search, quote=True)}" maxlength="200" placeholder="按名称、研究主题或标签检索"></label>'
        '<button type="submit">检索</button></form><p class="field-help">浏览只读取本地资料；不会加载模型上下文、请求数据或提交任务。公开行为档案是研究协议，不是人物实盘记录。</p>'
        f'<p role="status" aria-live="polite" data-asset-status>找到 {data["total"]} 项，当前显示 {len(data["items"])} 项；目录共 {data["catalog_total"]} 项。</p>'
        '<button type="button" data-asset-retry hidden>保留条件并重试</button>'
        f'{detail}<div class="asset-results" data-asset-results>{render_asset_items(data["items"])}</div>'
        f'<nav class="asset-pagination" aria-label="资料分页" data-asset-pagination>{pagination}</nav></section>')


ASSET_WORKSPACE_CSS = """
.asset-tabs{display:flex;gap:20px;flex-wrap:wrap;margin:0 0 24px;border-bottom:1px solid var(--border)}
.asset-tabs a{padding:10px 0;text-decoration:none}.asset-tabs a[aria-current]{color:var(--evidence);border-bottom:2px solid currentColor;font-weight:600}
.asset-search{display:flex;align-items:end;gap:12px;margin:0 0 12px}.asset-search label{flex:1;max-width:540px}
.asset-entry{padding:24px 0;border-bottom:1px solid var(--border);min-width:0}.asset-entry h2{margin:8px 0;line-height:1.5}
.asset-entry h2 a{text-decoration:none}.asset-entry p{max-width:72ch}.asset-state{font-size:12px;color:var(--text-secondary)}
.asset-entry details{margin:14px 0}.asset-entry summary{cursor:pointer;color:var(--evidence)}.asset-entry ul{padding-left:20px;overflow-wrap:anywhere}
.asset-action{display:inline-block;margin-top:4px}.asset-document{padding:24px 0;margin:16px 0;border-block:1px solid var(--border)}
.asset-document pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;line-height:1.8;max-width:76ch}
.asset-pagination{display:flex;gap:24px;padding:24px 0}.asset-results:empty{min-height:60px}
@media(max-width:600px){.asset-search{align-items:stretch;flex-direction:column}.asset-entry{padding:20px 0}.asset-tabs{gap:16px}}
"""

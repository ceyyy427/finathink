"""Unified, read-only projection of the published research directories."""
import json
import unicodedata
from collections import Counter
from copy import deepcopy
from html import escape
from urllib.parse import urlencode

from .asset_catalog import ResearchAssetCatalog
from .contracts import stable_digest
from .discovery_planner import PROFILES, question_templates
from .factor_library import FactorLibrary
from .strategy_catalog import StrategyCatalog

PAGE = "/research/workbench"
API = "/api/research/quant-workspace"
KINDS = {"knowledge": "知识", "factor": "因子", "strategy": "策略", "risk": "风险", "case": "反例"}
JOURNEYS = (
    ("knowledge", "理解趋势并提交研究", "过去的价格动量能否解释下一观察点的收益？", "/knowledge/paths/trend-research"),
    ("missing-data", "找到缺失字段并修复", "ATR 在缺最高最低价时能解释波动吗？", "/settings/data-connections"),
    ("financial", "检验财务质量", "ROE 能否区分盈利能力还是高杠杆？", "/knowledge/paths/fundamental-quality"),
    ("technical", "冻结技术因子测试", "RSI 过度下跌后会出现反转吗？", "/research/experiments"),
    ("event", "核对事件披露时点", "财报事件研究遇到公告滞后会怎样？", "/events"),
    ("etf", "观察 ETF 的相对强弱", "ETF 相对强弱策略计入成本后如何？", "/research/strategies"),
    ("falsify", "检验稳健性与反证", "账面市值比越高是否更容易出现价值陷阱？", "/research/factor-suites"),
    ("portfolio", "观察组合纸面账本", "等权组合策略能降低单一标的集中吗？", "/research/strategies"),
    ("risk", "检查风险与缺口", "收益波动率较低的资产是否更稳健？", "/knowledge/paths/risk-research"),
    ("pool", "复核决策来源与观察窗", "等权组合策略能降低单一标的集中吗？", "/research/pools"),
    ("learning", "阅读并审核复盘建议", "总资产增长是否与下一期收益有关？", "/research/learning"),
    ("replay", "比较并复现研究", "短期均值线与长期均线的差距值得研究吗？", "/research/compare"),
)
_PAGE_SIZE = 20
_STYLE = """
.quant-workbench{max-width:1100px;margin:auto}.quant-workbench .panel{margin-block:1rem}
.quant-workbench h1{margin-block:.25rem .5rem}.qw-nav{display:flex;gap:.75rem;flex-wrap:wrap;margin:1rem 0}
.qw-nav a{display:inline-flex;align-items:center;min-height:42px;padding:.2rem .6rem}
.qw-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.8rem;margin-block:1rem}
.qw-grid article{min-width:0;padding:1rem;border:1px solid var(--border);border-radius:12px;background:var(--surface);overflow-wrap:anywhere}
.qw-grid h3{font-size:1.05rem;margin:.35rem 0}.qw-grid p{margin:.5rem 0}
.qw-controls{display:flex;flex-wrap:wrap;gap:.75rem;align-items:end}.qw-controls label{flex:1;min-width:11rem}
.qw-chips{display:flex;gap:.55rem;flex-wrap:wrap;margin-block:.65rem}.qw-chips span{padding:.22rem .55rem;border-radius:8px;background:#eaf2ff;color:#173868}
.qw-detail{overflow-wrap:anywhere}.qw-detail pre,.quant-workbench textarea{white-space:pre-wrap;overflow-wrap:anywhere;max-width:100%}
.qw-steps{display:grid;gap:.6rem}.qw-steps summary{cursor:pointer;padding:.5rem 0;font-weight:650}
.quant-workbench :focus-visible{outline:2px solid var(--focus,#315cca);outline-offset:3px}
@media(max-width:680px){.qw-grid{grid-template-columns:minmax(0,1fr)}.qw-controls{display:grid;grid-template-columns:minmax(0,1fr)}}
"""


def _normalize(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _entry(kind, identity, title, summary, version, href, refs, definition, *,
           validation="DEFINITION_CHECKED", aliases=(), details=None, dedup=None):
    return {"object_id": f"{kind}:{identity}", "dedup": dedup or f"{kind}:{identity}",
            "kind": kind, "title": title, "summary": summary, "version": version,
            "href": href, "aliases": list(aliases), "details": details or {},
            "evidence": {"source_refs": refs, "definition_digest": definition,
                         "validation": validation, "approval": "REQUIRES_REVIEW"}}


class WorkspaceCatalog:
    def __init__(self, entries):
        self._objects = {}
        seen = set()
        for item in entries:
            if item["dedup"] in seen:
                continue
            seen.add(item["dedup"])
            if item["object_id"] in self._objects:
                raise ValueError("研究目录对象标识重复")
            self._objects[item["object_id"]] = item
        self.inventory = dict(Counter(row["kind"] for row in self._objects.values()))
        self._search = [
            (_normalize(row["title"]), _normalize(" ".join([
                row["object_id"], row["title"], row["summary"], *row["aliases"],
            ])), key)
            for key, row in self._objects.items()
        ]
        self.fingerprint = stable_digest(self._objects)

    @classmethod
    def packaged(cls, knowledge):
        assets = ResearchAssetCatalog.packaged()
        factors = FactorLibrary(assets)
        strategies = StrategyCatalog(assets)
        source_cards = {card["card_id"]: card for card in factors.cards()}
        cases_resource = assets.get_resource("factor-counterexamples")
        cases = json.loads(assets.read_text(cases_resource.resource_id, max_bytes=1048576))["cases"]
        entries = [
            _entry("knowledge", item["item_id"], item["title"], item["summary"], item["version"],
                   "/knowledge/items/" + item["item_id"], item["source_refs"], stable_digest(item),
                   validation=item["evidence_level"], aliases=item["aliases"],
                   details={"fields": item["required_fields"], "conditions": item["assumptions"],
                            "counterexamples": [row["correction"] for row in item["counterexamples"]]})
            for item in knowledge.export()["items"]
        ]
        entries.extend(
            _entry("factor", row["factor_id"], row["title"], row["economic_definition"], row["version"],
                   "/research/factor-library?" + urlencode({"factor": row["factor_id"]}),
                   [{"title": source_cards[ref]["title"], "url": source_cards[ref]["source_url"],
                     **{key: source_cards[ref][key] for key in ("repository", "commit", "path", "sha256")}}
                    for ref in row["source_card_ids"]], row["formula_fingerprint"],
                   details={"formula": row["formula"], "fields": row["required_fields"],
                            "conditions": [row["pit_policy"], row["missing_policy"]],
                            "counterexamples": [row["counterexample"]]}, dedup=row["formula_fingerprint"])
            for row in factors.entries()
        )
        entries.extend(
            _entry("strategy", row["template_id"], row["title"], row["selection_rule"], row["version"],
                   "/research/strategies?" + urlencode({"template": row["template_id"]}), row["source_refs"],
                   row["fingerprint"], details={"fields": row["required_fields"], "conditions": [row["execution_rule"]],
                                                "counterexamples": [row["failure_scenario"]]})
            for row in strategies.templates()
        )
        entries.extend(
            _entry("risk", row["rule_id"], row["title"], row["description"], row["version"],
                   "/research/strategies#risk-rules", row["source_refs"], row["fingerprint"],
                   details={"fields": [row["metric"]], "conditions": [f'{row["operator"]} {row["limit"]} · {row["unit"]}']})
            for row in strategies.risk_rules()
        )
        entries.extend(
            _entry("case", row["case_id"], row["title"], row["correction"], cases_resource.version,
                   "/research/factor-suites", [{"title": "Finathink 版本化反例", "sha256": cases_resource.sha256}],
                   stable_digest(row), validation="SYNTHETIC_CASE",
                   details={"conditions": [row["scope"]], "counterexamples": [row["correction"]]})
            for row in cases
        )
        return cls(entries)

    def source_objects(self, repository, *, paths=None):
        return sorted(row["object_id"] for row in self._objects.values() if any(
            ref.get("repository") == repository and (paths is None or ref.get("path") in paths)
            for ref in row["evidence"]["source_refs"]))

    def source_update_summaries(self, records):
        return [{**row["difference"],
                 "affected_items": row["repository_objects"] if row["decision"] == "WITHDRAW"
                                   else row["difference"]["affected_items"],
                 "ref": row["ref"], "decision": row["decision"], "rationale": row["rationale"]}
                for row in records]

    def search(self, query="", kind="", *, offset=0, selected=None):
        if (not isinstance(query, str) or len(query) > 200 or kind not in {"", *KINDS}
                or type(offset) is not int or not 0 <= offset <= 10000):
            raise ValueError("目录检索条件无效")
        terms = _normalize(query).split()
        matches = [(sum(term in title for term in terms), key) for title, body, key in self._search
                   if (not kind or self._objects[key]["kind"] == kind) and all(term in body for term in terms)]
        matches.sort(key=lambda row: (-row[0], row[1]))
        chosen = self._objects.get(selected)
        return deepcopy({"fingerprint": self.fingerprint, "inventory": self.inventory,
                         "total": len(matches), "offset": offset, "page_size": _PAGE_SIZE, "query": query, "kind": kind,
                         "items": [self._objects[key] for _, key in matches[offset:offset+_PAGE_SIZE]],
                         "selected": chosen, "journeys": [{"id": i, "title": t, "question": q, "href": h}
                                                            for i, t, q, h in JOURNEYS]})


def _href(**values):
    return PAGE + "?" + urlencode({key: value for key, value in values.items() if value})


def _list(items):
    return "<ul>" + "".join(f"<li>{escape(str(item))}</li>" for item in items) + "</ul>"


def _source_text(row):
    label = row.get("title") or row.get("source_id")
    commit = row.get("commit")
    return f'{label} · {commit[:12]}' if commit else label


def _detail(row):
    if row is None:
        return '<p>选择一个对象，查看输入、反例与出处。</p>'
    details, evidence = row["details"], row["evidence"]
    return (f'<h3>{escape(row["title"])}</h3><p>{escape(row["summary"])}</p>'
            f'<p>版本 {escape(row["version"])} · 验证 {escape(evidence["validation"])} · 用途待审核</p>'
            f'<a href="{escape(row["href"], quote=True)}">打开原始详情</a>'
            f'<details><summary>所需字段与适用条件</summary>{_list(details.get("fields", []))}{_list(details.get("conditions", []))}</details>'
            f'<details><summary>反例与边界</summary>{_list(details.get("counterexamples", []))}</details>'
            f'<details><summary>来源与定义摘要</summary>{_list(_source_text(s) for s in evidence["source_refs"])}'
            f'<code>{escape(evidence["definition_digest"])}</code></details>')


def workspace_page(payload, csrf, *, journey=None, updates=()):
    q, kind, offset = payload["query"], payload["kind"], payload["offset"]
    current = next((item for item in payload["journeys"] if item["id"] == journey), None)
    heading = current["question"] if current else ""
    cards = "".join(
        f'<article><span class="meta">{KINDS[row["kind"]]} · {escape(row["version"])}</span>'
        f'<h3><a data-qw-select="{escape(row["object_id"], quote=True)}" '
        f'href="{escape(_href(q=q, kind=kind, offset=offset, selected=row["object_id"]), quote=True)}">'
        f'{escape(row["title"])}</a></h3><p>{escape(row["summary"])}</p></article>' for row in payload["items"]
    )
    paths = "".join(f'<li><a href="{_href(journey=row["id"])}#qw-start">{escape(row["title"])}</a> · '
                    f'<a href="{row["href"]}">阅读所需资料</a></li>' for row in payload["journeys"])
    metrics = "".join(f'<span>{label} {payload["inventory"].get(key, 0)}</span>' for key, label in KINDS.items())
    kinds = "".join(f'<option value="{key}" {"selected" if kind == key else ""}>{label}</option>' for key, label in KINDS.items())
    profiles = "".join(f'<option value="{key}">{escape(label)}</option>' for key, label in PROFILES.items())
    next_steps = "".join(
        f'<a href="{escape(_href(q=q, kind=kind, offset=target), quote=True)}">{label}</a>'
        for target, label in ((offset-_PAGE_SIZE, "上一页"), (offset+_PAGE_SIZE, "下一页")) if 0 <= target < payload["total"]
    )
    update_rows = "".join(
        f'<li>{escape(row["source_id"])} · {escape(row["from_commit"][:12])} → {escape(row["to_commit"][:12])}'
        f' · {escape(row["decision"])} · 涉及 {len(row["affected_items"])} 个目录对象</li>'
        for row in updates[-10:]
    ) or "<li>暂无本地来源审阅记录。</li>"
    examples = "".join(f'<option value="{escape(row["question"], quote=True)}"></option>' for row in question_templates())
    return f'''<style>{_STYLE}</style><div class="quant-workbench" data-quant-workbench>
<header><p class="eyebrow">研究地图</p><h1>量化研究工作台</h1>
<p class="lede">从问题和反例出发，核对字段与方法，再查看纸面结果和来源。</p>
<div class="qw-chips" aria-label="已审核对象">{metrics}</div></header>
<nav class="qw-nav" aria-label="研究工具"><a href="/knowledge">知识库</a><a href="/research/factor-library">因子库</a>
<a href="/research/strategies">策略与风险</a><a href="/research/pools">研究池</a><a href="/research/experiments">实验</a>
<a href="/research/jobs">任务与报告</a><a href="/research/compare">对比与复现</a><a href="/library/sources">来源</a><a href="#qw-sources">来源审阅</a></nav>
<section class="panel" id="qw-start"><h2>从研究问题开始</h2>
<form data-qw-discovery>
<input type="hidden" name="csrf" value="{escape(csrf, quote=True)}">
<label>研究问题<input name="question" value="{escape(heading, quote=True)}" maxlength="1000" list="qw-questions" required></label>
<datalist id="qw-questions">{examples}</datalist>
<label>复核重点<select name="profile">{profiles}</select></label>
<label>输入面板<select name="mode"><option value="preview">仅准备问题</option>
<option value="sample">按问题加载合成样例</option><option value="saved">已保存的用户面板</option></select></label>
<details><summary>用户面板和附件</summary>
<label>面板合同<textarea name="contract" rows="4"></textarea></label>
<label>附件引用<textarea name="attachment_refs" rows="2">[]</textarea></label></details>
<div class="action-row"><button type="submit" disabled data-qw-preview>预览研究计划</button>
<button type="button" disabled data-qw-submit>确认并提交研究</button></div>
<p role="status" data-qw-discovery-status>预览后检查所需字段和反证条件。</p><div data-qw-blueprint></div>
<noscript><p>启用 JavaScript 以使用预览表单。也可直接浏览目录和研究路径。</p></noscript></form></section>
<section class="panel"><h2>12 条研究路径</h2><details class="qw-steps"><summary>选择研究路径</summary><ol>{paths}</ol></details></section>
<section class="panel" aria-labelledby="qw-catalog-title"><h2 id="qw-catalog-title">检索研究目录</h2>
<form method="get" action="{PAGE}" class="qw-controls" data-qw-search>
<label>关键词<input name="q" value="{escape(q, quote=True)}" maxlength="200" autocomplete="off"></label>
<label>内容类型<select name="kind"><option value="">全部</option>{kinds}</select></label>
<button type="submit">检索目录</button></form>
<p role="status" data-qw-status>找到 {payload["total"]} 个对象。</p>
<section class="qw-detail" data-qw-detail aria-label="所选对象">{_detail(payload["selected"])}</section>
<div class="qw-grid" data-qw-results>{cards}</div>
<p data-qw-empty {"hidden" if cards else ""}>暂无匹配对象，请调整关键词或类型。</p>
<nav data-qw-pagination aria-label="目录分页">{next_steps}</nav></section>
<section class="panel" id="qw-sources"><h2>来源版本与审阅</h2><ul>{update_rows}</ul>
<a href="/api/research/quant-workspace/sources">查看逐项差异与历史版本</a></section></div>'''


def pool_page(ref, entries, csrf):
    entries_html = "".join(f'<li>{escape(row["instrument"])} · {escape(row["decision_origin"])} · '
                           f'{escape(row["stage"])} · {escape(row["decision_at"])}</li>' for row in entries)
    return f'''<section class="panel"><h1>研究池</h1><p>按已有研究池标识查看决策出处和阶段。</p>
<form method="get" action="/research/pools"><label>研究池标识
<input name="pool_ref" value="{escape(ref, quote=True)}" maxlength="128" required></label><button>读取研究池</button></form>
<p role="status">{len(entries)} 条记录</p><ul>{entries_html or "<li>选择研究池后显示已登记决策。</li>"}</ul>
<p><a href="/research/workbench">返回量化研究工作台</a></p></section>
<section class="panel section"><h2>登记与观察</h2><p>使用已发布报告与明确截止时点。观察结果保留 1、5、20、60 个观察点的证据缺口。</p>
<form data-qw-pool>
<input type="hidden" name="csrf" value="{escape(csrf, quote=True)}">
<label>研究池标识<input name="pool_ref" maxlength="128" value="{escape(ref, quote=True)}" required></label>
<details><summary>从已发布报告登记决策</summary>
<label>后台任务 ID<input name="job_id" maxlength="128"></label>
<label>资产代码<input name="instrument" maxlength="128"></label>
<label>来源<select name="decision_origin"><option value="USER">用户复核</option><option value="QUANT_BASELINE">量化基线</option>
<option value="AUTO_SCAN">自动扫描</option><option value="MODEL">模型假设</option><option value="PUBLIC_METHOD">公开方法</option></select></label>
<label>阶段<select name="stage"><option value="WATCH">观察</option><option value="CANDIDATE">候选</option><option value="DECISION">决策</option></select></label>
<button type="button" data-pool-action="register" disabled>登记已核验报告</button></details>
<details><summary>冻结与观察</summary><label>截止时点（含时区）<input name="decision_at" placeholder="2025-01-02T16:00:00Z"></label>
<button type="button" data-pool-action="freeze" disabled>冻结观察队列</button>
<label>队列指纹<input name="cohort_ref" maxlength="64"></label><label>观察数据快照<input name="snapshot_ref" maxlength="64"></label>
<button type="button" data-pool-action="observe" disabled>读取同窗口观察结果</button></details>
<p role="status" data-pool-status>每次操作需要显式提交。</p><div data-pool-results></div></form></section>'''

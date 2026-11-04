"""Plan bounded research using reviewed questions and existing method contracts."""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass

from .asset_catalog import ResearchAssetCatalog
from .asset_contracts import MethodRouteRequest
from .contracts import stable_digest
from .factor_library import FactorLibrary
from .method_context import CONTEXT_MAX_BYTES, load_methods
from .method_router import select_methods
from .strategy_catalog import StrategyCatalog

VERSION = "study-blueprint.v1"
MAX_HYPOTHESES = 24
PROFILES = {
    "balanced": "收益、成本和风险等权观察",
    "defensive": "优先查看回撤、流动性及不利窗口",
    "low-turnover": "优先查看换手与成本侵蚀",
}
_SPLIT = {"train": 0.6, "validation": 0.2, "test": 0.2, "horizon": 1, "embargo": 1}


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True, slots=True)
class DiscoveryBudget:
    max_hypotheses: int = MAX_HYPOTHESES
    max_concurrent: int = 2
    max_context_bytes: int = CONTEXT_MAX_BYTES

    def __post_init__(self):
        for value, cap in ((self.max_hypotheses, MAX_HYPOTHESES), (self.max_concurrent, 2),
                           (self.max_context_bytes, CONTEXT_MAX_BYTES)):
            if type(value) is not int or not 1 <= value <= cap:
                raise ValueError("研究预算超出允许范围")


@dataclass(frozen=True, slots=True)
class StudyBlueprint:
    _encoded: str

    @classmethod
    def freeze(cls, fields, budget):
        payload = {**fields, "schema_version": VERSION, "budget": asdict(budget)}
        identity = stable_digest(payload)
        payload.update(blueprint_id="blueprint-" + identity[:24], fingerprint=identity)
        raw = encode(payload)
        if len(raw.encode()) > budget.max_context_bytes:
            raise ValueError("研究计划超过上下文预算")
        return cls(raw)

    @property
    def fingerprint(self):
        return self.to_dict()["fingerprint"]

    def to_dict(self):
        return json.loads(self._encoded)


def open_knowledge(catalog):
    from finahinking.p7_5.knowledge import DEFAULT_CATALOG
    from finahinking.p8_2b.catalog import DEFAULT_KNOWLEDGE_CATALOG

    from .knowledge_index import KnowledgeIndex

    return KnowledgeIndex.from_catalogs(DEFAULT_CATALOG, DEFAULT_KNOWLEDGE_CATALOG,
                                        catalog, catalog.knowledge_content())


def question_templates(catalog=None):
    catalog = catalog or ResearchAssetCatalog.packaged()
    raw = catalog.read_text("quant-research-questions", max_bytes=32768)
    return json.loads(raw)["questions"]


def _canonical(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _match(question, templates):
    exact = next((row for row in templates if _canonical(row["question"]) == question), None)
    if exact is not None:
        return exact
    candidates = [(row["target_id"], phrase, row) for row in templates
                  for raw in (row["phrase"], *row.get("aliases", ()))
                  if (phrase := _canonical(raw)) in question]
    # Specific phrases outrank their embedded general term. Independent
    # topics need clarification even if one happened to have a longer label.
    strongest = {target: row for target, phrase, row in candidates if not any(
        target != other_target and phrase != other and phrase in other
        for other_target, other, _ in candidates)}
    return next(iter(strongest.values())) if len(strongest) == 1 else None


def _references(index, question, template):
    hits = index.search(question[:200], limit=2)
    selected = {item["item_id"]: index.resolve(item["item_id"]) for item in hits}
    for item_id in template["knowledge_ids"]:
        selected[item_id] = index.resolve(item_id)
    evidence = []
    counterexamples = {}
    for item in selected.values():
        evidence.append({"item_id": item["item_id"], "version": item["version"],
                         "digest": stable_digest(item), "domain": item["domain"],
                         "source_ids": item["source_ref_ids"],
                         "counterexample_ids": list(item["counterexample_refs"])})
        counterexamples.update({row["counterexample_id"]: stable_digest(row) for row in item["counterexamples"]})
    return evidence, [{"counterexample_id": key, "digest": value} for key, value in sorted(counterexamples.items())]


def prepare_discovery(question, profile, snapshot_ref, budget=None, *, available_fields=(),
                      panel_ref=None, policy_ref=None, risk_ref=None, execution_ref=None, provider=None, index=None):
    """Return a preview. Neither retrieved prose nor a question grants authority."""
    if (not isinstance(question, str) or not 1 <= len(question.strip()) <= 1000
            or not isinstance(profile, str) or profile not in PROFILES):
        raise ValueError("请填写研究问题并选择研究风格")
    if snapshot_ref is not None and (not isinstance(snapshot_ref, str) or not re.fullmatch(r"[0-9a-f]{64}", snapshot_ref)):
        raise ValueError("快照指纹无效")
    budget = budget if isinstance(budget, DiscoveryBudget) else DiscoveryBudget(**(budget or {}))
    assets = ResearchAssetCatalog.packaged()
    fields = {"question": question.strip(), "profile": profile, "profile_focus": PROFILES[profile],
              "snapshot_ref": snapshot_ref, "panel_ref": panel_ref, "allowed_tools": [], "provider": provider}
    template = _match(_canonical(question), question_templates(assets))
    if template is None:
        return StudyBlueprint.freeze({**fields, "status": "NEEDS_CLARIFICATION"}, budget)

    if index is None:
        with open_knowledge(assets) as local:
            refs, counterexamples = _references(local, question, template)
    else:
        refs, counterexamples = _references(index, question, template)
    target = template["target_id"]
    is_factor = target.startswith("lib-")
    definition = FactorLibrary(assets).get(target) if is_factor else StrategyCatalog(assets).template(target)
    required = sorted(set(definition["required_fields"]) | {"close"})
    missing = sorted(set(required) - set(available_fields)) if snapshot_ref and panel_ref else required
    purpose = "factor-mining" if is_factor else "backtest"
    route = select_methods(MethodRouteRequest(purposes=(purpose,)), assets)
    methods = load_methods(route, assets, max_bytes=budget.max_context_bytes)
    status = "NEEDS_DATA" if missing else "READY"
    if not missing and methods.status != "READY":
        status = "BUDGET_STOPPED"
    fields.update(
        status=status, target_id=target, target_kind="FACTOR" if is_factor else "STRATEGY",
        target_digest=stable_digest(definition), method_version=definition["version"],
        required_fields=required, missing_fields=missing, repair_href="/settings/data-connections" if missing else None,
        knowledge_refs=refs, counterexample_refs=counterexamples,
        method_refs=list(route.skill_ids), method_context_digest=methods.fingerprint,
        method_manifest_digest=methods.manifest_fingerprint,
        split_spec=dict(_SPLIT) if is_factor else {"kind": "fixed-policy-paper-simulation"},
        cost_policy_ref=policy_ref, risk_policy_ref=risk_ref, execution_ref=execution_ref,
        hypothesis=(f"{definition['title']}：在固定观察窗内检验所选信号的方向及计费后表现。"),
        falsification=("样本外方向反转、费用后优势消失或证据不足时，不支持原假设。"),
    )
    if status == "READY":
        factor = FactorLibrary(assets).spec(target) if is_factor else None
        fields["candidates"] = [factor.to_dict() if factor else {
            "template_id": target, "version": definition["version"]}]
    return StudyBlueprint.freeze(fields, budget)

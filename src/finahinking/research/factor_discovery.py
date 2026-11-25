"""Plan multiple diagnostic trials from reviewed questions and canonical DSL."""

import re

from finahinking.data.research_fields import ResearchFieldStore

from .asset_catalog import ResearchAssetCatalog
from .contracts import _unsafe_public_text, stable_digest
from .discovery_planner import _canonical, _match, open_knowledge, question_templates
from .factor_library import FactorLibrary
from .factor_study_suite import prepare_suite
from .factor_validation_sample import discovery_sample_spec
from .source_updates import source_policy

VERSION = "multi-factor-discovery.v1"
MAX_CANDIDATES = 3
PURPOSE = "FORMULA_RESEARCH"
_MARKET = re.compile(r"[A-Z][A-Z0-9_-]{0,31}")

# Ordered before seeing returns. Each family lists a frozen question anchor and
# competing measurements. The matched formulation can add one other library row.
DESIGNS = {
    "trend_momentum": ("lib-trend-roc", "lib-trend-ma-spread", "lib-trend-slope", 1),
    "reversal": ("lib-reversal-rsi", "lib-reversal-boll-lower", "lib-reversal-williams", -1),
    "volatility_tail": ("lib-volatility-return-std", "lib-volatility-atr", "lib-volatility-maximum-return", -1),
    "liquidity_turnover": ("lib-liquidity-amihud", "lib-liquidity-turnover-volatility", "lib-liquidity-dollar-volume", -1),
    "value": ("lib-value-book-market", "lib-value-earnings-yield", "lib-value-free-cashflow-yield", 1),
    "quality_profitability_accrual": ("lib-quality-roe", "lib-quality-gross-profit-assets", "lib-quality-operating-margin", 1),
    "growth_investment": ("lib-growth-asset-growth", "lib-growth-sales-growth", "lib-growth-capex-growth", 1),
    "industry_style": ("lib-industry-book-market", "lib-industry-cashflow-market", "lib-industry-size", 1),
    "event_institutional": ("lib-event-equity-finance", "lib-event-debt-finance", "lib-event-share-issuance", 1),
    "etf_macro": ("lib-etf-macro-correlation", "lib-etf-macro-beta", "lib-etf-macro-term-spread", 1),
}
# Some comparators deliberately test a competing explanation with a different
# expected sign. These directions are fixed across every question and retry.
_DIRECTION_OVERRIDES = {"lib-liquidity-dollar-volume": 1, "lib-liquidity-turnover": 1}


class FactorDiscoveryInputError(ValueError):
    """An error with an actionable, public message for the discovery form."""


def discovery_questions():
    anchors = {ids[0] for ids in DESIGNS.values()}
    return [row["question"] for row in question_templates() if row["target_id"] in anchors]


def _expand_candidates(entries, target_id, design):
    candidates, excluded, seen = [], [], {}
    for factor_id in (target_id, *design[:3]):
        entry = entries[factor_id]
        formula_ref = entry["formula_fingerprint"]
        if formula_ref in seen:
            excluded.append({"factor_id": factor_id, "reason": "DUPLICATE_FORMULA",
                             "retained_factor_id": seen[formula_ref], "formula_fingerprint": formula_ref})
        elif len(candidates) == MAX_CANDIDATES:
            excluded.append({"factor_id": factor_id, "reason": "CANDIDATE_LIMIT", "formula_fingerprint": formula_ref})
        else:
            seen[formula_ref] = factor_id
            candidates.append(entry)
    if len(candidates) < 2:
        raise ValueError("多候选设计至少需要两条不同的受控公式。")
    return candidates, excluded


def prepare_discovery_suite(service, values):
    if not isinstance(values, dict) or set(values) - {"question", "saved_panel_ref", "market"}:
        raise FactorDiscoveryInputError("多候选发现只接受问题、已保存面板和市场标签。")
    question = values.get("question")
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1000 or _unsafe_public_text(question):
        raise FactorDiscoveryInputError("请填写 1 至 1000 字的研究问题。")
    catalog = ResearchAssetCatalog.packaged()
    templates = question_templates(catalog)
    matched = _match(_canonical(question), templates)
    if matched is None or not matched["target_id"].startswith("lib-"):
        return {"status": "NEEDS_CLARIFICATION", "examples": discovery_questions()}, None

    library = FactorLibrary(catalog)
    entries = {row["factor_id"]: row for row in library.entries()}
    target_id = matched["target_id"]
    family = entries[target_id]["family"]
    design = DESIGNS[family]
    choices, excluded = _expand_candidates(entries, target_id, design)
    cards = {card["card_id"]: card for card in library.cards()}
    source_refs = {source_id: cards[source_id] for entry in choices for source_id in entry["source_card_ids"]}
    blocked_sources = source_policy(service.root, source_refs.values())["blocked"]
    units = None
    panel_ref = None
    if "saved_panel_ref" in values:
        try:
            record = service.panels.load(values["saved_panel_ref"])
        except FileNotFoundError:
            raise FactorDiscoveryInputError("所选面板已不可读，请重新选择面板。") from None
        panel_inputs = {"contract": record["contract"], "attachment_refs": record["attachment_refs"]}
        units = dict(record["contract"]["field_units"])
        store = ResearchFieldStore(service.snapshots)
        for reference in record["attachment_refs"]:
            attachment = store.load(reference)
            if attachment.snapshot_ref != record["contract"]["snapshot_ref"]:
                raise FactorDiscoveryInputError("面板附件与行情快照不匹配，请重新保存面板。")
            for field, unit in attachment.units.items():
                if units.setdefault(field, unit) != unit:
                    raise FactorDiscoveryInputError("面板附件单位存在冲突，请重新保存面板。")
        panel_ref = record["panel_fingerprint"]
    else:
        if "market" in values:
            raise FactorDiscoveryInputError("内置样例使用固定的合成市场标识。")
        panel_inputs = {"sample_spec": discovery_sample_spec(family)}

    candidates = []
    with open_knowledge(catalog) as knowledge:
        for entry in choices:
            factor_id = entry["factor_id"]
            item_ids = sorted(set(matched["knowledge_ids"]) | set(entry["knowledge_refs"]))
            items = [knowledge.resolve(item_id) for item_id in item_ids]
            counterexamples = {case["counterexample_id"]: {"digest": stable_digest(case), **case}
                               for item in items for case in item["counterexamples"]}
            candidates.append({
                "factor_id": factor_id, "title": entry["title"], "family": family,
                "role": "PRIMARY" if factor_id == target_id else "COUNTERCHECK",
                "formula_fingerprint": entry["formula_fingerprint"], "factor_spec": library.spec(factor_id).to_dict(),
                "direction": _DIRECTION_OVERRIDES.get(factor_id, design[3]),
                "field_units": entry["field_units"],
                "field_issues": [] if units is None else [
                    {"field": field, "expected_unit": unit, "declared_unit": units.get(field)}
                    for field, unit in sorted(entry["field_units"].items()) if units.get(field) != unit
                ],
                "source_ids": entry["source_card_ids"],
                "knowledge_refs": [{"item_id": item["item_id"], "version": item["version"],
                                    "digest": stable_digest(item)} for item in items],
                "counterexamples": list(counterexamples.values()),
                "formula_counterexample": entry["counterexample"],
            })
    from .snapshot_tools import local_research_policy

    canonical_question = matched["question"]
    spec = prepare_suite(
        {"question": canonical_question, "factor_ids": [item["factor_id"] for item in candidates], **panel_inputs},
        service.snapshots, policy_fingerprint=stable_digest(local_research_policy()), library=library,
        discovery_family=VERSION, persist_sample=False,
    )
    market = values.get("market", "SYNTHETIC" if spec["data_mode"] == "SAMPLE" else None)
    if (not isinstance(market, str) or not _MARKET.fullmatch(market)
            or (market == "SYNTHETIC") != (spec["data_mode"] == "SAMPLE")):
        raise FactorDiscoveryInputError("请声明大写市场标签。合成面板使用 SYNTHETIC。")
    # The shortlist is independent of field coverage and returns. The charter
    # carries the immutable input but keeps per-candidate data gaps testable.
    plan = {
        "schema_version": VERSION, "status": "SOURCE_REVIEW_REQUIRED" if blocked_sources else "READY",
        "question": canonical_question, "target_id": target_id, "family": family,
        "hypothesis": "比较登记方向下各候选与下一观察点收益的关联及计费后分层表现。",
        "falsification": "方向或净收益不达已登记阈值时记为证伪，缺数据与执行失败分别留档。",
        "market": market, "purpose": PURPOSE, "candidates": candidates, "excluded": excluded,
        "sources": [source_refs[key] for key in sorted(source_refs)], "blocked_sources": blocked_sources,
        "budget": {"candidate_limit": MAX_CANDIDATES, "suite_limit": 24,
                   "evaluation_units": spec["evaluation_units"], "family_size": len(spec["declared_family"]),
                   "final_tests": 1},
        "evaluation": {key: spec[key] for key in ("data_mode", "snapshot_ref", "instrument_ids", "family_ref",
                       "sample_period", "test_period", "split", "validation_policy", "policy_fingerprint")},
        "input_ref": stable_digest(panel_inputs), "panel_fingerprint": panel_ref,
    }
    plan["fingerprint"] = stable_digest(plan)
    spec["multi_factor_discovery"] = plan
    return plan, spec


def verify_discovery_sources(root, plan):
    existing = {row["card_id"]: row for row in FactorLibrary().cards()}
    if (any(existing.get(row["card_id"]) != row for row in plan["sources"])
            or source_policy(root, plan["sources"])["blocked"]):
        raise FactorDiscoveryInputError("候选来源已改变或撤回，请复核来源并重新预览。")


def frozen_discovery_candidate(study, validation):
    """Show the immutable selected trial and everything needed to audit its freeze."""
    plan = study["spec"]["multi_factor_discovery"]
    result = next((item for item in validation["experiments"] if item["experiment_id"] == study["candidate_id"]), None)
    trial = next((row for row in validation["suite_validation"]["trials"] if row["factor_id"] == study["candidate_id"]), None)
    candidate = next((row for row in plan["candidates"] if row["factor_id"] == study["candidate_id"]), None)
    if (result is None or trial is None or candidate is None or result["digest"] != study["candidate_fingerprint"]
            or trial["factor_spec"] != candidate["factor_spec"] or trial["formula_fingerprint"] != candidate["formula_fingerprint"]):
        raise ValueError("冻结候选未与已发布的验证记录和公式绑定。")
    receipt = {
        "spec_ref": study["spec_ref"], "plan_ref": plan["fingerprint"], "factor_id": trial["factor_id"],
        "factor_spec": candidate["factor_spec"], "formula_fingerprint": candidate["formula_fingerprint"],
        "direction": candidate["direction"], "cost": {key: plan["evaluation"]["validation_policy"][key]
                                                      for key in ("fee_bps", "slippage_bps")},
        "candidate_fingerprint": result["digest"], "parameters": result["parameters"],
        "trial_ref": trial["fingerprint"], "family_ref": plan["evaluation"]["family_ref"],
        "test_period": plan["evaluation"]["test_period"],
    }
    return {**receipt, "fingerprint": stable_digest(receipt)}

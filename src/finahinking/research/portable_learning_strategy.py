"""Verify strategy observations, admissions and both sides against their frozen panel."""
import json
from datetime import date
from statistics import fmean

from finahinking.data.panel_contracts import panel_time

from .contracts import stable_digest, to_jsonable
from .learning_reliability import instant
from .learning_review import _baseline, _json
from .portable_learning import approval_product, approved_version, fields, require
from .strategy_learning import (
    BUDGET_RULE,
    METHOD,
    StrategyLearningService,
    _audit_windows,
    _frozen_inputs,
    _id,
    _scope,
)
from .strategy_learning_replay import create_pair
from .strategy_settlement import METHOD as SETTLEMENT_METHOD
from .strategy_settlement import MIN_SAMPLES, _LedgerWindows


def _outputs(inputs, pair, sides):
    answer = {"learning_approval": approval_product(inputs, inputs["observation"], inputs["decision"],
                                                     inputs["parent"], inputs["decision"]["policy"])}
    for name in ("baseline", "candidate"):
        side = sides[name]
        result = side["result"]
        answer["learning_" + name] = {
            "policy_ref": side["policy_ref"], "policy_fingerprint": side["policy_fingerprint"],
            "input_fingerprint": side["input_fingerprint"], "evidence_digest": stable_digest(side),
            "status": result["status"], "ledger": result["ledger"], "benchmark": result["benchmark"],
        }
    answer["learning_comparison"] = {key: pair[key] for key in
        ("pair_id", "proposal_id", "audit_id", "status", "comparison_kind", "metrics", "series", "fills_changed")}
    return answer


def freeze(app, pair_id):
    from .portable_strategy import freeze_source

    learning = StrategyLearningService(app)
    pair = learning.get_pair(pair_id)
    proposal = learning.store.get("proposal", pair["proposal_id"])
    audit = learning.store.get("audit", proposal["audit_id"])
    observation = learning.settlements.verify(audit["observation_ref"])
    decision = learning.store.get("decision", pair["proposal_id"])
    require(decision["policy"] and learning.store.get("policy", decision["policy"]["policy_ref"]) == decision["policy"],
            "冻结策略政策与其批准记录不符")
    inputs = {"pair_id": pair_id, "run_id": pair["run_id"], "source": freeze_source(app, pair["run_id"]),
              "observation": observation, "audit": audit, "proposal": proposal, "decision": decision,
              "parent": app.learning.store.policy(proposal["parent_policy_ref"])}
    sides = {name: learning.side(pair_id, name, pair=pair) for name in ("baseline", "candidate")}
    return inputs, _outputs(inputs, pair, sides)


def validate(inputs):
    from .portable_strategy import validate_source

    fields(inputs, "kind pair_id run_id source observation audit proposal decision parent")
    validate_source(inputs["source"])
    observation, audit, proposal, decision = (inputs[k] for k in ("observation", "audit", "proposal", "decision"))
    parent, candidate, admission = inputs["parent"], decision["policy"], decision["admission"]
    fields(decision, "admission decided_at policy requested_effective_on")
    scope, frozen = audit["scope"], audit["frozen"]
    source = inputs["source"]
    require(source["publication"]["run_id"] == inputs["run_id"]
            and source["publication"]["report_digest"] == observation["report_digest"] == frozen["report_digest"]
            and source["publication"]["ledger_digest"] == observation["ledger_digest"] == frozen["original_ledger_digest"]
            and source["binding"]["panel_ref"] == frozen["panel_ref"]
            and source["result_digest"] == frozen["original_result_digest"]
            and stable_digest(source["binding"]) == frozen["binding_digest"]
            and source["policy"] == parent["effective"], "策略审批与冻结原报告、账本或源政策失配")
    ref = observation["observation_ref"]
    require(observation["method"] == SETTLEMENT_METHOD and observation["status"] == "SETTLED"
            and ref == stable_digest([SETTLEMENT_METHOD, observation["scope_id"], inputs["run_id"], observation["as_of"]])
            and inputs["run_id"] == observation["run_id"] == frozen["run_id"]
            and audit["method"] == METHOD and audit["status"] == "READY" and audit["observation_ref"] == ref
            and audit["audit_id"] == _id("audit", [observation["scope_id"], METHOD, ref])
            and audit["receipt_fingerprint"] == stable_digest(observation)
            and proposal["audit_id"] == audit["audit_id"]
            and proposal["proposal_id"] == _id("proposal", [audit["audit_id"]])
            and inputs["pair_id"] == _id("pair", [proposal["proposal_id"]]), "策略观察、审核、提案或对照身份失配")
    refs = {row["sample_ref"] for row in audit["selected_samples"]}
    excluded = {row["sample_ref"] for row in observation["samples"]} - refs
    samples, _ = _audit_windows(observation, refs, excluded)
    require(len(refs) == len(samples) == audit["sample_count"] >= MIN_SAMPLES and samples == audit["selected_samples"]
            and audit["mean_excess_return"] == fmean(s["excess_return"] for s in samples) < 0
            and sum(s["excess_return"] < 0 for s in samples) * 2 > len(samples)
            and proposal["proposal"]["evidence_refs"] == [audit["audit_id"], *(r["sample_ref"] for r in samples)]
            and proposal["proposal"]["settlement_digest"] == stable_digest(observation)
            and proposal["proposal"]["risk_rule_updates"] == {BUDGET_RULE: audit["proposed"]}
            and proposal["proposal"]["factor_weight_updates"] == proposal["proposal"]["registry_updates"] == {}
            and audit["as_of"] == observation["as_of"], "审批提案与已结算策略观察不符")
    require(candidate and admission["decision"] == "APPROVED"
            and admission["admission_id"] == _id("decision", [proposal["proposal_id"], "APPROVED"])
            and admission["as_of"] == instant(decision["decided_at"]).date().isoformat()
            and instant(decision["decided_at"]) >= instant(observation["evaluated_at"])
            and candidate["proposal_id"] == proposal["proposal_id"], "策略政策缺少有效的已批准决定")
    decided_at = instant(decision["decided_at"])
    requested = decision["requested_effective_on"]
    if requested is not None:
        require(date.fromisoformat(requested) >= decided_at.date(), "政策生效日期早于审核")
    effective_at = max(decided_at, instant(requested)) if requested else decided_at
    require(instant(candidate["effective_at"]) == effective_at, "策略生效时间与审批不符")
    require(scope == proposal["scope"] == candidate["scope"]
            and stable_digest(scope) == audit["scope_fingerprint"] == proposal["scope_fingerprint"] == candidate["scope_fingerprint"]
            and scope["parent_policy_ref"] == parent["policy_ref"] == candidate["parent_ref"] == proposal["parent_policy_ref"]
            and stable_digest(parent["effective"]) == parent["runtime_fingerprint"] == frozen["baseline_runtime_fingerprint"]
            and parent["runtime_fingerprint"] == proposal["baseline_runtime_fingerprint"], "策略学习范围或基线政策不一致")
    scale = parent["effective"].get(BUDGET_RULE, 1.0)
    baseline = {**_baseline(parent), "risk_rules": {**parent["risk_rules"], BUDGET_RULE: scale},
                "ledger_digest": observation["ledger_digest"], "dataset_digest": observation["snapshot_ref"]}
    require(audit["baseline"] == baseline and audit["before"] == proposal["before"] == scale
            and audit["proposed"] == proposal["proposed"] == max(0.1, round(scale * 0.8, 8)) < scale
            and proposal["preview"]["policy_fingerprint"] == proposal["preview_fingerprint"], "策略政策变更与审核预览不符")
    version = approved_version(proposal["proposal"], admission, baseline, proposal["preview"])
    expected = {**parent["effective"], BUDGET_RULE: version.risk_rules[BUDGET_RULE]}
    require(candidate["typed_policy"] == to_jsonable(version) and candidate["version"] == version.version
            and candidate["effective"] == expected and candidate["runtime_fingerprint"] == stable_digest(expected)
            and candidate["paper_only"] is True
            and candidate["policy_ref"] == _id("policy", {k: v for k, v in candidate.items() if k != "policy_ref"}),
            "已批准政策与执行指纹不一致")


def replay(app, inputs):
    from .portable_strategy import replay_source

    source = replay_source(app, inputs["source"])
    observation, audit = inputs["observation"], inputs["audit"]
    require(_frozen_inputs(observation, source) == audit["frozen"] and _scope(observation, source) == audit["scope"],
            "复算的策略面板、账本或经济范围与审批证据不一致")
    cutoff = min(panel_time(observation["as_of"]), panel_time(observation["evaluated_at"]))
    # Settlement observes a published JSON ledger. The engine can return NumPy
    # scalars, which the published-number checks deliberately do not accept.
    published = {**source, "result": json.loads(_json(source["result"]))}
    samples, excluded, _ = _LedgerWindows(published).at(cutoff)
    expected = [{k: v for k, v in row.items() if k not in {"first_observation_ref", "duplicate"}}
                for row in observation["samples"]]
    require(to_jsonable(samples) == expected and to_jsonable(excluded) == observation["excluded_windows"]
            and cutoff == panel_time(observation["cutoff_at"]), "原观察窗口无法从复算账本独立重建")
    proposal = {**inputs["proposal"], "policy": inputs["decision"]["policy"]}
    pair, sides = create_pair(inputs["pair_id"], proposal, audit, source)
    return _outputs(inputs, pair, sides)

"""Ordinary two-child research comparisons use the offline backtest and risk tools."""
from math import isfinite

from finahinking.data.panel_contracts import panel_time, reference

from .contracts import stable_digest, to_jsonable
from .learning_assessments import LearningAssessmentService
from .learning_manager import LearningManager, LearningUpdateProposal, VersionedResearchPolicy
from .learning_reliability import assess_outcomes, instant
from .learning_replays import LearningReplayService
from .learning_review import _baseline, _effective
from .paper_trader import PaperLedger
from .portable_inputs import PortableUnavailable
from .portable_learning import approval_product, approved_version, fields, require
from .reproduction import (
    ReproductionService,
    _policy_supported,
    _portable_snapshot,
    _snapshot,
    products,
)
from .research_identity import execution_identity
from .settlement import SettlementEvent
from .snapshot_tools import SnapshotResearchTools, local_research_policy


def _approval(inputs):
    review = inputs["review"]
    return approval_product(inputs, review["observation"], review["admission"], inputs["parent"], inputs["candidate"])


def _side(policy, analytics):
    return {"policy_ref": policy["policy_ref"], "policy_fingerprint": policy["runtime_fingerprint"],
            "research": products(analytics)}


def _comparison(inputs, metrics):
    return {"pair_id": inputs["pair_id"], "comparison_kind": inputs["binding"]["comparison_kind"], "metrics": metrics}


def _publication(app, reproduction, run_id):
    _, _, request = app.learning._source(run_id)
    spec = request.research_plan.validation_spec
    entry, analytics = reproduction.source(run_id)
    frozen = analytics["reproduction"]
    require(not any(spec.get(k) for k in ("factor_study", "strategy_study", "strategy_validation", "discovery"))
            and not frozen.get("panel_scope") and frozen == entry["manifest"]["source_snapshot"]["reproduction"]
            and analytics["run_id"] == request.run_id and analytics["as_of"] == request.as_of.isoformat()
            and analytics["instrument"] == request.instrument and analytics["dataset_fingerprint"] == request.snapshot_ref,
            "学习对照仅支持已核验的普通离线子报告及其已发表输入")
    publication = {"run_id": run_id, "manifest_digest": stable_digest(entry["manifest"]), "as_of": analytics["as_of"],
                   "instrument": request.instrument, "snapshot_ref": request.snapshot_ref,
                   "publication_before": frozen["publication_before"]}
    return publication, analytics


def _publication_bounds(inputs):
    binding = inputs["binding"]
    publications = [inputs["children"][key] for key in ("left", "right")]
    anchor = inputs["anchor"]
    if anchor["snapshot_ref"] == binding["snapshot_ref"]:
        publications.append(anchor)
    dates = [record["as_of"] for record in publications]
    cuts = [panel_time(record["publication_before"]) for record in publications if record["publication_before"] is not None]
    return min(dates), min(cuts) if cuts else None


def freeze(app, pair_id):
    pair = LearningReplayService(app).get(pair_id)
    require(pair["status"] == "AVAILABLE", "两侧均需完成且报告校验可用")
    binding = pair["binding"]
    review = app.learning.get(binding["review_id"])
    assessment = LearningAssessmentService(app)
    if review.get("source_kind") == "LEARNING_COHORT":
        stored = assessment.get(review["assessment_id"])
        require(stored == review["observation"]["assessment"], "批准样本评估与原始快照不符")
        for proof in stored["proofs"]:
            require(assessment._proof(assessment._record(proof["review_id"])) == proof, "样本评估的结算证据已变化")
    else:
        assessment._proof(assessment._record(review["review_id"]))
    parent = app.learning.store.policy(binding["left_policy_ref"], as_of=binding["as_of"])
    candidate = app.learning.store.policy(binding["right_policy_ref"], as_of=binding["as_of"])
    snapshot = app.snapshots.load(binding["snapshot_ref"])
    reproduction = ReproductionService(app)
    anchor, _ = _publication(app, reproduction, binding["anchor_run_id"])
    inputs = {"pair_id": pair_id, "run_id": binding["anchor_run_id"], "binding": binding, "anchor": anchor,
              "review": review, "parent": parent, "candidate": candidate, "children": {}}
    expected = {"learning_approval": _approval(inputs), "learning_comparison": _comparison(inputs, pair["metrics"])}
    for key, policy, name in (("left", parent, "baseline"), ("right", candidate, "candidate")):
        run_id = pair["children"][key]["run_id"]
        publication, analytics = _publication(app, reproduction, run_id)
        frozen = analytics["reproduction"]
        require(frozen["identity"] == binding["execution_identity"] and frozen["policy"] == policy["effective"]
                and analytics["policy_fingerprint"] == policy["runtime_fingerprint"], "学习子报告政策与其执行身份不符")
        inputs["children"][key] = publication
        expected["learning_" + name] = _side(policy, analytics)
    as_of, cutoff = _publication_bounds(inputs)
    inputs["snapshot"] = _portable_snapshot(snapshot, cutoff, as_of=as_of)
    return inputs, expected


def _validate_observation(review):
    if review.get("source_kind") == "LEARNING_COHORT":
        assessment = review["observation"]["assessment"]
        ref = "assessment-" + stable_digest({key: val for key, val in assessment.items() if key != "assessment_id"})
        require(ref == assessment["assessment_id"] == review["assessment_id"] and assessment["status"] == "RECOMMENDED",
                "学习样本评估身份不符")
        computed = assess_outcomes(assessment["proofs"], scope=assessment["scope"], start=assessment["start"],
                                   as_of=assessment["as_of"])
        require(all(assessment.get(k) == v for k, v in computed.items()), "冻结样本无法重建批准的评估")
        selected = [row for row in assessment["proofs"] if row["review_id"] in assessment["selected_review_ids"]]
        return LearningUpdateProposal("PROPOSAL_READY", stable_digest(assessment), assessment["as_of"],
            factor_weight_updates=assessment["factor_weight_updates"], risk_rule_updates=assessment["risk_rule_updates"],
            ledger_digest=stable_digest([row["ledger_digest"] for row in selected]),
            dataset_digest=stable_digest([row["dataset_digest"] for row in selected]))
    require(review.get("source_kind") is None, "学习对照缺少可核对的数值观察")
    record = review["settlement"]
    event = SettlementEvent(**{**record, "ledger": PaperLedger(**record["ledger"])})
    require(event.run_id == review["run_id"] and event.fingerprint == review["settlement_fingerprint"]
            and to_jsonable(event.realized_outcomes["observation"]) == review["observation"], "原始结算与冻结观察不一致")
    return LearningManager().propose_update(event)


def validate(inputs):
    fields(inputs, "kind pair_id run_id binding anchor review parent candidate snapshot children")
    binding, review = inputs["binding"], inputs["review"]
    fields(binding, "scope_id review_id anchor_run_id snapshot_ref as_of instrument question left_policy_ref right_policy_ref execution_identity comparison_kind")
    fields(inputs["children"], "left right")
    require(inputs["pair_id"] == "pair-" + stable_digest(binding)
            and inputs["run_id"] == binding["anchor_run_id"] == review.get("anchor_run_id", review["run_id"])
            and binding["review_id"] == review["review_id"], "学习对照引用不匹配")
    anchor = inputs["anchor"]
    for publication in (anchor, *inputs["children"].values()):
        fields(publication, "run_id manifest_digest snapshot_ref instrument as_of publication_before")
        reference(publication["manifest_digest"])
        reference(publication["snapshot_ref"])
        SnapshotResearchTools._boundary(publication["as_of"])
        if publication["publication_before"] is not None:
            panel_time(publication["publication_before"])
    require(anchor["run_id"] == binding["anchor_run_id"] and anchor["instrument"] == binding["instrument"]
            and anchor["snapshot_ref"] == review["source_snapshot_ref"], "原学习报告与审核对象不符")
    for side in ("left", "right"):
        ref = inputs["children"][side]
        expected_run = "research-" + stable_digest({"scope": binding["scope_id"], "key": inputs["pair_id"] + "-" + side})[:24]
        require(all(ref[k] == binding[k] for k in ("instrument", "as_of", "snapshot_ref"))
                and ref["run_id"] == expected_run, "学习对照的子报告身份不匹配")
    parent, candidate = inputs["parent"], inputs["candidate"]
    proposal = _validate_observation(review)
    require(to_jsonable(proposal) == review["proposal"] and proposal.proposal_digest == review["proposal_digest"]
            and review["status"] == review["admission"]["decision"] == "APPROVED"
            and review["admission"]["admission_id"] == "review-" + stable_digest([review["review_id"], "APPROVED"])[:32]
            and review["preview_fingerprint"] == review["preview"]["policy_fingerprint"] == candidate["preview_fingerprint"],
            "审核、预览与提案内容不一致")
    require(_baseline(parent) == review["baseline"] and parent["policy_ref"] == binding["left_policy_ref"]
            and stable_digest(parent["effective"]) == parent["runtime_fingerprint"]
            and parent["policy_ref"] == review["parent_policy_ref"] == candidate["parent_ref"],
            "学习对照基线与批准范围不同")
    if parent["parent_ref"] is None:
        require(parent["version"] == 0 and parent["policy_ref"] == parent["runtime_fingerprint"], "初始政策指纹不符")
    else:
        typed = VersionedResearchPolicy(**parent["policy"])
        require(typed.fingerprint == parent["policy_ref"] and _effective(typed) == parent["effective"],
                "父政策内容与版本不符")
    version = approved_version(review["proposal"], review["admission"], review["baseline"], review["preview"])
    require(to_jsonable(version) == candidate["policy"] and version.fingerprint == candidate["policy_ref"] == binding["right_policy_ref"]
            and candidate["policy_ref"] == review["policy_ref"] and candidate["review_id"] == review["review_id"]
            and review["admission"]["as_of"] == candidate["available_as_of"] == review["available_as_of"] <= binding["as_of"]
            and candidate["effective"] == _effective(version) and candidate["runtime_fingerprint"] == stable_digest(candidate["effective"])
            and all(_policy_supported(p["effective"]) for p in (parent, candidate)), "学习对照政策版本或有效参数不符")
    snapshot = _snapshot(inputs["snapshot"])
    as_of, published_before = _publication_bounds(inputs)
    limit = SnapshotResearchTools._boundary(as_of)
    require(binding["instrument"] in snapshot.instruments and snapshot.pit_available(limit)
            and (published_before is None or all(max(row.timestamp, row.available_at) < published_before
                                                 for row in snapshot.observations)), "学习对照数据超出原报告发表范围")
    first = min(row.timestamp for row in snapshot.observations if row.instrument == binding["instrument"])
    cutoff = max(instant(review["as_of"], end=True), instant(candidate["available_as_of"], end=True))
    require(binding["comparison_kind"] == ("RETROSPECTIVE" if first <= cutoff else "FORWARD_DESCRIPTIVE"),
            "学习对照观察时间分类与冻结政策不符")


def replay(app, inputs):
    binding = inputs["binding"]
    if binding["execution_identity"] != execution_identity(local_research_policy()):
        raise PortableUnavailable("LEARNING_VERSION_MISMATCH")
    snapshot = _snapshot(inputs["snapshot"])
    results = {"learning_approval": _approval(inputs)}
    metrics = {}
    for name, policy, key in (("baseline", inputs["parent"], "left"), ("candidate", inputs["candidate"], "right")):
        tools = SnapshotResearchTools(snapshot, policy=policy["effective"],
                                      publication_before=inputs["children"][key]["publication_before"])
        tools.run_id = inputs["children"][key]["run_id"]
        args = {"dataset_fingerprint": snapshot.fingerprint, "as_of": binding["as_of"],
                "instrument": binding["instrument"], "factor_ids": [policy["effective"]["factor_id"]]}
        tools._backtest(args)
        tools._risk(args)
        results["learning_" + name] = _side(policy, tools.analysis)
        metrics[key] = tools.analysis["metrics"]
    deltas = []
    for key in sorted(metrics["left"].keys() & metrics["right"].keys()):
        left, right = metrics["left"][key], metrics["right"][key]
        if all(type(value) in (int, float) and isfinite(value) for value in (left, right)) and isfinite(right - left):
            deltas.append({"metric": key, "left": left, "right": right, "delta": right - left})
    results["learning_comparison"] = _comparison(inputs, deltas)
    return results

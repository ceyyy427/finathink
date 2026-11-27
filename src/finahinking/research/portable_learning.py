"""Portable, read-only replay of explicitly approved pairs of paper policies."""
import re

from .contracts import stable_digest, to_jsonable
from .learning_manager import LearningAdmissionRecord, LearningManager, LearningUpdateProposal
from .learning_replays import LearningReplayService
from .strategy_learning_store import StrategyLearningStore


def require(condition, message):
    if not condition:
        raise ValueError(message)


def fields(value, keys):
    require(isinstance(value, dict) and set(value) == set(keys.split()), "学习材料的字段不完整或不受支持")


def approved_version(proposal, admission, baseline, preview):
    manager = LearningManager()
    admitted = manager.admit_update(LearningUpdateProposal(**proposal), LearningAdmissionRecord(**admission))
    computed = manager.preview_apply(admitted, baseline)
    require(to_jsonable(computed) == preview, "学习审批的冻结预览与原政策不一致")
    return manager.apply(admitted, baseline)


def list_pairs(app):
    strategies = StrategyLearningStore(app).list("pair")
    ordinary = LearningReplayService(app).list()
    return [{"pair_id": row["pair_id"], "kind": kind, "status": row["status"]}
            for kind, rows in (("strategy_pair", strategies), ("research_pair", ordinary)) for row in rows]


def _adapter(kind):
    if kind == "research_pair":
        from . import portable_learning_research as adapter
    elif kind == "strategy_pair":
        from . import portable_learning_strategy as adapter
    else:
        raise ValueError("未知学习对照类型")
    return adapter


def freeze_pair(app, pair_id, include_data=True):
    require(include_data is True, "学习对照需要明确选择冻结数据范围")
    require(isinstance(pair_id, str) and re.fullmatch(r"(?:strategy-)?pair-[0-9a-f]{64}", pair_id),
            "学习对照标识无效")
    kind = "strategy_pair" if pair_id.startswith("strategy-") else "research_pair"
    inputs, expected = _adapter(kind).freeze(app, pair_id)
    inputs.update(kind=kind, pair_id=pair_id)
    validate_pair(inputs)
    return {"inputs": inputs, "expected": expected}


def validate_pair(inputs):
    require(isinstance(inputs, dict), "学习材料需要对象")
    try:
        _adapter(inputs.get("kind")).validate(inputs)
    except (KeyError, IndexError, AttributeError, OverflowError) as exc:
        raise ValueError("学习材料的证据关联不完整") from exc


def replay_pair(app, inputs):
    validate_pair(inputs)
    return _adapter(inputs["kind"]).replay(app, inputs)


def approval_product(inputs, observation, approval, baseline, candidate):
    return {"pair_id": inputs["pair_id"], "decision": "APPROVED",
            "observation_digest": stable_digest(observation), "approval_digest": stable_digest(approval),
            "baseline_policy_ref": baseline["policy_ref"], "candidate_policy_ref": candidate["policy_ref"],
            "baseline_runtime_fingerprint": baseline["runtime_fingerprint"],
            "candidate_runtime_fingerprint": candidate["runtime_fingerprint"]}

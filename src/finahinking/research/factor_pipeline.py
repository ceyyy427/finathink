"""Deterministic, paper-only factor research orchestration.

Natural language is reduced to the existing allow-listed DSL templates.  The
pipeline records evidence and admission proposals; it never writes a factor
to :class:`finahinking.factors.registry.FactorRegistry`.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import pandas as pd

from finahinking.factors.evaluation import FactorEvaluation
from finahinking.factors.mining import FactorCandidate
from finahinking.p6_6.workbench import ResearchCharter

from .factor_catalog import FactorCatalogEntry, HumanAdmissionRecord, audit_factor_catalog_entry
from .factor_loop import FactorResearchRound, FactorResearchRun, run_factor_research
from .factor_proposals import (
    FactorHypothesis,
    FactorProposal,
    FactorProposalCatalog,
    FactorTemplateRegistry,
    validate_factor_proposal,
)
from .factor_study_store import FactorStudyStore
from .observability import current_runtime_budget

_SUSPICIOUS_FIELD = re.compile(r"(?:future|lookahead|target|label|forward|next|tomorrow)", re.IGNORECASE)


def _jsonable(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _dataset_digest(dataset: Mapping[str, Any]) -> str:
    frame = dataset.get("frame")
    forward = dataset.get("forward_return")
    if not isinstance(frame, pd.DataFrame) or not isinstance(forward, pd.Series):
        raise TypeError("dataset requires frame and forward_return")
    if not frame.index.equals(forward.index):
        raise ValueError("forward_return must use the same index as frame")
    payload = {
        "columns": list(frame.columns),
        "index": [item.isoformat() if isinstance(item, pd.Timestamp) else str(item) for item in frame.index],
        "values": frame.astype(float).where(frame.notna(), None).to_dict(orient="list"),
        "forward_return": forward.astype(float).where(forward.notna(), None).tolist(),
    }
    return _digest(payload)


def _config_value(config: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in config:
            return config[name]
    return default


def _coerce_hypothesis(hypothesis: FactorHypothesis | Mapping[str, Any] | str, config: Mapping[str, Any], registry: FactorTemplateRegistry) -> FactorHypothesis:
    if isinstance(hypothesis, FactorHypothesis):
        return registry.bind(hypothesis)
    if isinstance(hypothesis, Mapping):
        values = dict(hypothesis)
    elif isinstance(hypothesis, str):
        return registry.resolve(hypothesis, config)
    else:
        raise TypeError("hypothesis must be a FactorHypothesis, mapping or string")
    values.setdefault("hypothesis_id", f"hypothesis-{_digest(values.get('text', 'hypothesis'))[:12]}")
    values.setdefault("text", values.get("hypothesis_id", "factor hypothesis"))
    values.setdefault("family", _config_value(config, "family", default="momentum"))
    values.setdefault("inputs", _config_value(config, "inputs", "required_fields", default=("close",)))
    values.setdefault("horizon", _config_value(config, "horizon", default=20))
    values.setdefault("direction", _config_value(config, "direction", default="positive"))
    return registry.bind(FactorHypothesis(**values))


@dataclass(frozen=True, slots=True)
class FactorRank:
    proposal_id: str
    candidate_id: str
    rank: int
    score: float
    information_coefficient: float | None
    information_ratio: float | None
    turnover: float | None
    decay_score: float | None
    status: str
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "candidate_id": self.candidate_id,
            "rank": self.rank,
            "score": self.score,
            "information_coefficient": self.information_coefficient,
            "information_ratio": self.information_ratio,
            "turnover": self.turnover,
            "decay_score": self.decay_score,
            "status": self.status,
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True, slots=True)
class FactorAdmissionProposal:
    proposal_id: str
    candidate_id: str
    status: str
    evaluation_fingerprint: str
    reasons: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    research_fingerprint: str = ""
    requires_human_admission: bool = True
    production_write: bool = False

    @property
    def fingerprint(self) -> str:
        return _digest(self._payload())

    def _payload(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "candidate_id": self.candidate_id,
            "status": self.status,
            "evaluation_fingerprint": self.evaluation_fingerprint,
            "reasons": list(self.reasons),
            "evidence_refs": list(self.evidence_refs),
            "research_fingerprint": self.research_fingerprint,
            "requires_human_admission": self.requires_human_admission,
            "production_write": self.production_write,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "fingerprint": self.fingerprint}


@dataclass(frozen=True, slots=True)
class FactorResearchResult:
    hypothesis: FactorHypothesis
    proposals: tuple[FactorProposal, ...]
    research_run: FactorResearchRun
    ranks: tuple[FactorRank, ...]
    admission_proposals: tuple[FactorAdmissionProposal, ...]
    catalog_entries: tuple[FactorCatalogEntry, ...]
    dataset_fingerprint: str
    config_digest: str

    @property
    def fingerprint(self) -> str:
        return _digest(self.to_dict())

    @property
    def run(self) -> FactorResearchRun:
        """Compatibility alias for callers that call the nested run ``run``."""

        return self.research_run

    @property
    def evaluations(self) -> tuple[FactorEvaluation, ...]:
        return tuple(item.evaluation for item in self.research_run.rounds)

    @property
    def decay(self) -> tuple[Any, ...]:
        return tuple(item.decay for item in self.evaluations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis": {
                "hypothesis_id": self.hypothesis.hypothesis_id,
                "text": self.hypothesis.text,
                "family": self.hypothesis.family,
                "inputs": list(self.hypothesis.inputs),
                "horizon": self.hypothesis.horizon,
                "direction": self.hypothesis.direction,
                "template_name": self.hypothesis.template_name,
                "template_version": self.hypothesis.template_version,
            },
            "proposals": [item.__dict__ if hasattr(item, "__dict__") else {"proposal_id": item.proposal_id, "expression": item.expression, "source_hypothesis": item.source_hypothesis, "required_fields": list(item.required_fields), "constraints": dict(item.constraints), "paper_only": item.paper_only} for item in self.proposals],
            "research_run": self.research_run.to_dict(),
            "ranks": [item.to_dict() for item in self.ranks],
            "admission_proposals": [item.to_dict() for item in self.admission_proposals],
            "catalog_entries": [item.to_dict() for item in self.catalog_entries],
            "dataset_fingerprint": self.dataset_fingerprint,
            "config_digest": self.config_digest,
        }

    def freeze(self, candidate_id: str) -> FactorResearchResult:
        return replace(self, research_run=self.research_run.freeze(candidate_id))

    def evaluate_test(self, dataset: Mapping[str, Any]) -> FactorResearchResult:
        return replace(self, research_run=self.research_run.evaluate_test(dataset))

    def admit(self, proposal_id: str, admission_record: HumanAdmissionRecord | None = None) -> FactorAdmissionProposal:
        if not isinstance(admission_record, HumanAdmissionRecord):
            raise TypeError("HumanAdmissionRecord is required")
        for proposal in self.admission_proposals:
            if proposal.proposal_id == proposal_id:
                if proposal.reasons:
                    raise ValueError("candidate has not passed deterministic admission")
                if admission_record.proposal_id != proposal.proposal_id or admission_record.evaluation_fingerprint != proposal.evaluation_fingerprint or admission_record.research_fingerprint != proposal.research_fingerprint:
                    raise ValueError("admission record does not bind proposal, evaluation and research fingerprints")
                return replace(proposal, status="HUMAN_ADMITTED", requires_human_admission=False)
        raise KeyError(proposal_id)


def _decay_score(evaluation: FactorEvaluation) -> float | None:
    values = [item for item in evaluation.decay.information_coefficients if item is not None]
    return None if not values else float(sum(values) / len(values))


def _rank_score(evaluation: FactorEvaluation) -> float:
    ic = abs(evaluation.information_coefficient or 0.0)
    icir = evaluation.information_ratio or 0.0
    turnover = evaluation.turnover or 0.0
    decay = _decay_score(evaluation) or 0.0
    return float(ic + 0.1 * icir + 0.1 * decay - 0.1 * turnover)


def _round_to_rank(item: FactorResearchRound) -> FactorRank:
    evaluation = item.evaluation
    limitations = tuple(dict.fromkeys((*evaluation.warnings, *item.admission.reasons)))
    return FactorRank(item.candidate.candidate_id, item.candidate.candidate_id, 0, _rank_score(evaluation), evaluation.information_coefficient, evaluation.information_ratio, evaluation.turnover, _decay_score(evaluation), item.admission.status, limitations)


def rank_factor_proposals(results: FactorResearchResult | Sequence[FactorResearchRound | FactorEvaluation]) -> tuple[FactorRank, ...]:
    """Rank only deterministic evidence; this function never emits advice."""

    if isinstance(results, FactorResearchResult):
        source: Sequence[FactorResearchRound | FactorEvaluation] = results.research_run.rounds
    elif isinstance(results, Sequence) and all(isinstance(item, FactorResearchResult) for item in results):
        source = tuple(round_item for result in results for round_item in result.research_run.rounds)
    else:
        source = results
    ranks: list[FactorRank] = []
    for item in source:
        if isinstance(item, FactorResearchRound):
            ranks.append(_round_to_rank(item))
        elif isinstance(item, FactorEvaluation):
            ranks.append(FactorRank(item.candidate_id, item.candidate_id, 0, _rank_score(item), item.information_coefficient, item.information_ratio, item.turnover, _decay_score(item), item.status.value, tuple(item.warnings)))
        else:
            raise TypeError("results must contain FactorResearchRound or FactorEvaluation")
    ordered = sorted(ranks, key=lambda item: (-item.score, item.proposal_id))
    return tuple(replace(item, rank=index) for index, item in enumerate(ordered, start=1))


class FactorResearchPipeline:
    """Run a bounded proposal/evaluation cycle without production mutation."""

    def __init__(self, registry: FactorTemplateRegistry | None = None) -> None:
        self.registry = registry if registry is not None else FactorTemplateRegistry()

    def run(self, hypothesis: FactorHypothesis | Mapping[str, Any] | str, dataset: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> FactorResearchResult:
        budget = current_runtime_budget()
        if budget is not None:
            budget.check_wall()
        if not isinstance(dataset, Mapping):
            raise TypeError("dataset must be a mapping")
        config = dict(config or {})
        normalized_hypothesis = _coerce_hypothesis(hypothesis, config, self.registry)
        template = self.registry.get(normalized_hypothesis.template_name)
        if "template_version" in config and config["template_version"] != template.version:
            raise ValueError("template version does not match registered version")
        frame = dataset.get("frame")
        forward = dataset.get("forward_return")
        if not isinstance(frame, pd.DataFrame) or not isinstance(forward, pd.Series):
            raise TypeError("dataset requires frame and forward_return")
        if frame.empty or not frame.index.is_unique or not frame.index.is_monotonic_increasing:
            raise ValueError("factor dataset index must be non-empty, unique and increasing")
        suspicious = next((str(column) for column in frame.columns if _SUSPICIOUS_FIELD.search(str(column))), None)
        if suspicious is not None:
            raise ValueError(f"future-looking field is not allowed: {suspicious}")
        raw_source_ids = _config_value(config, "source_ids", "sources", default=dataset.get("source_ids"))
        source_ids = () if raw_source_ids is None else ((raw_source_ids,) if isinstance(raw_source_ids, str) else tuple(raw_source_ids))
        license_status = _config_value(config, "license_status", "license", default=dataset.get("license_status"))
        pit_semantics = _config_value(config, "pit_semantics", "pit", default=dataset.get("pit_semantics"))
        raw_version = _config_value(config, "version", "factor_version", default="1.0.0")
        if not isinstance(raw_version, str) or not raw_version.strip():
            raise ValueError("version must be non-empty")
        version = raw_version.strip()
        proposals = FactorProposalCatalog(self.registry).propose(normalized_hypothesis, limit=int(_config_value(config, "max_proposals", default=5)))
        for proposal in proposals:
            validate_factor_proposal(proposal)
        candidates = tuple(FactorCandidate(item.proposal_id, item.expression, normalized_hypothesis.text, "controlled-template", {"required_fields": item.required_fields, "source_ids": source_ids}) for item in proposals)
        data_fingerprint = _dataset_digest(dataset)
        hard_constraints = {
            "shift_periods": int(_config_value(config, "shift_periods", default=1)),
            "paper_only": True,
            "min_samples": int(_config_value(config, "min_samples", default=8)),
            "min_abs_ic": float(_config_value(config, "min_abs_ic", default=0.0)),
            "min_icir": float(_config_value(config, "min_icir", default=-1_000_000.0)),
            "max_turnover": float(_config_value(config, "max_turnover", default=1_000_000.0)),
            "decay_horizons": tuple(_config_value(config, "decay_horizons", default=(1, 5, 20))),
        }
        split = dict(_config_value(config, "data_split", default={"train": 0.6, "validation": 0.2, "test": 0.2}))
        max_rounds = int(_config_value(config, "max_rounds", default=len(candidates)))
        if budget is not None:
            budget.charge_experiments(min(max_rounds, len(candidates)))
        charter = ResearchCharter(
            charter_id=str(_config_value(config, "charter_id", default=f"factor-{normalized_hypothesis.hypothesis_id}")),
            research_question=normalized_hypothesis.text,
            hypothesis_scope=normalized_hypothesis.family,
            dataset_reference=data_fingerprint,
            data_split=split,
            evaluation_metrics=("ic", "icir", "turnover", "decay"),
            hard_constraints=hard_constraints,
            allowed_primitives=("input", "return", "rolling", "rank", "negate", "combine"),
            max_experiments=max_rounds,
            iteration_budget=max_rounds,
        )
        research_run = run_factor_research(charter, candidates, dataset, {"max_rounds": max_rounds})
        ranks = rank_factor_proposals(research_run.rounds)
        template_identity = {"name": template.name, "version": template.version, "digest": template.digest}
        config_digest = _digest({**config, "factor_template": template_identity})
        research_lineage = _digest({
            "dataset_fingerprint": data_fingerprint,
            "config_digest": config_digest,
            "research_run_fingerprint": research_run.fingerprint,
            "provenance": {
                "source_ids": list(source_ids),
                "license_status": license_status,
                "pit_semantics": pit_semantics,
                "version": version,
                "template_name": normalized_hypothesis.template_name,
                "template_version": normalized_hypothesis.template_version,
                "template_digest": template.digest,
            },
        })
        catalog_entries: list[FactorCatalogEntry] = []
        admissions: list[FactorAdmissionProposal] = []
        for item in research_run.rounds:
            candidate_lineage = _digest({"research_lineage": research_lineage, "proposal_id": item.candidate.candidate_id, "evaluation_fingerprint": item.evaluation.fingerprint})
            catalog_entry = FactorCatalogEntry(item.candidate.candidate_id, version, source_ids, license_status, pit_semantics, item.candidate.metadata["required_fields"], candidate_lineage, "PROPOSED", item.evaluation.fingerprint)
            audit_factor_catalog_entry(catalog_entry)
            catalog_entries.append(catalog_entry)
            admissions.append(FactorAdmissionProposal(item.candidate.candidate_id, item.candidate.candidate_id, "PENDING_HUMAN_ADMISSION", item.evaluation.fingerprint, item.admission.reasons, item.admission.evidence_refs, candidate_lineage))
        return FactorResearchResult(normalized_hypothesis, proposals, research_run, ranks, tuple(admissions), tuple(catalog_entries), data_fingerprint, config_digest)

    def run_experiments(self, spec: Any, dataset: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> Any:
        """Run a bounded parameter grid through the shared factor evaluator."""

        from .factor_experiments import run_factor_experiments

        return run_factor_experiments(spec, dataset, config)

    def validate_factor(self, spec, panel, policy, *, gateway):
        """The same typed pilot evidence path, not a separate UI calculation."""
        from .factor_validation import run_factor_validation

        return run_factor_validation(spec, panel, policy, gateway=gateway)


def admit_factor_proposal(result: FactorResearchResult, proposal_id: str, admission_record: HumanAdmissionRecord) -> FactorAdmissionProposal:
    """Apply an explicit human admission record to a research proposal only."""

    return result.admit(proposal_id, admission_record)


__all__ = [
    "FactorAdmissionProposal",
    "FactorRank",
    "FactorResearchPipeline",
    "FactorResearchResult",
    "FactorStudyStore",
    "HumanAdmissionRecord",
    "admit_factor_proposal",
    "rank_factor_proposals",
]

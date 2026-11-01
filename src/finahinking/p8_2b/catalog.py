"""Loadable, deterministic flagship knowledge catalog."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from importlib import resources

from .contracts import (
    CodeSegment,
    DerivationStep,
    EquationDefinition,
    KnowledgeUnit,
    Proof,
    SymbolDefinition,
)
from .math import MathExpression
from .references import LocalReferenceCache, ReferenceRecord
from .validation import validate_catalog


@dataclass(frozen=True)
class KnowledgeCatalog:
    units: tuple[KnowledgeUnit, ...]
    references: LocalReferenceCache

    def canonical_ids(self) -> dict[str, str]:
        """Old teaching units remain readable under their original IDs."""
        aliases = {"returns": "return", "ols": "regression", "oos-overfitting": "oos"}
        return {unit.unit_id: aliases.get(unit.unit_id, unit.unit_id) for unit in self.units}

    @property
    def fingerprint(self) -> str:
        payload = {"units": [unit.to_dict() for unit in self.units], "references": [record.to_dict() for record in self.references.records()]}
        return sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _json(name: str) -> object:
    return json.loads(resources.files("finahinking.p8_2b.content").joinpath(name).read_text(encoding="utf-8"))


def _unit(payload: dict[str, object]) -> KnowledgeUnit:
    equations = tuple(EquationDefinition(item["equation_id"], MathExpression.from_node(item["expression"]), item["meaning"], tuple(item.get("symbol_ids", ())), item.get("number")) for item in payload["equations"])
    derivations = tuple(DerivationStep(item["step_id"], item.get("previous_equation_id"), MathExpression.from_node(item["result"]), item["operation"], item["reason"], item["rule_or_theorem"], tuple(item["assumptions"]), tuple(item.get("reference_ids", ()))) for item in payload.get("derivations", ()))
    proofs = tuple(Proof(item["proof_id"], item["statement"], item["strategy"], tuple(item["step_ids"]), item["status"]) for item in payload.get("proofs", ()))
    return KnowledgeUnit(
        unit_id=payload["unit_id"], title=payload["title"], domain=payload["domain"], level=payload["level"], why_now=payload["why_now"], background=payload["background"], history=payload.get("history", payload["background"]), intuition=payload["intuition"], symbols=tuple(SymbolDefinition(item["symbol_id"], item["notation"], item["meaning"], item["units"], item.get("current_value")) for item in payload["symbols"]), equations=equations, prerequisites=tuple(payload["prerequisites"]), code_segments=tuple(CodeSegment(item["segment_id"], item["code"], tuple(item["line_range"]), tuple(item["equation_ids"]), tuple(item.get("feature_ids", ())), item["data_input"], item["data_output"]) for item in payload["code_segments"]), references=tuple(payload["references"]), provenance=payload["provenance"], assumptions=tuple(payload["assumptions"]), limitations=tuple(payload["limitations"]), derivations=derivations, proofs=proofs, misconceptions=tuple(payload.get("misconceptions", ())), exercises=tuple(payload.get("exercises", ())), applications=tuple(payload.get("applications", ())), tags=tuple(payload.get("tags", ())))


def load_catalog() -> KnowledgeCatalog:
    records = tuple(ReferenceRecord(**item) for item in _json("references.json"))
    catalog = KnowledgeCatalog(tuple(_unit(item) for item in _json("flagship.json")), LocalReferenceCache(records))
    validate_catalog(catalog)
    return catalog


DEFAULT_KNOWLEDGE_CATALOG = load_catalog()


def get_knowledge_unit(unit_id: str, catalog: KnowledgeCatalog = DEFAULT_KNOWLEDGE_CATALOG) -> KnowledgeUnit:
    for unit in catalog.units:
        if unit.unit_id == unit_id:
            return unit
    raise KeyError(unit_id)


def search_catalog(query: str, catalog: KnowledgeCatalog = DEFAULT_KNOWLEDGE_CATALOG) -> tuple[KnowledgeUnit, ...]:
    needle = query.strip().casefold()
    if not needle:
        return catalog.units
    matches = []
    for unit in catalog.units:
        haystack = " ".join([unit.unit_id, unit.title, unit.domain, *unit.tags, *unit.applications, *(symbol.symbol_id for symbol in unit.symbols), *(symbol.meaning for symbol in unit.symbols), *(equation.meaning for equation in unit.equations), *(segment.code for segment in unit.code_segments)]).casefold()
        if needle in haystack:
            matches.append(unit)
    return tuple(matches)

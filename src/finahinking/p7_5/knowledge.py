"""Structured, deterministic P7.5 knowledge engine.

The reference curriculum is deliberately data, not prose files.  A concept
has typed prerequisites, equations, derivation steps, executable-looking code
examples, financial interpretation, and links into the quant/strategy
surfaces.  The catalog is immutable in-process; a user's mastery remains in
the existing P7 private graph and is never copied into this module.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import Enum


def _text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value.strip()


def _id(value: str, label: str) -> str:
    normalized = _text(value, label)
    if any(char.isspace() for char in normalized) or len(normalized) > 128:
        raise ValueError(f"{label} must be a bounded identifier")
    return normalized


def _tuple_text(values: Iterable[str], label: str) -> tuple[str, ...]:
    result = tuple(_text(value, label) for value in values)
    if len(set(result)) != len(result):
        raise ValueError(f"{label} values must be unique")
    return result


class KnowledgeType(str, Enum):
    """Supported typed content blocks in a concept record."""

    CONCEPT = "CONCEPT"
    DEFINITION = "DEFINITION"
    THEOREM = "THEOREM"
    EQUATION = "EQUATION"
    DERIVATION = "DERIVATION"
    PROOF = "PROOF"
    ASSUMPTION = "ASSUMPTION"
    PREREQUISITE = "PREREQUISITE"
    EXAMPLE = "EXAMPLE"
    CODE_EXAMPLE = "CODE_EXAMPLE"
    FINANCIAL_INTERPRETATION = "FINANCIAL_INTERPRETATION"
    QUANT_APPLICATION = "QUANT_APPLICATION"
    STRATEGY_APPLICATION = "STRATEGY_APPLICATION"
    MISCONCEPTION = "MISCONCEPTION"
    EXERCISE = "EXERCISE"
    SOURCE_REFERENCE = "SOURCE_REFERENCE"
    LEARNING_PATH = "LEARNING_PATH"


@dataclass(frozen=True)
class SourceReference:
    reference_id: str
    title: str
    kind: str
    locator: str
    notes: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference_id", _id(self.reference_id, "reference_id"))
        for name in ("title", "kind", "locator", "notes"):
            object.__setattr__(self, name, _text(getattr(self, name), name))

    def to_dict(self) -> dict[str, str]:
        return {
            "reference_id": self.reference_id,
            "title": self.title,
            "kind": self.kind,
            "locator": self.locator,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class Equation:
    equation_id: str
    expression: str
    variables: tuple[str, ...]
    meaning: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "equation_id", _id(self.equation_id, "equation_id"))
        object.__setattr__(self, "expression", _text(self.expression, "expression"))
        object.__setattr__(self, "variables", _tuple_text(self.variables, "variable"))
        object.__setattr__(self, "meaning", _text(self.meaning, "meaning"))

    def to_dict(self) -> dict[str, object]:
        return {
            "equation_id": self.equation_id,
            "expression": self.expression,
            "variables": list(self.variables),
            "meaning": self.meaning,
        }


@dataclass(frozen=True)
class DerivationStep:
    step_id: str
    statement: str
    what_changed: str
    why_valid: str
    rule_or_theorem: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _id(self.step_id, "step_id"))
        for name in ("statement", "what_changed", "why_valid", "rule_or_theorem"):
            object.__setattr__(self, name, _text(getattr(self, name), name))

    def to_dict(self) -> dict[str, str]:
        return {
            "step_id": self.step_id,
            "statement": self.statement,
            "what_changed": self.what_changed,
            "why_valid": self.why_valid,
            "rule_or_theorem": self.rule_or_theorem,
        }


@dataclass(frozen=True)
class CodeExample:
    example_id: str
    language: str
    code: str
    input_description: str
    output_description: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "example_id", _id(self.example_id, "example_id"))
        for name in ("language", "code", "input_description", "output_description"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if self.language.casefold() not in {"python", "sql", "pseudo"}:
            raise ValueError("code example language must be Python, SQL, or pseudo")

    def to_dict(self) -> dict[str, str]:
        return {
            "example_id": self.example_id,
            "language": self.language,
            "code": self.code,
            "input_description": self.input_description,
            "output_description": self.output_description,
        }


@dataclass(frozen=True)
class ApplicationLink:
    application_id: str
    application_type: str
    title: str
    description: str
    target_ids: tuple[str, ...]
    role: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "application_id", _id(self.application_id, "application_id"))
        for name in ("application_type", "title", "description", "role"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "target_ids", _tuple_text(self.target_ids, "target_id"))
        if self.application_type not in {"financial", "quant", "strategy", "event"}:
            raise ValueError("application_type is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "application_id": self.application_id,
            "application_type": self.application_type,
            "title": self.title,
            "description": self.description,
            "target_ids": list(self.target_ids),
            "role": self.role,
        }


@dataclass(frozen=True)
class Misconception:
    misconception_id: str
    claim: str
    correction: str
    source_reference_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "misconception_id", _id(self.misconception_id, "misconception_id"))
        object.__setattr__(self, "claim", _text(self.claim, "claim"))
        object.__setattr__(self, "correction", _text(self.correction, "correction"))
        object.__setattr__(self, "source_reference_ids", _tuple_text(self.source_reference_ids, "source_reference_id"))
        if self.claim.casefold() == self.correction.casefold():
            raise ValueError("misconception correction must differ from claim")

    def to_dict(self) -> dict[str, object]:
        return {
            "misconception_id": self.misconception_id,
            "claim": self.claim,
            "correction": self.correction,
            "source_reference_ids": list(self.source_reference_ids),
        }


@dataclass(frozen=True)
class KnowledgeConcept:
    concept_id: str
    title: str
    domain: str
    concept_type: KnowledgeType
    intuition: str
    formal_definition: str
    prerequisites: tuple[str, ...]
    equations: tuple[Equation, ...]
    derivations: tuple[DerivationStep, ...]
    code_examples: tuple[CodeExample, ...]
    financial_interpretations: tuple[ApplicationLink, ...]
    quant_applications: tuple[ApplicationLink, ...]
    strategy_applications: tuple[ApplicationLink, ...]
    misconceptions: tuple[Misconception, ...]
    source_references: tuple[SourceReference, ...]
    examples: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    exercises: tuple[str, ...] = ()
    current_context: str | None = None
    tags: tuple[str, ...] = ()
    event_links: tuple[ApplicationLink, ...] = ()
    proofs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "concept_id", _id(self.concept_id, "concept_id"))
        for name in ("title", "domain", "intuition", "formal_definition"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if not isinstance(self.concept_type, KnowledgeType):
            object.__setattr__(self, "concept_type", KnowledgeType(self.concept_type))
        object.__setattr__(self, "prerequisites", _tuple_text(self.prerequisites, "prerequisite"))
        object.__setattr__(self, "examples", _tuple_text(self.examples, "example"))
        object.__setattr__(self, "assumptions", _tuple_text(self.assumptions, "assumption"))
        object.__setattr__(self, "exercises", _tuple_text(self.exercises, "exercise"))
        object.__setattr__(self, "tags", _tuple_text(self.tags, "tag"))
        object.__setattr__(self, "event_links", tuple(self.event_links))
        object.__setattr__(self, "proofs", _tuple_text(self.proofs, "proof"))
        for name in (
            "equations",
            "derivations",
            "code_examples",
            "financial_interpretations",
            "quant_applications",
            "strategy_applications",
            "misconceptions",
            "source_references",
        ):
            values = tuple(getattr(self, name))
            if not values:
                raise ValueError(f"{name} are required for a reference concept")
            object.__setattr__(self, name, values)
        source_ids = {item.reference_id for item in self.source_references}
        for misconception in self.misconceptions:
            if not set(misconception.source_reference_ids) <= source_ids:
                raise ValueError(f"{self.concept_id} misconception cites an unknown source")
        if self.current_context is not None:
            object.__setattr__(self, "current_context", _text(self.current_context, "current_context"))

    @property
    def progressive_levels(self) -> tuple[int, ...]:
        levels = [1, 2]
        if self.equations:
            levels.append(3)
        if self.derivations:
            levels.append(4)
        if self.code_examples:
            levels.append(5)
        if self.financial_interpretations:
            levels.append(6)
        if self.quant_applications or self.strategy_applications:
            levels.append(7)
        if self.current_context:
            levels.append(8)
        return tuple(levels)

    @property
    def content_types(self) -> tuple[KnowledgeType, ...]:
        """Typed blocks present on this concept, in progressive order."""

        types = [KnowledgeType.CONCEPT, KnowledgeType.DEFINITION, KnowledgeType.PREREQUISITE]
        if self.equations:
            types.append(KnowledgeType.EQUATION)
        if self.derivations:
            types.append(KnowledgeType.DERIVATION)
        if self.proofs:
            types.append(KnowledgeType.PROOF)
        if self.code_examples:
            types.extend((KnowledgeType.EXAMPLE, KnowledgeType.CODE_EXAMPLE))
        if self.financial_interpretations:
            types.append(KnowledgeType.FINANCIAL_INTERPRETATION)
        if self.quant_applications:
            types.append(KnowledgeType.QUANT_APPLICATION)
        if self.strategy_applications:
            types.append(KnowledgeType.STRATEGY_APPLICATION)
        if self.misconceptions:
            types.append(KnowledgeType.MISCONCEPTION)
        if self.source_references:
            types.append(KnowledgeType.SOURCE_REFERENCE)
        if self.assumptions:
            types.append(KnowledgeType.ASSUMPTION)
        if self.exercises:
            types.append(KnowledgeType.EXERCISE)
        return tuple(types)

    def to_dict(self) -> dict[str, object]:
        return {
            "concept_id": self.concept_id,
            "title": self.title,
            "domain": self.domain,
            "concept_type": self.concept_type.value,
            "intuition": self.intuition,
            "formal_definition": self.formal_definition,
            "prerequisites": list(self.prerequisites),
            "equations": [item.to_dict() for item in self.equations],
            "derivations": [item.to_dict() for item in self.derivations],
            "code_examples": [item.to_dict() for item in self.code_examples],
            "financial_interpretations": [item.to_dict() for item in self.financial_interpretations],
            "quant_applications": [item.to_dict() for item in self.quant_applications],
            "strategy_applications": [item.to_dict() for item in self.strategy_applications],
            "misconceptions": [item.to_dict() for item in self.misconceptions],
            "source_references": [item.to_dict() for item in self.source_references],
            "examples": list(self.examples),
            "assumptions": list(self.assumptions),
            "exercises": list(self.exercises),
            "current_context": self.current_context,
            "tags": list(self.tags),
            "event_links": [item.to_dict() for item in self.event_links],
            "proofs": list(self.proofs),
            "progressive_levels": list(self.progressive_levels),
            "content_types": [item.value for item in self.content_types],
        }


@dataclass(frozen=True)
class LearningPath:
    path_id: str
    title: str
    path_type: str
    concept_ids: tuple[str, ...]
    description: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path_id", _id(self.path_id, "path_id"))
        for name in ("title", "path_type", "description"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "concept_ids", _tuple_text(self.concept_ids, "concept_id"))
        if len(self.concept_ids) < 2:
            raise ValueError("learning path needs at least two concepts")

    def to_dict(self) -> dict[str, object]:
        return {
            "path_id": self.path_id,
            "title": self.title,
            "path_type": self.path_type,
            "concept_ids": list(self.concept_ids),
            "description": self.description,
        }


@dataclass(frozen=True)
class DomainCoverage:
    """A typed map of a requested domain to authored or schema-ready nodes."""

    domain_id: str
    title: str
    category: str
    scope: str
    concept_ids: tuple[str, ...]
    source_reference_ids: tuple[str, ...]
    status: str = "SCHEMA_READY"

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain_id", _id(self.domain_id, "domain_id"))
        for name in ("title", "category", "scope", "status"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "concept_ids", _tuple_text(self.concept_ids, "concept_id"))
        object.__setattr__(self, "source_reference_ids", _tuple_text(self.source_reference_ids, "source_reference_id"))
        if self.status not in {"REFERENCE", "SCHEMA_READY", "PLANNED"}:
            raise ValueError("domain coverage status is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "domain_id": self.domain_id,
            "title": self.title,
            "category": self.category,
            "scope": self.scope,
            "concept_ids": list(self.concept_ids),
            "source_reference_ids": list(self.source_reference_ids),
            "status": self.status,
        }


@dataclass(frozen=True)
class KnowledgeCatalog:
    concepts: tuple[KnowledgeConcept, ...]
    learning_paths: tuple[LearningPath, ...]
    domain_coverage: tuple[DomainCoverage, ...] = ()
    version: str = "p7.5-reference-1"

    def __post_init__(self) -> None:
        if not self.concepts:
            raise ValueError("catalog requires concepts")
        by_id = {item.concept_id: item for item in self.concepts}
        if len(by_id) != len(self.concepts):
            raise ValueError("concept ids must be unique")
        for concept in self.concepts:
            unknown = set(concept.prerequisites) - set(by_id)
            if unknown:
                raise ValueError(f"{concept.concept_id} has unknown prerequisites: {sorted(unknown)}")
        path_ids = {path.path_id for path in self.learning_paths}
        if len(path_ids) != len(self.learning_paths):
            raise ValueError("learning path ids must be unique")
        for path in self.learning_paths:
            if not set(path.concept_ids) <= set(by_id):
                raise ValueError(f"{path.path_id} references an unknown concept")
        source_ids = {
            source.reference_id
            for concept in self.concepts
            for source in concept.source_references
        }
        coverage_ids = {item.domain_id for item in self.domain_coverage}
        if len(coverage_ids) != len(self.domain_coverage):
            raise ValueError("domain coverage ids must be unique")
        for coverage in self.domain_coverage:
            if not set(coverage.concept_ids) <= set(by_id):
                raise ValueError(f"{coverage.domain_id} references an unknown concept")
            if not set(coverage.source_reference_ids) <= source_ids:
                raise ValueError(f"{coverage.domain_id} references an unknown source")
        self._assert_acyclic(by_id)

    def canonical_ids(self) -> dict[str, str]:
        """Stable identity bridge; translations do not add concepts."""
        return {item.concept_id: item.concept_id for item in self.concepts}

    @staticmethod
    def _assert_acyclic(by_id: Mapping[str, KnowledgeConcept]) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(concept_id: str) -> None:
            if concept_id in visiting:
                raise ValueError("knowledge prerequisite graph contains a cycle")
            if concept_id in visited:
                return
            visiting.add(concept_id)
            for prerequisite in by_id[concept_id].prerequisites:
                visit(prerequisite)
            visiting.remove(concept_id)
            visited.add(concept_id)

        for concept_id in by_id:
            visit(concept_id)

    def get(self, concept_id: str) -> KnowledgeConcept:
        for concept in self.concepts:
            if concept.concept_id == concept_id:
                return concept
        raise KeyError(concept_id)

    def search(self, query: str, *, domain: str | None = None) -> tuple[KnowledgeConcept, ...]:
        query = _text(query, "query").casefold()
        normalized_domain = domain.casefold() if domain else None
        results = []
        exact = []
        for concept in self.concepts:
            if normalized_domain and concept.domain.casefold() != normalized_domain:
                continue
            if concept.concept_id.casefold() == query or concept.title.casefold() == query:
                exact.append(concept)
            haystack = json.dumps(concept.to_dict(), sort_keys=True, ensure_ascii=True).casefold()
            if query in haystack:
                results.append(concept)
        if exact:
            return tuple(sorted(exact, key=lambda item: (item.title.casefold(), item.concept_id)))
        return tuple(sorted(results, key=lambda item: (item.title.casefold(), item.concept_id)))

    def prerequisite_closure(self, concept_id: str) -> tuple[KnowledgeConcept, ...]:
        self.get(concept_id)
        seen: set[str] = set()
        ordered: list[KnowledgeConcept] = []

        def visit(item_id: str) -> None:
            if item_id in seen:
                return
            seen.add(item_id)
            concept = self.get(item_id)
            for prerequisite in concept.prerequisites:
                visit(prerequisite)
            ordered.append(concept)

        visit(concept_id)
        return tuple(ordered)

    def path(self, path_id: str) -> LearningPath:
        for path in self.learning_paths:
            if path.path_id == path_id:
                return path
        raise KeyError(path_id)

    def domain(self, domain_id: str) -> DomainCoverage:
        for coverage in self.domain_coverage:
            if coverage.domain_id == domain_id:
                return coverage
        raise KeyError(domain_id)

    def validate(self) -> tuple[str, ...]:
        """Return deterministic validation findings; raise on broken links."""
        findings: list[str] = []
        for concept in self.concepts:
            if not set(concept.prerequisites) <= {item.concept_id for item in self.concepts}:
                raise ValueError(f"{concept.concept_id} prerequisite link is broken")
            if concept.progressive_levels[:2] != (1, 2):
                raise ValueError(f"{concept.concept_id} lacks intuition/formal levels")
            findings.append(f"{concept.concept_id}:levels={','.join(map(str, concept.progressive_levels))}")
        for path in self.learning_paths:
            findings.append(f"path:{path.path_id}:nodes={len(path.concept_ids)}")
        for coverage in self.domain_coverage:
            findings.append(f"domain:{coverage.domain_id}:status={coverage.status}")
        return tuple(findings)

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "concepts": [concept.to_dict() for concept in self.concepts],
            "learning_paths": [path.to_dict() for path in self.learning_paths],
            "domain_coverage": [item.to_dict() for item in self.domain_coverage],
        }

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


_SRC_TEXT = SourceReference(
    "src-text-stat",
    "Statistical inference reference",
    "textbook",
    "https://openstax.org/details/books/introductory-statistics-2e",
    "Definitions and elementary statistical identities are labelled theory-backed.",
)
_SRC_OPENSTAX = SourceReference(
    "src-openstax-finance",
    "Principles of Finance",
    "textbook",
    "https://openstax.org/details/books/principles-finance",
    "Financial conventions are separated from mathematical theorems.",
)
_SRC_BLS = SourceReference(
    "src-bls-cpi",
    "BLS Consumer Price Index",
    "official-data",
    "https://www.bls.gov/cpi/",
    "Use captured, revision-aware observations in offline mode; do not treat a live release as a forecast.",
)
_SRC_FAMA = SourceReference(
    "src-fama-1992",
    "The cross-section of expected stock returns",
    "empirical-paper",
    "https://doi.org/10.1086/261849",
    "Empirical relationships are research evidence, not universal laws.",
)
_SRC_LOPEZ = SourceReference(
    "src-lopez-backtest",
    "Advances in Financial Machine Learning",
    "research-method",
    "https://www.wiley-vch.de/en/areas-interest/finance-economics-law/finance/finance-investments-special-topics-13fiz/advances-in-financial-machine-learning-978-1-119-48208-6",
    "Publisher record for the 2018 Wiley edition; backtest and multiple-testing cautions are methodological guidance, not a theorem or investment recommendation.",
)


def _equation(concept_id: str, expression: str, variables: tuple[str, ...], meaning: str) -> Equation:
    return Equation(f"eq-{concept_id}", expression, variables, meaning)


def _derivation(concept_id: str, statement: str, changed: str, why: str, rule: str) -> DerivationStep:
    return DerivationStep(f"der-{concept_id}", statement, changed, why, rule)


def _code(concept_id: str, code: str, output: str) -> CodeExample:
    return CodeExample(f"code-{concept_id}", "Python", code, "A pandas Series named `returns` or a compatible numeric sequence.", output)


def _links(concept_id: str, title: str, target: str, role: str) -> tuple[ApplicationLink, ...]:
    return (
        ApplicationLink(f"fin-{concept_id}", "financial", title, f"Interpret {title} without implying a forecast.", (target,), role),
    )


def _concept(
    concept_id: str,
    title: str,
    domain: str,
    prerequisites: tuple[str, ...],
    intuition: str,
    formal: str,
    expression: str,
    variables: tuple[str, ...],
    changed: str,
    why: str,
    rule: str,
    code: str,
    finance_role: str,
    quant_role: str,
    strategy_role: str,
    misconception_claim: str,
    misconception_correction: str,
    *,
    source: tuple[SourceReference, ...] = (_SRC_TEXT, _SRC_OPENSTAX),
    current_context: str | None = "Show the concept alongside the user's latest saved research or strategy artifact; absence of context is valid.",
    assumptions: tuple[str, ...] = ("Inputs are labelled with their observation window and source status.",),
    tags: tuple[str, ...] = (),
) -> KnowledgeConcept:
    return KnowledgeConcept(
        concept_id=concept_id,
        title=title,
        domain=domain,
        concept_type=KnowledgeType.CONCEPT,
        intuition=intuition,
        formal_definition=formal,
        prerequisites=prerequisites,
        equations=(_equation(concept_id, expression, variables, formal),),
        derivations=(_derivation(concept_id, expression, changed, why, rule),),
        code_examples=(_code(concept_id, code, f"A deterministic {title} result is returned; inspect missing-data and window assumptions."),),
        financial_interpretations=_links(concept_id, title, concept_id, finance_role),
        quant_applications=(ApplicationLink(f"quant-{concept_id}", "quant", f"{title} in a research run", quant_role, (concept_id,), "quant research"),),
        strategy_applications=(ApplicationLink(f"strategy-{concept_id}", "strategy", f"{title} as a strategy component", strategy_role, (concept_id,), "strategy lab"),),
        misconceptions=(Misconception(f"mis-{concept_id}", misconception_claim, misconception_correction, tuple(item.reference_id for item in source)),),
        source_references=source,
        examples=(f"A small labelled example of {title} is computed from captured returns.",),
        assumptions=assumptions,
        exercises=(f"Explain one limitation of using {title} in a backtest.",),
        current_context=current_context,
        tags=tags or (domain.casefold(), "reference-curriculum"),
    )


def seed_reference_curriculum() -> KnowledgeCatalog:
    """Return the versioned, offline-safe Return→Overfitting curriculum."""

    concepts = (
        _concept("return", "Return", "financial-markets", (), "A return compares what you have now with what you had before.", "A simple return is the proportional change in an observed price or value over a stated interval.", "r_t = P_t / P_{t-1} - 1", ("P_t", "P_{t-1}", "r_t"), "Turn a price ratio into a difference from one.", "Prices are non-negative and the denominator is observed.", "Algebraic rearrangement", "returns = prices.pct_change()", "A holding-period result before costs, cash flows, and tax treatment.", "The target variable for many factor and regression studies.", "The basic signal input; never hide the sampling interval.", "A positive return means the investment is safe.", "A realised return is an observation, not a guarantee; risk and horizon remain explicit.", source=(_SRC_OPENSTAX,), tags=("financial-markets", "returns")),
        _concept("compounding", "Compounding", "portfolio-theory", ("return",), "Gains and losses multiply the capital that comes next.", "Compound wealth is the product of one plus each period return.", "W_T = W_0 ∏(1 + r_t)", ("W_0", "W_T", "r_t"), "Replace repeated additions with a product of growth factors.", "Multiplication models reinvestment of the current wealth.", "Product rule and definition of a return", "wealth = (1 + returns).cumprod()", "Path-dependent wealth is different from the arithmetic average return.", "A cumulative performance series for experiments and attribution.", "The equity curve used before drawdown and OOS checks.", "Average return can be compounded directly.", "Compounding uses products; arithmetic averages are summaries and are not equivalent to realised wealth.", tags=("portfolio-theory", "wealth")),
        _concept("variance", "Variance", "statistics", ("return",), "Variance measures squared dispersion around a chosen centre.", "Variance is the expected or sample average squared deviation from the mean, with the estimator convention stated.", "s² = Σ(xᵢ − x̄)² / (n − 1)", ("xᵢ", "x̄", "n", "s²"), "Centre each observation, square deviations, then average with the chosen denominator.", "Squaring prevents positive and negative deviations cancelling.", "Sample-variance definition; Bessel correction for an unbiased estimate under assumptions", "variance = returns.var(ddof=1)", "A dispersion estimate, not a complete definition of financial risk.", "A volatility precursor and a feature in regression/portfolio research.", "A risk input that must carry window and estimator metadata.", "Higher variance always means a worse investment.", "Variance is a distribution summary; loss preferences, tail risk, liquidity, and horizon also matter.", tags=("statistics", "risk")),
        _concept("standard-deviation", "Standard deviation", "statistics", ("variance",), "Undo variance's squared units so dispersion is comparable to the original measure.", "Standard deviation is the non-negative square root of variance.", "s = √s²", ("s²", "s"), "Apply the square-root transform to variance.", "The square root reverses squared units while preserving non-negativity.", "Principal square-root definition", "std = returns.std(ddof=1)", "A scale estimate in return units under a stated sampling convention.", "A baseline risk estimate; annualisation needs a frequency assumption.", "A rolling risk feature, not a future-risk oracle.", "Standard deviation is the probability of loss.", "It is a dispersion scale, not a probability and not a directional forecast.", tags=("statistics", "risk")),
        _concept("volatility", "Volatility", "financial-markets", ("standard-deviation",), "Volatility is a time-scale-specific way to describe changing dispersion.", "Volatility is an estimate of return variability over a stated window and sampling frequency.", "σ_annual = σ_period × √N", ("σ_period", "N", "σ_annual"), "Scale a period estimate by the square root of the number of periods, when assumptions justify it.", "Variance adds across independent periods; standard deviation is its square root.", "Square-root-of-time convention; independence and stable variance are assumptions", "annualized = returns.std(ddof=1) * (252 ** 0.5)", "A convention for comparing variability across horizons, not a natural constant of an asset.", "A rolling feature with explicit window, frequency, and missing-data policy.", "A strategy risk control, position-sizing input, or regime descriptor.", "Annualised volatility is always comparable across assets.", "Comparability requires consistent data, frequency, liquidity, and estimator assumptions.", tags=("financial-markets", "risk", "feature")),
        _concept("covariance", "Covariance", "statistics", ("return", "variance"), "Covariance records whether two variables tend to move together.", "Covariance is the average product of centred deviations under a stated estimator convention.", "cov(X,Y) = Σ(xᵢ−x̄)(yᵢ−ȳ)/(n−1)", ("X", "Y", "x̄", "ȳ"), "Multiply paired centred deviations before averaging.", "Same-sign deviations contribute positive co-movement; opposite signs contribute negative co-movement.", "Linearity of expectation and sample-covariance definition", "cov = returns_a.cov(returns_b)", "A co-movement estimate whose units depend on both variables.", "An input to beta, factor models, and portfolio covariance matrices.", "A diversification and exposure diagnostic in a strategy portfolio.", "Positive covariance proves one asset causes the other.", "Covariance is association under the sampling design, not causal evidence.", tags=("statistics", "portfolio-theory")),
        _concept("correlation", "Correlation", "statistics", ("covariance", "standard-deviation"), "Correlation rescales covariance to a unit-free co-movement score.", "Correlation is covariance divided by the product of the two standard deviations when both are non-zero.", "ρ_XY = cov(X,Y)/(σ_X σ_Y)", ("cov(X,Y)", "σ_X", "σ_Y", "ρ_XY"), "Normalise covariance by each variable's scale.", "Division by positive scales removes units but not sampling uncertainty.", "Cauchy–Schwarz inequality bounds the result in [−1,1]", "corr = returns_a.corr(returns_b)", "A sample association measure; it can be unstable in small or shifting samples.", "A dependence summary for factor selection and portfolio construction.", "A diversification input, never a guarantee that correlations stay fixed.", "Correlation of zero means the assets are independent.", "Zero linear correlation does not rule out nonlinear dependence.", tags=("statistics", "portfolio-theory")),
        _concept("regression", "Regression", "econometrics", ("correlation",), "Regression asks how an outcome changes with predictors under an explicit model.", "Ordinary least squares selects coefficients minimising the sum of squared residuals for a specified design matrix.", "β̂ = (XᵀX)⁻¹Xᵀy", ("X", "y", "β̂"), "Write residuals as y−Xβ, expand the squared norm, and set the gradient to zero.", "The objective is convex; full-rank X makes the normal equations uniquely solvable.", "Projection theorem and first-order condition", "beta = sm.OLS(y, sm.add_constant(X)).fit().params", "A conditional linear association under the model and data-generating assumptions.", "A factor or macro exposure estimate with uncertainty and diagnostics.", "A signal/feature relationship to validate OOS rather than a standalone strategy.", "A significant coefficient proves causality.", "Regression estimates conditional association; confounding, specification, and timing remain possible.", tags=("econometrics", "ols")),
        _concept("beta", "Beta", "asset-pricing", ("regression", "covariance", "variance"), "Beta measures an asset's linear sensitivity to a benchmark's returns.", "Market beta is the slope coefficient in a regression of asset excess returns on benchmark excess returns.", "β = cov(R_i,R_m)/var(R_m)", ("R_i", "R_m", "β"), "Substitute the covariance and variance expressions into the one-factor slope.", "The OLS slope equals covariance divided by regressor variance in the one-factor case.", "OLS normal equation and covariance definition", "beta = returns_i.cov(returns_m) / returns_m.var(ddof=1)", "A conditional sensitivity estimate, not total risk and not a forecast.", "A feature/exposure for factor attribution and risk decomposition.", "A portfolio control input whose stability must be checked across windows and OOS periods.", "Beta above one means the asset must earn more.", "Beta describes sensitivity in the sample; expected return is a separate asset-pricing claim.", tags=("asset-pricing", "factor")),
        _concept("sharpe", "Sharpe ratio", "portfolio-theory", ("return", "standard-deviation"), "Sharpe compares average excess return with return variability.", "The sample Sharpe ratio is mean excess return divided by standard deviation, with risk-free rate, frequency, and estimator stated.", "S = (E[R_p] − R_f) / σ_p", ("R_p", "R_f", "σ_p", "S"), "Subtract the risk-free return, then divide by the chosen dispersion estimate.", "A ratio normalises an excess-return location estimate by a scale estimate.", "Definition of a standardised excess-return measure", "sharpe = (returns - risk_free).mean() / returns.std(ddof=1)", "A conditional historical risk-adjusted summary, sensitive to non-normality and costs.", "A comparable experiment metric with confidence/uncertainty and selection controls.", "A strategy score alongside drawdown, turnover, OOS, and economic rationale.", "A high Sharpe guarantees a robust strategy.", "Selection bias, non-stationarity, costs, and multiple testing can make an in-sample ratio misleading.", tags=("portfolio-theory", "performance")),
        _concept("drawdown", "Drawdown", "portfolio-theory", ("compounding",), "Drawdown measures how far wealth is below a previous peak.", "Drawdown is current wealth divided by its running maximum minus one; maximum drawdown is the minimum over a window.", "DD_t = W_t / max_{u≤t}(W_u) − 1", ("W_t", "DD_t"), "Maintain the running peak and compare each wealth observation with it.", "The running maximum defines the prior high-water mark.", "Monotonicity of cumulative maximum", "wealth = (1 + returns).cumprod(); drawdown = wealth / wealth.cummax() - 1", "A path-dependent loss from peak, with recovery and horizon context.", "A robustness and tail-path diagnostic for quant experiments.", "A guardrail/constraint for strategy review, not merely a cosmetic metric.", "Maximum drawdown is the worst possible future loss.", "It is the worst observed loss in the chosen sample and window, not a bound on future losses.", tags=("portfolio-theory", "risk")),
        _concept("momentum", "Momentum", "quant-research", ("return", "compounding"), "Momentum ranks or conditions on recent performance to form a directional hypothesis.", "A momentum feature is a rule-defined transformation of lagged returns or prices; its lookback, rebalance, and universe must be explicit.", "M_t = Π_{k=1}^L(1+r_{t-k}) − 1", ("r_t", "L", "M_t"), "Exclude the current observation, compound the selected lagged window, then rank or threshold.", "Lagging avoids using information that was unavailable at decision time.", "Indexing and compounding definitions; no causal theorem is implied", "momentum = (1 + returns.shift(1)).rolling(20).apply(np.prod) - 1", "An empirical pattern whose strength varies by universe, costs, and regime.", "A feature hypothesis requiring leakage, turnover, and OOS checks.", "A signal component that must be tested with costs and realistic execution timing.", "Momentum is a law that always works.", "It is an empirical hypothesis; replication and regime sensitivity are required.", source=(_SRC_FAMA, _SRC_LOPEZ), tags=("quant-research", "factor", "signal")),
        _concept("backtesting", "Backtesting", "quant-research", ("momentum", "drawdown"), "A backtest replays a dated decision rule against historical information constraints.", "A backtest is a reproducible simulation with specified data vintage, signal timing, costs, portfolio rules, and evaluation metrics.", "P_{t+1} = P_t(1 + w_t r_{t+1}) − costs_t", ("w_t", "r_{t+1}", "costs_t"), "Separate information available at t from realised return at t+1 and subtract explicit costs.", "The time index enforces the information boundary; accounting identities track wealth.", "Temporal causality in the simulation contract, not proof of future performance", "result = run_backtest(signal.shift(1), returns, transaction_cost_bps=5)", "A historical what-if experiment with data and model limitations.", "A research artifact linking hypothesis, code, result, and evidence.", "The strategy lab's gate before paper mode and OOS interpretation.", "A profitable backtest proves the strategy works live.", "It only shows simulated historical behaviour under stated assumptions; live slippage and regime shifts remain.", source=(_SRC_LOPEZ,), tags=("quant-research", "backtest")),
        _concept("oos", "Out-of-sample validation", "quant-research", ("backtesting",), "Out-of-sample data is kept untouched until the evaluation is specified.", "An OOS result measures a pre-registered strategy on observations not used for fitting or discretionary selection.", "R_OOS = f(θ_train, D_OOS)", ("θ_train", "D_OOS", "R_OOS"), "Freeze parameters from training, then evaluate exactly once or under a declared protocol.", "Data separation prevents the evaluation set from influencing model selection.", "Holdout design and conditional evaluation", "oos = evaluate(fitted_model, untouched_test_data)", "Evidence about transportability under the chosen split, not a guarantee.", "A validity artifact with split fingerprint, code version, and limitations.", "A release gate before paper claims or community projection.", "One good OOS result proves no overfitting.", "OOS lowers one source of bias but does not remove leakage, selection, or regime risk.", source=(_SRC_LOPEZ,), tags=("quant-research", "validation")),
        _concept("overfitting", "Overfitting", "quant-research", ("backtesting", "oos"), "Overfitting is fitting noise or selection quirks rather than a stable mechanism.", "A model is overfit when in-sample optimisation exploits idiosyncratic data and its performance degrades under honest validation or perturbation.", "min_θ L_train(θ) + λΩ(θ)", ("L_train", "θ", "λ", "Ω"), "Add a complexity penalty or constrain selection, then compare train/validation behaviour.", "Regularisation and honest validation trade fit for generalisation; neither creates a causal mechanism.", "Bias–variance framing and holdout validation", "gap = train_metric - oos_metric", "A model/data mismatch warning rather than a single numeric label.", "A diagnostic that links parameter search, selection count, and OOS degradation.", "A strategy research stop condition before expanding data or features.", "Overfitting only means using too many variables.", "It can arise from repeated trials, leakage, flexible rules, regime selection, and human choices even with few variables.", source=(_SRC_LOPEZ,), tags=("quant-research", "bias", "multiple-testing")),
    )
    derivation_additions = {
        "variance": (
            DerivationStep("der-variance-center", "dᵢ = xᵢ − x̄", "Introduce centred deviations.", "The mean is the reference point for dispersion.", "Definition of the sample mean"),
            DerivationStep("der-variance-square", "s² = Σdᵢ²/(n−1)", "Square and average the deviations.", "Squaring prevents cancellation; n−1 is the stated sample convention.", "Bessel-corrected sample variance"),
        ),
        "regression": (
            DerivationStep("der-regression-residual", "e = y − Xβ", "Express the residual vector.", "The model prediction is Xβ, so the unexplained component is the difference.", "Linear model definition"),
            DerivationStep("der-regression-normal", "XᵀXβ̂ = Xᵀy", "Set the squared-error gradient to zero.", "The objective is differentiable and convex under the stated design.", "First-order condition"),
        ),
        "beta": (
            DerivationStep("der-beta-slope", "β̂ = cov(Rᵢ,Rₘ)/var(Rₘ)", "Reduce the one-factor slope to covariance over variance.", "The one-regressor normal equation gives this ratio.", "OLS normal equation"),
        ),
        "sharpe": (
            DerivationStep("der-sharpe-excess", "Rₑ = Rₚ − R_f", "Convert returns to excess returns.", "The risk-free benchmark is subtracted at the same frequency.", "Definition of excess return"),
        ),
    }
    concepts = tuple(
        replace(item, derivations=item.derivations + derivation_additions.get(item.concept_id, ()))
        for item in concepts
    )
    proofs = {
        "variance": ("The non-negative sum of squared deviations is minimised at the sample mean; the n−1 denominator is the chosen unbiased-estimator convention.",),
        "correlation": ("Cauchy–Schwarz bounds the normalised covariance between −1 and 1 when both variances are positive.",),
        "regression": ("At the OLS solution, residuals are orthogonal to every column of X, so no included linear direction reduces the squared-error objective.",),
        "beta": ("In the one-factor model, the slope is covariance with the factor divided by factor variance.",),
    }
    concepts = tuple(replace(item, proofs=proofs.get(item.concept_id, ())) for item in concepts)
    event_links = {
        "return": (ApplicationLink("event-return-cpi", "event", "CPI to valuation", "Trace a captured CPI release through inflation, rates, bonds, discounting, and valuation.", ("sample-cpi-2026-01",), "event learning"),),
        "volatility": (ApplicationLink("event-volatility-rates", "event", "Rates to volatility context", "Use the event chain as context; it is not a directional forecast.", ("sample-cpi-2026-01",), "event learning"),),
    }
    concepts = tuple(replace(item, event_links=event_links.get(item.concept_id, ())) for item in concepts)
    paths = (
        LearningPath("reference-curriculum", "Return to overfitting", "REFERENCE", tuple(item.concept_id for item in concepts), "The first Finathink reference path from measurement to honest research."),
        LearningPath("regression-path", "Regression and factor exposure", "REGRESSION", ("return", "variance", "covariance", "correlation", "regression", "beta"), "Build the mathematical path from returns to a conditional beta."),
        LearningPath("quant-research-path", "Quantitative research", "QUANT_RESEARCH", ("return", "momentum", "backtesting", "oos", "overfitting"), "Move from a feature hypothesis to a reproducible and sceptical evaluation."),
        LearningPath("strategy-path", "Strategy lab", "STRATEGY", ("volatility", "momentum", "backtesting", "drawdown", "oos", "overfitting"), "Connect feature, signal, path risk, validation, and strategy review."),
    )
    source_ids = tuple(dict.fromkeys(source.reference_id for concept in concepts for source in concept.source_references))
    domain_coverage = (
        DomainCoverage("mathematics-linear-algebra", "Linear algebra", "MATHEMATICS", "Projection, vectors, matrices, and OLS support.", ("regression", "beta"), source_ids[:1]),
        DomainCoverage("mathematics-calculus", "Calculus", "MATHEMATICS", "Derivatives and optimisation are schema-ready for model objectives.", ("regression", "overfitting"), source_ids[:1]),
        DomainCoverage("mathematics-optimization", "Optimization", "MATHEMATICS", "Convex objective, regularisation, and portfolio optimisation hooks.", ("sharpe", "overfitting"), source_ids[:1]),
        DomainCoverage("probability", "Probability", "PROBABILITY", "Random variables, expectation, and uncertainty support.", ("variance", "correlation"), source_ids[:1]),
        DomainCoverage("statistics", "Statistics", "STATISTICS", "Dispersion, association, and estimation reference slice.", ("variance", "correlation", "regression"), source_ids[:1], "REFERENCE"),
        DomainCoverage("econometrics-regression", "Regression", "ECONOMETRICS", "OLS and conditional exposure support.", ("regression", "beta"), source_ids[:1], "REFERENCE"),
        DomainCoverage("econometrics-time-series", "Time series", "ECONOMETRICS", "Lagging, windows, and evaluation boundaries for sequential data.", ("return", "momentum", "backtesting"), source_ids[:1]),
        DomainCoverage("quantitative-research", "Quantitative research", "QUANTITATIVE_RESEARCH", "Factors, backtests, OOS, bias, and multiple-testing guardrails.", ("momentum", "backtesting", "oos", "overfitting"), source_ids[-1:], "REFERENCE"),
        DomainCoverage("portfolio-theory-risk", "Portfolio theory and risk", "PORTFOLIO_THEORY", "Volatility, Sharpe, drawdown, and covariance hooks.", ("volatility", "sharpe", "drawdown", "covariance"), source_ids[:1], "REFERENCE"),
        DomainCoverage("asset-pricing", "Asset pricing", "ASSET_PRICING", "Beta and factor exposure interpretation.", ("beta",), source_ids[-2:-1] or source_ids[:1], "REFERENCE"),
        DomainCoverage("markets-equities", "Equities", "FINANCIAL_MARKETS", "Price returns and momentum research.", ("return", "momentum"), source_ids[:1]),
        DomainCoverage("markets-bonds", "Bonds", "FINANCIAL_MARKETS", "Duration/rates extensions attach to volatility and drawdown context.", ("volatility", "drawdown"), source_ids[:1]),
        DomainCoverage("markets-macro", "Macro", "FINANCIAL_MARKETS", "Event-to-rates context attaches to return and volatility.", ("return", "volatility"), source_ids[:1]),
        DomainCoverage("markets-derivatives", "Derivatives", "FINANCIAL_MARKETS", "Implied/realised volatility extension point.", ("volatility",), source_ids[:1]),
        DomainCoverage("accounting-fundamentals", "Accounting and fundamentals", "ACCOUNTING", "Fundamental data adapters can join returns with source status.", ("return",), source_ids[:1]),
        DomainCoverage("behavioral-science", "Behavioral science", "BEHAVIORAL", "Momentum and overfitting interpretations remain hypotheses, not diagnoses.", ("momentum", "overfitting"), source_ids[-1:], "REFERENCE"),
        DomainCoverage("computer-science-finance", "Computer science for finance", "COMPUTER_SCIENCE", "Reproducible code, versioning, and validation contracts.", ("backtesting", "oos"), source_ids[-1:], "REFERENCE"),
    )
    catalog = KnowledgeCatalog(tuple(concepts), paths, domain_coverage)
    catalog.validate()
    return catalog


DEFAULT_CATALOG = seed_reference_curriculum()


__all__ = [
    "DEFAULT_CATALOG",
    "ApplicationLink",
    "CodeExample",
    "DerivationStep",
    "DomainCoverage",
    "Equation",
    "KnowledgeCatalog",
    "KnowledgeConcept",
    "KnowledgeType",
    "LearningPath",
    "Misconception",
    "SourceReference",
    "seed_reference_curriculum",
]

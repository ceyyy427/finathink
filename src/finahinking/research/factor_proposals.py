"""Governed, paper-only factor proposal catalog.

Natural language selects a versioned family of fixed expressions; it never
becomes executable Python or a registry mutation.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from string import Formatter
from typing import Any

import pandas as pd

from finahinking.factors.dsl import evaluate_expression, parse_factor_expression

_FIELDS = frozenset({"close", "volume", "return_1d", "turnover_rate", "amount"})
_FAMILIES = frozenset({"mean_reversion", "momentum", "volatility", "liquidity"})
_DIRECTIONS = frozenset({"positive", "negative", "neutral"})
_UNSAFE = re.compile(r"(?:eval|exec|__import__|shell|subprocess|https?://|file://|/|\\|future|lookahead)", re.IGNORECASE)
_TEMPLATE_FIELDS = frozenset({"window", "field"})


def _norm(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return " ".join(value.split()).casefold()


@dataclass(frozen=True, slots=True)
class FactorHypothesis:
    hypothesis_id: str
    text: str
    family: str
    inputs: tuple[str, ...]
    horizon: int
    direction: str
    template_name: str = ""
    template_version: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "hypothesis_id", _norm(self.hypothesis_id, "hypothesis_id"))
        object.__setattr__(self, "text", _norm(self.text, "text"))
        family = _norm(self.family, "family").replace("-", "_").replace(" ", "_")
        if family not in _FAMILIES:
            raise ValueError(f"unknown factor family: {family}")
        object.__setattr__(self, "family", family)
        fields = tuple(sorted({_norm(item, "inputs") for item in self.inputs}))
        if not fields or any(item not in _FIELDS for item in fields):
            raise ValueError("inputs contain a field outside the data whitelist")
        if family == "liquidity" and not ({"volume", "turnover_rate", "amount"} & set(fields)):
            raise ValueError("liquidity hypotheses require explicit liquidity fields")
        if family in {"momentum", "mean_reversion", "volatility"} and not ({"close", "return_1d"} & set(fields)):
            raise ValueError(f"{family} hypotheses require close or return_1d")
        if _UNSAFE.search(self.text):
            raise ValueError("hypothesis contains unsafe or future-looking text")
        object.__setattr__(self, "inputs", fields)
        if isinstance(self.horizon, bool) or not isinstance(self.horizon, int) or not 1 <= self.horizon <= 252:
            raise ValueError("horizon must be an integer between 1 and 252")
        direction = _norm(self.direction, "direction")
        if direction not in _DIRECTIONS:
            raise ValueError("direction is not supported")
        object.__setattr__(self, "direction", direction)
        if self.template_name:
            object.__setattr__(self, "template_name", _norm(self.template_name, "template_name"))
        if self.template_version:
            object.__setattr__(self, "template_version", _norm(self.template_version, "template_version"))


@dataclass(frozen=True, slots=True)
class FactorTemplate:
    name: str
    family: str
    expression_template: str
    required_fields: tuple[str, ...]
    version: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(repr((self.name, self.family, self.expression_template, self.required_fields, self.version)).encode()).hexdigest()


class FactorTemplateRegistry:
    """Registry for fixed, versioned factor DSL expressions.

    Template rendering only substitutes a bounded window and allow-listed field;
    the resulting expression is always parsed by the factor DSL parser.
    """

    def __init__(self, templates: Mapping[str, FactorTemplate] | None = None) -> None:
        self._templates: dict[str, FactorTemplate] = {}
        for template in (_default_templates() if templates is None else templates.values()):
            self.register(template.name, template.family, template.expression_template, template.required_fields, template.version)

    def register(self, name: str, family: str, expression_template: str, required_fields: Sequence[str], version: str) -> FactorTemplate:
        normalized_name = _norm(name, "name").replace(" ", "_").replace("-", "_")
        normalized_family = _norm(family, "family").replace(" ", "_").replace("-", "_")
        if normalized_family not in _FAMILIES:
            raise ValueError(f"unknown factor family: {normalized_family}")
        if not isinstance(expression_template, str) or not expression_template.strip() or len(expression_template) > 512:
            raise ValueError("expression_template must be a bounded non-empty string")
        if _UNSAFE.search(expression_template):
            raise ValueError("template contains unsafe or future-looking content")
        names = tuple(sorted({_norm(item, "required_fields") for item in required_fields}))
        if not names or any(item not in _FIELDS for item in names):
            raise ValueError("required_fields contain a field outside the data whitelist")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("version must be non-empty")
        try:
            for _, placeholder, spec, conversion in Formatter().parse(expression_template):
                if placeholder is not None and (placeholder not in _TEMPLATE_FIELDS or spec or conversion):
                    raise ValueError("template contains an unsupported placeholder")
            for window in (1, 20, 252):
                parsed = parse_factor_expression(expression_template.format(field=names[0], window=window), names)
                if parsed.fields != names:
                    raise ValueError("template dependencies must match required_fields")
                evaluate_expression(parsed.expression, pd.DataFrame({name: [1.0, 2.0] for name in names}))
        except (KeyError, IndexError) as exc:
            raise ValueError("template placeholders are invalid") from exc
        if normalized_name in self._templates:
            raise ValueError(f"factor template {normalized_name} is already registered")
        template = FactorTemplate(normalized_name, normalized_family, expression_template.strip(), names, version.strip())
        self._templates[normalized_name] = template
        return template

    def get(self, name: str) -> FactorTemplate:
        key = _norm(name, "name").replace(" ", "_").replace("-", "_")
        try:
            return self._templates[key]
        except KeyError as exc:
            raise ValueError(f"unknown factor template: {key}") from exc

    def resolve(self, text: str, config: Mapping[str, Any]) -> FactorHypothesis:
        if not isinstance(text, str) or not text.strip() or _UNSAFE.search(text):
            raise ValueError("unknown or unsafe factor language")
        config = dict(config or {})
        lowered = " ".join(text.casefold().replace("_", " ").replace("-", " ").split())
        requested = config.get("template") or config.get("template_name")
        template = self.get(str(requested)) if requested is not None else None
        if template is None:
            # Exact template names and a small, explicit family vocabulary only.
            for candidate in sorted(self._templates.values(), key=lambda item: len(item.name), reverse=True):
                if candidate.name.replace('_', ' ') == lowered:
                    template = candidate
                    break
        if template is None:
            family = config.get("family")
            aliases = (("mean_reversion", ("mean reversion", "mean revert", "reversion", "reversal")), ("momentum", ("momentum", "trend", "momentum signal", "price momentum should persist")), ("volatility", ("volatility", "vol")), ("liquidity", ("liquidity", "volume")))
            if family is None:
                family = next((name for name, words in aliases if lowered in words), None)
            if family is None:
                raise ValueError("unknown factor language")
            template = self.get(str(family))
        if "template_version" in config and config["template_version"] != template.version:
            raise ValueError("template version does not match registered version")
        raw_window = config.get("horizon", config.get("window", 20))
        if isinstance(raw_window, bool) or not isinstance(raw_window, int) or not 1 <= raw_window <= 252:
            raise ValueError("window must be an integer between 1 and 252")
        raw_fields = config.get("inputs", config.get("required_fields", template.required_fields))
        fields = tuple(sorted({_norm(item, "inputs") for item in raw_fields}))
        if not fields or any(item not in _FIELDS for item in fields) or not set(template.required_fields).issubset(fields):
            raise ValueError("inputs contain a field outside the data whitelist or omit template fields")
        base_field = "return_1d" if "return_1d" in fields and "return_1d" in template.required_fields else template.required_fields[0]
        expression = template.expression_template.format(window=raw_window, field=base_field)
        parse_factor_expression(expression, fields)
        family = template.family
        direction = config.get("direction", "positive")
        return FactorHypothesis(
            str(config.get("hypothesis_id", f"hypothesis-{hashlib.sha256(text.encode()).hexdigest()[:12]}")), text,
            family, fields, raw_window, direction, template.name, template.version,
        )

    def list(self) -> tuple[FactorTemplate, ...]:
        return tuple(self._templates[key] for key in sorted(self._templates))

    def bind(self, hypothesis: FactorHypothesis) -> FactorHypothesis:
        if hypothesis.template_name:
            template = self.get(hypothesis.template_name)
        else:
            # Explicit compatibility for original object/mapping hypotheses.
            name = hypothesis.family
            if name in {"momentum", "volatility"} and "return_1d" not in hypothesis.inputs:
                name += "_close"
            if name == "mean_reversion" and "return_1d" in hypothesis.inputs:
                name += "_returns"
            template = self.get(name)
        if hypothesis.template_version and hypothesis.template_version != template.version:
            raise ValueError("template version does not match registered version")
        if hypothesis.family != template.family or not set(template.required_fields).issubset(hypothesis.inputs):
            raise ValueError("hypothesis family or fields do not match registered template")
        return replace(hypothesis, template_name=template.name, template_version=template.version)


def _default_templates() -> tuple[FactorTemplate, ...]:
    return (
        FactorTemplate("mean_reversion", "mean_reversion", "negate(rank(rolling_mean({field},{window})))", ("close",), "1.0.0"),
        FactorTemplate("momentum", "momentum", "rank(rolling_mean({field},{window}))", ("return_1d",), "1.0.0"),
        FactorTemplate("volatility", "volatility", "negate(rank(rolling_std({field},{window})))", ("return_1d",), "1.0.0"),
        FactorTemplate("liquidity", "liquidity", "rank(rolling_mean(volume,{window}))", ("volume",), "1.0.0"),
        FactorTemplate("short_term_reversal", "mean_reversion", "negate(rank(rolling_mean({field},{window})))", ("return_1d",), "1.0.0"),
        FactorTemplate("volume_trend", "liquidity", "rank(rolling_mean(volume,{window}))", ("volume",), "1.0.0"),
        FactorTemplate("momentum_close", "momentum", "rank(rolling_mean(close,{window}))", ("close",), "1.0.0"),
        FactorTemplate("volatility_close", "volatility", "negate(rank(rolling_std(close,{window})))", ("close",), "1.0.0"),
        FactorTemplate("mean_reversion_returns", "mean_reversion", "negate(rank(rolling_mean(return_1d,{window})))", ("return_1d",), "1.0.0"),
    )


@dataclass(frozen=True, slots=True)
class FactorProposal:
    proposal_id: str
    expression: str
    source_hypothesis: str
    required_fields: tuple[str, ...]
    constraints: Mapping[str, Any]
    paper_only: bool = True


def _templates(hypothesis: FactorHypothesis, registry: FactorTemplateRegistry) -> tuple[str, ...]:
    template = registry.get(hypothesis.template_name)
    windows = tuple(dict.fromkeys((hypothesis.horizon, min(252, hypothesis.horizon * 2))))
    return tuple(template.expression_template.format(field=template.required_fields[0], window=window) for window in windows)


class FactorProposalCatalog:
    def __init__(self, registry: FactorTemplateRegistry | None = None) -> None:
        self.registry = registry if registry is not None else FactorTemplateRegistry()

    def propose(self, hypothesis: FactorHypothesis, limit: int = 5) -> tuple[FactorProposal, ...]:
        if not isinstance(hypothesis, FactorHypothesis):
            raise TypeError("hypothesis must be a FactorHypothesis")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 32:
            raise ValueError("limit must be between 1 and 32")
        hypothesis = self.registry.bind(hypothesis)
        proposals: list[FactorProposal] = []
        for expression in _templates(hypothesis, self.registry):
            parsed = parse_factor_expression(expression, hypothesis.inputs)
            payload = f"{hypothesis.hypothesis_id}|{parsed.expression}|{','.join(parsed.fields)}".encode()
            digest = hashlib.sha256(payload).hexdigest()[:12]
            proposals.append(FactorProposal(
                proposal_id=f"factor-proposal-{digest}",
                expression=parsed.expression,
                source_hypothesis=hypothesis.hypothesis_id,
                required_fields=parsed.fields,
                constraints={"max_window": 252, "point_in_time": True, "paper_only": True},
            ))
        proposals.sort(key=lambda item: item.proposal_id)
        return tuple(proposals[:limit])


def validate_factor_proposal(proposal: FactorProposal) -> None:
    if not isinstance(proposal, FactorProposal):
        raise TypeError("proposal must be a FactorProposal")
    if proposal.paper_only is not True:
        raise ValueError("factor proposals must be paper-only")
    if _UNSAFE.search(proposal.expression) or _UNSAFE.search(proposal.source_hypothesis):
        raise ValueError("proposal contains unsafe or future-looking content")
    fields = tuple(str(item).strip() for item in proposal.required_fields)
    if not fields or any(item not in _FIELDS for item in fields):
        raise ValueError("proposal references a field outside the data whitelist")
    parsed = parse_factor_expression(proposal.expression, fields)
    if parsed.fields != tuple(sorted(set(fields))):
        raise ValueError("required_fields must match expression dependencies")
    constraints = dict(proposal.constraints)
    if constraints != {"max_window": 252, "point_in_time": True, "paper_only": True}:
        raise ValueError("proposal constraints are not the governed catalog constraints")
    payload = f"{proposal.source_hypothesis}|{parsed.expression}|{','.join(parsed.fields)}".encode()
    expected = f"factor-proposal-{hashlib.sha256(payload).hexdigest()[:12]}"
    if proposal.proposal_id != expected:
        raise ValueError("proposal provenance digest does not match its contents")


def proposal_from_handoff(package, artifact, *, catalog, policy) -> FactorProposal:
    """Accept a saved typed artifact, never arbitrary executable model output."""
    from .contracts import stable_digest
    from .factor_recipes import pilot_factor_specs
    from .method_handoff import MethodHandoffPackage, _outputs

    if not isinstance(package, MethodHandoffPackage) or package.envelope.role != "research":
        raise ValueError("factor proposal requires a research method package")
    methods = package.context.to_dict()["method_context"]["manifest"]["methods"]
    if "factor-mining-workflow" not in {row["skill_id"] for row in methods}:
        raise ValueError("factor mining method was not loaded for this task")
    catalog.get_resource("factor-validation-v1")
    if not isinstance(policy, Mapping) or type(policy.get("max_candidates", 1)) is not int or not 1 <= policy.get("max_candidates", 1) <= 8:
        raise ValueError("factor proposal exceeds the declared candidate budget")
    if not isinstance(artifact, dict) or set(artifact) != {
        "artifact_ref", "artifact_type", "artifact_digest", "content_digest", "payload",
    }:
        raise ValueError("factor proposal requires a saved method artifact reference")
    kind = artifact["artifact_type"]
    schemas = {"ResearchPlan": "research-plan.v1", "FactorProposal": "factor-proposal.v1"}
    if kind not in schemas:
        raise ValueError("unsupported method output type")
    output = {"artifact_type": kind, "schema_version": schemas[kind],
              "input_digest": package.envelope.input_digest, "content_digest": artifact["content_digest"],
              "payload": artifact["payload"]}
    document = {"schema_version": "method-output.v1", "package_digest": package.package_digest,
                "task_id": package.envelope.task_id, "output": output}
    digest = stable_digest(document)
    if artifact["artifact_digest"] != digest or artifact["artifact_ref"] != f"artifact:method-{digest}":
        raise ValueError("method proposal artifact belongs to another package or changed content")
    _outputs(package, {"output_artifacts": [output]})
    if kind == "FactorProposal":
        proposal = FactorProposal(**{**artifact["payload"], "required_fields": tuple(artifact["payload"]["required_fields"])})
    else:
        plan = artifact["payload"]
        if len(plan["factor_ids"]) != 1 or len(plan["hypotheses"]) != 1:
            raise ValueError("a method study consumes one explicitly declared factor/hypothesis")
        if plan["risk_policy_version"] != policy.get("risk_policy_version", "risk.v1"):
            raise ValueError("research plan cannot replace the server risk policy")
        window = plan["validation_spec"].get("window", 5)
        selected = next((item for item in pilot_factor_specs(window) if item.factor_id == plan["factor_ids"][0]), None)
        if selected is None:
            raise ValueError("research plan references an unaudited factor definition")
        source = plan["hypotheses"][0]
        identity = hashlib.sha256(f"{source}|{selected.formula}|{','.join(selected.required_fields)}".encode()).hexdigest()[:12]
        proposal = FactorProposal(f"factor-proposal-{identity}", selected.formula, source, selected.required_fields,
                                  {"max_window": 252, "point_in_time": True, "paper_only": True})
    validate_factor_proposal(proposal)
    windows = policy.get("window_grid", [3, 5, 8])
    if not isinstance(windows, (list, tuple)) or not 1 <= len(windows) <= 8:
        raise ValueError("factor proposal needs a bounded predeclared window set")
    if not any(spec.formula == proposal.expression for window in windows for spec in pilot_factor_specs(window)):
        raise ValueError("factor proposal has no audited pilot implementation in the declared windows")
    return proposal

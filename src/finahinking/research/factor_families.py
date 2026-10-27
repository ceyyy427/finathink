"""Five independently defined pilots, not blanket approval of whole families."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pandas as pd

from finahinking.data.research_fields import field_time

from .contracts import stable_digest
from .factor_recipes import family_definitions, family_factor_specs


@dataclass(frozen=True, slots=True)
class FamilyRequirements:
    family_id: str
    required_fields: tuple[str, ...]
    units: tuple[tuple[str, str], ...]
    time_fields: tuple[str, ...]
    version: str = "family-inputs.v1"


@dataclass(frozen=True, slots=True)
class FamilyInputAudit:
    _json: str

    def to_dict(self):
        return json.loads(self._json)

    @property
    def fingerprint(self):
        return stable_digest(self.to_dict())


def family_for_spec(spec):
    return next((row["family_id"] for row in family_definitions() if row["factor_id"] == spec.factor_id), None)


def family_requirements(family_id):
    spec = next((value for value in family_factor_specs() if family_for_spec(value) == family_id), None)
    if spec is None:
        raise ValueError("unknown audited family")
    row = next(row for row in family_definitions() if row["family_id"] == family_id)
    extras = {"industry": ("industry",), "price-limit": ("limit_rule_version",),
              "institutional": ("seat_identity_confidence",)}.get(family_id, ())
    timing = {"financial": ("announcement_at", "report_period_end"), "industry": ("effective_from",),
              "price-limit": ("effective_from",)}.get(family_id, ())
    return FamilyRequirements(family_id, (*spec.required_fields, *extras), tuple(sorted(row["required_input_units"].items())), timing)


def audit_family_inputs(family_id, panel, policy) -> FamilyInputAudit:
    requirements = family_requirements(family_id)
    definition = next(row for row in family_definitions() if row["family_id"] == family_id)
    if not isinstance(policy, dict):
        raise TypeError("family audit policy must be an object")
    codes = set()
    if panel.status != "READY":
        codes.add("PANEL_NOT_READY")
    missing = panel.missing_inputs(requirements.required_fields)
    if missing:
        codes.add("MISSING_FIELDS")
    for name, unit in requirements.units:
        if panel.units.get(name) != unit:
            codes.add("UNIT_MISMATCH")
    if policy.get("factor_spec") is not None:
        expected = next(spec for spec in family_factor_specs() if family_for_spec(spec) == family_id)
        if policy["factor_spec"] != expected.to_dict():
            codes.add("DEFINITION_MISMATCH")
    for row in panel.to_dict()["rows"]:
        signal = field_time(row["timestamp"])
        denominator = {"financial": "market_cap", "price-limit": "limit_up_price"}.get(family_id)
        if denominator and row.get(denominator) is not None and row[denominator] <= 0:
            codes.add("NONPOSITIVE_DENOMINATOR")
        if family_id == "financial":
            if row.get("book_equity") is None:
                continue
            provenance = row["field_provenance"].get("book_equity", {})
            announcement = provenance.get("announcement_at")
            period = provenance.get("report_period_end")
            known = provenance.get("available_at")
            if not announcement or not known or field_time(announcement) > signal or field_time(announcement) > field_time(known):
                codes.add("ANNOUNCEMENT_NOT_KNOWN")
            if not period or (announcement and field_time(period) > field_time(announcement)):
                codes.add("REPORT_PERIOD_END_MISSING_OR_INVALID")
            revision = provenance.get("revision_at")
            if revision and (field_time(revision) > signal or field_time(revision) > field_time(known)
                             or (announcement and field_time(revision) < field_time(announcement))):
                codes.add("REVISION_NOT_KNOWN")
        elif family_id == "industry":
            provenance = row["field_provenance"].get("industry", {})
            if not provenance.get("effective_from") or not row.get("industry"):
                codes.add("HISTORICAL_CLASSIFICATION_MISSING")
        elif family_id == "price-limit":
            provenance = row["field_provenance"].get("limit_rule_version", {})
            if not row.get("limit_rule_version") or not provenance.get("effective_from"):
                codes.add("LIMIT_RULE_VERSION_MISSING")
        elif family_id == "institutional":
            if row.get("seat_identity_confidence") != "SOURCE_VERIFIED_INSTITUTIONAL_AGGREGATE":
                codes.add("SEAT_IDENTITY_UNCERTAIN")
    if family_id == "industry" and panel.missing_inputs(("industry_relative_return",)):
        codes.add("INSUFFICIENT_CONTEMPORANEOUS_PEERS")
    limitations = [definition.get("rights_notice", "User-declared units, timing and identities are not independent source verification."),
        "Only this named pilot is adapted; other family members remain SOURCE_ONLY.",
        "Static declared universe and source classifications are not survivorship or data-license certification."]
    result = {"schema_version": "family-input-audit.v1", "family_id": family_id, "version": requirements.version,
        "status": "BLOCKED_DATA" if codes else "READY", "codes": sorted(codes), "missing_fields": list(missing),
        "panel_fingerprint": panel.fingerprint, "definition_fingerprint": stable_digest(definition),
        "source_reference": definition.get("source_reference"), "reuse_basis": definition.get("reuse_basis", "FINATHINK_ORIGINAL_DEFINITION"),
        "reproduction_scope": "FORMULA_FIXTURE" if panel.to_dict()["data_mode"] in {"SAMPLE", "SYNTHETIC"} else "USER_DATA_EVALUATION",
        "limitations": limitations}
    return FamilyInputAudit(json.dumps(result, ensure_ascii=False, allow_nan=False, sort_keys=True))


def evaluate_family_factor(spec, panel) -> pd.Series:
    from .factor_recipes import evaluate_factor_spec

    family = family_for_spec(spec)
    if family is None or audit_family_inputs(family, panel, {"factor_spec": spec.to_dict()}).to_dict()["status"] != "READY":
        raise ValueError("BLOCKED_DATA: audited family inputs required")
    return evaluate_factor_spec(spec, panel)

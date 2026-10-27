"""Fail-closed loading and human-gated admission of factor catalogs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from finahinking.factors.registry import FactorRegistry, RegisteredFactor

from .factor_catalog import (
    FactorCatalog,
    FactorCatalogEntry,
    HumanAdmissionRecord,
    audit_factor_catalog_entry,
)

_ENTRY_FIELDS = frozenset({
    "factor_id", "version", "source_ids", "license_status", "pit_semantics",
    "required_fields", "research_fingerprint", "status", "fingerprint",
    "evaluation_fingerprint",
})


@dataclass(frozen=True, slots=True)
class CatalogAdmissionProposal:
    entry: FactorCatalogEntry
    reason: str = "explicit human admission is required"
    production_write: bool = False

    @property
    def proposal_id(self) -> str:
        return self.entry.factor_id

    @property
    def fingerprint(self) -> str:
        return self.entry.fingerprint


def _entry(raw: Any) -> FactorCatalogEntry:
    if not isinstance(raw, dict):
        raise TypeError("catalog entries must be objects")
    unknown = set(raw) - _ENTRY_FIELDS
    if unknown:
        raise ValueError(f"unknown catalog field: {min(unknown)}")
    values = {key: raw[key] for key in _ENTRY_FIELDS - {"fingerprint"} if key in raw}
    entry = FactorCatalogEntry(**values)
    audit = audit_factor_catalog_entry(entry, require_evaluation=True)
    if not audit.eligible:
        raise ValueError(f"catalog entry is blocked: {entry.factor_id}")
    if "fingerprint" not in raw:
        raise ValueError("catalog fingerprint is required")
    declared = raw["fingerprint"]
    if not isinstance(declared, str) or declared != entry.fingerprint:
        raise ValueError(f"catalog fingerprint mismatch for {entry.factor_id}")
    return entry


def load_audited_catalog(path: str | Path | None) -> FactorCatalog:
    """Load a JSON catalog and validate every entry before returning it."""

    if path is None:
        return BUILTIN_AUDITED_CATALOG
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise FileNotFoundError(f"factor catalog not found: {source}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("factor catalog must be valid JSON") from exc
    rows = payload.get("entries") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise TypeError("factor catalog must contain an entries list")
    catalog = FactorCatalog()
    for row in rows:
        catalog = catalog.propose(_entry(row))
    return catalog


def admit_catalog_entry(
    entry: FactorCatalogEntry,
    admission: HumanAdmissionRecord | None = None,
    *,
    registry: FactorRegistry | None = None,
) -> RegisteredFactor | CatalogAdmissionProposal:
    """Return a proposal unless an explicit human record authorizes the write."""

    audit = audit_factor_catalog_entry(entry, require_evaluation=True)
    if not audit.eligible:
        raise ValueError("catalog entry is blocked")
    if admission is None:
        return CatalogAdmissionProposal(entry)
    target = registry if registry is not None else _DEFAULT_REGISTRY
    return target.register_catalog_entry(entry, admission)


def _builtin_entry(factor_id: str, fingerprint: str) -> FactorCatalogEntry:
    return FactorCatalogEntry(
        factor_id=factor_id,
        version="1.0.0",
        source_ids=("finathink-fixture",),
        license_status="MIT",
        pit_semantics="T+1 point-in-time",
        required_fields=("close",),
        research_fingerprint=fingerprint,
        status="PROPOSED",
        evaluation_fingerprint=fingerprint,
    )


BUILTIN_AUDITED_CATALOG = FactorCatalog(
    (
        _builtin_entry("momentum_20d", "1" * 64),
        _builtin_entry("momentum_60d", "2" * 64),
    )
)
DEFAULT_FACTOR_CATALOG = BUILTIN_AUDITED_CATALOG
_DEFAULT_REGISTRY = FactorRegistry()


def load_research_recipes():
    """The current research menu; legacy fixture admission metadata stays separate."""
    from .factor_recipes import research_recipes

    return research_recipes()


def get_research_recipe(recipe_id):
    for recipe in load_research_recipes():
        if recipe.recipe_id == recipe_id:
            return recipe
    raise KeyError(recipe_id)


__all__ = [
    "BUILTIN_AUDITED_CATALOG",
    "DEFAULT_FACTOR_CATALOG",
    "CatalogAdmissionProposal",
    "admit_catalog_entry",
    "get_research_recipe",
    "load_audited_catalog",
    "load_research_recipes",
]

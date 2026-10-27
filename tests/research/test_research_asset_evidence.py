"""Source assertions, formula definitions and local research are separate axes."""

import hashlib
import json

import pytest

from finahinking.research.asset_catalog import AssetLoadError, ResearchAssetCatalog
from tests.research.test_research_asset_catalog import _write_catalog


def _catalog(root, *, claims=(), lessons=(), definitions=(), aliases=()):
    manifest = _write_catalog(root)
    bodies = {
        "source-claims": ("evidence/source-claims.jsonl", "".join(json.dumps(c) + "\n" for c in claims)),
        "lessons": ("lessons/falsifications.jsonl", "".join(json.dumps(c) + "\n" for c in lessons)),
        "source-definitions": ("factors/source-definitions.json", json.dumps({
            "schema_version": "source-definitions.v1", "definitions": list(definitions),
            "aliases": list(aliases)})),
    }
    for resource_id, (path, text) in bodies.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        raw = text.encode()
        manifest["resources"].append({
            "resource_id": resource_id, "path": path, "sha256": hashlib.sha256(raw).hexdigest(),
            "byte_size": len(raw), "availability": "PACKAGED", "kind": "reference-data",
            "author": "test", "license_id": "MIT", "license_ref": "license", "version": "1.0.0",
        })
    (root / "provenance/upstream-lock.json").write_text(json.dumps(manifest))
    return ResearchAssetCatalog(root)


def _claim(**changes):
    return {"claim_id": "c1", "subject_id": "candidate", "evidence_status": "SOURCE_REPORTED",
            "metric_name": "ICIR", "metric_value": .317, "metric_units": "ratio",
            "definition": "source metric, horizon not independently confirmed",
            "scope": "upstream summary, original data absent", "source_resource_ids": ["guide"],
            **changes}


def test_source_icir_is_not_a_local_metric(tmp_path):
    catalog = _catalog(tmp_path, claims=[_claim()])
    claim, = catalog.claims_for("candidate")
    assert claim.metric_value == .317 and claim.evidence_status == "SOURCE_REPORTED"
    assert claim.local_run_ref is None and claim.reproduction_scope is None
    assert catalog.inventory().locally_reproduced_claim_count == 0


def test_conflicting_claims_are_both_retained(tmp_path):
    catalog = _catalog(tmp_path, claims=[_claim(evidence_status="CONFLICTING_EVIDENCE"),
                       _claim(claim_id="c2", metric_value=-.2, scope="different window")])
    claims = catalog.claims_for("candidate")
    assert [(c.claim_id, c.metric_value) for c in claims] == [("c1", .317), ("c2", -.2)]
    assert len({c.scope for c in claims}) == 2


def test_alias_does_not_increase_unique_definition_count(tmp_path):
    definition = {"definition_id": "one", "name": "return", "source_id": "source-a",
                  "definition_status": "FORMULA_LOCATED", "formula": "return(close,5)",
                  "source_resource_ids": ["guide"]}
    catalog = _catalog(tmp_path, definitions=[definition],
                       aliases=[{"alias_id": "other-name", "canonical_id": "one"}])
    assert catalog.inventory().unique_definition_count == 1
    assert catalog.inventory().source_factor_count == 1
    assert catalog.inventory().alias_count == 1


def test_same_name_different_definition_is_not_merged(tmp_path):
    definitions = [{"definition_id": key, "name": "same", "source_id": key,
                    "definition_status": "FORMULA_LOCATED", "formula": formula,
                    "source_resource_ids": ["guide"]}
                   for key, formula in [("a", "return(close,5)"), ("b", "rolling_std(close,5)")]]
    assert _catalog(tmp_path, definitions=definitions).inventory().unique_definition_count == 2


def test_missing_report_is_not_falsification_evidence(tmp_path):
    lesson = {"lesson_id": "l1", "title": "缺失的报告", "tags": ["coverage"],
              "failure_type": "RESEARCH_REJECTION", "summary": "unverified",
              "source_resource_ids": ["missing-report"], "mitigation": "obtain original",
              "regression_check": "reproduce", "revisit_when": "original available",
              "is_financial_falsification": True}
    catalog = _catalog(tmp_path, lessons=[lesson])
    with pytest.raises(AssetLoadError) as error:
        catalog.lessons_for(("coverage",))
    assert error.value.code == "INVALID_EVIDENCE"


def test_packaged_claim_cannot_self_declare_local_reproduction(tmp_path):
    catalog = _catalog(tmp_path, claims=[_claim(evidence_status="FINATHINK_REPRODUCED",
                                               local_run_ref="invented-run",
                                               reproduction_scope="USER_DATA")])
    with pytest.raises(AssetLoadError) as error:
        catalog.claims_for("candidate")
    assert error.value.code == "INVALID_EVIDENCE"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, 10**1000],
                         ids=["nan", "inf", "bool", "huge-int"])
def test_invalid_numeric_claim_has_structured_error(tmp_path, value):
    catalog = _catalog(tmp_path, claims=[_claim(metric_value=value)])
    with pytest.raises(AssetLoadError) as error:
        catalog.claims_for("candidate")
    assert error.value.code == "INVALID_EVIDENCE"


def test_real_source_inventory_is_explicit_about_unresolved_formulas():
    catalog = ResearchAssetCatalog.packaged()
    inventory = catalog.inventory()
    assert inventory.skill_count == 12
    assert inventory.source_factor_count >= 8
    assert inventory.unresolved_definition_count >= 3
    assert inventory.locally_reproduced_claim_count == 0
    assert inventory.financial_falsification_count == 0
    assert catalog.claims_for("dshq-turn-mid-prox")
    lessons = catalog.lessons_for(("correlation",))
    assert lessons and all(lesson.regression_check and lesson.revisit_when for lesson in lessons)

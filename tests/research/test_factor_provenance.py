"""Projections never upgrade source claims or borrow a same-name formula."""

import copy
import importlib
import importlib.util
from dataclasses import replace

import pytest

from finahinking.research.asset_catalog import ResearchAssetCatalog
from finahinking.research.asset_contracts import EvidenceClaim
from finahinking.research.factor_validation import (
    FactorValidationGateway,
    factor_validation_policy,
    run_factor_validation,
)
from tests.research.test_factor_validation_pipeline import fixture_panel, selected


def api():
    name = "finahinking.research.factor_provenance"
    assert importlib.util.find_spec(name) is not None, "factor evidence projection is missing"
    return importlib.import_module(name)


class Repository:
    def __init__(self, *runs):
        self.entries = {f"run-{number}": {"run_id": f"run-{number}", "report_status": "AVAILABLE",
            "manifest": {"source_snapshot": {"factor_study": {"validation_run": run,
                "charter": {"policy_fingerprint": "a" * 64}}}}} for number, run in enumerate(runs)}

    def list_run_refs(self, *, limit=200, offset=0):
        return tuple(self.entries)[offset:offset + limit]

    def get_run(self, ref):
        return copy.deepcopy(self.entries[ref])

    def count_runs(self):
        return len(self.entries)


@pytest.fixture(scope="module")
def computed():
    return run_factor_validation(selected(), fixture_panel(), factor_validation_policy(),
                                 gateway=FactorValidationGateway()).to_dict()


def test_source_only_claim_is_never_a_local_metric_or_reproduction():
    result = api().load_factor_evidence("dshq-f-score", catalog=ResearchAssetCatalog.packaged(),
                                        run_repository=Repository()).to_dict()
    assert result["source_claims"][0]["metric_value"] == 0.49
    assert result["local_runs"] == []
    assert result["reproduction_scope"] == []
    assert result["missing_dependencies"]
    assert result["locally_reproduced_claim_count"] == 0


def test_same_name_partial_implementation_does_not_inherit_original_claim(computed):
    catalog = ResearchAssetCatalog.packaged()
    original = api().load_factor_evidence("dshq-f-score", catalog=catalog, run_repository=Repository(computed)).to_dict()
    partial = api().load_factor_evidence("dshq-fscore-available-items", catalog=catalog,
                                        run_repository=Repository(computed)).to_dict()
    assert original["local_runs"] == partial["local_runs"] == []
    assert not any(claim["metric_value"] == 0.49 for claim in partial["source_claims"])
    assert original["definition"]["definition_status"] == "UNRESOLVED"
    assert partial["definition"]["definition_status"] == "FORMULA_LOCATED"


def test_verified_fixture_preserves_exact_metrics_versions_and_scope(computed):
    view = api().load_factor_evidence(selected(), catalog=ResearchAssetCatalog.packaged(),
                                       run_repository=Repository(computed))
    result = view.to_dict()
    record = result["local_runs"][0]
    assert record["local_metric"] == {step["stage_id"]: step["metrics"] for step in computed["steps"]}
    assert record["evaluation_fingerprint"] == computed["fingerprint"]
    assert record["scope"]["universe"] == computed["bindings"]["universe"]
    assert record["scope"]["oos_window"] == computed["bindings"]["oos_window"]
    assert record["definition_matches"] is True
    assert result["reproduction_scope"] == ["FORMULA_FIXTURE"]
    assert result["locally_reproduced_claim_count"] == 0
    result["local_runs"][0]["local_metric"].clear()
    assert view.to_dict()["local_runs"][0]["local_metric"]


def test_source_positive_and_local_negative_remain_non_comparable(computed, monkeypatch):
    catalog = ResearchAssetCatalog.packaged()
    monkeypatch.setattr(catalog, "claims_for", lambda _: (EvidenceClaim("source-positive", selected().factor_id,
        "SOURCE_REPORTED", "ICIR", 2.5, "ratio", "source declaration", "unverified upstream sample", ()),))
    result = api().load_factor_evidence(selected(), catalog=catalog, run_repository=Repository(computed)).to_dict()
    assert result["source_claims"][0]["metric_value"] == 2.5
    assert result["local_runs"][0]["local_metric"] == {step["stage_id"]: step["metrics"] for step in computed["steps"]}
    assert any(item["code"] == "SOURCE_LOCAL_NOT_DIRECTLY_COMPARABLE" for item in result["conflicts"])
    assert result["locally_reproduced_claim_count"] == 0


def test_formula_version_mismatch_keeps_evidence_without_endorsing_it(computed):
    requested = replace(selected(), version="different-definition")
    result = api().load_factor_evidence(requested, catalog=ResearchAssetCatalog.packaged(),
                                        run_repository=Repository(computed)).to_dict()
    assert result["local_runs"][0]["definition_matches"] is False
    assert any(item["code"] == "DEFINITION_MISMATCH" for item in result["conflicts"])


@pytest.mark.parametrize("damage", ["artifact", "bundle"])
def test_corruption_cannot_turn_into_missing_or_valid_numeric_evidence(computed, damage):
    repository = Repository(computed)
    if damage == "artifact":
        repository.entries["run-0"]["manifest"]["source_snapshot"]["factor_study"]["validation_run"]["steps"][2]["metrics"].clear()
    else:
        repository.entries["run-0"]["report_status"] = "CORRUPT"
    result = api().load_factor_evidence(selected(), catalog=ResearchAssetCatalog.packaged(),
                                        run_repository=repository).to_dict()
    assert result["local_runs"][0]["status"] == "CORRUPT"
    assert result["local_runs"][0]["local_metric"] is None
    assert result["missing_dependencies"]


def test_scan_is_bounded_and_bad_identifiers_are_rejected(computed):
    repository = Repository(*([computed] * 201))
    result = api().load_factor_evidence(selected(), catalog=ResearchAssetCatalog.packaged(),
                                        run_repository=repository).to_dict()
    assert len(result["local_runs"]) == 200
    assert result["truncated"] is True
    for bad in ("../../private", "unknown-factor", "", True):
        with pytest.raises(ValueError):
            api().load_factor_evidence(bad, catalog=ResearchAssetCatalog.packaged(), run_repository=repository)


def test_real_index_ref_listing_is_scoped_and_corrupt_records_are_isolated(tmp_path):
    from tests.research.test_run_repository import completed, repository

    repo = repository(tmp_path)
    result, manifest = completed(tmp_path / "reports")
    repo.record_result(result, manifest)
    assert repo.list_run_refs() == ("persisted-run",)
    assert repository(tmp_path, scope="other").list_run_refs() == ()
    with repo._connect() as db:
        db.execute("UPDATE research_run_index SET record_digest=?", ("0" * 64,))
    result = api().load_factor_evidence("dshq-f-score", catalog=ResearchAssetCatalog.packaged(),
                                        run_repository=repo).to_dict()
    assert result["local_runs"] == []
    assert any(item["code"] == "INDEX_RECORD_CORRUPT" for item in result["missing_dependencies"])


def test_explicit_old_report_remains_reviewable_beyond_the_recent_inventory(computed):
    repository = Repository(*([computed] * 201))
    result = api().load_factor_evidence(selected(), catalog=ResearchAssetCatalog.packaged(),
        run_repository=repository, run_refs=("run-200",)).to_dict()
    assert len(result["local_runs"]) == 200
    assert result["local_runs"][0]["run_ref"] == "run-200"

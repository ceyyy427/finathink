"""Purpose-based selection must not load every profile or expand authority."""

import importlib
import importlib.util
from dataclasses import FrozenInstanceError

import pytest

from finahinking.research.asset_catalog import ResearchAssetCatalog


def _api():
    name = "finahinking.research.method_router"
    assert importlib.util.find_spec(name) is not None, "deterministic method routing is not implemented"
    return importlib.import_module(name)


@pytest.mark.parametrize("purposes,profiles,expected", [
    (("factor-mining",), (), {"factor-mining-workflow", "alpha-gpt-factor-mining"}),
    (("backtest",), (), {"backtest-acceptance"}),
    (("research",), (), {"alpha-gpt-researcher"}),
    (("public-behavior",), (), {"niu-san-distillation"}),
    (("public-behavior",), ("niu-san-linyuan",), {"niu-san-distillation", "niu-san-linyuan"}),
    (("maintenance",), (), {"github-maintainer"}),
    (("factor-mining", "backtest"), ("niu-san-fengliu",),
     {"factor-mining-workflow", "alpha-gpt-factor-mining", "backtest-acceptance",
      "niu-san-distillation", "niu-san-fengliu"}),
])
def test_purpose_routes_only_relevant_methods(purposes, profiles, expected):
    api = _api()
    request = api.MethodRouteRequest(purposes=purposes, public_profiles=profiles,
                                     market="CN_A", available_fields=("close", "volume"))
    selection = api.select_methods(request, ResearchAssetCatalog.packaged())
    assert selection.status == "READY"
    assert set(selection.skill_ids) == expected
    assert set(dict(selection.reasons)) == expected
    assert all(reason for _, reason in selection.reasons)
    assert selection.required_resources
    assert "factor-validation-v1" in {ref.resource_id for ref in selection.required_resources}


def test_selection_is_deterministic_deduplicated_and_immutable():
    api = _api()
    catalog = ResearchAssetCatalog.packaged()
    first = api.select_methods(api.MethodRouteRequest(
        purposes=("backtest", "factor-mining", "backtest"), available_fields=("volume", "close")), catalog)
    second = api.select_methods(api.MethodRouteRequest(
        purposes=("factor-mining", "backtest"), available_fields=("close", "volume")), catalog)
    assert first == second and first.fingerprint == second.fingerprint
    assert len(first.skill_ids) == len(set(first.skill_ids))
    assert len(first.required_resources) == len({ref.resource_id for ref in first.required_resources})
    with pytest.raises(FrozenInstanceError):
        first.status = "DONE"


def test_unknown_request_is_explicit_and_loads_nothing():
    api = _api()
    selection = api.select_methods(api.MethodRouteRequest(purposes=("unknown-task",)),
                                    ResearchAssetCatalog.packaged())
    assert selection.status == "NEEDS_CLARIFICATION"
    assert selection.skill_ids == () and selection.required_resources == ()
    assert selection.unsupported == ("purpose:unknown-task",)


def test_unknown_profile_does_not_load_unrelated_people():
    api = _api()
    selection = api.select_methods(api.MethodRouteRequest(public_profiles=("unknown-person",)),
                                    ResearchAssetCatalog.packaged())
    assert selection.status == "NEEDS_CLARIFICATION"
    assert not any(skill.startswith("niu-san-") for skill in selection.skill_ids)


def test_missing_turnover_is_a_gap_not_a_volume_substitution():
    api = _api()
    selection = api.select_methods(api.MethodRouteRequest(
        purposes=("factor-mining",), factor_families=("turnover",),
        available_fields=("close", "volume")), ResearchAssetCatalog.packaged())
    assert selection.status == "READY"
    assert "field:turnover_rate" in selection.missing_inputs
    assert selection.optional_resources


def test_route_request_rejects_mutable_or_scalar_collections():
    api = _api()
    with pytest.raises((TypeError, ValueError)):
        api.MethodRouteRequest(purposes="factor-mining")
    with pytest.raises((TypeError, ValueError)):
        api.MethodRouteRequest(purposes=({},))

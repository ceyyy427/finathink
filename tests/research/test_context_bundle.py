"""Run/role/PIT/consent boundaries for material sent to a model."""

from dataclasses import replace

import pytest

from finahinking.p8_2.data_sources import FixtureMarketDataSource
from tests.research.test_snapshot_workflow import request


def material(**changes):
    return {
        "role": "news", "instrument": "AAA", "summary": "The company announced a factory opening.",
        "source_label": "User supplied company announcement", "observed_at": "2026-09-01T00:00:00Z",
        "available_at": "2026-09-02T00:00:00Z", "authorized": True, **changes,
    }


def build(role="technical", *, data=None, run_id="context-run", materials=(), **kwargs):
    from finahinking.research.context_bundle import build_context_bundle

    snapshot = data or FixtureMarketDataSource(points=8).snapshot("AAA")
    return build_context_bundle(request(snapshot.fingerprint, run_id), snapshot, role, materials=materials, **kwargs)


def test_technical_bundle_contains_readable_selected_prices_question_and_binding():
    bundle = build()
    payload = bundle.to_dict()
    assert payload["question"] == "bounded local study"
    assert payload["role"] == "technical" and payload["instrument"] == "AAA"
    assert "price" in payload["role_task"] and "server" in payload["role_task"]
    assert payload["dataset_fingerprint"] == FixtureMarketDataSource(points=8).snapshot("AAA").fingerprint
    assert payload["materials"][0]["summary"]["observations"][0]["close"] == 99.75
    assert payload["evidence_index"] and payload["material_status"] == "AVAILABLE"
    assert "credential" not in str(payload) and "endpoint" not in str(payload)


def test_role_materials_are_partitioned_and_missing_data_is_explicit():
    news = build("news", materials=(material(),))
    assert news.to_dict()["materials"][0]["summary"] == "The company announced a factory opening."
    fundamentals = build("fundamentals", materials=(material(),))
    assert fundamentals.to_dict()["material_status"] == "INSUFFICIENT_DATA"
    assert fundamentals.to_dict()["materials"] == [] and not fundamentals.evidence_index


@pytest.mark.parametrize("changes", [
    {"authorized": False}, {"authorized": "true"}, {"available_at": None},
    {"available_at": "2027-01-01T00:00:00Z"}, {"observed_at": "2026-09-03T00:00:00Z"},
    {"instrument": "BBB"}, {"role": "execute"}, {"summary": "api_key=private-value"},
])
def test_unavailable_unauthorized_cross_asset_or_secret_material_is_rejected(changes):
    with pytest.raises((TypeError, ValueError)):
        build("news", materials=(material(**changes),))


def test_evidence_existing_in_another_run_or_role_cannot_be_cited():
    first = build("news", run_id="first", materials=(material(),))
    second = build("news", run_id="second", materials=(material(),))
    ref = next(iter(first.evidence_index))
    first.validate_references((ref,))
    for bundle in (second, build("technical", run_id="first", materials=(material(),))):
        with pytest.raises(ValueError):
            bundle.validate_references((ref,))
    with pytest.raises(ValueError):
        first.validate_references(())


def test_snapshot_substitution_and_future_market_data_are_rejected():
    data = FixtureMarketDataSource(points=8).snapshot("AAA")
    from finahinking.research.context_bundle import build_context_bundle

    with pytest.raises(ValueError):
        build_context_bundle(request("0" * 64), data, "technical")
    unknown = replace(data, observations=tuple(replace(row, available_at=None) for row in data.observations))
    with pytest.raises(ValueError):
        build(data=unknown)


def test_context_byte_limit_blocks_before_provider_invocation():
    with pytest.raises(ValueError, match="context"):
        build(max_bytes=64)


def test_method_context_is_optional_and_uses_same_merged_budget():
    import importlib.util

    assert importlib.util.find_spec("finahinking.research.method_context") is not None
    from finahinking.research.asset_catalog import ResearchAssetCatalog
    from finahinking.research.asset_contracts import MethodRouteRequest
    from finahinking.research.method_context import load_methods
    from finahinking.research.method_router import select_methods

    catalog = ResearchAssetCatalog.packaged()
    loaded = load_methods(select_methods(MethodRouteRequest(purposes=("factor-mining",)), catalog), catalog)
    original = build()
    combined = build(loaded_methods=loaded)
    assert combined.evidence_index == original.evidence_index
    assert combined.to_dict()["method_context"] == loaded.to_dict()
    assert len(combined._json.encode()) <= 32768
    assert original.to_dict().get("method_context") is None
    assert original.fingerprint == build(loaded_methods=None).fingerprint


def test_methods_do_not_receive_an_extra_budget_above_materials():
    import importlib.util
    import json

    assert importlib.util.find_spec("finahinking.research.method_context") is not None
    from finahinking.research.asset_catalog import ResearchAssetCatalog
    from finahinking.research.asset_contracts import MethodRouteRequest
    from finahinking.research.context_bundle import ResearchContextBundle, merge_method_context
    from finahinking.research.method_context import load_methods
    from finahinking.research.method_router import select_methods

    catalog = ResearchAssetCatalog.packaged()
    loaded = load_methods(select_methods(MethodRouteRequest(purposes=("factor-mining",)), catalog), catalog)
    payload = build().to_dict()
    payload["authorized_extra"] = "研" * 7000
    original = ResearchContextBundle(json.dumps(payload, ensure_ascii=False))
    assert len(original._json.encode()) < 32768 and loaded.actual_bytes < 32768
    with pytest.raises(ValueError, match="BLOCKED_CONTEXT"):
        merge_method_context(original, loaded)

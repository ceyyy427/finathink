"""The selected snapshot, not a fixture, feeds every deterministic stage."""

from dataclasses import replace

import pytest

from finahinking.data.snapshot_store import SnapshotStore
from finahinking.p8_2.data_sources import FixtureMarketDataSource
from finahinking.research.contracts import ResearchPlan, ResearchRequest, ResearchState, to_jsonable
from finahinking.research.drivers import OfflineDriver
from finahinking.research.reports import ReportBundleWriter
from finahinking.research.snapshot_tools import SnapshotResearchTools
from finahinking.research.tools import ResearchToolRequest, ResearchToolStatus
from finahinking.research.workflow import ResearchOrchestrator


def request(ref, run_id="selected-run"):
    return ResearchRequest(
        run_id=run_id, instrument="AAA", as_of="2026-10-01",
        research_plan=ResearchPlan(hypotheses=("bounded local study",), factor_ids=("momentum_2d",)),
        analyst_roles=("technical",), asset_class="EQUITY", workflow_version="local-research.v1",
        config_digest="local-defaults-v1", snapshot_ref=ref,
    )


def test_different_selected_prices_change_real_factor_and_backtest_results(tmp_path):
    first = FixtureMarketDataSource(points=20).snapshot("AAA")
    second = replace(first, observations=tuple(
        replace(item, close=100 - index, high=max(item.open, 100 - index) + 1, low=min(item.open, 100 - index) - 1)
        for index, item in enumerate(first.observations)
    ))
    store = SnapshotStore(tmp_path / "snapshots")
    outputs = []
    for index, snapshot in enumerate((first, second)):
        ref = store.save(snapshot)
        tools = SnapshotResearchTools(store.load(ref))
        result = ResearchOrchestrator().run(request(ref, f"selected-{index}"), OfflineDriver(), tools)
        assert result.state.current_state is ResearchState.LEARNING_RECORDED
        assert result.decision is not None and result.decision.paper_only
        assert tools.analysis["dataset_fingerprint"] == ref
        assert tools.analysis["factor"]["dataset_fingerprint"] == ref
        assert tools.risk_payload["snapshot"]["dataset_fingerprint"] == ref
        manifest = ReportBundleWriter().write(result, tmp_path / "reports", quant_analytics=tools.analysis)
        assert manifest.source_snapshot["dataset_fingerprint"] == ref
        outputs.append(tools.analysis)
    assert outputs[0]["factor"]["latest_value"] > 0
    assert outputs[1]["factor"]["latest_value"] < 0
    assert outputs[0]["metrics"]["total_return"] > 0
    assert outputs[1]["metrics"]["total_return"] == 0


def test_selected_snapshot_cannot_be_substituted_by_another_gateway(tmp_path):
    a = FixtureMarketDataSource(source_id="feed-a").snapshot("AAA")
    b = FixtureMarketDataSource(source_id="feed-b").snapshot("AAA")
    result = ResearchOrchestrator().run(request(a.fingerprint), OfflineDriver(), SnapshotResearchTools(b))
    assert result.state.current_state is ResearchState.DATA_UNAVAILABLE
    assert result.decision is None


def test_unknown_pit_is_blocked_before_factor_or_backtest():
    original = FixtureMarketDataSource().snapshot("AAA")
    snapshot = replace(original, observations=tuple(replace(item, available_at=None) for item in original.observations))
    tools = SnapshotResearchTools(snapshot)
    result = ResearchOrchestrator().run(request(snapshot.fingerprint), OfflineDriver(), tools)
    assert result.state.current_state is ResearchState.DATA_UNAVAILABLE
    assert result.decision is None
    assert tools.analysis == {}


def test_unsupported_factor_is_rejected_rather_than_silently_ignored():
    snapshot = FixtureMarketDataSource().snapshot("AAA")
    selected = replace(request(snapshot.fingerprint), research_plan=ResearchPlan(factor_ids=("unknown-factor",)))
    result = ResearchOrchestrator().run(selected, OfflineDriver(), SnapshotResearchTools(snapshot))
    assert result.state.current_state is ResearchState.VALIDATION_FAILED
    assert result.decision is None


@pytest.mark.parametrize("ref", ["fixture", "../data", "a" * 63])
def test_research_request_only_accepts_content_references(ref):
    with pytest.raises(ValueError, match="snapshot"):
        request(ref)


def test_optional_snapshot_ref_preserves_legacy_queue_serialization():
    legacy = replace(request("a" * 64), snapshot_ref=None)
    assert "snapshot_ref" not in to_jsonable(legacy)


@pytest.mark.parametrize("as_of", [None, "not-a-date"])
def test_invalid_tool_boundary_is_a_typed_rejection(as_of):
    snapshot = FixtureMarketDataSource().snapshot("AAA")
    result = SnapshotResearchTools(snapshot).execute(ResearchToolRequest(
        "research.inspect_dataset", {"dataset_id": snapshot.fingerprint, "as_of": as_of}, "bad-boundary",
    ))
    assert result.status is ResearchToolStatus.REJECTED


def test_malformed_risk_snapshot_is_blocked_instead_of_raising():
    snapshot = FixtureMarketDataSource().snapshot("AAA")

    class MalformedRiskTools(SnapshotResearchTools):
        def _risk(self, parameters):
            return {"dataset_fingerprint": snapshot.fingerprint, "snapshot": []}

    result = ResearchOrchestrator().run(request(snapshot.fingerprint), OfflineDriver(), MalformedRiskTools(snapshot))
    assert result.state.current_state is ResearchState.VALIDATION_FAILED
    assert result.decision is None

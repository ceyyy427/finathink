from dataclasses import replace

import pytest

from finahinking.p8_2.contracts import DatasetSnapshot, MarketObservation
from finahinking.p8_2.data_sources import FixtureMarketDataSource
from finahinking.research.contracts import (
    ResearchPlan,
    ResearchRequest,
    ResearchState,
    stable_digest,
)
from finahinking.research.drivers import OfflineDriver
from finahinking.research.reports import ReportBundleWriter
from finahinking.research.snapshot_tools import SnapshotResearchTools, local_research_policy
from finahinking.research.workflow import ResearchOrchestrator


def run(snapshot):
    tools = SnapshotResearchTools(snapshot)
    request = ResearchRequest(run_id="risk-research", instrument="AAA", as_of="2026-10-01", research_plan=ResearchPlan(factor_ids=(local_research_policy()["factor_id"],)), analyst_roles=("technical",), snapshot_ref=snapshot.fingerprint, asset_class="EQUITY", workflow_version="local-research.v1", config_digest="controlled")
    return ResearchOrchestrator().run(request, OfflineDriver(), tools), tools


def test_optional_industry_and_suspension_are_immutable_snapshot_input():
    original = FixtureMarketDataSource(points=20).snapshot("AAA")
    old_payload = original.to_json()
    assert "industry" not in original.observations[0].to_dict()
    assert DatasetSnapshot.from_json(old_payload).fingerprint == original.fingerprint
    snapshot = replace(original, observations=tuple(replace(item, industry="technology", suspended=False) for item in original.observations))
    assert snapshot.fingerprint != original.fingerprint
    assert DatasetSnapshot.from_json(snapshot.to_json()).observations[0].industry == "technology"
    with pytest.raises((TypeError, ValueError)):
        replace(snapshot.observations[0], suspended="false")
    assert MarketObservation.from_dict(snapshot.observations[0].to_dict()) == snapshot.observations[0]


def test_user_api_optional_risk_fields_survive_normalization_and_snapshot_capture():
    from finahinking.data.field_mapping import normalize_records
    from finahinking.p8_2.data_sources import snapshot_from_batch
    raw = {"instrument": "AAA", "timestamp": "2026-01-01T00:00:00Z", "available_at": "2026-01-01T00:00:00Z", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 0, "industry": "technology", "suspended": True}
    batch = normalize_records([raw], {key: key for key in raw}, "prices")
    row = snapshot_from_batch(batch).observations[0]
    assert row.suspended is True and row.industry == "technology"
    assert row.available_at is not None
    with pytest.raises(ValueError):
        normalize_records([dict(raw, suspended="false")], {key: key for key in raw}, "prices")


@pytest.mark.parametrize("kind", ["normal", "missing_industry", "halt", "illiquid", "jump"])
def test_default_workflow_binds_exposure_stress_allocation_fills_and_attribution(tmp_path, kind):
    source = FixtureMarketDataSource(points=24).snapshot("AAA")
    observations = tuple(replace(item, industry="technology", volume=10000) for item in source.observations)
    if kind == "missing_industry":
        observations = tuple(replace(item, industry=None) for item in observations)
    if kind == "halt":
        observations = (*observations[:-1], replace(observations[-1], suspended=True))
    if kind == "illiquid":
        observations = (*observations[:-1], replace(observations[-1], volume=0))
    if kind == "jump":
        last = observations[-1]
        observations = (*observations[:-1], replace(last, close=last.close * 2, high=last.close * 2 + 1))
    snapshot = replace(source, observations=observations)
    result, tools = run(snapshot)
    assert result.state.current_state is ResearchState.LEARNING_RECORDED
    analysis = tools.analysis
    combined = analysis["risk_portfolio"]
    assert combined["run_id"] == result.state.run_id
    assert combined["dataset_fingerprint"] == snapshot.fingerprint
    assert combined["policy_fingerprint"] == analysis["policy_fingerprint"] == stable_digest(local_research_policy())
    assert combined["exposure"]["passed"] and combined["stress"]["passed"]
    assert combined["exposure"]["cvar"] >= 0
    assert combined["allocation"]["weights"] == dict(result.decision.weights) == {"AAA": analysis["proposed_target_weight"]}
    if kind in {"halt", "illiquid", "jump"}:
        assert result.decision.weights["AAA"] == 0
    if kind == "missing_industry":
        assert combined["industry_status"] == "UNKNOWN"
        assert combined["exposure"]["industry_exposure"] == {"UNKNOWN": result.decision.weights["AAA"]}
        assert result.decision.weights["AAA"] <= local_research_policy()["unknown_industry_cap"]
    assert combined["paper_ledger"]["entries"][0]["event"] == "paper_pending"
    assert combined["paper_ledger"]["entries"][0]["quantity"] == 0
    assert combined["paper_ledger"]["entries"][0]["cash"] == local_research_policy()["starting_cash"]
    assert all(item["after_weight"] <= item["before_weight"] and item["cash_added"] >= 0 for item in combined["constraint_effects"])
    contributions = combined["attribution"]
    assert sum(contributions[key] for key in ("strategy", "cost", "risk_scaling")) == pytest.approx(analysis["metrics"]["total_return"])
    assert contributions["residual"] == pytest.approx(0, abs=1e-12)
    manifest = ReportBundleWriter().write(result, tmp_path, quant_analytics=analysis)
    assert manifest.source_snapshot["risk_portfolio"] == combined
    html = (tmp_path / result.state.run_id / "complete_report.html").read_text()
    assert "风险与组合" in html and "约束对仓位与现金的影响" in html and "路径归因" in html


def test_risk_scaling_is_prefix_causal_when_only_later_prices_change():
    original = FixtureMarketDataSource(points=24).snapshot("AAA")
    changed = replace(original, observations=tuple(replace(item, close=item.close * 2, high=item.close * 2 + 1) if index >= 18 else item for index, item in enumerate(original.observations)))
    _, a = run(original)
    _, b = run(changed)
    for field in ("positions", "weights", "equity_curve"):
        assert a.analysis["backtest"][field][:18] == b.analysis["backtest"][field][:18]


def test_one_gateway_cannot_mix_runs_or_reuse_a_completed_risk_binding():
    from finahinking.research.tools import ResearchToolRequest, ResearchToolStatus
    snapshot = FixtureMarketDataSource(points=20).snapshot("AAA")
    tools = SnapshotResearchTools(snapshot)
    args = {"dataset_id": snapshot.fingerprint, "as_of": "2026-10-01"}
    assert tools.execute(ResearchToolRequest("research.inspect_dataset", args, "a")).status is ResearchToolStatus.SUCCEEDED
    assert tools.execute(ResearchToolRequest("research.inspect_dataset", args, "b")).status is ResearchToolStatus.REJECTED

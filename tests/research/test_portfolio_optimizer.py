from __future__ import annotations

import pytest

from finahinking.research.paper_trader import PaperLedger, PaperTrader
from finahinking.research.portfolio_runtime import PortfolioManager
from finahinking.research.risk_runtime import RiskReviewResult


def risk(**overrides: object) -> RiskReviewResult:
    values: dict[str, object] = {
        "status": "PASSED",
        "passed": True,
        "gates": ("pit", "factor", "concentration"),
        "metrics": {"pit_status": "AVAILABLE"},
        "snapshot_digest": "snapshot-digest",
    }
    values.update(overrides)
    return RiskReviewResult(
        **values,
    )


def candidates() -> tuple[dict[str, object], ...]:
    return (
        {"instrument": "AAA", "score": 2.0, "industry": "technology", "factors": {"value": 1.0}},
        {"instrument": "BBB", "score": 1.0, "industry": "healthcare", "factors": {"value": -1.0}},
    )


def test_optimize_honors_long_only_exposure_cash_single_name_industry_and_factor_caps() -> None:
    proposal = PortfolioManager().optimize(
        candidates(),
        risk(),
        {
            "max_single_weight": 0.6,
            "max_exposure": 0.8,
            "cash_buffer": 0.2,
            "industry_limits": {"technology": 0.5},
            "factor_limits": {"value": (-0.2, 0.2)},
        },
    )
    assert proposal.status == "PASSED"
    assert proposal.paper_only is True
    assert all(weight >= 0 for weight in proposal.weights.values())
    assert max(proposal.weights.values()) <= 0.6 + 1e-9
    assert sum(proposal.weights.values()) <= 0.8 + 1e-9
    assert proposal.cash_weight >= 0.2 - 1e-9
    assert proposal.weights["AAA"] <= 0.5 + 1e-9
    value_exposure = proposal.weights["AAA"] - proposal.weights["BBB"]
    assert -0.2 - 1e-9 <= value_exposure <= 0.2 + 1e-9


def test_missing_constrained_factor_is_not_silently_treated_as_zero():
    proposal = PortfolioManager().optimize([{"instrument": "AAA", "industry": "technology"}], risk(), {"factor_limits": {"value": 0.2}})
    assert not proposal.passed and not proposal.weights


def test_duplicate_candidates_cannot_silently_overwrite_each_other():
    duplicate = [dict(candidates()[0]), dict(candidates()[0], score=99)]
    assert not PortfolioManager().optimize(duplicate, risk(), {}).passed


def test_optimize_enforces_turnover_and_cost_limits_deterministically() -> None:
    constraints = {
        "max_exposure": 0.8,
        "max_turnover": 0.1,
        "previous_weights": {"AAA": 0.4, "BBB": 0.4},
        "transaction_cost_bps": 20,
        "max_transaction_cost": 0.001,
    }
    first = PortfolioManager().optimize(candidates(), risk(), constraints)
    second = PortfolioManager().optimize(candidates(), risk(), constraints)
    assert first == second
    turnover = sum(abs(first.weights.get(name, 0.0) - old) for name, old in constraints["previous_weights"].items())
    assert turnover <= 0.1 + 1e-9
    assert turnover * 20 / 10000 <= 0.001 + 1e-9


def test_optimize_returns_blocked_when_constraints_are_infeasible_without_fallback() -> None:
    blocked = PortfolioManager().optimize(
        candidates(),
        risk(),
        {"max_exposure": 1.0, "max_single_weight": 0.1, "industry_limits": {"technology": 0.0, "healthcare": 0.0}, "require_target_exposure": True},
    )
    assert blocked.status == "BLOCKED"
    assert blocked.passed is False
    assert blocked.weights == {}
    assert blocked.cash_weight == 1.0


def test_rebalance_binds_snapshot_policy_and_proposal_fingerprints() -> None:
    proposal = PortfolioManager().optimize(candidates(), risk(), {"max_exposure": 0.8, "execution_policy": {"fee_bps": 10, "slippage_bps": 5, "initial_cash": 1000}})
    snapshot = {"snapshot_id": "snap-1", "as_of": "2026-10-01", "pit_status": "AVAILABLE", "observations": [{"instrument": "AAA", "close": 10}, {"instrument": "BBB", "close": 20}]}
    ledger = PaperTrader().rebalance(proposal, snapshot)
    assert isinstance(ledger, PaperLedger)
    assert ledger.snapshot_digest
    assert ledger.proposal_digest == proposal.fingerprint
    assert ledger.policy_digest
    assert ledger.policy_digest == ledger.execution_policy["fingerprint"]
    assert ledger.entries


def test_optimize_blocks_contradictory_passed_risk_metrics_and_limits() -> None:
    contradictory = risk(
        metrics={"pit_status": "AVAILABLE", "concentration": 0.9, "drawdown": 0.3, "liquidity": 10.0},
        limits={"max_concentration": 0.5, "max_drawdown": 0.2, "min_liquidity": 100.0},
    )
    proposal = PortfolioManager().optimize(candidates(), contradictory, {"max_exposure": 0.8})
    assert proposal.status == "BLOCKED"
    assert proposal.passed is False
    assert proposal.weights == {}


def test_unsafe_execution_policy_values_are_rejected_without_leaking() -> None:
    blocked = PortfolioManager().optimize(
        candidates(),
        risk(),
        {"execution_policy": {"note": "api_key=SECRET /Users/private/x"}},
    )
    assert blocked.status == "BLOCKED"
    assert "SECRET" not in repr(blocked.to_dict())
    assert "/Users/private" not in repr(blocked.to_dict())
    with pytest.raises(ValueError, match="unsafe|paper-only|secret|path"):
        PaperTrader().simulate(
            PortfolioManager().optimize(candidates(), risk(), {"max_exposure": 0.8}),
            {"pit_status": "AVAILABLE", "observations": [{"instrument": "AAA", "close": 1}, {"instrument": "BBB", "close": 1}]},
            {"note": "/Users/private/x"},
        )


def test_proposal_and_ledger_nested_policy_mappings_are_immutable() -> None:
    proposal = PortfolioManager().optimize(
        candidates(), risk(), {"max_exposure": 0.8, "execution_policy": {"fee_bps": 10, "metadata": {"desk": "paper"}}}
    )
    proposal_fingerprint = proposal.fingerprint
    with pytest.raises(TypeError):
        proposal.constraints["execution_policy"]["metadata"]["desk"] = "changed"  # type: ignore[index]
    assert proposal.fingerprint == proposal_fingerprint
    ledger = PaperTrader().rebalance(
        proposal,
        {"pit_status": "AVAILABLE", "observations": [{"instrument": "AAA", "close": 1}, {"instrument": "BBB", "close": 1}]},
    )
    ledger_fingerprint = ledger.fingerprint
    with pytest.raises(TypeError):
        ledger.execution_policy["metadata"]["desk"] = "changed"  # type: ignore[index]
    assert ledger.fingerprint == ledger_fingerprint


def test_negative_previous_weight_blocks_instead_of_clamping() -> None:
    blocked = PortfolioManager().optimize(
        candidates(), risk(), {"previous_weights": {"AAA": -0.1}, "max_turnover": 0.2}
    )
    assert blocked.status == "BLOCKED"
    assert blocked.passed is False

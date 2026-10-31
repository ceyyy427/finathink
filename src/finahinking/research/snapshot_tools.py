"""Run-scoped deterministic tools bound to one verified market snapshot."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date
from itertools import pairwise
from types import MappingProxyType

import pandas as pd

from finahinking.data.models import Dataset, Provenance
from finahinking.factors.core import momentum_factor
from finahinking.p8_2.contracts import DatasetSnapshot
from finahinking.quant.engines import BacktestEngine
from finahinking.quant.evaluation import evaluate_backtest
from finahinking.quant.interfaces import BacktestConfig
from finahinking.quant.runtime import FactorThresholdStrategy
from finahinking.quant.services import QuantServiceGateway

from .contracts import FailureKind, stable_digest
from .paper_trader import PaperTrader
from .portfolio_research import RiskScaledStrategy, constrained_target
from .portfolio_runtime import PortfolioManager
from .research_identity import execution_identity
from .risk_runtime import RiskManager
from .tools import (
    ResearchToolGateway,
    ResearchToolRequest,
    ResearchToolResponse,
    ResearchToolStatus,
)

_LOCAL_POLICY = MappingProxyType({
    "version": "local-paper.v3", "factor_id": "momentum_2obs", "momentum_window": 2, "bar_frequency": "one-observation-per-utc-date",
    "metric_basis": "OBSERVATION", "annualization": 1,
    "strategy_version": f"{RiskScaledStrategy.strategy_id}.{RiskScaledStrategy.version}",
    "engine_version": BacktestEngine.engine_version, "threshold": 0.0,
    "target_weight": 0.75, "starting_cash": 10000, "fee_bps": 5, "slippage_bps": 5,
    "max_drawdown": 0.35, "max_concentration": 1.0, "min_liquidity": 1.0,
    "cash_buffer": 0.25, "industry_cap": 0.6, "unknown_industry_cap": 0.25,
    "signal_exposure_cap": 0.05, "cvar_confidence": 0.95, "max_cvar": 0.05,
    "risk_lookback": 60, "max_volume_participation": 0.01, "max_price_jump": 0.25,
    "market_shock": -0.3, "market_loss_limit": 0.1, "gap_shock": -0.5, "gap_loss_limit": 0.15,
    "industry_shock": -0.2, "industry_loss_limit": 0.1,
    "allocation_method": "deterministic_constrained.v1", "delay_observations": 1,
})


def local_research_policy():
    """The same parameters drive execution and the durable semantic identity."""
    return dict(_LOCAL_POLICY)


class SnapshotResearchTools(ResearchToolGateway):
    """Use the in-house engine; never resolve a missing input to a fixture."""

    def __init__(self, snapshot: DatasetSnapshot, *, publication_before=None, policy=None, policy_lineage=None) -> None:
        if not isinstance(snapshot, DatasetSnapshot):
            raise TypeError("snapshot must be a DatasetSnapshot")
        # Defensive roundtrip also verifies nested content before it is used.
        self.snapshot = DatasetSnapshot.from_json(snapshot.to_json())
        self.publication_before = pd.Timestamp(publication_before) if publication_before is not None else None
        if self.publication_before is not None and (pd.isna(self.publication_before) or self.publication_before.tzinfo is None):
            raise ValueError("publication boundary must be timezone-aware")
        self.observations = tuple(item for item in self.snapshot.observations if self.publication_before is None or item.timestamp < self.publication_before)
        self.policy = dict(local_research_policy() if policy is None else policy)
        self.policy_lineage = dict(policy_lineage or {})
        self.analysis: dict = {}
        self.risk_payload: dict = {}
        self.run_id: str | None = None
        quant = QuantServiceGateway()
        quant.register("quant.run_backtest", self._backtest)
        quant.register("quant.analyze_risk", self._risk)
        super().__init__(quant_gateway=quant)

    def execute(self, request):
        if self.run_id is not None and request.run_id != self.run_id:
            return self._rejected(request.name, "snapshot gateway is bound to another run", request_id=request.run_id)
        self.run_id = request.run_id
        return super().execute(request)

    @staticmethod
    def _boundary(as_of):
        # ResearchRequest is low-frequency/date-based: a date includes that
        # entire UTC day. Neither data timestamps nor availability may exceed it.
        if not isinstance(as_of, str):
            raise TypeError("as_of must be an ISO research date")
        parsed = date.fromisoformat(as_of)
        return pd.Timestamp(parsed, tz="UTC") + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)

    def _execute_research(self, request: ResearchToolRequest, digest: str) -> ResearchToolResponse:
        if request.name != "research.inspect_dataset":
            return self._rejected(request.name, "tool is not implemented for selected snapshots", request_id=request.run_id, digest=digest)
        valid = request.args.get("dataset_id") == self.snapshot.fingerprint
        try:
            boundary = self._boundary(request.args["as_of"])
        except (KeyError, TypeError, ValueError):
            return self._rejected(request.name, "as_of must be an ISO research date", request_id=request.run_id, digest=digest)
        if not valid or not self.snapshot.pit_available(boundary):
            return ResearchToolResponse(
                request_id=request.run_id, name=request.name, status=ResearchToolStatus.UNAVAILABLE,
                failure_kind=FailureKind.DATA_UNAVAILABLE,
                result={"reason": "snapshot mismatch or unknown/future available_at"}, request_digest=digest,
            )
        return ResearchToolResponse(
            request_id=request.run_id, name=request.name, status=ResearchToolStatus.SUCCEEDED,
            result={
                "dataset_fingerprint": self.snapshot.fingerprint,
                "data_fingerprint": self.snapshot.provenance.get("data_fingerprint", self.snapshot.fingerprint),
                "records": [item.to_dict() for item in self.observations],
                "pit_available": "AVAILABLE", "mode": self.snapshot.mode,
            },
            provenance={"dataset_fingerprint": self.snapshot.fingerprint}, request_digest=digest,
        )

    def _check_binding(self, parameters):
        if parameters.get("dataset_fingerprint") != self.snapshot.fingerprint:
            raise ValueError("selected dataset fingerprint mismatch")
        if not self.snapshot.pit_available(self._boundary(parameters["as_of"])):
            raise ValueError("selected snapshot has unknown or future availability")

    def _backtest(self, parameters):
        self._check_binding(parameters)
        requested = tuple(parameters.get("factor_ids", ()))
        if requested not in {(), (self.policy["factor_id"],)}:
            raise ValueError("selected factor is not supported by this deterministic workflow")
        instrument = str(parameters["instrument"])
        rows = [item for item in self.observations if item.instrument == instrument]
        if len(rows) < 4:
            raise ValueError("selected instrument requires at least four OHLCV observations")
        if len({item.timestamp.date() for item in rows}) != len(rows):
            raise ValueError("low-frequency baseline requires at most one observation per UTC date")
        # Every value in a historical signal must already be available when
        # the next-bar engine can execute it. End-of-research PIT alone is
        # not sufficient. Delayed feeds need an availability-aware engine.
        if any(previous.available_at is None or previous.available_at > following.timestamp for previous, following in pairwise(rows)):
            raise ValueError("historical data was unavailable at its next-bar execution boundary")
        frame = pd.DataFrame(
            [{**{key: getattr(item, key) for key in ("open", "high", "low", "close", "volume")}, "suspended": item.suspended is True} for item in rows],
            index=pd.DatetimeIndex([item.timestamp for item in rows]),
        )
        dataset = Dataset(frame, Provenance("user-snapshot", "local snapshot reference", license="user-declared; local research"))
        policy = self.policy
        factor_definition = replace(momentum_factor(policy["momentum_window"]), name=policy["factor_id"])
        strategy = FactorThresholdStrategy(factor=factor_definition, threshold=policy["threshold"], target_weight=policy["target_weight"])
        config = BacktestConfig(**{key: policy[key] for key in ("starting_cash", "fee_bps", "slippage_bps", "annualization")}, frequency="observation")
        config = replace(config, max_volume_participation=policy["max_volume_participation"])
        raw_weights = strategy.generate(dataset)
        gross = BacktestEngine().run(dataset, strategy, replace(config, fee_bps=0, slippage_bps=0))
        cost_baseline = BacktestEngine().run(dataset, strategy, config)
        scaled = RiskScaledStrategy(rows, raw_weights, policy)
        backtest = BacktestEngine().run(dataset, scaled, config)
        evaluation = evaluate_backtest(backtest)
        metrics = dict(evaluation.metrics)
        factor_values = strategy.factor.compute(frame["close"])
        target_weight, effects, details = constrained_target(rows, float(raw_weights.iloc[-1]), policy)
        self._risk_details = details
        self._constraint_effects = effects
        gross_return = gross.equity_curve[-1][1] / policy["starting_cash"] - 1
        cost_return = cost_baseline.equity_curve[-1][1] / policy["starting_cash"] - 1
        net_return = backtest.equity_curve[-1][1] / policy["starting_cash"] - 1
        self._attribution = {"strategy": gross_return, "cost": cost_return - gross_return, "risk_scaling": net_return - cost_return, "net_return": net_return, "residual": net_return - (gross_return + (cost_return - gross_return) + (net_return - cost_return)), "method": "ordered counterfactual path: raw zero-cost → raw cost → prefix-risk cost; not unique causal attribution", "baseline_fingerprints": [gross.fingerprint, cost_baseline.fingerprint, backtest.fingerprint]}
        from .reports import redact_public_payload

        source = redact_public_payload({
            "data_mode": self.snapshot.mode,
            "source_metadata": {("origin_id" if key == "source" else key): self.snapshot.provenance[key] for key in ("connection_id", "source_declaration", "provider", "source", "data_fingerprint", "retrieved_at", "timezone", "adjustment", "calendar", "quality_issues") if key in self.snapshot.provenance},
            "source_limitations": list(self.snapshot.limitations),
        })
        factor = {
            "factor_id": strategy.factor.name, "dataset_fingerprint": self.snapshot.fingerprint,
            "latest_value": float(factor_values.iloc[-1]),
            "definition": strategy.factor.definition,
        }
        self.analysis = {
            "dataset_fingerprint": self.snapshot.fingerprint,
            "reproduction": {"identity": execution_identity(policy), "policy": dict(policy),
                "publication_before": self.publication_before.isoformat() if self.publication_before is not None else None},
            "run_id": self.run_id,
            "engine_dataset_fingerprint": backtest.dataset_fingerprint,
            "policy_fingerprint": stable_digest(policy), "strategy_version": policy["strategy_version"],
            "instrument": instrument, "as_of": parameters["as_of"],
            "factor": factor, "metrics": metrics, "proposed_target_weight": target_weight,
            "metric_basis": policy["metric_basis"], "data_mode": source["data_mode"],
            "source_metadata": source["source_metadata"],
            "comparison_conditions": {"dataset_fingerprint": self.snapshot.fingerprint, "sample_period": [frame.index[0].isoformat(), frame.index[-1].isoformat()], "scoring_index": stable_digest([timestamp.isoformat() for timestamp in frame.index]), "split": {"kind": "fixed in-sample baseline", "oos": False}, "cost": {"fee_bps": policy["fee_bps"], "slippage_bps": policy["slippage_bps"]}, "version": policy["strategy_version"] + ":" + policy["engine_version"], "metric_basis": policy["metric_basis"], "policy_fingerprint": stable_digest(policy)},
            "backtest": backtest.to_dict(), "config": asdict(config),
            "limitations": sorted(set(evaluation.limitations) | set(source["source_limitations"]) | {"Observation-period metrics; calendar not verified, so annualized return, volatility, Sharpe and Sortino are withheld.", "Historical signal availability is checked against each next-bar execution boundary; delayed feeds are rejected.", "Paper fills use archived marks, not live market execution.", "Single selected instrument only; no implicit purchase of other snapshot instruments. Missing industry stays UNKNOWN and receives a conservative cap.", "Factor exposure is weight times observed momentum return, not an inferred market/style beta. CVaR is a descriptive fractional empirical tail loss, not a probability forecast.", "Volume capacity assumes normalized tradable units and common price/cash currency; a 1% model is not proof of executable liquidity. Latest intents stay pending without a known next observation."}), "paper_only": True,
        }
        self.analysis["comparison_conditions"]["instrument"] = instrument
        if self.policy_lineage:
            self.analysis["policy_lineage"] = self.policy_lineage
        self.analysis["publication_scope"] = {"kind": "PRE_TEST_ONLY" if self.publication_before is not None else "FULL_SELECTED_PERIOD", "before_exclusive": self.publication_before.isoformat() if self.publication_before is not None else None}
        self.analysis["fingerprint"] = stable_digest(self.analysis)
        return self.analysis

    def _risk(self, parameters):
        self._check_binding(parameters)
        if not self.analysis or any(self.analysis[key] != parameters[key] for key in ("instrument", "as_of")):
            raise ValueError("risk requires the matching completed backtest")
        last = next(item for item in reversed(self.observations) if item.instrument == parameters["instrument"])
        historical_drawdown = abs(float(self.analysis["metrics"]["max_drawdown"]))
        target_weight = self.analysis["proposed_target_weight"]
        drawdown = historical_drawdown if target_weight else 0.0
        policy = self.policy
        details = self._risk_details
        factor_map = {last.instrument: {"industry": details["industry"], "factors": {policy["factor_id"]: details["momentum"]}}}
        scenarios = {
            "market_down": {"return_shock": policy["market_shock"], "max_loss": policy["market_loss_limit"]},
            "gap_down": {"return_shock": policy["gap_shock"], "max_loss": policy["gap_loss_limit"]},
            "industry_down": {"industry_shocks": {details["industry"]: policy["industry_shock"]}, "max_loss": policy["industry_loss_limit"]},
            "signal_reversal": {"factor_shocks": {policy["factor_id"]: -1.0}, "max_loss": policy["signal_exposure_cap"]},
        }
        self.risk_payload = {
            "dataset_fingerprint": self.snapshot.fingerprint,
            "snapshot": {
                "dataset_fingerprint": self.snapshot.fingerprint,
                "snapshot_id": self.snapshot.fingerprint,
                "as_of": min(self._boundary(parameters["as_of"]), self.publication_before - pd.Timedelta(nanoseconds=1)).isoformat() if self.publication_before is not None else self._boundary(parameters["as_of"]).isoformat(), "pit_status": "AVAILABLE",
                "observations": [{**{key: last.to_dict()[key] for key in ("instrument", "timestamp", "available_at", "close", "volume")}, "industry": details["industry"], "factors": {policy["factor_id"]: details["momentum"]}, "returns": details["returns"], "suspended": last.suspended is True}],
                "drawdown": drawdown,
                "concentration": target_weight, "proposed_target_weight": target_weight,
                "historical_strategy_drawdown": historical_drawdown,
                "proposed_weights": {last.instrument: target_weight},
            },
            "factor_result": {"status": "READY", "metrics": {"drawdown": drawdown}},
            "constraints": {**{key: policy[key] for key in ("max_drawdown", "max_concentration", "max_cvar")}, "policy_version": policy["version"], "min_liquidity": policy["min_liquidity"] if target_weight else 0.0, "stress_scenarios": scenarios},
            "candidates": [{"instrument": last.instrument, "score": 1.0, "industry": details["industry"], "factors": {policy["factor_id"]: details["momentum"]}}],
            "portfolio_constraints": {"max_single_weight": target_weight, "max_exposure": target_weight, "cash_buffer": 1 - target_weight, "require_target_exposure": True, "industry_limits": {details["industry"]: policy["unknown_industry_cap"] if details["industry"] == "UNKNOWN" else policy["industry_cap"]}, "factor_limits": {policy["factor_id"]: policy["signal_exposure_cap"]}, "max_turnover": policy["target_weight"], "transaction_cost_bps": policy["fee_bps"] + policy["slippage_bps"], "max_transaction_cost": policy["target_weight"] * (policy["fee_bps"] + policy["slippage_bps"]) / 10000},
            "allocation_method": policy["allocation_method"],
            "execution_policy": {"initial_cash": policy["starting_cash"], "fee_bps": policy["fee_bps"], "slippage_bps": policy["slippage_bps"], "delay_observations": policy["delay_observations"], "signal_at": last.timestamp.isoformat(), "max_volume_participation": policy["max_volume_participation"]},
        }
        risk = RiskManager()
        base_snapshot = self.risk_payload["snapshot"]
        portfolio = {"weights": {last.instrument: target_weight}}
        exposure = risk.exposure(base_snapshot, portfolio, factor_map)
        stress = risk.stress(base_snapshot, portfolio, scenarios)
        base_snapshot.update(exposure_report=exposure.to_dict(), stress_report=stress.to_dict())
        reviewed = risk.review(base_snapshot, self.risk_payload["factor_result"], self.risk_payload["constraints"])
        proposal = PortfolioManager().optimize(self.risk_payload["candidates"], reviewed, self.risk_payload["portfolio_constraints"])
        ledger = PaperTrader().simulate(proposal, base_snapshot, self.risk_payload["execution_policy"]) if proposal.passed else None
        self.analysis["risk_portfolio"] = {
            "run_id": self.run_id, "dataset_fingerprint": self.snapshot.fingerprint, "policy_fingerprint": stable_digest(policy),
            "industry_status": "UNKNOWN" if last.industry is None else "USER_SUPPLIED", "factor_definition": "position weight × observed momentum return; not a style/market beta", "cvar_sample_size": len(details["returns"]),
            "exposure": exposure.to_dict(), "stress": stress.to_dict(), "review": reviewed.to_dict(),
            "allocation": proposal.to_dict(), "constraint_effects": self._constraint_effects,
            "paper_ledger": ledger.to_dict() if ledger else None, "attribution": self._attribution,
            "planned_cash_weight": proposal.cash_weight, "execution_status": "PENDING_NEXT_OBSERVATION" if ledger else "BLOCKED",
            "method": policy["allocation_method"], "paper_only": True,
        }
        from .quant_evidence import compute_quant_evidence

        self.analysis["quant_evidence"] = compute_quant_evidence(self.snapshot, parameters["as_of"], self.publication_before)
        self.risk_payload["allocation_fingerprint"] = proposal.fingerprint
        self.analysis["fingerprint"] = stable_digest({key: value for key, value in self.analysis.items() if key != "fingerprint"})
        return self.risk_payload

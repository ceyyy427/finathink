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
from .tools import (
    ResearchToolGateway,
    ResearchToolRequest,
    ResearchToolResponse,
    ResearchToolStatus,
)

_LOCAL_POLICY = MappingProxyType({
    "version": "local-paper.v2", "factor_id": "momentum_2obs", "momentum_window": 2, "bar_frequency": "one-observation-per-utc-date",
    "metric_basis": "OBSERVATION", "annualization": 1,
    "strategy_version": f"{FactorThresholdStrategy.strategy_id}.{FactorThresholdStrategy.version}",
    "engine_version": BacktestEngine.engine_version, "threshold": 0.0,
    "target_weight": 0.75, "starting_cash": 10000, "fee_bps": 5, "slippage_bps": 5,
    "max_drawdown": 0.35, "max_concentration": 1.0, "min_liquidity": 1.0,
})


def local_research_policy():
    """The same parameters drive execution and the durable semantic identity."""
    return dict(_LOCAL_POLICY)


class SnapshotResearchTools(ResearchToolGateway):
    """Use the in-house engine; never resolve a missing input to a fixture."""

    def __init__(self, snapshot: DatasetSnapshot) -> None:
        if not isinstance(snapshot, DatasetSnapshot):
            raise TypeError("snapshot must be a DatasetSnapshot")
        # Defensive roundtrip also verifies nested content before it is used.
        self.snapshot = DatasetSnapshot.from_json(snapshot.to_json())
        self.policy = local_research_policy()
        self.analysis: dict = {}
        self.risk_payload: dict = {}
        quant = QuantServiceGateway()
        quant.register("quant.run_backtest", self._backtest)
        quant.register("quant.analyze_risk", self._risk)
        super().__init__(quant_gateway=quant)

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
                "records": [item.to_dict() for item in self.snapshot.observations],
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
        rows = [item for item in self.snapshot.observations if item.instrument == instrument]
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
            [{key: getattr(item, key) for key in ("open", "high", "low", "close", "volume")} for item in rows],
            index=pd.DatetimeIndex([item.timestamp for item in rows]),
        )
        dataset = Dataset(frame, Provenance("user-snapshot", "local snapshot reference", license="user-declared; local research"))
        policy = self.policy
        factor_definition = replace(momentum_factor(policy["momentum_window"]), name=policy["factor_id"])
        strategy = FactorThresholdStrategy(factor=factor_definition, threshold=policy["threshold"], target_weight=policy["target_weight"])
        config = BacktestConfig(**{key: policy[key] for key in ("starting_cash", "fee_bps", "slippage_bps", "annualization")}, frequency="observation")
        backtest = BacktestEngine().run(dataset, strategy, config)
        evaluation = evaluate_backtest(backtest)
        metrics = dict(evaluation.metrics)
        factor_values = strategy.factor.compute(frame["close"])
        target_weight = float(strategy.generate(dataset).iloc[-1])
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
            "engine_dataset_fingerprint": backtest.dataset_fingerprint,
            "policy_fingerprint": stable_digest(policy), "strategy_version": policy["strategy_version"],
            "instrument": instrument, "as_of": parameters["as_of"],
            "factor": factor, "metrics": metrics, "proposed_target_weight": target_weight,
            "metric_basis": policy["metric_basis"], "data_mode": source["data_mode"],
            "source_metadata": source["source_metadata"],
            "backtest": backtest.to_dict(), "config": asdict(config),
            "limitations": sorted(set(evaluation.limitations) | set(source["source_limitations"]) | {"Observation-period metrics; calendar not verified, so annualized return, volatility, Sharpe and Sortino are withheld.", "Historical signal availability is checked against each next-bar execution boundary; delayed feeds are rejected.", "Paper fills use archived marks, not live market execution."}), "paper_only": True,
        }
        self.analysis["fingerprint"] = stable_digest(self.analysis)
        return self.analysis

    def _risk(self, parameters):
        self._check_binding(parameters)
        if not self.analysis or any(self.analysis[key] != parameters[key] for key in ("instrument", "as_of")):
            raise ValueError("risk requires the matching completed backtest")
        last = next(item for item in reversed(self.snapshot.observations) if item.instrument == parameters["instrument"])
        historical_drawdown = abs(float(self.analysis["metrics"]["max_drawdown"]))
        target_weight = self.analysis["proposed_target_weight"]
        drawdown = historical_drawdown if target_weight else 0.0
        policy = self.policy
        self.risk_payload = {
            "dataset_fingerprint": self.snapshot.fingerprint,
            "snapshot": {
                "dataset_fingerprint": self.snapshot.fingerprint,
                "snapshot_id": self.snapshot.fingerprint,
                "as_of": self._boundary(parameters["as_of"]).isoformat(), "pit_status": "AVAILABLE",
                "observations": [{key: last.to_dict()[key] for key in ("instrument", "timestamp", "available_at", "close", "volume")}],
                "drawdown": drawdown,
                "concentration": target_weight, "proposed_target_weight": target_weight,
                "historical_strategy_drawdown": historical_drawdown,
                "stress_results": {"historical_drawdown": {"passed": drawdown <= policy["max_drawdown"]}},
            },
            "factor_result": {"status": "READY", "metrics": {"drawdown": drawdown}},
            "constraints": {**{key: policy[key] for key in ("max_drawdown", "max_concentration")}, "min_liquidity": policy["min_liquidity"] if target_weight else 0.0, "stress_scenarios": {"historical_drawdown": {"drawdown": drawdown}}},
            "candidates": [parameters["instrument"]],
            "portfolio_constraints": {"max_single_weight": policy["target_weight"], "max_exposure": target_weight},
            "execution_policy": {"initial_cash": policy["starting_cash"], "fee_bps": policy["fee_bps"], "slippage_bps": policy["slippage_bps"]},
        }
        return self.risk_payload

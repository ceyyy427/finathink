"""Run-scoped deterministic tools bound to one verified market snapshot."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date

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


class SnapshotResearchTools(ResearchToolGateway):
    """Use the in-house engine; never resolve a missing input to a fixture."""

    def __init__(self, snapshot: DatasetSnapshot) -> None:
        if not isinstance(snapshot, DatasetSnapshot):
            raise TypeError("snapshot must be a DatasetSnapshot")
        # Defensive roundtrip also verifies nested content before it is used.
        self.snapshot = DatasetSnapshot.from_json(snapshot.to_json())
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
        if requested not in {(), ("momentum_2d",)}:
            raise ValueError("selected factor is not supported by this deterministic workflow")
        instrument = str(parameters["instrument"])
        rows = [item for item in self.snapshot.observations if item.instrument == instrument]
        if len(rows) < 4:
            raise ValueError("selected instrument requires at least four OHLCV observations")
        frame = pd.DataFrame(
            [{key: getattr(item, key) for key in ("open", "high", "low", "close", "volume")} for item in rows],
            index=pd.DatetimeIndex([item.timestamp for item in rows]),
        )
        dataset = Dataset(frame, Provenance("user-snapshot", "local snapshot reference", license="user-declared; local research"))
        strategy = FactorThresholdStrategy(factor=momentum_factor(2))
        config = BacktestConfig(starting_cash=10000, fee_bps=5, slippage_bps=5)
        backtest = BacktestEngine().run(dataset, strategy, config)
        evaluation = evaluate_backtest(backtest)
        factor_values = strategy.factor.compute(frame["close"])
        factor = {
            "factor_id": strategy.factor.name, "dataset_fingerprint": self.snapshot.fingerprint,
            "latest_value": float(factor_values.iloc[-1]),
            "definition": strategy.factor.definition,
        }
        self.analysis = {
            "dataset_fingerprint": self.snapshot.fingerprint,
            "engine_dataset_fingerprint": backtest.dataset_fingerprint,
            "instrument": instrument, "as_of": parameters["as_of"],
            "factor": factor, "metrics": dict(evaluation.metrics),
            "backtest": backtest.to_dict(), "config": asdict(config),
            "limitations": list(evaluation.limitations), "paper_only": True,
        }
        self.analysis["fingerprint"] = stable_digest(self.analysis)
        return self.analysis

    def _risk(self, parameters):
        self._check_binding(parameters)
        if not self.analysis or any(self.analysis[key] != parameters[key] for key in ("instrument", "as_of")):
            raise ValueError("risk requires the matching completed backtest")
        last = next(item for item in reversed(self.snapshot.observations) if item.instrument == parameters["instrument"])
        drawdown = abs(float(self.analysis["metrics"]["max_drawdown"]))
        self.risk_payload = {
            "dataset_fingerprint": self.snapshot.fingerprint,
            "snapshot": {
                "dataset_fingerprint": self.snapshot.fingerprint,
                "snapshot_id": self.snapshot.fingerprint,
                "as_of": self._boundary(parameters["as_of"]).isoformat(), "pit_status": "AVAILABLE",
                "observations": [{key: last.to_dict()[key] for key in ("instrument", "timestamp", "available_at", "close", "volume")}],
                "drawdown": drawdown,
                "stress_results": {"historical_drawdown": {"passed": drawdown <= 0.35}},
            },
            "factor_result": {"status": "READY", "metrics": {"drawdown": drawdown}},
            "constraints": {"max_drawdown": 0.35, "max_concentration": 1.0, "min_liquidity": 1.0, "stress_scenarios": {"historical_drawdown": {"drawdown": drawdown}}},
            "candidates": [parameters["instrument"]],
            "portfolio_constraints": {"max_single_weight": 0.75, "max_exposure": 0.75},
            "execution_policy": {"initial_cash": 10000, "fee_bps": 5, "slippage_bps": 5},
        }
        return self.risk_payload

"""Server-side research view models for the P8.2 local workspace.

The browser receives a small, already-normalized payload.  It may render and
select values, but it never derives returns, features, signals, or scores.
This module is intentionally independent from optional Qlib/vectorbt/QMT SDKs;
those systems can only enter through the Finathink-owned contracts and source
adapters.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from finahinking.p8_2.contracts import DatasetSnapshot, MarketObservation


def _number(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _observation_dict(item: MarketObservation) -> dict[str, Any]:
    return item.to_dict()


def _fixture_snapshot() -> DatasetSnapshot:
    """Load the deterministic fixture through the P8.2 source boundary."""

    from finahinking.p8_2.data_sources import FixtureMarketDataSource

    return FixtureMarketDataSource().snapshot()


def _sweep_payload(dataset: DatasetSnapshot) -> dict[str, Any]:
    """Return a bounded, transparent parameter sweep for the sample chart."""

    from finahinking.p8_2.contracts import ParameterSweepSpecification
    from finahinking.p8_2.sweeps import run_parameter_sweep

    spec = ParameterSweepSpecification(
        strategy_version="fixture-momentum-v1",
        parameter_ranges={"window": (3, 5, 8), "threshold": (0.0, 0.01)},
        train_scope={"start": "2026-01-01", "end": "2026-01-04"},
        validation_scope={"start": "2026-01-04", "end": "2026-01-06"},
        oos_scope={"start": "2026-01-06", "end": "2026-01-10"},
        parameters={"dataset_fingerprint": dataset.fingerprint},
    )

    def evaluate(parameters: dict[str, object]) -> dict[str, object]:
        window = _number(parameters["window"], "window")
        threshold = _number(parameters["threshold"], "threshold")
        train_score = round((window / 100.0) + threshold, 6)
        # This is a deterministic fixture metric, not a claim about a best
        # strategy.  The OOS value is deliberately retained beside train.
        return {
            "train": {"score": train_score},
            "validation": {"score": round(train_score - 0.003, 6)},
            "oos": {"score": round(train_score - 0.01, 6)},
        }

    result = run_parameter_sweep(spec, evaluate)
    payload = result.to_dict()
    payload["dataset_fingerprint"] = dataset.fingerprint
    payload["selection_policy"] = spec.selection_policy
    return payload


def _factor_research_payload(dataset: DatasetSnapshot) -> dict[str, Any]:
    """Run the bounded, validation-only factor loop for the local report."""

    from finahinking.factors.mining import generate_candidates
    from finahinking.p6_6.workbench import ResearchCharter
    from finahinking.research.factor_loop import run_factor_research

    observations = sorted(dataset.observations, key=lambda item: (item.timestamp, item.instrument))
    instrument = observations[0].instrument if observations else "DEMO"
    rows = [item for item in observations if item.instrument == instrument]
    index = pd.DatetimeIndex([item.timestamp for item in rows])
    close = pd.Series([float(item.close) for item in rows], index=index, dtype=float)
    frame = pd.DataFrame(
        {
            "close": close,
            "volume": pd.Series([float(item.volume) for item in rows], index=index, dtype=float),
            "return_1d": close.pct_change(),
        },
        index=index,
    )
    forward_return = close.pct_change().shift(-1).rename("forward_return")
    charter = ResearchCharter(
        charter_id="fixture-factor-charter-v1",
        research_question="Which bounded factor template is worth a paper-only follow-up?",
        hypothesis_scope="trend, mean reversion, volatility, and liquidity templates",
        dataset_reference=dataset.fingerprint,
        data_split={"train": 0.4, "validation": 0.4, "test": 0.2},
        evaluation_metrics=("ic", "icir", "turnover", "decay"),
        hard_constraints={
            "shift_periods": 1,
            "min_samples": 4,
            "train_ratio": 0.6,
            "validation_ratio": 0.2,
            "decay_horizons": (1, 3),
            "paper_only": True,
        },
        allowed_primitives=("input", "return", "rolling", "rank", "combine", "negate"),
        max_experiments=6,
        iteration_budget=6,
    )
    candidates = generate_candidates(
        "discover bounded trend, mean reversion, volatility, and liquidity factors",
        field_catalog=tuple(frame.columns),
        max_candidates=charter.max_experiments,
    )
    run = run_factor_research(
        charter,
        candidates,
        {"frame": frame, "forward_return": forward_return},
        limits={"max_rounds": charter.iteration_budget},
    )
    result = run.to_dict()
    result["dataset_fingerprint"] = dataset.fingerprint
    result["instrument"] = instrument
    result["boundary"] = "paper-only; validation evidence is visible, test/OOS remains hidden until a strategy is frozen"
    return result


def _provider_status_payload() -> dict[str, Any]:
    from finahinking.research.provider_status import provider_status_payload

    config = {
        "defaults": {
            "provider": os.environ.get("FINAHINKING_PROVIDER", "offline"),
            "model": os.environ.get("FINAHINKING_MODEL", "fixture-v1"),
            "role_models": {},
        },
        "providers": [
            {
                "name": "offline",
                "model": "fixture-v1",
                "capabilities": ["structured_output", "offline"],
                "enabled": True,
                "offline": True,
            },
            {
                "name": "user-compatible",
                "model": os.environ.get("FINAHINKING_MODEL", "user-model"),
                "capabilities": ["structured_output", "tool_calling"],
                "enabled": True,
                "offline": False,
                "credential_ref": {"env_var": "FINAHINK_USER_API_KEY"},
            },
        ],
    }
    return provider_status_payload(config, os.environ)


def build_research_payload(snapshot: DatasetSnapshot | None = None) -> dict[str, Any]:
    """Build the canonical JSON payload consumed by the research workspace."""

    dataset = snapshot or _fixture_snapshot()
    observations = tuple(dataset.observations)
    previous_close: float | None = None
    points: list[dict[str, Any]] = []
    for index, item in enumerate(observations):
        close = _number(item.close, "close")
        one_period_return = 0.0 if previous_close is None else round((close / previous_close) - 1.0, 8)
        range_pct = round((item.high - item.low) / item.close, 8)
        points.append(
            {
                "id": f"{item.instrument}-{item.timestamp.strftime('%Y%m%dT%H%M%SZ')}",
                "instrument": item.instrument,
                "time": item.timestamp.isoformat(),
                "open": _number(item.open, "open"),
                "high": _number(item.high, "high"),
                "low": _number(item.low, "low"),
                "close": close,
                "volume": _number(item.volume, "volume"),
                "features": {
                    "return_1d": one_period_return,
                    "range_pct": range_pct,
                    "close_rank": round((index + 1) / len(observations), 8),
                },
                "events": ["fixture-start"] if index == 0 else [],
                "signal": "observation",
                "source": item.source,
                "provider": item.provider,
                "available_at": item.available_at.isoformat() if item.available_at is not None else None,
            }
        )
        previous_close = close

    feature_meta = [
        {
            "id": "return_1d",
            "label": "One-period return",
            "definition": "Close_t / Close_(t-1) − 1; first observation is explicitly 0 in the fixture.",
            "source": "server-normalized fixture",
            "lookahead": "not used",
        },
        {
            "id": "range_pct",
            "label": "Intraday range",
            "definition": "(High − Low) / Close",
            "source": "server-normalized fixture",
            "lookahead": "not used",
        },
        {
            "id": "close_rank",
            "label": "Chronological close rank",
            "definition": "Position in the ordered sample, not a predictive score.",
            "source": "server-normalized fixture",
            "lookahead": "descriptive only",
        },
    ]
    events = [
        {
            "id": "fixture-start",
            "label": "Fixture start",
            "kind": "DATASET",
            "description": "First available observation in the deterministic sample.",
            "source": dataset.provenance.get("source", "finathink.fixture"),
        }
    ]
    factor_research = _factor_research_payload(dataset)
    rounds = factor_research["rounds"]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "view": "research-series",
        "dataset": {
            "id": dataset.dataset_id,
            "fingerprint": dataset.fingerprint,
            "mode": dataset.mode,
            "as_of": dataset.as_of.isoformat(),
            "provider": dataset.provenance.get("provider", "finahinking.fixture"),
            "source": dataset.provenance.get("source", "deterministic fixture"),
            "pit_available": dataset.pit_available(dataset.as_of),
            "limitations": list(dataset.limitations),
        },
        "points": points,
        "features": feature_meta,
        "events": events,
        "sweep": _sweep_payload(dataset),
        "factor_research": factor_research,
        "factor_candidates": [item["candidate"] for item in rounds],
        "factor_evaluations": [item["evaluation"] for item in rounds],
        "factor_decay": [
            {"candidate_id": item["candidate"]["candidate_id"], **item["evaluation"]["decay"]}
            for item in rounds
        ],
        "factor_admission": [item["admission"] for item in rounds],
        "research_rounds": rounds,
        "provider_status": _provider_status_payload(),
        "limitations": list(dataset.limitations)
        + [
            "Sample data only; no live market feed is connected.",
            "Feature values are descriptive fixture outputs, not forecasts or trading instructions.",
            "Parameter results retain train, validation, OOS, and multiple-testing warnings.",
        ],
    }
    payload["workbench"] = _default_workbench_payload(dataset)
    payload["payload_fingerprint"] = _digest(payload)
    return payload


def _default_workbench_payload(dataset: DatasetSnapshot) -> dict[str, Any]:
    """Build the bounded factor/position/risk slice shown beside the series."""

    from finahinking.p6_6.workbench import (
        ExecutionPolicy,
        FaultPolicy,
        PositionPolicySpec,
        RiskStatePolicy,
    )
    from finahinking.p6_6.workbench_engine import run_workbench
    from finahinking.p6_6.workbench_explanations import build_explanation_package

    first_close: dict[str, float] = {}
    previous_close: dict[str, float] = {}
    rows: list[dict[str, Any]] = []
    for item in sorted(dataset.observations, key=lambda observation: (observation.timestamp, observation.instrument)):
        first = first_close.setdefault(item.instrument, float(item.close))
        previous = previous_close.get(item.instrument)
        one_period_return = 0.0 if previous is None else (float(item.close) / previous) - 1.0
        score = (float(item.close) / first) - 1.0
        rows.append(
            {
                "id": f"{item.instrument}-{item.timestamp.strftime('%Y%m%dT%H%M%SZ')}",
                "time": item.timestamp.isoformat(),
                "available_at": item.available_at.isoformat(),
                "instrument": item.instrument,
                "score": round(score, 8),
                "signal": bool(previous is not None and score > 0),
                "volatility": round(abs((item.high - item.low) / item.close), 8),
                "return": round(one_period_return, 8),
                "price": float(item.close),
                "volume": float(item.volume),
            }
        )
        previous_close[item.instrument] = float(item.close)
    run = run_workbench(
        "fixture-workbench-v1",
        rows,
        PositionPolicySpec("fixture-position", "v1", "equal_weight", target_volatility=0.20, max_exposure=0.90, cash_buffer=0.10),
        RiskStatePolicy("fixture-risk", "v1"),
        ExecutionPolicy("fixture-execution", "v1", fee_bps=5.0, slippage_bps=5.0, stress_slippage_bps=15.0),
        FaultPolicy("fixture-fault", "v1"),
        dataset_fingerprint=dataset.fingerprint,
        strategy_fingerprint=_digest({"strategy": "lagged-close-strength", "version": "v1"}),
    )
    explanation = build_explanation_package(
        run,
        strategy_id="fixture-strategy",
        parameter_changes={"lookback": {"before": 20, "after": 40}},
        intent="Test whether a slower signal would reduce noise and turnover.",
        formula_before="P_(t-1) / P_(t-21) - 1",
        formula_after="P_(t-1) / P_(t-41) - 1",
        code_trace=("momentum", "lag", "weights", "risk", "costs", "oos"),
    )
    workbench = build_workbench_payload(run, explanation)
    factor_research = _factor_research_payload(dataset)
    workbench["factor_research"] = factor_research
    rounds = factor_research["rounds"]
    workbench["factor_candidates"] = [item["candidate"] for item in rounds]
    workbench["factor_evaluations"] = [item["evaluation"] for item in rounds]
    workbench["factor_decay"] = [
        {"candidate_id": item["candidate"]["candidate_id"], **item["evaluation"]["decay"]}
        for item in rounds
    ]
    workbench["factor_admission"] = [item["admission"] for item in rounds]
    workbench["research_rounds"] = rounds
    workbench["provider_status"] = _provider_status_payload()
    return workbench


def build_workbench_payload(run: Any, explanation: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Normalize one engine run for renderers; no browser-side calculation."""

    from finahinking.p6_6.workbench_engine import WorkbenchRun

    if not isinstance(run, WorkbenchRun):
        raise TypeError("run must be a WorkbenchRun")
    points = [point.to_dict() for point in sorted(run.points, key=lambda item: (item.time, item.instrument))]
    by_ref = lambda point: {"id": point["point_id"], "time": point["time"], "instrument": point["instrument"]}
    workbench: dict[str, Any] = {
        "schema_version": 1,
        "view": "factor-strategy-workbench",
        "run_id": run.run_id,
        "paper_only": True,
        "points": points,
        "factor_observations": [{**by_ref(point), "score": point["score"], "available_at": point["available_at"]} for point in points],
        "signals": [{**by_ref(point), "signal": point["signal"], "score": point["score"]} for point in points],
        "raw_weights": [{**by_ref(point), "value": point["raw_weight"]} for point in points],
        "risk_scales": [{**by_ref(point), "value": point["risk_scale"], "state": point["risk_state"]} for point in points],
        "final_weights": [{**by_ref(point), "value": point["final_weight"], "held": point["held_weight"]} for point in points],
        "exposure": [{**by_ref(point), "value": point["exposure"]} for point in points],
        "cash": [{**by_ref(point), "value": point["cash"]} for point in points],
        "risk_states": [{**by_ref(point), "state": point["risk_state"], "reasons": point["risk_reasons"], "allowed_actions": point["allowed_actions"]} for point in points],
        "trades": [{**by_ref(point), "weight": point["trade_weight"]} for point in points if point["trade_weight"] != 0],
        "costs": [{**by_ref(point), "fees": point["fees"], "slippage": point["slippage"]} for point in points],
        "slippage": [{**by_ref(point), "value": point["slippage"]} for point in points],
        "fault_events": [{**event, "point_id": point["point_id"]} for point in points for event in point["fault_events"]],
        "metrics": dict(run.metrics),
        "limitations": list(run.limitations),
        "provenance": {"run_id": run.run_id, "dataset_fingerprint": run.dataset_fingerprint, "strategy_fingerprint": run.strategy_fingerprint, "policy_fingerprint": _digest(run.to_dict()["policies"])},
        "explanation_refs": [{"id": explanation.get("parameter_change", {}).get("explanation_id", f"explanation-{run.run_id}"), "components": sorted((explanation or {}).get("traces", {}).keys())}],
        "baseline_variant_refs": [{"baseline": (explanation or {}).get("parameter_change", {}).get("paired_metrics", {}).get("baseline", run.metrics), "variant": (explanation or {}).get("parameter_change", {}).get("paired_metrics", {}).get("variant", run.metrics)}],
    }
    if explanation is not None:
        workbench["explanation"] = explanation
    workbench["payload_fingerprint"] = _digest(workbench)
    return workbench


def capability_payload() -> dict[str, Any]:
    """Return capability status without importing optional SDK modules."""

    from finahinking.p8_2.capabilities import CapabilityRegistry

    return CapabilityRegistry.detect().to_dict()


def qmt_payload() -> dict[str, Any]:
    """Return the read-only QMT bridge status and its explicit boundary."""

    from finahinking.p8_2.qmt import QMTBridgeClient

    status = QMTBridgeClient().status()
    return {
        **status.to_dict(),
        "permissions": ["market_data_read", "snapshot_read"],
        "denied": ["order_stock", "cancel_order", "account_read", "passwords"],
    }


def ml_payload(dataset: DatasetSnapshot | None = None) -> dict[str, Any]:
    """Describe the ML lab with a typed optional-adapter result."""

    snapshot = dataset or _fixture_snapshot()
    from finahinking.p8_2.adapters import QlibResearchAdapter
    from finahinking.p8_2.contracts import MLResearchSpecification

    spec = MLResearchSpecification(
        research_id="fixture-ml-v1",
        dataset_fingerprint=snapshot.fingerprint,
        target="close",
        features=("close", "volume"),
        train_scope={"start": "2026-01-01", "end": "2026-01-04"},
        validation_scope={"start": "2026-01-04", "end": "2026-01-06"},
        test_scope={"start": "2026-01-06", "end": "2026-01-10"},
    )
    result = QlibResearchAdapter(force_unavailable=True).run(spec, snapshot)
    return {
        "specification": spec.to_dict(),
        "result": result.to_dict(),
        "boundary": "Qlib is an optional sandbox; raw Qlib objects never cross this response boundary.",
    }


def parameter_payload(dataset: DatasetSnapshot | None = None) -> dict[str, Any]:
    payload = build_research_payload(dataset)
    sweep = payload["sweep"]
    return {
        "dataset": payload["dataset"],
        "sweep": sweep,
        "interpretation": {
            "oos_first": True,
            "multiple_testing_visible": True,
            "selection_policy": sweep.get("selection_policy", "pre_specified"),
            "warning": "This view compares declared experiments; it does not name a best strategy.",
        },
    }


def utc_now_label() -> str:
    """Stable-format helper used only for UI copy, never for data timestamps."""

    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

"""Typed, paper-only settlement evidence for the learning boundary.

Settlement is deliberately a value object.  It contains the immutable paper
ledger fingerprint and realized, normalized outcomes; it never accepts a
broker response, model object, prompt, or executable callback.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from .contracts import stable_digest, to_jsonable
from .paper_trader import PaperLedger

_PUBLIC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SENSITIVE = re.compile(
    r"(?:api[-_]?key|secret|token|password|credential|authorization|prompt|"
    r"raw(?:[-_ ]?provider)?[-_ ]?response|endpoint|private[-_ ]?key|"
    r"file[-_ ]?path|absolute[-_ ]?path)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE = re.compile(
    r"(?:api[-_]?key|secret|token|password|credential|authorization)\s*[=:]|\bprompt\b",
    re.IGNORECASE,
)
_URI = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_PATH = re.compile(r"(?:^|[\s:=])(?:~[\\/]|[\\/]|\.\.?[\\/]|[A-Za-z]:[\\/])")


def _sensitive_key(value: str) -> bool:
    """Reject provider/raw response variants across naming conventions."""

    compact = re.sub(r"[^a-z0-9]", "", value.casefold())
    if _SENSITIVE.search(value):
        return True
    if "provider" in compact:
        return True
    if compact.startswith("raw") and any(
        marker in compact for marker in ("response", "object", "payload", "result", "output")
    ):
        return True
    return compact in {"rawresponse", "rawobject", "rawpayload", "rawresult", "rawoutput"}


def _as_date(value: date | str, name: str = "as_of") -> date:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{name} must be an ISO date") from exc
    if not isinstance(value, date):
        raise TypeError(f"{name} must be a date")
    return value


def _safe(value: Any, path: str = "value") -> Any:
    """Normalize JSON-like values and reject secrets/provider objects."""

    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must be finite")
        return value
    if isinstance(value, str):
        if _URI.search(value) or _PATH.search(value) or _SENSITIVE_VALUE.search(value):
            raise ValueError(f"{path} contains endpoint or path material")
        return value
    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, str) or not key.strip() or _sensitive_key(key):
                raise ValueError(f"{path} contains sensitive field")
            clean[key.strip()] = _safe(child, f"{path}.{key}")
        return clean
    if isinstance(value, (tuple, list)):
        return tuple(_safe(item, f"{path}[]") for item in value)
    # Dataclasses/raw provider SDK instances are intentionally not coerced.
    raise TypeError(f"{path} must contain normalized JSON data")


def _ledger_date(value: str) -> date | None:
    text = str(value).strip()
    if not text or text.casefold() in {"paper-time", "unknown"}:
        return None
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class SettlementEvent:
    """A single realized paper outcome at a declared point in time."""

    run_id: str
    ledger: PaperLedger
    as_of: date | str
    realized_outcomes: Mapping[str, Any] = field(default_factory=dict)
    costs: Mapping[str, float] = field(default_factory=dict)
    dataset_digest: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not _PUBLIC_ID.fullmatch(self.run_id.strip()):
            raise ValueError("run_id must be a stable public identifier")
        object.__setattr__(self, "run_id", self.run_id.strip())
        if not isinstance(self.ledger, PaperLedger) or self.ledger.paper_only is not True:
            raise TypeError("settlement requires a paper-only PaperLedger")
        event_as_of = _as_date(self.as_of)
        object.__setattr__(self, "as_of", event_as_of)
        if not isinstance(self.dataset_digest, str) or not _PUBLIC_ID.fullmatch(self.dataset_digest.strip()):
            raise ValueError("dataset_digest must be a stable public identifier")
        object.__setattr__(self, "dataset_digest", self.dataset_digest.strip())
        if not self.ledger.snapshot_digest:
            raise ValueError("paper ledger snapshot digest is required")
        if self.ledger.snapshot_digest != self.dataset_digest:
            raise ValueError("dataset digest does not match paper ledger snapshot")
        # PaperLedger predates this boundary and accepts an execution-policy
        # mapping.  Re-validate that nested mapping here so provider/raw
        # payloads cannot survive into ``to_dict`` or the event fingerprint.
        _safe(self.ledger.execution_policy, "ledger.execution_policy")
        for item in self.ledger.entries:
            entry_date = _ledger_date(item.timestamp)
            if entry_date is not None and entry_date > event_as_of:
                raise ValueError("settlement as_of cannot precede ledger timestamp")
        outcomes = _safe(self.realized_outcomes, "realized_outcomes")
        if not isinstance(outcomes, Mapping):
            raise TypeError("realized_outcomes must be a mapping")
        object.__setattr__(self, "realized_outcomes", dict(outcomes))
        costs = _safe(self.costs, "costs")
        if not isinstance(costs, Mapping):
            raise TypeError("costs must be a mapping")
        normalized_costs: dict[str, float] = {}
        for key, value in costs.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError("settlement costs must be finite numbers")
            if float(value) < 0:
                raise ValueError("settlement costs cannot be negative")
            normalized_costs[str(key)] = float(value)
        object.__setattr__(self, "costs", normalized_costs)

    @property
    def ledger_fingerprint(self) -> str:
        return self.ledger.fingerprint

    @property
    def fingerprint(self) -> str:
        return stable_digest(
            {
                "run_id": self.run_id,
                "ledger_fingerprint": self.ledger_fingerprint,
                "as_of": self.as_of,
                "realized_outcomes": self.realized_outcomes,
                "costs": self.costs,
                "dataset_digest": self.dataset_digest,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "ledger": self.ledger.to_dict(),
            "as_of": self.as_of,
            "realized_outcomes": to_jsonable(self.realized_outcomes),
            "costs": dict(self.costs),
            "dataset_digest": self.dataset_digest,
            "ledger_fingerprint": self.ledger_fingerprint,
            "fingerprint": self.fingerprint,
        }


def observe_paper_outcome(*, run_id, source, evidence, source_as_of, as_of, allocation, execution_policy, factor_id, factor_weights, risk_rules):
    """Observe a frozen next-bar paper intent at a fixed five-interval mark.

    Research cutoff, rather than a stale last quote, is the earliest decision
    boundary. The selected valuation slice never grows when later data arrives.
    """
    from dataclasses import replace

    import pandas as pd

    from finahinking.factors.evaluation import evaluate_factor_candidate

    from .factor_experiments import _candidate_for
    from .paper_trader import PaperLedgerEntry, PaperTrader
    from .portfolio_runtime import PaperPortfolioProposal

    cutoff = pd.Timestamp(_as_date(as_of), tz="UTC") + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    decision = pd.Timestamp(_as_date(source_as_of), tz="UTC") + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    if not source.pit_available(decision) or not evidence.pit_available(cutoff):
        raise ValueError("复盘数据包含未知或未来可用时间")
    proposal = PaperPortfolioProposal(**{key: value for key, value in allocation.items() if key != "fingerprint"})
    if proposal.fingerprint != allocation.get("fingerprint") or not proposal.passed or len(proposal.weights) != 1:
        raise ValueError("复盘需要原始已验证的单标的纸面配置")
    instrument = next(iter(proposal.weights))
    original = [row for row in source.observations if row.instrument == instrument]
    incoming = [row for row in evidence.observations if row.instrument == instrument]
    if not original or not incoming or execution_policy.get("delay_observations") != 1:
        raise ValueError("复盘缺少标的价格或原始延迟执行规则")
    fields = ("timestamp", "available_at", "open", "high", "low", "close", "volume", "amount", "industry", "suspended")
    known = {row.timestamp: tuple(getattr(row, key) for key in fields) for row in original}
    if any(row.timestamp in known and tuple(getattr(row, key) for key in fields) != known[row.timestamp] for row in incoming):
        raise ValueError("复盘数据修改了原研究中的已知价格或状态")
    if pd.Timestamp(execution_policy["signal_at"]) != original[-1].timestamp:
        raise ValueError("原纸面信号与所选快照不一致")
    following = [row for row in incoming if row.timestamp > decision]
    if len(following) < 6:
        raise ValueError("纸面观察尚未到期：需要成交观察及之后5条观察")
    selected = (*original, *following[:6])
    if len({row.timestamp.tz_convert("UTC").date() for row in selected}) != len(selected):
        raise ValueError("复盘仅接受每UTC日期一条观察")
    mark = following[5]
    available = max(max(row.timestamp, row.available_at) for row in selected).tz_convert("UTC")
    modes = {source.mode, evidence.mode}
    mode = "SYNTHETIC" if "SYNTHETIC" in modes else "SAMPLE" if "SAMPLE" in modes else "CAPTURED"
    derived = replace(source, dataset_id="paper-observation-" + stable_digest([source.fingerprint, evidence.fingerprint, mark.timestamp])[:24], observations=selected, as_of=available, mode=mode, provenance={"source_refs": [source.fingerprint, evidence.fingerprint], "method": "fixed-paper-observation.v1", "calendar": "UNKNOWN"})
    simulation = {"dataset_fingerprint": derived.fingerprint, "pit_status": "AVAILABLE", "as_of": available.isoformat(), "observations": [row.to_dict() for row in selected]}
    ledger = PaperTrader().simulate(proposal, simulation, execution_policy)
    fills = [entry for entry in ledger.entries if entry.event in {"paper_fill", "paper_partial"} and entry.quantity > 0]
    if proposal.weights[instrument] > 0 and not fills:
        raise ValueError("原执行观察未成交，不能生成已实现学习结果")
    quantity = sum(entry.quantity for entry in fills)
    cash = ledger.entries[-1].cash
    equity = cash + quantity * mark.close
    initial_cash = float(execution_policy["initial_cash"])
    net_return = equity / initial_cash - 1
    ledger = ledger.append(PaperLedgerEntry(timestamp=available.isoformat(), event="paper_mark", instrument=instrument, reference_price=mark.close, cash=cash, equity=equity))
    updates, rules = {}, {}
    if net_return < -1e-12 and quantity:
        weight, cvar = float(factor_weights.get(factor_id, 1.0)), float(risk_rules["max_cvar"])
        if max(0.1, weight * 0.9) < weight:
            updates[factor_id] = max(0.1, weight * 0.9)
        if max(0.01, cvar * 0.9) < cvar:
            rules["max_cvar"] = max(0.01, cvar * 0.9)
    frame = pd.DataFrame([{key: getattr(row, key) for key in ("open", "high", "low", "close", "volume")} for row in selected], index=pd.DatetimeIndex([row.timestamp for row in selected]))
    candidate = _candidate_for({"expression": "return(close,2)"}, {"frame": frame}, 1)
    diagnostic = evaluate_factor_candidate(candidate, frame, frame.close.pct_change(fill_method=None).shift(-1), {"phase": "learning_diagnostic", "oos": False, "shift_periods": 1, "min_samples": 8, "decay_horizons": (1, 3, 5), "as_of": frame.index[-1]})
    observation = {"instrument": instrument, "method": "fixed-paper-observation.v1", "decision_cutoff": decision.isoformat(), "mark_timestamp": mark.timestamp.isoformat(), "available_at": available.isoformat(), "horizon_observations": 5, "reference_price": fills[0].reference_price if fills else None, "mark_price": mark.close, "quantity": quantity, "cash": cash, "equity": equity, "net_return": net_return, "fees": ledger.total_fees, "slippage": ledger.total_slippage, "planned_weight": proposal.weights[instrument], "filled_weight": quantity * fills[0].reference_price / initial_cash if fills else 0.0, "factor_decay": {"status": diagnostic.status.value, "sample_size": diagnostic.sample_size, "horizons": list(diagnostic.decay.horizons), "information_coefficients": list(diagnostic.decay.information_coefficients), "oos": False}, "recommendation_basis": "固定观察期纸面净损失时，建议动量预算与CVaR上限收缩10%；否则保留配置。该规则需审核后才形成新版本。", "has_updates": bool(updates or rules)}
    event = SettlementEvent(run_id, ledger, available.date(), {"factor_weight_updates": updates, "risk_rule_updates": rules, "observation": observation}, {"fees": ledger.total_fees, "slippage": ledger.total_slippage}, ledger.snapshot_digest)
    return event, observation, derived


__all__ = ["SettlementEvent", "observe_paper_outcome"]

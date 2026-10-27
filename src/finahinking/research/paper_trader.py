"""Pure paper simulation and append-only ledger.

No method in this module has a broker, order, account, cancel, or live-data
surface.  Inputs are normalized snapshot records and deterministic policies.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import pandas as pd

from .contracts import stable_digest
from .portfolio_runtime import PaperPortfolioProposal

_FORBIDDEN = {"broker", "order", "orders", "cancel", "account", "live", "endpoint", "credential", "secret"}
_UNSAFE_KEY = re.compile(r"(?:api[-_ ]?key|secret|token|password|credential|authorization)", re.IGNORECASE)
_UNSAFE_TEXT = re.compile(r"(?:api[-_ ]?key|secret|token|password|credential|authorization)\s*[:=]|(?:https?|file|ftp|ssh|s3)://|(?:^|[\s:(])(?:/|~[/]|\.{1,2}[/])|\\", re.IGNORECASE)


def _safe(value: Any, path: str = "value") -> Any:
    if callable(value):
        raise TypeError(f"{path} contains forbidden executable input")
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, child in value.items():
            name = str(key)
            if any(token in name.casefold() for token in _FORBIDDEN) or _UNSAFE_KEY.search(name):
                raise ValueError(f"{path} contains forbidden paper-only field")
            result[name] = _safe(child, f"{path}.{name}")
        return result
    if isinstance(value, (tuple, list)):
        return [_safe(item, f"{path}[]") for item in value]
    if isinstance(value, str):
        if _UNSAFE_TEXT.search(value):
            raise ValueError(f"{path} contains unsafe paper-only text")
        return value
    if value is None or isinstance(value, (int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must be finite")
        return value
    item = getattr(value, "item", None)
    if callable(item):
        return _safe(item(), path)
    raise TypeError(f"{path} must be normalized JSON data")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(child) for child in value)
    if isinstance(value, tuple):
        return tuple(_freeze(child) for child in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(child) for child in value]
    return value


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return _safe(value, label)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        if isinstance(result, Mapping):
            return _safe(result, label)
    raise TypeError(f"{label} must be a structured normalized value")


def _number(value: Any, label: str, default: float | None = None) -> float:
    if value is None and default is not None:
        return default
    if isinstance(value, bool):
        raise TypeError(f"{label} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _records(snapshot: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw = snapshot.get("observations", snapshot.get("records", snapshot.get("data", ())))
    if isinstance(raw, Mapping):
        raw = tuple(raw.values())
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise TypeError("snapshot records must be a sequence")
    values: list[Mapping[str, Any]] = []
    for item in raw:
        if isinstance(item, Mapping):
            values.append(item)
        else:
            to_dict = getattr(item, "to_dict", None)
            if not callable(to_dict) or not isinstance(to_dict(), Mapping):
                raise TypeError("snapshot records must be normalized mappings")
            values.append(to_dict())
    return tuple(values)


@dataclass(frozen=True, slots=True)
class PaperLedgerEntry:
    timestamp: str
    event: str
    instrument: str = ""
    target_weight: float = 0.0
    quantity: float = 0.0
    reference_price: float = 0.0
    execution_price: float = 0.0
    notional: float = 0.0
    fees: float = 0.0
    slippage: float = 0.0
    cash: float = 0.0
    equity: float = 0.0

    def __post_init__(self) -> None:
        if not str(self.event).strip():
            raise ValueError("ledger event is required")
        if any(token in str(self.event).casefold() for token in _FORBIDDEN):
            raise ValueError("ledger event is not paper-only")
        object.__setattr__(self, "timestamp", str(self.timestamp))
        object.__setattr__(self, "event", str(self.event))
        object.__setattr__(self, "instrument", str(self.instrument))
        for name in ("target_weight", "quantity", "reference_price", "execution_price", "notional", "fees", "slippage", "cash", "equity"):
            value = _number(getattr(self, name), name)
            if name in {"quantity", "reference_price", "execution_price", "notional", "fees", "slippage"} and value < -1e-12:
                raise ValueError(f"{name} must be non-negative")
            object.__setattr__(self, name, value)

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in ("timestamp", "event", "instrument", "target_weight", "quantity", "reference_price", "execution_price", "notional", "fees", "slippage", "cash", "equity")}


@dataclass(frozen=True, slots=True)
class PaperLedger:
    entries: tuple[PaperLedgerEntry, ...] = ()
    paper_only: bool = True
    snapshot_digest: str = ""
    proposal_digest: str = ""
    execution_policy: Mapping[str, Any] = field(default_factory=dict)
    policy_digest: str = ""

    def __post_init__(self) -> None:
        normalized = tuple(item if isinstance(item, PaperLedgerEntry) else PaperLedgerEntry(**dict(item)) for item in self.entries)
        object.__setattr__(self, "entries", normalized)
        clean_policy = _safe(self.execution_policy, "execution_policy")
        object.__setattr__(self, "execution_policy", _freeze(clean_policy))
        object.__setattr__(self, "policy_digest", str(self.policy_digest) or str(self.execution_policy.get("fingerprint", "")) or stable_digest(self.execution_policy))
        if self.paper_only is not True:
            raise ValueError("paper ledger must remain paper-only")

    @property
    def total_fees(self) -> float:
        return float(sum(item.fees for item in self.entries))

    @property
    def total_slippage(self) -> float:
        return float(sum(item.slippage for item in self.entries))

    @property
    def fingerprint(self) -> str:
        return stable_digest(self)

    def append(self, entry: PaperLedgerEntry | Mapping[str, Any]) -> PaperLedger:
        if isinstance(entry, Mapping):
            clean = _safe(entry, "ledger_entry")
            clean.setdefault("timestamp", "paper-time")
            entry = PaperLedgerEntry(**clean)
        if not isinstance(entry, PaperLedgerEntry):
            raise TypeError("ledger entry must be a PaperLedgerEntry")
        return PaperLedger(self.entries + (entry,), True, self.snapshot_digest, self.proposal_digest, self.execution_policy, self.policy_digest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [item.to_dict() for item in self.entries],
            "paper_only": self.paper_only,
            "snapshot_digest": self.snapshot_digest,
            "proposal_digest": self.proposal_digest,
            "execution_policy": _thaw(self.execution_policy),
            "policy_digest": self.policy_digest,
            "fingerprint": self.fingerprint,
        }


class PaperTrader:
    """Simulate delayed paper fills with declared fee and slippage costs."""

    def simulate(self, proposal: PaperPortfolioProposal | Mapping[str, Any], snapshot: Any, execution_policy: Mapping[str, Any] | Any) -> PaperLedger:
        if not isinstance(proposal, PaperPortfolioProposal):
            raise TypeError("proposal must be a PaperPortfolioProposal")
        proposal_data = proposal.to_dict()
        if proposal_data.get("passed") is not True and str(proposal_data.get("status", "")).upper() != "PASSED":
            raise ValueError("paper simulation requires a passed portfolio proposal")
        snap = _mapping(snapshot, "snapshot")
        pit = str(snap.get("pit_status", snap.get("pit", "UNKNOWN"))).upper()
        if pit == "UNKNOWN" and snap.get("as_of") is not None:
            raw_records = snap.get("observations", snap.get("records", ()))
            if isinstance(raw_records, Sequence) and not isinstance(raw_records, (str, bytes)):
                available = [str(item.get("available_at", item.get("timestamp"))) <= str(snap["as_of"]) for item in raw_records if isinstance(item, Mapping)]
                if available and all(available):
                    pit = "AVAILABLE"
        if pit not in {"AVAILABLE", "VALID", "VERIFIED", "KNOWN", "READY", "TRUE", "OK"}:
            raise ValueError("paper simulation requires known PIT snapshot")
        policy = _mapping(execution_policy, "execution_policy")
        policy = dict(policy)
        supplied_policy_digest = str(policy.pop("fingerprint", ""))
        policy_digest = stable_digest(policy)
        if supplied_policy_digest and supplied_policy_digest != policy_digest:
            raise ValueError("execution policy fingerprint mismatch")
        policy["fingerprint"] = policy_digest
        fee_bps = _number(policy.get("fee_bps", policy.get("transaction_cost_bps", 0.0)), "fee_bps")
        slippage_bps = _number(policy.get("slippage_bps", policy.get("slippage", 0.0)), "slippage_bps")
        initial_cash = _number(policy.get("initial_cash", policy.get("starting_cash", 1.0)), "initial_cash")
        if min(fee_bps, slippage_bps, initial_cash) < 0:
            raise ValueError("execution costs and initial cash must be non-negative")
        records = _records(snap)
        by_instrument: dict[str, Mapping[str, Any]] = {}
        for record in records:
            instrument = str(record.get("instrument", record.get("symbol", record.get("id", ""))))
            if not instrument:
                continue
            if "close" not in record and "price" not in record:
                continue
            by_instrument[instrument] = record
        entries: list[PaperLedgerEntry] = []
        cash = initial_cash
        weights = proposal_data.get("weights", {})
        if not isinstance(weights, Mapping):
            raise TypeError("proposal weights must be a mapping")
        total_weight = sum(_number(value, f"weights.{key}") for key, value in weights.items())
        if total_weight < -1e-12 or total_weight + _number(proposal_data.get("cash_weight", 0.0), "cash_weight") > 1.0 + 1e-9:
            raise ValueError("paper proposal exposure exceeds one")
        fee_rate = fee_bps / 10000.0
        timestamp = str(snap.get("as_of", "paper-time"))
        delay = policy.get("delay_observations", 0)
        if isinstance(delay, bool) or not isinstance(delay, int) or not 0 <= delay <= 252:
            raise ValueError("paper delay must be an observation count in [0, 252]")
        participation = policy.get("max_volume_participation")
        if participation is not None:
            participation = _number(participation, "volume participation")
            if not 0 < participation <= 1:
                raise ValueError("volume participation must be in (0, 1]")
        signal_at = boundary = None
        if delay:
            signal_at, boundary = pd.Timestamp(policy.get("signal_at")), pd.Timestamp(snap.get("as_of"))
            if pd.isna(signal_at) or pd.isna(boundary) or signal_at.tzinfo is None or boundary.tzinfo is None or signal_at > boundary:
                raise ValueError("delayed paper simulation needs a known signal and cutoff")
        contexts = []
        for instrument in sorted(str(key) for key in weights):
            weight = _number(weights[instrument], f"weights.{instrument}")
            if weight < -1e-12:
                raise ValueError("paper trader is long-only")
            if instrument not in by_instrument:
                raise ValueError(f"missing normalized price for {instrument}")
            record = by_instrument[instrument]
            event = "paper_fill"
            fill_timestamp = timestamp
            if delay:
                history = sorted((item for item in records if item.get("instrument") == instrument), key=lambda item: pd.Timestamp(item["timestamp"]))
                known = [item for item in history if pd.Timestamp(item["timestamp"]) <= boundary and item.get("available_at") is not None and pd.Timestamp(item["available_at"]) <= boundary]
                signals = [item for item in known if pd.Timestamp(item["timestamp"]) == signal_at]
                if not signals:
                    raise ValueError("paper signal needs a known snapshot observation")
                later = [item for item in history if signal_at < pd.Timestamp(item["timestamp"]) <= boundary]
                record = signals[-1]
                if len(later) < delay:
                    event = "paper_pending"
                else:
                    execution_record = later[delay - 1]
                    if execution_record not in known or pd.Timestamp(signals[-1]["available_at"]) > pd.Timestamp(execution_record["timestamp"]):
                        event = "paper_pending"
                    else:
                        record = execution_record
                        fill_timestamp = pd.Timestamp(record["timestamp"]).isoformat()
            if record.get("suspended") is True:
                event = "paper_pending"
                fill_timestamp = timestamp
            contexts.append((fill_timestamp, instrument, record, event))
        if delay:
            contexts.sort(key=lambda item: (pd.Timestamp(item[0]), item[1]))
        held = {}
        marks = {}
        for fill_timestamp, instrument, record, event in contexts:
            weight = _number(weights[instrument], f"weights.{instrument}")
            reference = _number(record.get("close", record.get("price")), f"price.{instrument}")
            if reference <= 0:
                raise ValueError("paper prices must be positive")
            allocation = initial_cash * weight
            execution = reference * (1.0 + slippage_bps / 10000.0)
            quantity = allocation / (execution * (1.0 + fee_rate)) if allocation else 0.0
            if event == "paper_pending":
                quantity = 0.0
            elif participation is not None:
                volume = _number(record.get("volume"), "volume")
                if volume < 0:
                    raise ValueError("volume must be non-negative")
                capacity = volume * participation
                if quantity > capacity:
                    quantity = capacity
                    event = "paper_partial" if capacity else "paper_pending"
            notional = quantity * execution
            fees = notional * fee_bps / 10000.0
            slippage = abs(quantity * (execution - reference))
            cash -= notional + fees
            if cash < -1e-9:
                raise ValueError("paper simulation would create negative cash")
            held[instrument] = quantity
            marks[instrument] = reference
            if delay:
                for name in held:
                    prices = [item for item in records if item.get("instrument") == name and pd.Timestamp(item["timestamp"]) <= pd.Timestamp(fill_timestamp) and item.get("available_at") is not None and pd.Timestamp(item["available_at"]) <= pd.Timestamp(fill_timestamp)]
                    if prices:
                        latest = max(prices, key=lambda item: pd.Timestamp(item["timestamp"]))
                        marks[name] = _number(latest["close"], "known mark")
            market_value = sum(held[name] * marks[name] for name in held)
            entries.append(PaperLedgerEntry(fill_timestamp, event, instrument, weight, quantity, reference, execution, notional, fees, slippage, cash, cash + market_value))
        return PaperLedger(tuple(entries), True, stable_digest(snap), str(proposal_data.get("fingerprint", "")), policy, policy_digest)

    def rebalance(self, proposal: PaperPortfolioProposal, snapshot: Any) -> PaperLedger:
        """Record a paper-only rebalance using the policy frozen in proposal."""
        if not isinstance(proposal, PaperPortfolioProposal):
            raise TypeError("proposal must be a PaperPortfolioProposal")
        if proposal.passed is not True or proposal.status != "PASSED":
            raise ValueError("paper rebalance requires a passed portfolio proposal")
        constraints = _mapping(proposal.constraints, "proposal.constraints")
        execution_policy = constraints.get("execution_policy", constraints.get("paper_execution_policy", {}))
        return self.simulate(proposal, snapshot, execution_policy)


__all__ = ["PaperLedger", "PaperLedgerEntry", "PaperTrader"]

"""Immutable optional research observations bound to a market snapshot."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

NUMERIC_RESEARCH_FIELDS = frozenset({
    "turnover_rate", "profit_yoy", "net_profit", "earnings_per_share", "total_assets",
    "book_equity", "market_cap", "limit_up_price", "limit_down_price", "institutional_net_buy",
})
TEXT_RESEARCH_FIELDS = frozenset({"limit_rule_version", "seat_identity_confidence"})
DATE_RESEARCH_FIELDS = frozenset({"announcement_at", "revision_at", "report_period_end", "effective_from", "effective_until"})
RESEARCH_FIELDS = NUMERIC_RESEARCH_FIELDS | TEXT_RESEARCH_FIELDS
IDENTITY_FIELDS = frozenset({"instrument", "timestamp", "available_at", "market", "exchange"})
_CURRENCIES = frozenset({"CNY", "USD", "HKD", "EUR", "JPY", "GBP"})
_UNIT_RULES = {
    "turnover_rate": frozenset({"ratio_free_float", "percent_free_float", "ratio_total_shares", "percent_total_shares"}),
    "profit_yoy": frozenset({"ratio", "percent"}),
    "earnings_per_share": frozenset(f"{currency}_per_share" for currency in _CURRENCIES),
    **{name: _CURRENCIES for name in ("net_profit", "total_assets", "book_equity", "market_cap", "institutional_net_buy", "amount")},
    "limit_up_price": _CURRENCIES, "limit_down_price": _CURRENCIES,
    "limit_rule_version": frozenset({"rule_version"}),
    "seat_identity_confidence": frozenset({"declared_confidence"}),
    "volume": frozenset({"shares"}), "industry": frozenset({"historical_classification"}),
}
MAX_FIELD_BYTES = 10 * 1024 * 1024


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def field_time(value):
    if not isinstance(value, (str, pd.Timestamp)):
        raise TypeError("research time must be an ISO timestamp")
    try:
        stamp = pd.Timestamp(value)
        if pd.isna(stamp):
            raise ValueError("missing timestamp")
        return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("research timestamp is invalid") from exc


def _label(value, *, maximum=128):
    from finahinking.research.contracts import _unsafe_public_text

    if not isinstance(value, str) or not value.strip() or len(value) > maximum or _unsafe_public_text(value):
        raise ValueError("research metadata must be bounded, non-sensitive text")
    return value.strip()


@dataclass(frozen=True, slots=True, init=False, repr=False)
class ResearchFieldAttachment:
    _json: str

    def __init__(self, snapshot_ref, market, units, records, source_declaration="User-supplied research fields"):
        if not isinstance(snapshot_ref, str) or not re.fullmatch(r"[0-9a-f]{64}", snapshot_ref):
            raise ValueError("research fields require a snapshot digest")
        market = _label(market).upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_.-]{0,31}", market):
            raise ValueError("research market is invalid")
        if not isinstance(units, Mapping) or not units or len(units) > 24:
            raise ValueError("research fields require explicit units")
        units = dict(units)
        if any(name not in _UNIT_RULES or not isinstance(unit, str) or unit not in _UNIT_RULES[name] for name, unit in units.items()):
            raise ValueError("research field units conflict with their definition")
        if not isinstance(records, (tuple, list)) or len(records) > 10000:
            raise ValueError("research records must be a bounded array")
        normalized, seen = [], set()
        for record in records:
            if not isinstance(record, Mapping) or set(record) - (IDENTITY_FIELDS | RESEARCH_FIELDS | DATE_RESEARCH_FIELDS):
                raise ValueError("unsupported research record fields or native snapshot overwrite")
            if not {"instrument", "timestamp", "available_at"} <= set(record):
                raise ValueError("research record identity and availability are required")
            item = {"instrument": _label(record["instrument"]),
                    "timestamp": field_time(record["timestamp"]).isoformat(),
                    "available_at": field_time(record["available_at"]).isoformat()}
            if not re.fullmatch(r"[A-Za-z0-9._:/-]{1,64}", item["instrument"]):
                raise ValueError("research instrument is invalid")
            for name, value in record.items():
                if name in {"instrument", "timestamp", "available_at"}:
                    continue
                if name in DATE_RESEARCH_FIELDS:
                    item[name] = None if value is None else field_time(value).isoformat()
                elif name in {"market", "exchange"}:
                    item[name] = _label(value).upper()
                    if name == "market" and market != "MULTI" and item[name] != market:
                        raise ValueError("record market conflicts with its attachment")
                elif name in TEXT_RESEARCH_FIELDS:
                    item[name] = _label(value)
                else:
                    if name not in units or isinstance(value, bool) or (value is not None and not isinstance(value, (int, float))):
                        raise ValueError("research value requires a numeric unit binding")
                    if value is not None and (not math.isfinite(value) or (name == "turnover_rate" and value < 0)):
                        raise ValueError("research value is invalid")
                    item[name] = None if value is None else float(value)
            if any(name not in units for name in set(item) & TEXT_RESEARCH_FIELDS):
                raise ValueError("research labels require explicit semantics")
            key = (item["instrument"], item["timestamp"], item["available_at"])
            if key in seen:
                raise ValueError("ambiguous duplicate research revision")
            seen.add(key)
            normalized.append(item)
        normalized.sort(key=lambda item: (item["instrument"], item["timestamp"], item["available_at"]))
        encoded = _json({"schema_version": "research-fields.v1", "snapshot_ref": snapshot_ref,
                         "market": market, "units": units, "records": normalized,
                         "source_declaration": _label(source_declaration, maximum=500)})
        if len(encoded.encode()) > MAX_FIELD_BYTES - 128:
            raise ValueError("research attachment exceeds byte limit")
        object.__setattr__(self, "_json", encoded)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {
            "schema_version", "snapshot_ref", "market", "units", "records", "source_declaration", "fingerprint",
        } or value["schema_version"] != "research-fields.v1":
            raise ValueError("research attachment schema is invalid")
        result = cls(**{name: item for name, item in value.items() if name not in {"schema_version", "fingerprint"}})
        if result.fingerprint != value["fingerprint"]:
            raise ValueError("research attachment integrity mismatch")
        return result

    def to_dict(self):
        return {**json.loads(self._json), "fingerprint": self.fingerprint}

    @property
    def fingerprint(self):
        return hashlib.sha256(self._json.encode()).hexdigest()

    @property
    def snapshot_ref(self):
        return json.loads(self._json)["snapshot_ref"]

    @property
    def market(self):
        return json.loads(self._json)["market"]

    @property
    def units(self):
        return json.loads(self._json)["units"]

    @property
    def records(self):
        return tuple(json.loads(self._json)["records"])

    @property
    def source_declaration(self):
        return json.loads(self._json)["source_declaration"]


class ResearchFieldStore:
    def __init__(self, snapshot_store):
        self.snapshots = snapshot_store
        self.root = snapshot_store.root / "research-fields"
        if self.root.is_symlink():
            raise ValueError("research field directory is invalid")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, fingerprint):
        if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise ValueError("research field fingerprint is invalid")
        return self.root / f"{fingerprint}.json"

    def _bound_snapshot(self, attachment):
        try:
            snapshot = self.snapshots.load(attachment.snapshot_ref)
        except (OSError, ValueError) as exc:
            raise ValueError("research fields are not bound to an available snapshot") from exc
        instruments = set(snapshot.instruments)
        if any(item["instrument"] not in instruments for item in attachment.records):
            raise ValueError("research field instrument is outside its snapshot")

    def save(self, attachment):
        if not isinstance(attachment, ResearchFieldAttachment):
            raise TypeError("typed research attachment required")
        self._bound_snapshot(attachment)
        destination = self._path(attachment.fingerprint)
        if destination.exists() or destination.is_symlink():
            self.load(attachment.fingerprint)
            return attachment.fingerprint
        descriptor, name = tempfile.mkstemp(prefix=".research-field-", dir=self.root)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(_json(attachment.to_dict()).encode())
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                self.load(attachment.fingerprint)
        finally:
            temporary.unlink(missing_ok=True)
        return attachment.fingerprint

    def load(self, fingerprint):
        path = self._path(fingerprint)
        if path.is_symlink() or self.root.is_symlink():
            raise ValueError("research field path is invalid")
        try:
            with path.open("rb") as handle:
                encoded = handle.read(MAX_FIELD_BYTES + 1)
            if len(encoded) > MAX_FIELD_BYTES:
                raise ValueError("research field size limit exceeded")
            result = ResearchFieldAttachment.from_dict(json.loads(encoded))
            if result.fingerprint != fingerprint:
                raise ValueError("research field reference integrity mismatch")
            self._bound_snapshot(result)
            return result
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            raise ValueError("research field artifact is unavailable or invalid") from exc

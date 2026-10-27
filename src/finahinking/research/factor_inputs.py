"""Asset-local, point-in-time input panels from original snapshot + attachment."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import pandas as pd

from finahinking.data.research_fields import RESEARCH_FIELDS, ResearchFieldAttachment, field_time
from finahinking.p8_2.contracts import DatasetSnapshot

from .contracts import stable_digest

_DAILY_FIELDS = {"turnover_rate", "market_cap", "limit_up_price", "limit_down_price", "institutional_net_buy", "seat_identity_confidence"}


@dataclass(frozen=True, slots=True)
class UniverseSpec:
    market: str
    instruments: tuple[str, ...]
    membership_policy: str = "STATIC_USER_DECLARED"

    def __post_init__(self):
        if not isinstance(self.market, str) or not re.fullmatch(r"[A-Z][A-Z0-9_.-]{0,31}", self.market):
            raise ValueError("universe market must be explicit")
        if (not isinstance(self.instruments, (tuple, list)) or not 1 <= len(self.instruments) <= 100
                or any(not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9._:/-]{1,64}", item)
                       for item in self.instruments) or len(set(self.instruments)) != len(self.instruments)):
            raise ValueError("universe requires unique complete instrument identities")
        if self.membership_policy != "STATIC_USER_DECLARED":
            raise ValueError("unsupported universe membership policy")
        object.__setattr__(self, "instruments", tuple(sorted(self.instruments)))

    def to_dict(self):
        return {"market": self.market, "instruments": list(self.instruments),
                "membership_policy": self.membership_policy, "survivorship": "NOT_VERIFIED"}

    @property
    def fingerprint(self):
        return stable_digest(self.to_dict())


@dataclass(frozen=True, slots=True, repr=False)
class FactorPanel:
    _json: str

    def to_dict(self):
        return json.loads(self._json)

    @property
    def fingerprint(self):
        return stable_digest(self.to_dict())

    @property
    def snapshot_ref(self):
        return self.to_dict()["snapshot_ref"]

    @property
    def attachment_ref(self):
        return self.to_dict()["attachment_ref"]

    @property
    def units(self):
        return self.to_dict()["units"]

    @property
    def issues(self):
        return tuple(self.to_dict()["issues"])

    @property
    def status(self):
        payload = self.to_dict()
        return "BLOCKED_DATA" if not payload["rows"] or payload["issues"] else "READY"

    def to_frame(self):
        rows = self.to_dict()["rows"]
        frame = pd.DataFrame(rows)
        if frame.empty:
            return pd.DataFrame(columns=["asset", "timestamp", "close", "return_1d"])
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        return frame.set_index(["asset", "timestamp"], drop=False)

    def missing_inputs(self, fields):
        frame = self.to_frame()
        return tuple(sorted(name for name in fields if name not in frame or not frame[name].notna().any()))


def _boundary(value):
    result = field_time(value)
    return result + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1) if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else result


class _FieldCursor:
    """Advance each asset's vintages once; daily fields never forward-fill."""

    def __init__(self, records):
        self.events = []
        for sequence, item in enumerate(records):
            period, known = field_time(item["timestamp"]), field_time(item["available_at"])
            effective = field_time(item["effective_from"]) if item.get("effective_from") else period
            self.events.append((max(period, known, effective), period, known, sequence, item))
        self.events.sort(key=lambda event: event[:4])
        self.position, self.daily, self.latest = 0, {}, {}

    def at(self, timestamp):
        while self.position < len(self.events) and self.events[self.position][0] <= timestamp:
            _, period, known, _, item = self.events[self.position]
            for name in RESEARCH_FIELDS & set(item):
                target = self.daily.setdefault(period.normalize(), {}) if name in _DAILY_FIELDS else self.latest
                if name not in target or (period, known) >= target[name][0]:
                    target[name] = ((period, known), item)
            self.position += 1
        selected = {**self.latest, **self.daily.get(timestamp.normalize(), {})}
        return {name: item for name, (_, item) in selected.items()
                if not item.get("effective_until") or timestamp < field_time(item["effective_until"])}


def build_factor_panel(snapshot, attachment, *, as_of, universe) -> FactorPanel:
    if not isinstance(snapshot, DatasetSnapshot) or not isinstance(universe, UniverseSpec):
        raise TypeError("typed snapshot and universe required")
    if not set(universe.instruments) <= set(snapshot.instruments):
        raise ValueError("universe instrument is outside the snapshot")
    if attachment is not None and (not isinstance(attachment, ResearchFieldAttachment)
            or attachment.snapshot_ref != snapshot.fingerprint or attachment.market != universe.market):
        raise ValueError("research attachment has the wrong snapshot or market")
    boundary = _boundary(as_of)
    source_records = () if attachment is None else attachment.records
    instruments = set(snapshot.instruments)
    if any(item["instrument"] not in instruments for item in source_records):
        raise ValueError("research field instrument is outside the snapshot")
    units = {} if attachment is None else attachment.units
    original_units = dict(units)
    for name, unit in original_units.items():
        if unit.startswith("percent"):
            units[name] = unit.replace("percent", "ratio", 1)
    grouped = {asset: [] for asset in universe.instruments}
    for record in source_records:
        if record["instrument"] in grouped:
            grouped[record["instrument"]].append(record)
    cursors = {asset: _FieldCursor(records) for asset, records in grouped.items()}
    issues, rows, previous, dates = [], [], {}, set()
    for item in snapshot.observations:
        if item.instrument not in grouped or item.timestamp > boundary:
            continue
        market_prefix = item.instrument.split(":", 1)[0]
        if (market_prefix in {"CN", "US", "HK", "JP", "GB", "EU"}
                and universe.market not in {"MULTI", "UNSPECIFIED", market_prefix}):
            raise ValueError("instrument market conflicts with the declared universe")
        if item.available_at is None or item.available_at > item.timestamp:
            issues.append("UNKNOWN_OR_DELAYED_BAR_AVAILABILITY")
            continue
        if item.available_at > boundary:
            continue
        day = (item.instrument, item.timestamp.date())
        if day in dates:
            raise ValueError("factor panel requires low-frequency observations")
        dates.add(day)
        row = {"asset": item.instrument, "timestamp": item.timestamp.isoformat(),
               **{name: getattr(item, name) for name in ("open", "high", "low", "close", "volume", "amount", "industry", "suspended")},
               "available_at": item.available_at.isoformat(),
               "return_1d": None if item.instrument not in previous else item.close / previous[item.instrument] - 1,
               "field_provenance": {}}
        previous[item.instrument] = item.close
        visible = cursors[item.instrument].at(item.timestamp)
        for name in RESEARCH_FIELDS & set(units):
            record = visible.get(name)
            value = None if record is None else record[name]
            if value is not None and original_units[name].startswith("percent"):
                value /= 100
            if name == "industry" and value is not None and item.industry is not None and value != item.industry:
                issues.append("CONFLICTING_HISTORICAL_INDUSTRY")
                value = None
            row[name] = value
            if record is not None:
                row["field_provenance"][name] = {key: value for key, value in record.items()
                    if key not in RESEARCH_FIELDS and key != "instrument"}
        rows.append(row)
    # A contemporaneous cross-section is computed once before splitting assets.
    # No present-day classification or future date is used as a substitute.
    groups = {}
    for row in rows:
        provenance = row["field_provenance"].get("industry", {})
        row["industry_relative_return"] = None
        if (row.get("industry") and row["return_1d"] is not None and provenance.get("effective_from")
                and field_time(provenance["effective_from"]) <= field_time(row["timestamp"])):
            groups.setdefault((row["timestamp"], row["industry"]), []).append(row)
    for group in groups.values():
        if len(group) >= 2:
            mean = sum(row["return_1d"] for row in group) / len(group)
            for row in group:
                row["industry_relative_return"] = row["return_1d"] - mean
    if len(rows) > 10000:
        raise ValueError("factor panel exceeds the bounded row limit")
    payload = {"schema_version": "factor-panel.v1", "snapshot_ref": snapshot.fingerprint,
               "attachment_ref": None if attachment is None else attachment.fingerprint,
               "as_of": boundary.isoformat(), "universe": universe.to_dict(), "data_mode": snapshot.mode,
               "units": units, "original_units": original_units, "rows": rows, "issues": sorted(set(issues)),
               "limitations": ["Static user-declared universe; survivorship is not independently verified."]}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode()) > 10 * 1024 * 1024:
        raise ValueError("factor panel exceeds the bounded byte limit")
    return FactorPanel(encoded)

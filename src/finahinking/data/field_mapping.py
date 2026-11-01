"""Deterministic, script-free field mapping and record normalization."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from .panel_contracts import FIELD_DEFINITIONS, panel_time
from .research_fields import DATE_RESEARCH_FIELDS, NUMERIC_RESEARCH_FIELDS, TEXT_RESEARCH_FIELDS
from .user_api_contracts import DataBatch, fingerprint_records

_NUMERIC_FIELDS = frozenset({"open", "high", "low", "close", "volume", "amount", "value"}) | NUMERIC_RESEARCH_FIELDS
_PANEL_NUMERIC = frozenset(name for name, spec in FIELD_DEFINITIONS.items() if spec[1] == "number")
_PANEL_BOOLEAN = frozenset(name for name, spec in FIELD_DEFINITIONS.items() if spec[1] == "boolean")
_ALLOWED_FIELDS = frozenset({"instrument", "timestamp", "available_at", "as_of", "industry", "suspended", "market", "exchange", *_NUMERIC_FIELDS}) | TEXT_RESEARCH_FIELDS | DATE_RESEARCH_FIELDS


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, (str, datetime)):
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(result):
        return None
    return int(result) if result.is_integer() else result


def normalize_records(
    records: Sequence[Mapping[str, Any]],
    field_mapping: Mapping[str, str],
    dataset_kind: str,
    *,
    connection_id: str = "user-data",
    source_declaration: str = "User-declared data source; Finathink has not independently verified it.",
    retrieved_at: datetime | None = None,
    as_of: str | datetime | None = None,
) -> DataBatch:
    """Map user fields into the stable Finathink record vocabulary.

    Values are copied and converted through a small allow-list.  User supplied
    Python, SQL, or expression strings are never evaluated.
    """

    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise TypeError("records must be a sequence of objects")
    mapping = dict(field_mapping)
    allowed = _ALLOWED_FIELDS | frozenset(FIELD_DEFINITIONS) if dataset_kind == "panel_fields" else _ALLOWED_FIELDS
    if any(key not in allowed for key in mapping):
        raise ValueError("field mapping contains an unsupported canonical field")
    if any(not isinstance(value, str) or not value or value.startswith("$") for value in mapping.values()):
        raise ValueError("field mapping source names must be plain fields")
    quality: list[str] = []
    normalized: list[dict[str, object]] = []
    seen: set[tuple[str, ...]] = set()
    as_of_value = _timestamp(as_of) if as_of is not None else None
    for index, raw in enumerate(records):
        if not isinstance(raw, Mapping):
            quality.append(f"record {index}: record is not an object")
            continue
        item: dict[str, object] = {}
        for canonical, source in mapping.items():
            if source not in raw:
                quality.append(f"record {index}: missing {canonical}")
                continue
            value = raw[source]
            if canonical in {"timestamp", "available_at", "as_of"} | DATE_RESEARCH_FIELDS:
                unknown_available = dataset_kind == "panel_fields" and canonical == "available_at" and value is None
                converted = (None if unknown_available else panel_time(value).isoformat()) if dataset_kind == "panel_fields" else _timestamp(value)
                if converted is None and not unknown_available:
                    quality.append(f"record {index}: invalid {canonical}")
                    continue
            elif canonical in _PANEL_BOOLEAN:
                if not isinstance(value, bool):
                    raise ValueError("suspended mapping requires an explicit boolean")
                converted = value
            elif canonical in _NUMERIC_FIELDS or (dataset_kind == "panel_fields" and canonical in _PANEL_NUMERIC):
                converted = _number(value)
                if converted is None:
                    quality.append(f"record {index}: invalid {canonical}")
                    continue
            else:
                converted = str(value).strip() if value is not None else ""
                if not converted:
                    quality.append(f"record {index}: missing {canonical}")
                    continue
            item[canonical] = converted
        if "instrument" not in item:
            quality.append(f"record {index}: missing instrument")
        if "timestamp" not in item:
            quality.append(f"record {index}: missing timestamp")
        if dataset_kind == "prices" and "close" not in item:
            quality.append(f"record {index}: missing close")
        if "instrument" in item and "timestamp" in item:
            key = (str(item["instrument"]), str(item["timestamp"]))
            if dataset_kind in {"research_fields", "panel_fields"}:
                key = (*key, str(item.get("available_at", "")))
            if key in seen:
                quality.append(f"record {index}: duplicate instrument/timestamp")
            seen.add(key)
        normalized.append(item)
    available_values = [item.get("available_at") for item in normalized]
    future_available = bool(
        as_of_value is not None
        and any(isinstance(value, str) and value > as_of_value for value in available_values)
    )
    if future_available:
        quality.append("available_at is later than request as_of; PIT is unavailable")
        pit = "UNAVAILABLE"
    elif available_values and all(isinstance(value, str) for value in available_values) and len(available_values) == len(normalized):
        pit = "AVAILABLE"
    else:
        pit = "UNKNOWN"
    timestamp = retrieved_at or datetime.now(UTC)
    return DataBatch(
        records=tuple(normalized),
        connection_id=connection_id,
        retrieved_at=timestamp,
        source_declaration=source_declaration,
        data_fingerprint=fingerprint_records(normalized),
        quality_issues=tuple(dict.fromkeys(quality)),
        pit_available=pit,
    )


__all__ = ["normalize_records"]

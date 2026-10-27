"""Deterministic, paper-only performance and parameter robustness analytics.

The functions in this module consume normalized mappings or ``PaperLedger``
objects and return server-owned snapshots.  No browser code is expected to
recalculate any metric.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


def _finite(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _plain(value: Any) -> Any:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _plain(value.to_dict())
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ParameterSurface:
    points: tuple[Mapping[str, Any], ...] = ()
    parameter_names: tuple[str, ...] = ()
    robustness_regions: tuple[Mapping[str, Any], ...] = ()
    chart_data: Mapping[str, Any] = field(default_factory=dict)
    table_data: tuple[Mapping[str, Any], ...] = ()
    limitations: tuple[str, ...] = ()
    fingerprint: str = ""
    criteria: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        payload = {"points": self.points, "parameter_names": self.parameter_names, "regions": self.robustness_regions, "limitations": self.limitations, "criteria": self.criteria}
        if not self.fingerprint:
            object.__setattr__(self, "fingerprint", _digest(payload))

    def to_dict(self) -> dict[str, Any]:
        return {"points": list(self.points), "parameter_names": list(self.parameter_names), "robustness_regions": list(self.robustness_regions), "chart_data": dict(self.chart_data), "table_data": list(self.table_data), "limitations": list(self.limitations), "criteria": dict(self.criteria), "fingerprint": self.fingerprint, "paper_only": True}


@dataclass(frozen=True, slots=True)
class PerformanceReport:
    metrics: Mapping[str, float | None] = field(default_factory=dict)
    series: tuple[Mapping[str, Any], ...] = ()
    benchmark: Mapping[str, Any] | None = None
    chart_data: Mapping[str, Any] = field(default_factory=dict)
    table_data: tuple[Mapping[str, Any], ...] = ()
    limitations: tuple[str, ...] = ()
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.fingerprint:
            object.__setattr__(self, "fingerprint", _digest({"metrics": self.metrics, "series": self.series, "benchmark": self.benchmark, "limitations": self.limitations}))

    def to_dict(self) -> dict[str, Any]:
        return {"metrics": dict(self.metrics), "series": list(self.series), "benchmark": self.benchmark, "chart_data": dict(self.chart_data), "table_data": list(self.table_data), "limitations": list(self.limitations), "fingerprint": self.fingerprint, "paper_only": True}


def compute_parameter_surface(results: Any, *, axes=None, min_metric=None, max_neighbor_delta=None, min_region_size=2) -> ParameterSurface:
    """Connected plateaus on a declared Cartesian grid, never input-order runs.

    A component needs at least two eligible vertices above the declared metric
    floor. Edges change one axis by one declared step and stay within the
    declared metric delta. Missing/failed grid points cannot bridge a plateau.
    This describes the tested grid only, not an interpolated parameter range.
    """
    if isinstance(results, Mapping):
        results = results.get("results", results.get("points", results.get("experiments", ())))
    if results is None:
        results = ()
    if isinstance(results, (str, bytes)) or not isinstance(results, Sequence):
        raise TypeError("results must be a sequence or mapping")
    if len(results) > 4096 or type(min_region_size) is not int or min_region_size < 2:
        raise ValueError("surface size or minimum region size is invalid")
    declared = axes is not None and min_metric is not None and max_neighbor_delta is not None
    grid = {}
    if declared:
        if not isinstance(axes, Mapping) or not axes or any(not isinstance(key, str) or not key for key in axes):
            raise ValueError("parameter axes must be declared")
        for key, values in sorted(axes.items()):
            if not isinstance(values, (list, tuple)) or not values or len(values) > 256 or any(isinstance(value, bool) or not isinstance(value, (str, int, float)) or isinstance(value, (int, float)) and _finite(value) is None for value in values):
                raise ValueError("parameter axis is invalid")
            if not (all(isinstance(value, str) for value in values) or all(isinstance(value, (int, float)) for value in values)) or len(set(values)) != len(values):
                raise ValueError("parameter axis contains duplicate or mixed values")
            grid[key] = sorted(values)
        if isinstance(min_metric, bool) or isinstance(max_neighbor_delta, bool) or _finite(min_metric) is None or _finite(max_neighbor_delta) is None or float(max_neighbor_delta) < 0:
            raise ValueError("surface thresholds must be finite")
    criteria = {"axes": grid, "min_metric": min_metric, "max_neighbor_delta": max_neighbor_delta, "min_region_size": min_region_size, "adjacency": "one declared Cartesian axis step"} if declared else {}
    points: list[dict[str, Any]] = []
    names: set[str] = set()
    limitations: list[str] = []
    for index, raw in enumerate(results):
        if not isinstance(raw, Mapping):
            limitations.append(f"point {index}: invalid record")
            continue
        params = raw.get("parameters", raw.get("params", {}))
        if not isinstance(params, Mapping):
            params = {}
            limitations.append(f"point {index}: missing parameters")
        names.update(str(k) for k in params)
        metric = _finite(raw.get("metric", raw.get("score", raw.get("return", raw.get("sharpe")))))
        status = str(raw.get("status", "")).upper()
        valid = not status.startswith(("INVALID", "MISSING", "FAILED")) and metric is not None
        explicit_flag = raw.get("robust", raw.get("passed", None))
        if explicit_flag is not None and not isinstance(explicit_flag, bool):
            limitations.append("invalid eligibility flag; point excluded")
        if metric is None:
            limitations.append(f"point {index}: missing or invalid metric")
        if declared and (set(params) != set(grid) or any(params[key] not in grid[key] for key in grid)):
            raise ValueError("point does not belong to declared parameter axes")
        eligible = declared and valid and (explicit_flag is None or explicit_flag is True) and metric >= float(min_metric)
        point = {"parameters": dict(params), "metric": metric, "valid": valid, "eligible": eligible, "robust": False, "status": status or ("VALID" if valid else "INVALID")}
        for key in ("returns", "volatility", "max_drawdown", "turnover", "costs", "slippage", "fingerprint"):
            if key in raw:
                point[key] = raw[key]
        points.append(point)
    points.sort(key=lambda point: tuple(grid[name].index(point["parameters"][name]) for name in grid) if declared else json.dumps(point["parameters"], sort_keys=True))
    for index, point in enumerate(points):
        point["index"] = index
    identities = [json.dumps(point["parameters"], sort_keys=True) for point in points]
    if len(set(identities)) != len(identities):
        raise ValueError("duplicate parameter point")
    regions: list[dict[str, Any]] = []
    remaining = {index for index, point in enumerate(points) if point["eligible"]}
    coordinates = {index: tuple(grid[name].index(points[index]["parameters"][name]) for name in grid) for index in remaining}
    while remaining:
        pending = [min(remaining)]
        component = []
        remaining.remove(pending[0])
        while pending:
            index = pending.pop()
            component.append(index)
            neighbors = [other for other in sorted(remaining) if sum(abs(a - b) for a, b in zip(coordinates[index], coordinates[other])) == 1 and abs(points[index]["metric"] - points[other]["metric"]) <= float(max_neighbor_delta)]
            remaining.difference_update(neighbors)
            pending.extend(neighbors)
        if len(component) >= min_region_size:
            component.sort()
            for index in component:
                points[index]["robust"] = True
            regions.append({"indices": component, "start_index": component[0], "end_index": component[-1], "size": len(component), "parameter_bounds": {name: [min(points[index]["parameters"][name] for index in component), max(points[index]["parameters"][name] for index in component)] for name in sorted(names)}})
    if not declared:
        limitations.append("No declared grid and metric thresholds; finite metrics or flags do not establish robustness.")
    limitations.append("Plateaus describe tested grid adjacency only; not statistical significance or untested interpolation.")
    if not points:
        limitations.append("no parameter results were supplied")
    table = tuple({"index": p["index"], **p["parameters"], "metric": p["metric"], "status": p["status"], "robust": p["robust"]} for p in points)
    chart = {"x": [p["index"] for p in points], "y": [p["metric"] for p in points], "valid": [p["valid"] for p in points], "robust": [p["robust"] for p in points]}
    return ParameterSurface(tuple(points), tuple(sorted(names)), tuple(regions), chart, table, tuple(dict.fromkeys(limitations)), criteria=criteria)


def _entries(ledger: Any) -> list[Mapping[str, Any]]:
    raw = getattr(ledger, "entries", ledger)
    if isinstance(raw, Mapping):
        raw = raw.get("entries", ())
    if raw is None or isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise TypeError("ledger must contain a sequence of entries")
    if all(_finite(item) is not None for item in raw):
        return [{"timestamp": i, "equity": float(item)} for i, item in enumerate(raw)]
    return [dict(item) if isinstance(item, Mapping) else dict(item.to_dict()) for item in raw if isinstance(item, Mapping) or callable(getattr(item, "to_dict", None))]


def _returns_from_values(values: list[float | None], limitations: list[str]) -> list[float]:
    out: list[float] = []
    previous: float | None = None
    for value in values:
        if value is None or value <= 0:
            limitations.append("missing or invalid equity observations were omitted")
            previous = None
            continue
        if previous is not None:
            out.append(value / previous - 1.0)
        previous = value
    return out


def _aggregate_entries(entries: list[Mapping[str, Any]], limitations: list[str]) -> list[dict[str, Any]]:
    """Collapse fill rows into one portfolio observation per timestamp."""
    groups: dict[Any, list[Mapping[str, Any]]] = {}
    order: list[Any] = []
    for index, entry in enumerate(entries):
        timestamp = entry.get("timestamp", index)
        if timestamp not in groups:
            groups[timestamp] = []
            order.append(timestamp)
        groups[timestamp].append(entry)
    result: list[dict[str, Any]] = []
    for timestamp in order:
        rows = groups[timestamp]
        equity_values = [_finite(row.get("equity")) for row in rows]
        valid_equity = [value for value in equity_values if value is not None]
        if valid_equity and len(set(valid_equity)) > 1:
            limitations.append("conflicting equity values at one timestamp; latest value used")
        weights = [_finite(row.get("target_weight")) for row in rows]
        result.append({"timestamp": timestamp, "equity": valid_equity[-1] if valid_equity else None, "target_weight": sum(weights) if weights and all(value is not None for value in weights) else None})
    return result


def _cost_total(entries: list[Mapping[str, Any]], field: str, limitations: list[str]) -> float | None:
    values: list[float] = []
    invalid = False
    for entry in entries:
        if field not in entry:
            continue
        value = _finite(entry.get(field))
        if value is None or value < 0:
            invalid = True
        else:
            values.append(value)
    if invalid:
        limitations.append(f"invalid {field} observations were reported as unavailable")
        return None
    return sum(values)


def compute_performance_report(ledger: Any, benchmark: Any = None, *, annualization: int = 252) -> PerformanceReport:
    if type(annualization) is not int or not 1 <= annualization <= 366:
        raise ValueError("annualization must be an explicitly bounded observation scale")
    limitations: list[str] = ["paper-only historical analytics; no investment advice"]
    entries = _entries(ledger)
    aggregated_entries = _aggregate_entries(entries, limitations)
    direct_returns = getattr(ledger, "returns", None)
    if direct_returns is None and isinstance(ledger, Mapping):
        direct_returns = ledger.get("returns")
    if isinstance(direct_returns, Sequence) and not isinstance(direct_returns, (str, bytes)):
        returns = [value for value in (_finite(item) for item in direct_returns) if value is not None]
        if len(returns) != len(direct_returns):
            limitations.append("missing or invalid return observations were omitted")
    else:
        equities = [_finite(item.get("equity")) for item in aggregated_entries]
        returns = _returns_from_values(equities, limitations)
    mean = sum(returns) / len(returns) if returns else None
    volatility = (sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)) ** 0.5 * math.sqrt(annualization) if len(returns) > 1 and mean is not None else None
    cumulative = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in returns:
        cumulative *= 1 + value
        peak = max(peak, cumulative)
        max_drawdown = min(max_drawdown, cumulative / peak - 1)
    weights = [_finite(item.get("target_weight")) for item in aggregated_entries]
    turnover = sum(abs(weights[i] - weights[i - 1]) for i in range(1, len(weights)) if weights[i] is not None and weights[i - 1] is not None) if len(weights) > 1 else None
    fees = _cost_total(entries, "fees", limitations)
    slippage = _cost_total(entries, "slippage", limitations)
    if not entries:
        limitations.append("ledger has no usable entries")
    total_return = (cumulative - 1.0) if returns else None
    metrics: dict[str, float | None] = {"total_return": total_return, "return": total_return, "gross_return": total_return, "net_return": total_return, "volatility": volatility, "max_drawdown": max_drawdown if returns else None, "turnover": turnover, "costs": fees, "fees": fees, "slippage": slippage, "observations": float(len(returns))}
    benchmark_payload = None
    if benchmark is not None:
        b_direct = benchmark.get("returns") if isinstance(benchmark, Mapping) else getattr(benchmark, "returns", None)
        if isinstance(b_direct, Sequence) and not isinstance(b_direct, (str, bytes)):
            b_returns = []
            for value in b_direct:
                parsed = _finite(value)
                if parsed is None:
                    limitations.append("missing or invalid benchmark return observations were omitted")
                else:
                    b_returns.append(parsed)
        else:
            b_entries = _aggregate_entries(_entries(benchmark), limitations)
            b_returns = _returns_from_values([_finite(item.get("equity")) for item in b_entries], limitations)
        benchmark_payload = {"total_return": (math.prod(1 + v for v in b_returns) - 1) if b_returns else None, "observations": len(b_returns)}
        metrics["benchmark_total_return"] = benchmark_payload["total_return"]
        if not b_returns:
            limitations.append("benchmark supplied without usable observations")
    if isinstance(direct_returns, Sequence) and not isinstance(direct_returns, (str, bytes)):
        dated_returns = [_finite(value) for value in direct_returns]
        if len(dated_returns) == len(aggregated_entries) - 1:
            dated_returns = [None, *dated_returns]
        elif len(dated_returns) != len(aggregated_entries):
            limitations.append("direct returns do not align with dated equity entries; dated returns left unknown")
            dated_returns = [None] * len(aggregated_entries)
    else:
        dated_returns = []
        previous = None
        for item in aggregated_entries:
            current = _finite(item.get("equity"))
            dated_returns.append(current / previous - 1 if current is not None and current > 0 and previous is not None and previous > 0 else None)
            previous = current
    series = tuple({"timestamp": item.get("timestamp"), "equity": _finite(item.get("equity")), "return": dated_returns[i]} for i, item in enumerate(aggregated_entries))
    chart = {"timestamps": [item.get("timestamp") for item in series], "equity": [item.get("equity") for item in series], "returns": [item.get("return") for item in series]}
    table = tuple({"timestamp": item.get("timestamp"), "equity": item.get("equity"), "return": item.get("return")} for item in series)
    return PerformanceReport(metrics, series, benchmark_payload, chart, table, tuple(dict.fromkeys(limitations)))


__all__ = ["ParameterSurface", "PerformanceReport", "compute_parameter_surface", "compute_performance_report"]

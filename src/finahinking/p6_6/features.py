"""Deterministic feature registry and point-in-time feature graph evaluator."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .models import FeatureDefinition, FeatureGraph, FeatureNode, FeatureVersion


class PointInTimeViolation(ValueError):
    """Raised when a feature would use information unavailable at signal time."""


_PRIMITIVES = {
    "return",
    "rolling_mean",
    "rolling_volatility",
    "momentum",
    "cross_sectional_rank",
    "z_score",
    "threshold",
    "interaction",
}


def _as_frame(data: pd.DataFrame | pd.Series) -> pd.DataFrame:
    if isinstance(data, pd.Series):
        return data.to_frame("close")
    if not isinstance(data, pd.DataFrame):
        raise TypeError("feature input must be a pandas DataFrame or Series")
    frame = data.copy()
    if "date" in frame.columns:
        frame.index = pd.to_datetime(frame["date"], errors="raise")
    elif not isinstance(frame.index, pd.DatetimeIndex):
        frame.index = pd.to_datetime(frame.index)
    frame = frame.sort_index(kind="stable")
    if frame.index.has_duplicates and "asset" not in frame.columns:
        raise ValueError("feature input index contains duplicate timestamps")
    return frame


def _availability(frame: pd.DataFrame) -> pd.Series:
    if "available_at" not in frame.columns:
        return pd.Series(frame.index, index=frame.index, dtype="datetime64[ns]")
    values = pd.to_datetime(frame["available_at"], errors="coerce")
    if values.isna().any():
        raise ValueError("available_at contains an invalid timestamp")
    return values


def assert_point_in_time(frame: pd.DataFrame, signal_times: pd.Index | None = None) -> None:
    """Validate that every source row is observable at its signal timestamp."""

    source = _as_frame(frame)
    availability = _availability(source)
    signal_index = pd.DatetimeIndex(signal_times if signal_times is not None else source.index)
    if len(signal_index) != len(source):
        raise PointInTimeViolation("signal timestamps must align with source rows")
    bad = availability.to_numpy() > signal_index.to_numpy()
    if bool(np.any(bad)):
        first = int(np.flatnonzero(bad)[0])
        raise PointInTimeViolation(
            f"feature row at {signal_index[first].isoformat()} is unavailable until {availability.iloc[first].isoformat()}"
        )


def _series(frame: pd.DataFrame, field: str = "close") -> pd.Series:
    if field not in frame.columns:
        raise ValueError(f"feature input field is missing: {field}")
    return pd.to_numeric(frame[field], errors="coerce").astype(float)


def _apply_missing(values: pd.Series, policy: str) -> pd.Series:
    if policy in {"propagate", "drop"}:
        return values
    if policy == "zero":
        return values.fillna(0.0)
    if policy == "ffill":
        return values.ffill()
    raise ValueError(f"unsupported missing_policy: {policy}")


def _primitive(definition: FeatureDefinition, frame: pd.DataFrame, inputs: Mapping[str, pd.Series] | None = None) -> pd.Series:
    name = definition.category.lower()
    parameters = dict(definition.parameters)
    input_values = list((inputs or {}).values())
    base = input_values[0] if input_values else _series(frame, definition.input_fields[0] if definition.input_fields else "close")
    window = definition.window or int(parameters.get("window", 1))
    if window < 1 or window > 10_000:
        raise ValueError("feature window is outside the supported bounds")
    if name == "return" or name == "momentum":
        values = base.pct_change(periods=window, fill_method=None)
    elif name == "rolling_mean":
        values = base.rolling(window=window, min_periods=window).mean()
    elif name == "rolling_volatility":
        values = base.pct_change(fill_method=None).rolling(window=window, min_periods=window).std() * float(parameters.get("annualization", 252.0)) ** 0.5
    elif name == "cross_sectional_rank":
        if definition.cross_sectional_scope == "date":
            groups = frame["date"] if "date" in frame.columns else frame.index
            values = base.groupby(groups).rank(pct=True)
        elif "asset" in frame.columns:
            values = base.groupby(frame["asset"].to_numpy(), sort=False).transform(lambda group: group.expanding(min_periods=1).rank(pct=True))
        else:
            values = base.expanding(min_periods=1).rank(pct=True)
    elif name == "z_score":
        mean = base.rolling(window=window, min_periods=window).mean()
        std = base.rolling(window=window, min_periods=window).std()
        values = (base - mean) / std.replace(0.0, np.nan)
    elif name == "threshold":
        threshold = float(parameters.get("threshold", 0.0))
        operator = str(parameters.get("operator", "ge")).lower()
        if operator == "le":
            values = base.le(threshold).astype(float)
        elif operator == "ge":
            values = base.ge(threshold).astype(float)
        else:
            raise ValueError(f"unsupported threshold operator: {operator}")
    elif name == "interaction":
        if len(input_values) < 2:
            raise ValueError("interaction requires two feature inputs")
        values = input_values[0] * input_values[1]
    else:
        raise ValueError(f"unsupported feature primitive: {name}")
    if definition.lag:
        values = values.groupby(frame["asset"].to_numpy(), sort=False).shift(definition.lag) if "asset" in frame else values.shift(definition.lag)
    if definition.missing_policy == "ffill" and "asset" in frame:
        return values.groupby(frame["asset"].to_numpy(), sort=False).ffill().astype(float)
    return _apply_missing(values.astype(float), definition.missing_policy)


@dataclass(frozen=True)
class FeatureRegistry:
    """Small allow-listed registry; registration is explicit and deterministic."""

    definitions: tuple[FeatureVersion, ...] = ()

    def __post_init__(self) -> None:
        versions = tuple(self.definitions)
        if any(not isinstance(item, FeatureVersion) for item in versions):
            raise TypeError("registry definitions must be FeatureVersion objects")
        ids = [item.feature_id for item in versions]
        if len(ids) != len(set(ids)):
            raise ValueError("registry contains duplicate feature ids")
        object.__setattr__(self, "definitions", versions)

    def register(self, feature: FeatureVersion | FeatureDefinition) -> FeatureRegistry:
        version = feature if isinstance(feature, FeatureVersion) else FeatureVersion(feature)
        if version.definition.category not in _PRIMITIVES:
            raise ValueError(f"unsupported feature primitive: {version.definition.category}")
        if any(item.feature_id == version.feature_id for item in self.definitions):
            raise ValueError(f"feature is already registered: {version.feature_id}")
        return FeatureRegistry(self.definitions + (version,))

    def get(self, feature_id: str) -> FeatureVersion:
        for item in self.definitions:
            if item.feature_id == feature_id:
                return item
        raise KeyError(feature_id)

    def evaluate(self, feature: FeatureVersion | str, data: pd.DataFrame | pd.Series, inputs: Mapping[str, pd.Series] | None = None) -> pd.Series:
        version = self.get(feature) if isinstance(feature, str) else feature
        if version.definition.category not in _PRIMITIVES:
            raise ValueError(f"unsupported feature primitive: {version.definition.category}")
        frame = _as_frame(data)
        assert_point_in_time(frame)
        if "asset" in frame and version.definition.category != "cross_sectional_rank":
            result = pd.Series(float("nan"), index=frame.index)
            for positions in frame.groupby(frame["asset"].to_numpy(), sort=False).indices.values():
                selected_inputs = {name: values.iloc[positions] for name, values in (inputs or {}).items()}
                result.iloc[positions] = _primitive(version.definition, frame.iloc[positions], selected_inputs).to_numpy()
        else:
            result = _primitive(version.definition, frame, inputs)
        result.name = version.feature_id
        return result

    def evaluate_graph(self, graph: FeatureGraph, data: pd.DataFrame | pd.Series) -> dict[str, pd.Series]:
        frame = _as_frame(data)
        values: dict[str, pd.Series] = {}
        for node in graph.nodes:
            input_values = {input_id: values[input_id] for input_id in node.inputs}
            values[node.node_id] = self.evaluate(node.feature, frame, input_values)
        return {node_id: values[node_id] for node_id in graph.outputs}


def builtin_feature_registry() -> FeatureRegistry:
    """Return the P6.6 reference registry with no mutable global state."""

    specs = (
        FeatureDefinition("return_1d", "One-period return", "Price return", "return", "close.pct_change(1)", input_fields=("close",), lag=1, version="v1"),
        FeatureDefinition("momentum_20d", "Lagged 20-day momentum", "Trailing momentum", "momentum", "close.pct_change(20).shift(1)", input_fields=("close",), window=20, lag=1, version="v1"),
        FeatureDefinition("volatility_20d", "Lagged 20-day realized volatility", "Annualized trailing standard deviation", "rolling_volatility", "close.pct_change().rolling(20).std()*sqrt(252).shift(1)", input_fields=("close",), window=20, lag=1, version="v1"),
        FeatureDefinition("moving_average_20d", "Lagged 20-day moving average", "Trailing simple moving average", "rolling_mean", "close.rolling(20).mean().shift(1)", input_fields=("close",), window=20, lag=1, version="v1"),
        FeatureDefinition("threshold_low_volatility", "Low-volatility filter", "Boolean threshold", "threshold", "volatility <= threshold", parameters={"threshold": 0.60, "operator": "le"}, version="v1"),
        FeatureDefinition("rank_momentum", "Cross-sectional momentum rank", "Percentile rank", "cross_sectional_rank", "rank(momentum)", cross_sectional_scope="date", version="v1"),
    )
    registry = FeatureRegistry()
    for definition in specs:
        registry = registry.register(definition)
    return registry


def feature_graph_for_template(
    template: str,
    *,
    moving_average_window: int = 20,
    lookback: int = 20,
    volatility_window: int = 20,
) -> tuple[FeatureGraph, tuple[FeatureVersion, ...]]:
    registry = builtin_feature_registry()
    if template == "lagged_momentum_low_volatility":
        if any(isinstance(value, bool) or int(value) < 1 for value in (lookback, volatility_window)):
            raise ValueError("lookback and volatility_window must be positive integers")
        lookback = int(lookback)
        volatility_window = int(volatility_window)
        if lookback == 20:
            momentum = registry.get("momentum_20d")
        else:
            momentum = FeatureVersion(
                FeatureDefinition(
                    f"momentum_{lookback}d",
                    f"Lagged {lookback}-day momentum",
                    "Trailing momentum",
                    "momentum",
                    f"close.pct_change({lookback}).shift(1)",
                    input_fields=("close",),
                    window=lookback,
                    lag=1,
                    version="v1",
                )
            )
        if volatility_window == 20:
            volatility = registry.get("volatility_20d")
        else:
            volatility = FeatureVersion(
                FeatureDefinition(
                    f"volatility_{volatility_window}d",
                    f"Lagged {volatility_window}-day realized volatility",
                    "Annualized trailing standard deviation",
                    "rolling_volatility",
                    f"close.pct_change().rolling({volatility_window}).std()*sqrt(252).shift(1)",
                    input_fields=("close",),
                    window=volatility_window,
                    lag=1,
                    version="v1",
                )
            )
        threshold = registry.get("threshold_low_volatility")
        nodes = (
            FeatureNode("momentum", momentum, explanation="Lagged 20-day momentum avoids using the current close."),
            FeatureNode("volatility", volatility, explanation="Lagged realized volatility is measured from trailing returns."),
            FeatureNode("low_volatility", threshold, ("volatility",), "Keep only observations below the volatility threshold."),
            FeatureNode("momentum_rank", registry.get("rank_momentum"), ("momentum",), "Rank momentum within the available cross-section."),
        )
        return FeatureGraph(nodes, ("low_volatility", "momentum_rank")), (momentum, volatility, threshold, registry.get("rank_momentum"))
    if template == "moving_average_trend":
        if isinstance(moving_average_window, bool) or int(moving_average_window) < 1:
            raise ValueError("moving_average_window must be a positive integer")
        moving_average_window = int(moving_average_window)
        if moving_average_window == 20:
            moving_average = registry.get("moving_average_20d")
        else:
            moving_average = FeatureVersion(
                FeatureDefinition(
                    f"moving_average_{moving_average_window}d",
                    f"Lagged {moving_average_window}-day moving average",
                    "Trailing simple moving average",
                    "rolling_mean",
                    f"close.rolling({moving_average_window}).mean().shift(1)",
                    input_fields=("close",),
                    window=moving_average_window,
                    lag=1,
                    version="v1",
                )
            )
        return FeatureGraph((FeatureNode("moving_average", moving_average, explanation="Lagged moving average is available before the next trade."),), ("moving_average",)), (moving_average,)
    raise ValueError(f"unsupported strategy template: {template}")


__all__ = ["FeatureRegistry", "PointInTimeViolation", "assert_point_in_time", "builtin_feature_registry", "feature_graph_for_template"]

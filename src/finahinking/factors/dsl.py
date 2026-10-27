"""Small, allow-listed factor expression language.

The parser is intentionally not a Python evaluator. It accepts only names,
numbers and registered function calls, then interprets the resulting tree with
deterministic pandas operations.
"""

from __future__ import annotations

import ast
import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd

_RETURN_ALIAS = re.compile(r"\breturn\s*\(")
_OP_ALIASES = {"ret": "return", "return": "return"}
_DEFAULT_OPERATORS = {
    "return",
    "lag",
    "rolling_mean",
    "rolling_std",
    "zscore",
    "rank",
    "winsorize",
    "combine",
    "negate",
    "abs",
    "divide",
}
_KNOWN_OPERATORS = _DEFAULT_OPERATORS | {"ret"}
_MAX_WINDOW = 252


def _canonical_number(value: float) -> str:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError("numeric literals must be finite")
    if numeric.is_integer():
        return str(int(numeric))
    return format(numeric, ".12g")


def _canonical(node: ast.AST, fields: set[str], operators: set[str]) -> str:
    if isinstance(node, ast.Name):
        if node.id in _OP_ALIASES:
            raise ValueError("operator names must be called")
        fields.add(node.id)
        return node.id
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return _canonical_number(node.value)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.keywords:
            raise ValueError("keyword arguments are not supported")
        operator = _OP_ALIASES.get(node.func.id, node.func.id if node.func.id in _KNOWN_OPERATORS else None)
        if operator is None:
            raise ValueError(f"unknown factor operator: {node.func.id}")
        if (operator == "abs" and len(node.args) != 1) or (operator == "divide" and len(node.args) != 2):
            raise ValueError("invalid bounded primitive argument count")
        if operator in {"return", "lag", "rolling_mean", "rolling_std", "zscore"}:
            if len(node.args) != 2:
                raise ValueError(f"{operator} requires a series and a window")
            window = node.args[1]
            if not isinstance(window, ast.Constant) or isinstance(window.value, bool) or not isinstance(window.value, (int, float)):
                raise ValueError("window must be a numeric literal")
            if int(window.value) != window.value or not 1 <= int(window.value) <= _MAX_WINDOW:
                raise ValueError("window is outside the bounded factor range")
        operators.add(operator)
        return f"{operator}({','.join(_canonical(arg, fields, operators) for arg in node.args)})"
    raise ValueError("factor expression contains an unsupported syntax node")


@dataclass(frozen=True, slots=True)
class FactorExpression:
    expression: str
    fields: tuple[str, ...]
    operators: tuple[str, ...]
    fingerprint: str


def parse_factor_expression(
    text: str,
    allowed_fields: Sequence[str],
    allowed_operators: Sequence[str] | None = None,
) -> FactorExpression:
    if not isinstance(text, str) or not text.strip() or len(text) > 512:
        raise ValueError("factor expression must be a bounded non-empty string")
    fields_allowed = {str(field).strip() for field in allowed_fields if str(field).strip()}
    operators_allowed = {_OP_ALIASES.get(str(item), str(item)) for item in (allowed_operators or _DEFAULT_OPERATORS)}
    source = _RETURN_ALIAS.sub("ret(", text.strip())
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise ValueError("factor expression syntax is invalid") from exc
    fields: set[str] = set()
    operators: set[str] = set()
    expression = _canonical(tree.body, fields, operators)
    unknown_fields = fields - fields_allowed
    if unknown_fields:
        raise ValueError(f"unknown factor field: {min(unknown_fields)}")
    unknown_operators = operators - operators_allowed
    if unknown_operators:
        raise ValueError(f"operator is not allowed: {min(unknown_operators)}")
    if not fields:
        raise ValueError("factor expression must reference a field")
    fingerprint = hashlib.sha256(expression.encode("utf-8")).hexdigest()
    return FactorExpression(expression, tuple(sorted(fields)), tuple(sorted(operators)), fingerprint)


def _window(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise ValueError("window must be an integer")
    result = int(value)
    if result < 1 or result > _MAX_WINDOW:
        raise ValueError("window is outside the bounded factor range")
    return result


def _series(value: Any, *, name: str) -> pd.Series:
    if not isinstance(value, pd.Series):
        raise TypeError(f"{name} expects a series")
    result = value.astype(float)
    if not result.index.is_unique or not result.index.is_monotonic_increasing:
        raise ValueError("factor frame index must be unique and increasing")
    return result


def _history(series, frame, operation):
    return series.groupby(frame["asset"].to_numpy(), sort=False).transform(operation) if "asset" in frame else operation(series)


def _evaluate_node(node: ast.AST, frame: pd.DataFrame) -> pd.Series | float:
    if isinstance(node, ast.Name):
        if node.id not in frame.columns:
            raise ValueError(f"unknown factor field: {node.id}")
        return _series(frame[node.id], name=node.id)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.keywords:
        raise ValueError("factor expression contains unsupported syntax")
    operator = _OP_ALIASES.get(node.func.id, node.func.id if node.func.id in _KNOWN_OPERATORS else None)
    if operator is None:
        raise ValueError(f"unknown factor operator: {node.func.id}")
    args = [_evaluate_node(arg, frame) for arg in node.args]
    if operator == "return":
        if len(args) != 2 or not isinstance(args[0], pd.Series):
            raise ValueError("return(series, window) is required")
        return _history(args[0], frame, lambda values: values.pct_change(periods=_window(args[1]), fill_method=None))
    if operator == "lag":
        if len(args) != 2 or not isinstance(args[0], pd.Series):
            raise ValueError("lag(series, periods) is required")
        return _history(args[0], frame, lambda values: values.shift(_window(args[1])))
    if operator in {"rolling_mean", "rolling_std", "zscore"}:
        if len(args) != 2 or not isinstance(args[0], pd.Series):
            raise ValueError(f"{operator}(series, window) is required")
        window = _window(args[1])
        mean = _history(args[0], frame, lambda values: values.rolling(window, min_periods=window).mean())
        if operator == "rolling_mean":
            return mean
        std = _history(args[0], frame, lambda values: values.rolling(window, min_periods=window).std(ddof=0))
        if operator == "rolling_std":
            return std
        return (args[0] - mean) / std.replace(0.0, float("nan"))
    if operator == "rank":
        if len(args) != 1 or not isinstance(args[0], pd.Series):
            raise ValueError("rank(series) is required")
        # Single-instrument DSL: rank the current observation against only
        # its historical prefix, never against future observations.
        return _history(args[0], frame, lambda values: values.expanding(min_periods=1).rank(method="average", pct=True))
    if operator == "winsorize":
        if len(args) != 3 or not isinstance(args[0], pd.Series):
            raise ValueError("winsorize(series, lower, upper) is required")
        lower, upper = float(args[1]), float(args[2])
        if not 0 <= lower < upper <= 1:
            raise ValueError("winsorize bounds must satisfy 0 <= lower < upper <= 1")
        return _history(args[0], frame, lambda values: values.clip(
            values.expanding(min_periods=1).quantile(lower), values.expanding(min_periods=1).quantile(upper)))
    if operator == "combine":
        if len(args) < 2 or not all(isinstance(item, pd.Series) for item in args):
            raise ValueError("combine requires at least two series")
        return pd.concat(args, axis=1).mean(axis=1, skipna=False)
    if operator == "negate":
        if len(args) != 1 or not isinstance(args[0], pd.Series):
            raise ValueError("negate(series) is required")
        return -args[0]
    if operator == "abs":
        if len(args) != 1 or not isinstance(args[0], pd.Series):
            raise ValueError("abs(series) is required")
        return args[0].abs()
    if operator == "divide":
        if len(args) != 2 or not any(isinstance(value, pd.Series) for value in args):
            raise ValueError("divide requires a series operand")
        denominator = args[1].replace(0.0, float("nan")) if isinstance(args[1], pd.Series) else (float("nan") if args[1] == 0 else args[1])
        return args[0] / denominator
    raise ValueError(f"operator is not implemented: {operator}")


def evaluate_expression(
    expression: str,
    frame: pd.DataFrame,
    *,
    as_of: Any | None = None,
    available_at: Mapping[str, Any] | None = None,
) -> pd.Series:
    """Evaluate a validated expression against a point-in-time frame."""

    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError("factor frame must be a non-empty DataFrame")
    if "asset" in frame:
        if frame["asset"].isna().any() or not all(isinstance(value, str) and value for value in frame["asset"]):
            raise ValueError("factor panel requires explicit asset identities")
        times = pd.DatetimeIndex(pd.to_datetime(frame.get("timestamp", frame.get("date", frame.index)), utc=True))
        if times.hasnans or pd.MultiIndex.from_arrays([frame["asset"], times]).has_duplicates:
            raise ValueError("factor panel has invalid or duplicate asset timestamps")
        if any(not times[positions].is_monotonic_increasing for positions in frame.groupby(frame["asset"].to_numpy(), sort=False).indices.values()):
            raise ValueError("factor panel history must be ordered within each asset")
        mask = times <= pd.Timestamp(as_of) if as_of is not None else [True] * len(frame)
        selected = frame.loc[mask]
        original_index = selected.index
        working = selected.reset_index(drop=True)
        if available_at:
            cutoff = pd.Timestamp(as_of) if as_of is not None else times.max()
            if any(field in working and pd.Timestamp(value) > cutoff for field, value in available_at.items()):
                raise ValueError("field availability is after as_of")
        parsed = parse_factor_expression(expression, tuple(str(column) for column in working.columns))
        result = _evaluate_node(ast.parse(_RETURN_ALIAS.sub("ret(", parsed.expression), mode="eval").body, working)
        if not isinstance(result, pd.Series):
            raise TypeError("factor expression must produce a series")
        return result.replace([float("inf"), float("-inf")], float("nan")).set_axis(original_index)
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError("factor frame index must be unique and increasing")
    bounded = frame
    if as_of is not None:
        cutoff = pd.Timestamp(as_of)
        if pd.isna(cutoff):
            raise ValueError("as_of is invalid")
        bounded = frame.loc[frame.index <= cutoff]
    if available_at:
        cutoff = pd.Timestamp(as_of) if as_of is not None else bounded.index.max()
        for field, value in available_at.items():
            if field in bounded.columns and pd.Timestamp(value) > cutoff:
                raise ValueError(f"field availability is after as_of: {field}")
    parsed = parse_factor_expression(expression, tuple(str(column) for column in bounded.columns))
    source = _RETURN_ALIAS.sub("ret(", parsed.expression)
    tree = ast.parse(source, mode="eval")
    result = _evaluate_node(tree.body, bounded)
    if not isinstance(result, pd.Series):
        raise TypeError("factor expression must produce a series")
    return result.replace([float("inf"), float("-inf")], float("nan"))

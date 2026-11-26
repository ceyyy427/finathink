"""Frozen rolling historical validation over a published strategy's audited inputs."""

from __future__ import annotations

import math
from collections import Counter

from finahinking.data.panel_contracts import PanelContract, panel_time
from finahinking.data.research_panel import ResearchPanel

from .contracts import stable_digest
from .strategy_catalog import StrategyCatalog, bind_parameters
from .strategy_walkforward_evaluation import evaluate_window

METHOD = "strategy-walk-forward.v1"
FOLDS = 3
_EXECUTION_KEYS = ("starting_cash", "fee_bps", "slippage_bps", "max_volume_participation")


def _numeric(name, value, lower, upper, *, integer=False):
    allowed_types = (int,) if integer else (int, float)
    if type(value) not in allowed_types or not lower <= value <= upper or not math.isfinite(value):
        raise ValueError(f"{name} 超出允许范围 [{lower}, {upper}]")
    return value


def _options(template, parameters, policy, requested):
    if not isinstance(requested, dict):
        raise TypeError("滚动验证配置需要是对象")
    allowed = {"train_size", "test_size", "parameter_values", "stability_tolerance", *_EXECUTION_KEYS[1:]}
    if set(requested) - allowed:
        raise ValueError("未知的滚动验证配置")
    opts = {
        "train_size": _numeric("train_size", requested.get("train_size", 32), 6, 120, integer=True),
        "test_size": _numeric("test_size", requested.get("test_size", 16), 3, 60, integer=True),
        "stability_tolerance": _numeric("stability_tolerance", requested.get("stability_tolerance", .002), 0, .1),
    }
    for name in _EXECUTION_KEYS[1:]:
        limit = 1 if name == "max_volume_participation" else 100
        opts[name] = _numeric(name, requested.get(name, policy[name]), 0, limit)
    param = {"PAPER_LONG_ONLY": "rebalance_every", "THEORETICAL": "window", "STATISTICS": "event_horizon"}[
        template["execution_mode"]]
    bounds = template["parameter_bounds"][param]
    center = parameters[param]
    default_grid = sorted({max(bounds["minimum"], center - 1), center, min(bounds["maximum"], center + 1)})
    grid = requested.get("parameter_values", default_grid)
    if not isinstance(grid, list) or not 1 <= len(grid) <= 5:
        raise ValueError("参数网格需要 1 至 5 个值")
    for value in grid:
        _numeric(param, value, bounds["minimum"], bounds["maximum"], integer=True)
    if len(set(grid)) != len(grid):
        raise ValueError("参数网格含有重复值")
    return {**opts, "parameter_values": sorted(grid)}, param


def _scenarios(opts, policy, mode):
    baseline = {"id": "baseline", **{k: policy[k] if k == "starting_cash" else opts[k] for k in _EXECUTION_KEYS}}
    if mode != "PAPER_LONG_ONLY":
        return [baseline]
    scenarios = [baseline]
    for name, key in (("higher_fees", "fee_bps"), ("higher_slippage", "slippage_bps")):
        scenarios.append({**baseline, "id": name, key: min(100, max(2 * baseline[key], baseline[key] + 5))})
    scenarios.append({**baseline, "id": "lower_liquidity",
                      "max_volume_participation": baseline["max_volume_participation"] / 10})
    return scenarios


def _bounds(stamps, train_size, test_size):
    first = max(0, len(stamps) - train_size - FOLDS * test_size)
    windows = []
    for index in range(FOLDS):
        start = first + index * test_size
        train, test = stamps[start:start + train_size], stamps[start + train_size:start + train_size + test_size]
        windows.append({"index": index + 1, "train_start": train[0] if train else None,
                        "train_end": train[-1] if train else None, "test_start": test[0] if test else None,
                        "test_end": test[-1] if test else None, "train_observations": len(train),
                        "test_observations": len(test)})
    return windows


def build_validation_plan(source, options=None):
    """Freeze the schedule, parameter grid, execution scenarios and scoring rule from metadata."""
    binding, panel, policy = source["binding"], source["panel"], source["policy"]
    if binding["panel_ref"] != panel.fingerprint or binding["template_id"] != source["template_id"]:
        raise ValueError("已发布策略的面板或模板绑定不一致")
    template = StrategyCatalog().template(source["template_id"])
    if stable_digest(template) != binding["template_digest"]:
        raise ValueError("策略模板与发布版本不一致")
    parameters = bind_parameters(template, binding["parameters"])
    opts, param = _options(template, parameters, policy, {} if options is None else options)
    mode = template["execution_mode"]
    scenarios = _scenarios(opts, policy, mode)
    stamps = panel.contract.observed_at
    windows = _bounds(stamps, opts["train_size"], opts["test_size"])
    assets = len(panel.contract.instrument_ids)
    # Bound total account / decision work, including frequency-one studies on a wide universe.
    complete_windows = sum(row["train_observations"] == opts["train_size"] and row["test_observations"] == opts["test_size"]
                           for row in windows)
    max_rows = complete_windows * assets * (len(opts["parameter_values"]) * opts["train_size"] +
                                            len(scenarios) * (opts["train_size"] + opts["test_size"]))
    if max_rows > 120_000:
        raise ValueError("滚动验证的资产与时间窗组合超过 120000 行计算预算")
    refs = {key: source[key] for key in ("run_id", "report_digest", "ledger_digest", "snapshot_ref", "policy_ref",
                                         "data_mode")}
    objective = {"PAPER_LONG_ONLY": "net_return", "THEORETICAL": "mean_forward_spread_price_return",
                 "STATISTICS": "mean_matured_event_price_return"}[mode]
    return {"method": METHOD, "evaluation_kind": "RETROSPECTIVE_WALK_FORWARD", "template_id": source["template_id"],
            "source": {**refs, "panel_ref": panel.fingerprint, "binding_digest": stable_digest(binding),
                       "policy_digest": stable_digest(policy), "policy_available_at": source.get("policy_available_at"),
                       "template_digest": binding["template_digest"], "asset_classes_digest": stable_digest(source["asset_classes"]),
                       "observations": len(stamps), "assets": assets},
            "options": opts, "parameter": param, "parameter_values": opts["parameter_values"],
            "objective": objective, "selection_rule": "MAX_TRAIN_SCORE_THEN_NEAREST_PUBLISHED_THEN_LOWEST",
            "stability_rule": "CONTIGUOUS_GRID_VALUES_WITHIN_ABSOLUTE_TOLERANCE_OF_TRAIN_MAX",
            "account_initialization": "FRESH_CASH_ZERO_POSITIONS_ONE_OBSERVATION_DELAY",
            "warmup": "TRAIN_WINDOW_FOR_OOS_SIGNALS_ONLY", "windows": windows, "scenarios": scenarios,
            "evaluation_units": complete_windows * (len(opts["parameter_values"]) + len(scenarios)),
            "max_evaluation_rows": max_rows}


def _slice(frame, audit, original, start, end):
    """Subset the immutable as-of rows and their evidence; perform no new as-of join."""
    frame = frame.loc[panel_time(start):panel_time(end)]
    dates = frame.index.get_level_values("observed_at").unique()
    definition = {k: v for k, v in original.contract.to_dict().items() if k not in {"fingerprint", "schema_version"}}
    contract = PanelContract(**{**definition, "observed_at": list(dates)})
    reasons = [r for r in audit["row_reasons"] if start <= r["observed_at"] <= end]
    selections = [r for r in audit["selections"] if start <= r["observed_at"] <= end]
    coverage = {name: int(frame[name].notna().sum()) for name in audit["coverage"]}
    subset = {"price_basis": audit["price_basis"], "rule": audit["rule"], "coverage": coverage,
              "status": "NEEDS_DATA" if reasons else "READY", "units": audit["units"], "row_reasons": reasons,
              "selections": selections, "attachment_refs": audit["attachment_refs"],
              "selection_count": len(selections), "reasons": sorted({v for r in reasons for v in r["reasons"]})}
    return ResearchPanel(frame, subset, contract, stable_digest([METHOD, original.fingerprint, start, end]))


def _selection(plan, candidates, published):
    comparable = [c for c in candidates if c["score"] is not None]
    selected = None
    regions, stable = [], []
    if comparable:
        winner = min(comparable, key=lambda c: (-c["score"], abs(c["value"] - published), c["value"]))
        selected = winner["value"]
        cutoff = winner["score"] - plan["options"]["stability_tolerance"]
        current = None
        for candidate in candidates:
            value = candidate["value"]
            if candidate["score"] is None or candidate["score"] < cutoff:
                current = None
                continue
            if current is None:
                current = {"lower": value, "upper": value, "values": []}
                regions.append(current)
            current["upper"] = value
            current["values"].append(value)
            stable.append(value)
    return {"parameter": plan["parameter"], "selected_value": selected, "stable_values": stable,
            "stable_regions": regions, "candidates": candidates}


def _empty_case(case, status, reasons):
    return {"id": case["id"], "status": status, "reasons": reasons, "metrics": {}, "ledger_digest": None, "series": []}


def run_validation_plan(source, plan):
    """Score only training candidates, lock the winner, then replay independent OOS accounts."""
    if not isinstance(plan, dict) or stable_digest(build_validation_plan(source, plan.get("options"))) != stable_digest(plan):
        raise ValueError("滚动验证方案与冻结的来源、参数或窗口不符")
    panel, binding, policy = source["panel"], source["binding"], source["policy"]
    template = StrategyCatalog().template(source["template_id"])
    frame, audit = panel.frame, panel.audit
    published = binding["parameters"][plan["parameter"]]
    windows = []
    for bounds in plan["windows"]:
        opts = plan["options"]
        candidates, reasons = [], []
        if bounds["train_observations"] != opts["train_size"] or bounds["test_observations"] != opts["test_size"]:
            reasons.append("INSUFFICIENT_OBSERVATIONS")
        elif source.get("policy_available_at") and panel_time(source["policy_available_at"]) > panel_time(bounds["train_start"]):
            reasons.append("POLICY_NOT_EFFECTIVE")
        if not reasons:
            train = _slice(frame, audit, panel, bounds["train_start"], bounds["train_end"])
            for value in plan["parameter_values"]:
                parameters = {**binding["parameters"], plan["parameter"]: value}
                trial = evaluate_window(template, train, bounds["train_start"], parameters, source["asset_classes"],
                                        policy, plan["scenarios"][0])
                candidates.append({"value": value, "score": trial["metrics"].get(plan["objective"]),
                                   "status": trial["status"], "reasons": trial["reasons"]})
        selection = _selection(plan, candidates, published)
        if selection["selected_value"] is None:
            # Unfilled training orders alone say nothing about holdout liquidity.
            zero_capacity = (not reasons and template["execution_mode"] == "PAPER_LONG_ONLY"
                             and opts["max_volume_participation"] == 0)
            status = "UNTRADEABLE" if zero_capacity else "INSUFFICIENT_DATA"
            reasons = reasons or sorted({"NO_ELIGIBLE_TRAIN_CANDIDATE", *(r for c in candidates for r in c["reasons"])})
            scenarios = [_empty_case(s, status, reasons) for s in plan["scenarios"]]
        else:
            evaluation = _slice(frame, audit, panel, bounds["train_start"], bounds["test_end"])
            parameters = {**binding["parameters"], plan["parameter"]: selection["selected_value"]}
            scenarios = [evaluate_window(template, evaluation, bounds["test_start"], parameters, source["asset_classes"],
                                         policy, case) for case in plan["scenarios"]]
            status, reasons = scenarios[0]["status"], scenarios[0]["reasons"]
        windows.append({**bounds, "status": status, "reasons": reasons, "selection": selection, "scenarios": scenarios})
    return {"method": METHOD, "template_id": template["template_id"], "plan_digest": stable_digest(plan),
            "windows": windows, "status_counts": dict(Counter(w["status"] for w in windows)), "paper_only": True}

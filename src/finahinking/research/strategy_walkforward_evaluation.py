"""Evaluation measures for executable accounts and non-executable research templates."""

from __future__ import annotations

import math

from finahinking.data.panel_contracts import panel_time

from .contracts import stable_digest
from .strategy_runner import execute_strategy
from .strategy_selection import adjusted_price_matrix, select_strategy


def _output(case, status, reasons=(), *, metrics=None, ledger=None, series=None):
    return {"id": case["id"], "status": status, "reasons": sorted(set(reasons)), "metrics": metrics or {},
            "ledger_digest": None if ledger is None else stable_digest(ledger), "series": series or []}


def _decision_gaps(decisions, end, *, training):
    """Only fully observed price lookbacks may skip an initial training decision."""
    gaps = set()
    for row in decisions:
        if row["observed_at"] == end:
            continue  # No remaining execution observation in this scoring interval.
        gates = [r for r in [*row["risk"], *row["approved_policy_checks"]] if r["action"] == "GATE"]
        if any(r["metric"] == "pit_valid" and r["status"] != "PASS" for r in gates):
            gaps.add("INVALID_POINT_IN_TIME_INPUT")
        if row["status"] != "NEEDS_DATA" or (training and row.get("lookback_warmup")):
            continue
        missing = [r["metric"] for r in gates if r["status"] == "NEEDS_DATA"]
        gaps.add("MISSING_DECISION_EVIDENCE")
        if missing:
            gaps.update("MISSING_" + metric.upper() for metric in missing)
        elif row["reason"]:
            gaps.add(row["reason"])
    return gaps


def _zero_fill_status(decisions, orders, end):
    # Final-bar decisions cannot execute within this interval. A structurally
    # short but complete price lookback can also precede the first valid target.
    actionable = [d for d in decisions if d["observed_at"] < end and not d.get("lookback_warmup")]
    if not actionable:
        return "INSUFFICIENT_DATA", {"INSUFFICIENT_SIGNAL_HISTORY"}
    if not orders and all(d["status"] == "SCHEDULED" and all(w == 0 for w in d["target_weights"].values())
                          for d in actionable):
        return "PAPER_SIMULATED", {"INTENTIONAL_CASH"}
    reasons = {order["reason"] for order in orders if order["reason"]}
    for decision in actionable:
        if decision["status"] == "BLOCKED_RISK":
            reasons.update("RISK_" + r.get("rule_id", r.get("policy_field", "")).upper()
                           for r in [*decision["risk"], *decision["approved_policy_checks"]]
                           if r["action"] == "GATE" and r["status"] != "PASS")
    return "UNTRADEABLE", reasons | {"NO_FILLS"}


def _paper(template, panel, start, params, classes, policy, case):
    if case["max_volume_participation"] == 0:
        return _output(case, "UNTRADEABLE", ["ZERO_VOLUME_CAPACITY"])
    execution = {key: value for key, value in case.items() if key != "id"}
    parameters = {**params, **{k: execution[k] for k in ("starting_cash", "fee_bps", "slippage_bps")}}
    result = execute_strategy(template["template_id"], panel, parameters, asset_classes=classes,
                              policy={**policy, **execution}, account_start=start)
    ledger, benchmark = result["ledger"], result["benchmark"]
    if not ledger or not benchmark:
        return _output(case, "INSUFFICIENT_DATA", [result.get("reason") or "LEDGER_UNAVAILABLE"])
    order_reasons = {r["reason"] for r in ledger["orders"] if r["reason"]}
    if ledger["status"] != "COMPLETE" or benchmark["status"] != "COMPLETE":
        reasons = {r for r in order_reasons if r.startswith("MISSING_")}
        if ledger["failure"]:
            reasons.add(ledger["failure"]["reason"])
        if benchmark.get("reason"):
            reasons.add(benchmark["reason"])
        return _output(case, "INSUFFICIENT_DATA", reasons or ["BENCHMARK_OR_LEDGER_UNAVAILABLE"], ledger=ledger)
    gaps = _decision_gaps(result["decisions"], panel.contract.observed_at[-1],
                          training=start == panel_time(panel.contract.observed_at[0]))
    if gaps:
        return _output(case, "INSUFFICIENT_DATA", gaps, ledger=ledger)
    if not ledger["fills"]:
        status, order_reasons = _zero_fill_status(result["decisions"], ledger["orders"], panel.contract.observed_at[-1])
        if status != "PAPER_SIMULATED":
            return _output(case, status, order_reasons, ledger=ledger)
    marks, bench = ledger["marks"], benchmark["marks"]
    series = [{"timestamp": row["timestamp"], "equity": row["equity"], "benchmark": bm["equity"]}
              for row, bm in zip(marks, bench, strict=True)]
    net = marks[-1]["equity"] - case["starting_cash"]
    fees, slippage = ledger["total_fees"], ledger["total_slippage"]
    benchmark_return = bench[-1]["equity"] / bench[0]["equity"] - 1
    net_return = net / case["starting_cash"]
    metrics = {"starting_cash": case["starting_cash"], "currency": ledger["currency"], "final_equity": marks[-1]["equity"],
               "net_return": net_return, "benchmark_return": benchmark_return, "excess_return": net_return - benchmark_return,
               "gross_pnl": net + fees + slippage, "fees": fees, "slippage": slippage, "net_pnl": net,
               "fill_count": len(ledger["fills"]), "order_count": len(ledger["orders"])}
    return _output(case, "PAPER_SIMULATED", order_reasons, metrics=metrics, ledger=ledger, series=series)


def _event(template, frame, start, params, classes, case):
    # Restrict both the event's origin and label maturity to this scoring interval.
    interval = frame.loc[start:]
    selection = select_strategy(template, interval, params, classes)
    if selection["status"] != "RESEARCH_ONLY":
        return _output(case, "INSUFFICIENT_DATA", [selection["reason"]])
    diagnostics = selection["diagnostics"]
    count = diagnostics["matured_events"]
    mean = sum(row["count"] * row["mean_price_return"] for row in diagnostics["groups"].values()) / count
    return _output(case, "THEORETICAL", ["EVENT_STATISTICS_ONLY"], metrics={
        "mean_matured_event_price_return": mean, "groups": diagnostics["groups"], "matured_events": count,
        "pending_events": diagnostics["pending_events"], "missing_marks": diagnostics["missing_marks"]})


def _theoretical(template, frame, start, params, classes, case):
    # This is a forward signal statistic. Each decision has one separate, delayed
    # price-return observation; it neither borrows shares nor compounds an account.
    interval = frame.loc[start:].index.get_level_values("observed_at").unique()
    prices = adjusted_price_matrix(frame, sorted(classes), len(interval))
    samples, reasons = [], set()
    for index in range(0, len(interval) - 2, params["rebalance_every"]):
        decision_at, entry, exit_at = interval[index:index + 3]
        selection = select_strategy(template, frame.loc[:decision_at], params, classes)
        if selection["status"] != "RESEARCH_ONLY":
            reasons.add(selection["reason"])
            continue
        weights = selection["diagnostics"]["theoretical_weights"]
        if not weights:
            reasons.add("NO_THEORETICAL_SIGNAL")
            continue
        value = sum(weight * (prices.at[exit_at, asset] / prices.at[entry, asset] - 1)
                    for asset, weight in weights.items())
        if not math.isfinite(value):
            reasons.add("MISSING_FORWARD_PRICE")
            continue
        samples.append({"decision_at": decision_at.isoformat(), "entry_at": entry.isoformat(),
                        "exit_at": exit_at.isoformat(), "weights": weights, "spread_price_return": float(value)})
    if not samples:
        return _output(case, "INSUFFICIENT_DATA", reasons or ["NO_MATURE_THEORETICAL_SIGNAL"])
    return _output(case, "THEORETICAL", reasons | {"BORROW_NOT_SUPPORTED"}, metrics={
        "mean_forward_spread_price_return": sum(r["spread_price_return"] for r in samples) / len(samples),
        "observation_count": len(samples), "observations": samples})


def evaluate_window(template, panel, start, parameters, classes, policy, case):
    start = panel_time(start)
    if template["execution_mode"] == "PAPER_LONG_ONLY":
        return _paper(template, panel, start, parameters, classes, policy, case)
    if template["execution_mode"] == "STATISTICS":
        return _event(template, panel.frame, start, parameters, classes, case)
    return _theoretical(template, panel.frame, start, parameters, classes, case)

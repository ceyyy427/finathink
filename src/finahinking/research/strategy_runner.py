"""Shared, causal paper account for mixed equity and ETF strategy studies."""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np

from finahinking.data.research_panel import ResearchPanel
from finahinking.quant.interfaces import BacktestConfig, Trade

from .contracts import stable_digest

VERSION = "strategy-runner.v1"
EPSILON = 1e-9


class StrategyDataError(ValueError):
    """Required point-in-time account metadata is unavailable."""


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _boolean(value, expected):
    return isinstance(value, (bool, np.bool_)) and bool(value) is expected


def _positive(value):
    return _finite(value) and value > 0


def _lot_floor(quantity, lot):
    return max(0.0, math.floor(quantity / lot + 1e-12) * lot)


def _execution_config(policy):
    if type(policy.get("delay_observations")) is not int or policy["delay_observations"] != 1:
        raise ValueError("paper strategies require exactly one observation of execution delay")
    config = BacktestConfig(
        starting_cash=policy["starting_cash"],
        fee_bps=policy["fee_bps"],
        slippage_bps=policy["slippage_bps"],
        frequency="observation",
        annualization=1,
        max_volume_participation=policy["max_volume_participation"],
    )
    if config.slippage_bps >= 10_000 or config.fee_bps >= 10_000:
        raise ValueError("paper costs must be below 10000 bps")
    if config.max_volume_participation is None:
        raise ValueError("paper strategies require a volume participation limit")
    return config


def _strategy_panel(panel, asset_classes):
    if not isinstance(panel, ResearchPanel):
        raise TypeError("strategy input must be a ResearchPanel")
    assets = tuple(panel.contract.instrument_ids)
    if (not isinstance(asset_classes, Mapping) or set(asset_classes) != set(assets)
            or any(value not in {"EQUITY", "ETF"} for value in asset_classes.values())):
        raise StrategyDataError("each instrument requires an explicit equity or ETF classification")
    frame = panel.frame
    times = frame.index.get_level_values("observed_at").unique()
    currencies = panel.contract.currencies
    units = panel.audit["units"]
    currency_set = set(currencies.values())
    if (set(currencies) != set(assets) or len(currency_set) != 1
            or "UNKNOWN" in currency_set or panel.contract.price_basis != "raw"
            or units.get("close") not in currency_set or units.get("volume") != "shares"):
        raise StrategyDataError("paper account requires same-currency raw close and share volume")
    for asset in assets:
        if asset not in panel.contract.timezones:
            raise StrategyDataError("asset timezone is missing")
        local_dates = times.tz_convert(panel.contract.timezones[asset]).date
        if len(set(local_dates)) != len(times):
            raise ValueError("paper account requires at most one observation per asset-local date")
    selections = {
        (row["observed_at"], row["instrument"], row["field"]): row
        for row in panel.audit["selections"]
        if row["field"] in {"split_factor", "cash_dividend", "limit_rule_version", "limit_up_price", "limit_down_price", "is_session"}
    }
    return frame, times, assets, next(iter(currency_set)), selections


class PaperAccount:
    """Cash and position ledger. Each order expires on its scheduled observation."""

    def __init__(self, panel, asset_classes, policy):
        self.frame, self.times, self.assets, self.currency, self.sources = _strategy_panel(panel, asset_classes)
        self.config = _execution_config(policy)
        self.asset_classes = dict(asset_classes)
        self.policy = dict(policy)
        self.panel = panel
        self.cash = self.config.starting_cash
        self.positions = dict.fromkeys(self.assets, 0.0)
        self.split_factors = {}
        self.sessions = dict.fromkeys(self.assets, 0)
        self.share_locks = {asset: [] for asset in self.assets}
        self.cash_receivables = []
        self.marks = []
        self.orders = []
        self.fills = []
        self.corporate_actions = []
        self.failure = None

    def _source(self, time, asset, field, ref):
        source = self.sources.get((time.isoformat(), asset, field))
        return source if source and source["attachment_ref"] == ref else None

    def _corporate_state(self, time, asset, row):
        split = row.get("split_factor")
        dividend = row.get("cash_dividend")
        ref = self.panel.contract.adjustment_ref
        split_source = self._source(time, asset, "split_factor", ref)
        dividend_source = self._source(time, asset, "cash_dividend", ref)
        if not (_positive(split) and _finite(dividend) and dividend >= 0 and split_source and dividend_source):
            return None, "MISSING_CORPORATE_ACTION_EVIDENCE"
        previous = self.split_factors.get(asset, split) if self.positions[asset] > EPSILON else split
        if split != previous and split_source["source_observed_at"] != time.isoformat():
            return None, "LATE_SPLIT_DISCLOSURE"
        return (float(split), float(dividend), float(split / previous)), None

    def _advance_sessions(self, time, quotes):
        for asset, row in quotes.iterrows():
            if (_boolean(row["is_session"], True)
                    and self._source(time, asset, "is_session", self.panel.contract.calendar_ref)):
                self.sessions[asset] += 1
        pending = []
        for asset, release_at, cash in self.cash_receivables:
            if self.sessions[asset] >= release_at:
                self.cash += cash
            else:
                pending.append((asset, release_at, cash))
        self.cash_receivables = pending

    def _apply_actions(self, time, states):
        for asset, (split, dividend, ratio) in states.items():
            old_qty = self.positions[asset]
            if ratio != 1:
                self.positions[asset] *= ratio
                self.share_locks[asset] = [
                    (release_at, quantity * ratio)
                    for release_at, quantity in self.share_locks[asset]
                ]
                self.corporate_actions.append({
                    "kind": "SPLIT", "instrument": asset, "timestamp": time.isoformat(),
                    "ratio": ratio, "quantity_before": old_qty,
                    "quantity_after": self.positions[asset], "cash_change": 0.0,
                })
            if dividend > 0:
                cash = self.positions[asset] * dividend
                self.cash += cash
                self.corporate_actions.append({
                    "kind": "DIVIDEND", "instrument": asset, "timestamp": time.isoformat(),
                    "per_share": dividend, "quantity": self.positions[asset], "cash_change": cash,
                })
            self.split_factors[asset] = split

    def _order(self, time, asset, decision_at, side, requested, filled, reason=None):
        self.orders.append({
            "order_id": len(self.orders) + 1, "instrument": asset,
            "asset_class": self.asset_classes[asset], "timestamp": time.isoformat(),
            "decision_at": decision_at.isoformat(), "side": side,
            "requested_quantity": requested, "filled_quantity": filled,
            "status": "REJECTED" if not filled else "PARTIAL" if filled + EPSILON < requested else "FILLED",
            "reason": reason,
        })

    def _check_limits(self, time, asset, row, side, price):
        rule_ref = self.panel.contract.trade_rule_ref
        version = row.get("limit_rule_version")
        if not isinstance(version, str) or not self._source(time, asset, "limit_rule_version", rule_ref):
            return "MISSING_PRICE_LIMIT_RULE"
        lower, upper = row.get("limit_down_price"), row.get("limit_up_price")
        if version == "UNRESTRICTED" and not _finite(lower) and not _finite(upper):
            return None
        if not (_positive(lower) and _positive(upper) and lower < upper
                and self._source(time, asset, "limit_down_price", rule_ref)
                and self._source(time, asset, "limit_up_price", rule_ref)):
            return "MISSING_PRICE_LIMIT_BANDS"
        mark = float(row["close"])
        if price < lower or price > upper or (side == "buy" and mark >= upper) or (side == "sell" and mark <= lower):
            return "PRICE_LIMIT"
        return None

    def _trade(self, time, asset, row, decision_at, weight, equity):
        mark, lot, settlement, volume = (row.get(key) for key in ("close", "lot_size", "settlement_days", "volume"))
        position = self.positions[asset]
        if not _positive(mark):
            if weight > 0 or position > 0:
                self._order(time, asset, decision_at, None, None, 0.0, "MISSING_EXECUTION_MARK")
            return
        target = equity * weight / mark
        delta = target - position
        if abs(delta) <= EPSILON:
            return
        side = "buy" if delta > 0 else "sell"
        requested = abs(delta)
        if _boolean(row.get("suspended"), True):
            reason = "SUSPENDED"
        elif _boolean(row.get("is_session"), False):
            reason = "CLOSED_SESSION"
        elif _boolean(row.get("trade_allowed"), False) or _boolean(row.get("in_universe"), False):
            reason = "TRADE_NOT_ALLOWED"
        elif not (_boolean(row.get("execution_eligible"), True) and _positive(lot) and _finite(settlement)
                  and settlement >= 0 and int(settlement) == settlement and _finite(volume) and volume >= 0):
            reason = "MISSING_EXECUTION_RULES"
        else:
            reason = None
        if reason:
            self._order(time, asset, decision_at, side, requested, 0.0, reason)
            return
        state, action_reason = self._corporate_state(time, asset, row)
        price = mark * (1 + (1 if side == "buy" else -1) * self.config.slippage_bps / 10_000)
        limit_reason = self._check_limits(time, asset, row, side, price)
        if action_reason or limit_reason:
            self._order(time, asset, decision_at, side, requested, 0.0, action_reason or limit_reason)
            return
        fee_rate = self.config.fee_bps / 10_000
        lot_quantity = _lot_floor(requested, lot)
        capacity = volume * self.config.max_volume_participation
        if side == "buy":
            limit = self.cash / (price * (1 + fee_rate))
            limit_reason = "INSUFFICIENT_CASH"
        else:
            locked = sum(quantity for release_at, quantity in self.share_locks[asset] if self.sessions[asset] < release_at)
            limit = position - locked
            limit_reason = "SETTLEMENT_LOCK"
        quantity = _lot_floor(min(lot_quantity, capacity, limit), lot)
        reason = None
        if quantity + EPSILON < requested:
            reason = (
                limit_reason if limit < min(lot_quantity, capacity)
                else "VOLUME_PARTICIPATION" if capacity < lot_quantity else "LOT_SIZE"
            )
        self._order(time, asset, decision_at, side, requested, quantity, reason)
        if quantity == 0:
            return
        notional = quantity * price
        fees = notional * fee_rate
        release_at = self.sessions[asset] + int(settlement)
        if side == "buy":
            self.cash = max(0.0, self.cash - notional - fees)
            self.positions[asset] += quantity
            self.split_factors[asset] = state[0]
            if settlement:
                self.share_locks[asset].append((release_at, quantity))
        else:
            proceeds = notional - fees
            self.positions[asset] -= quantity
            if settlement:
                self.cash_receivables.append((asset, release_at, proceeds))
            else:
                self.cash += proceeds
        trade = Trade(time, side, quantity, price, notional, fees, abs(price - mark) * quantity, "next-observation target")
        self.fills.append({
            **trade.to_dict(), "order_id": len(self.orders), "instrument": asset,
            "asset_class": self.asset_classes[asset], "decision_at": decision_at.isoformat(),
            "mark": float(mark), "settlement_sessions": int(settlement),
        })

    def _equity(self, quotes):
        position_value = sum(
            quantity * float(quotes.at[asset, "close"])
            for asset, quantity in self.positions.items() if quantity > EPSILON
        )
        unsettled = sum(cash for _, _, cash in self.cash_receivables)
        return self.cash + unsettled + position_value, position_value, unsettled

    def step(self, time, quotes, pending):
        states = {}
        for asset, row in quotes.iterrows():
            if self.positions[asset] <= EPSILON:
                continue
            state, reason = self._corporate_state(time, asset, row)
            if not state or not _positive(row.get("close")):
                self.failure = {
                    "timestamp": time.isoformat(), "instrument": asset,
                    "reason": reason or "MISSING_POSITION_MARK",
                }
                return False
            states[asset] = state
        self._advance_sessions(time, quotes)
        self._apply_actions(time, states)
        if pending is not None:
            decision_at, weights = pending
            self._rebalance(time, quotes, decision_at, weights)
        equity, position_value, unsettled = self._equity(quotes)
        self.marks.append({
            "timestamp": time.isoformat(), "cash": self.cash + unsettled,
            "available_cash": self.cash, "unsettled_cash": unsettled,
            "positions": self.positions.copy(), "position_value": position_value, "equity": equity,
            "position_marks": {
                asset: float(quotes.at[asset, "close"])
                for asset, quantity in self.positions.items() if quantity > EPSILON
            },
        })
        return True

    def _rebalance(self, time, quotes, decision_at, weights):
        if not isinstance(weights, Mapping) or set(weights) - set(self.assets) or not all(_finite(w) for w in weights.values()):
            raise ValueError("target must provide finite weights for declared assets")
        if any(weight < 0 for weight in weights.values()):
            for asset, weight in weights.items():
                if weight < 0:
                    self._order(time, asset, decision_at, "sell", None, 0.0, "SHORT_UNSUPPORTED")
            return
        if sum(weights.values()) > 1 + EPSILON:
            raise ValueError("target weights exceed available capital")
        equity = self._equity(quotes)[0]
        # Sell first; unsettled proceeds remain unavailable. Ties use asset ID.
        def order_key(asset):
            price = quotes.at[asset, "close"]
            value = self.positions[asset] * price if _positive(price) else 0.0
            return (weights.get(asset, 0.0) * equity >= value, asset)

        for asset in sorted(self.assets, key=order_key):
            self._trade(time, asset, quotes.loc[asset], decision_at, weights.get(asset, 0.0), equity)

    def result(self):
        return {
            "status": "NEEDS_DATA" if self.failure or any(
                (order["reason"] or "").startswith("MISSING_") for order in self.orders
            ) else "COMPLETE",
            "currency": self.currency, "marks": self.marks, "orders": self.orders,
            "fills": self.fills, "corporate_actions": self.corporate_actions, "failure": self.failure,
            "total_fees": sum(fill["fees"] for fill in self.fills),
            "total_slippage": sum(fill["slippage"] for fill in self.fills),
            "accounting": {
                "prices": "raw close; signal at previous declared observation",
                "corporate_actions": "cumulative split factor, then exact cash dividend per post-split share",
                "cash": "available cash plus unsettled sale receivables",
                "settlement": "per-asset declared sessions for resale lock and sale cash availability",
            },
        }


def simulate_paper_ledger(panel, targets, *, asset_classes, policy):
    """Execute declared targets without fitting or generating signals."""
    account = PaperAccount(panel, asset_classes, policy)
    if not isinstance(targets, Mapping) or set(targets) - set(account.times):
        raise ValueError("targets must bind declared observations")
    pending = None
    for time in account.times:
        if not account.step(time, account.frame.xs(time), pending):
            break
        pending = (time, targets[time]) if time in targets else None
    return account.result()


def _benchmark(account, definition):
    """Value a frictionless equal-weight starting basket on the same observation grid."""
    universe = definition["universe"]
    members = [
        asset for asset in account.assets
        if universe == "all" or account.asset_classes[asset] == universe.upper()
    ]
    output = {**definition, "instruments": members, "marks": [], "status": "NEEDS_DATA"}
    if not members:
        return output
    positions, factors = {}, {}
    cash = 0.0
    adjustment_ref = account.panel.contract.adjustment_ref
    for time in account.times:
        quotes = account.frame.xs(time)
        values = []
        for asset in members:
            row = quotes.loc[asset]
            price, factor, dividend = (row.get(key) for key in ("close", "split_factor", "cash_dividend"))
            source = account._source(time, asset, "split_factor", adjustment_ref)
            dividend_source = account._source(time, asset, "cash_dividend", adjustment_ref)
            if not (_positive(price) and _positive(factor) and _finite(dividend)
                    and dividend >= 0 and source and dividend_source):
                output["reason"] = "MISSING_BENCHMARK_MARK_OR_ACTION"
                return output
            if asset not in positions:
                positions[asset] = account.config.starting_cash / len(members) / price
            else:
                if factor != factors[asset] and source["source_observed_at"] != time.isoformat():
                    output["reason"] = "LATE_SPLIT_DISCLOSURE"
                    return output
                positions[asset] *= factor / factors[asset]
                cash += positions[asset] * dividend
            factors[asset] = factor
            values.append(positions[asset] * price)
        equity = float(sum(values) + cash)
        previous = output["marks"][-1]["equity"] if output["marks"] else equity
        output["marks"].append({"timestamp": time.isoformat(), "equity": equity, "return": equity / previous - 1})
    output["status"] = "COMPLETE"
    return output


def _approved_targets(targets, quotes, policy):
    """Respect the approved long-only allocation limits without redistributing capped weight."""
    if any(weight < 0 for weight in targets.values()):
        return targets
    cap = min(policy.get("target_weight", 1.0), policy.get("max_concentration", 1.0))
    scale = policy.get("signal_budget_scale", 1.0)
    buffer = policy.get("cash_buffer", 0.0)
    industry_cap = policy.get("industry_cap", 1.0)
    unknown_cap = policy.get("unknown_industry_cap", industry_cap)
    if not all(_finite(value) and 0 <= value <= 1 for value in (cap, scale, buffer, industry_cap, unknown_cap)):
        raise ValueError("invalid approved allocation limits")
    weights = {asset: min(float(weight), cap) * scale for asset, weight in targets.items()}
    groups = {}
    for asset in weights:
        industry = quotes.loc[asset].get("industry")
        groups.setdefault(industry if isinstance(industry, str) else "UNKNOWN", []).append(asset)
    for industry, assets in groups.items():
        total = sum(weights[asset] for asset in assets)
        limit = unknown_cap if industry == "UNKNOWN" else industry_cap
        if total > limit:
            for asset in assets:
                weights[asset] *= limit / total
    total = sum(weights.values())
    if total > 1 - buffer:
        weights = {asset: value * (1 - buffer) / total for asset, value in weights.items()}
    return weights


def _approved_risk(metrics, weights, policy):
    checks = []
    if not any(weight > 0 for weight in weights.values()):
        return checks
    for metric, key in (("tail_loss", "max_cvar"), ("drawdown", "max_drawdown")):
        if key not in policy:
            continue
        limit = policy[key]
        if not _finite(limit) or limit < 0:
            raise ValueError(f"invalid approved limit {key}")
        value = metrics[metric]
        status = "NEEDS_DATA" if value is None else "TRIGGERED" if value > limit else "PASS"
        checks.append({
            "policy_field": key, "metric": metric, "status": status,
            "limit": limit, "value": value, "action": "GATE",
        })
    return checks


def execute_strategy(template_id, panel, parameters, *, asset_classes, policy):
    """Select at the current cutoff and apply permitted weights on the next bar."""
    from .strategy_catalog import StrategyCatalog, bind_parameters
    from .strategy_risk import evaluate_risk_rules, strategy_risk_inputs
    from .strategy_selection import select_strategy

    catalog = StrategyCatalog()
    template = catalog.template(template_id)
    parameters = dict(parameters)
    for key in ("starting_cash", "fee_bps", "slippage_bps"):
        if key in parameters and parameters[key] != policy[key]:
            raise ValueError(f"{key} is fixed by the approved policy")
        parameters[key] = policy[key]
    bound = bind_parameters(template, parameters)
    rules = catalog.risk_rules()
    result = {
        "schema_version": VERSION, "template_id": template_id,
        "template_fingerprint": template["fingerprint"],
        "rule_fingerprints": template["risk_rule_fingerprints"],
        "panel_fingerprint": panel.fingerprint, "policy_fingerprint": stable_digest(policy),
        "parameters": bound, "paper_only": True, "production_write": False,
    }
    try:
        account = PaperAccount(panel, asset_classes, policy)
    except StrategyDataError as exc:
        return {**result, "status": "NEEDS_DATA", "reason": str(exc), "decisions": [], "ledger": None, "benchmark": None}
    bad_rows = {
        (row["observed_at"], row["instrument"])
        for row in panel.audit["row_reasons"]
    }
    decisions = []
    pending = None
    observed = set()
    for idx, time in enumerate(account.times):
        quotes = account.frame.xs(time)
        if not account.step(time, quotes, pending):
            break
        pending = None
        if idx % bound["rebalance_every"]:
            continue
        prefix = account.frame.loc[:time]
        mark = account.marks[-1]
        current = {
            asset: quantity * mark["position_marks"][asset] / mark["equity"]
            for asset, quantity in mark["positions"].items()
            if quantity > EPSILON and mark["equity"] > EPSILON
        }
        selection = select_strategy(template, prefix, bound, asset_classes, previous_weights=current)
        status = selection["status"]
        weights = _approved_targets(selection.get("target_weights", {}), quotes, policy)
        involved = {asset for asset, weight in weights.items() if weight > 0} | set(current)
        pit_valid = not any((time.isoformat(), asset) in bad_rows for asset in involved)
        metrics = strategy_risk_inputs(
            prefix, weights, [row["equity"] for row in account.marks[:-1]],
            mark["equity"], asset_classes, pit_valid=pit_valid, current_weights=current,
        )
        risk = evaluate_risk_rules(rules, metrics)
        approved_checks = _approved_risk(metrics, weights, policy)
        if status == "READY" and idx + 1 < len(account.times):
            blocked = [rule for rule in [*risk, *approved_checks] if rule["action"] == "GATE" and rule["status"] != "PASS"]
            if blocked:
                status = "NEEDS_DATA" if any(rule["status"] == "NEEDS_DATA" for rule in blocked) else "BLOCKED_RISK"
            else:
                pending = (time, weights)
                status = "SCHEDULED"
        observed.add(status)
        decisions.append({
            "observed_at": time.isoformat(), "status": status, "target_weights": weights,
            "reason": selection.get("reason"), "diagnostics": selection.get("diagnostics", {}),
            "risk": risk, "approved_policy_checks": approved_checks,
        })
    ledger = account.result()
    benchmark = _benchmark(account, template["benchmark"])
    if ledger["status"] == "NEEDS_DATA" or benchmark["status"] == "NEEDS_DATA":
        status = "NEEDS_DATA"
    elif "SCHEDULED" in observed:
        status = "COMPLETE"
    elif "RESEARCH_ONLY" in observed:
        status = "RESEARCH_ONLY"
    elif "BLOCKED_RISK" in observed:
        status = "BLOCKED_RISK"
    else:
        status = "NEEDS_DATA"
    output = {**result, "status": status, "decisions": decisions, "ledger": ledger, "benchmark": benchmark}
    return {**output, "fingerprint": stable_digest(output)}


def strategy_sample():
    """Compatibility entry point for the Phase 26 worker acceptance fixture."""
    from .strategy_sample import strategy_sample as build_sample

    return build_sample()

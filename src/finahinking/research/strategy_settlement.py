"""Observe fixed, non-overlapping windows of a published paper strategy ledger."""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from datetime import UTC, date, datetime
from math import isfinite

import pandas as pd

from finahinking.data.panel_contracts import panel_time
from finahinking.data.research_panel import native_observation_index, native_quote_at

from .contracts import stable_digest
from .job_queue import JobStatus, _ref
from .learning_review import _decode, _json
from .strategy_catalog import StrategyCatalog
from .strategy_jobs import load_bound_strategy_panel
from .strategy_runner import _boolean

METHOD = "published-strategy-windows.v1"
HORIZON = 5
MIN_SAMPLES = 3
HISTORICAL_FIXTURES = frozenset({"SAMPLE", "SYNTHETIC"})
# A live feed and its captured copy can carry the same economic observations.
# Keep the original source label on the report, receipt and policy scope.
ECONOMIC_MODE = {"LIVE": "CAPTURED", "CAPTURED": "CAPTURED", "SAMPLE": "SAMPLE",
                 "SYNTHETIC": "SAMPLE", "PAPER": "PAPER"}


def _cutoff(value, now):
    if not isinstance(value, str):
        raise TypeError("观察截止日需要 ISO 日期或带时区时间")
    if len(value) == 10:
        day = date.fromisoformat(value)
        if day > now.date():
            raise ValueError("观察截止日不能在未来")
        return (pd.Timestamp(day, tz="UTC") + pd.Timedelta(days=1, nanoseconds=-1)).isoformat()
    stamp = panel_time(value)
    if stamp > now:
        raise ValueError("观察截止时间不能在未来")
    return stamp.isoformat()


def _observed_scope(members, classes):
    kinds = {classes.get(asset) for asset in members}
    if kinds == {"EQUITY", "ETF"}:
        return "MIXED"
    return next(iter(kinds)) if kinds in ({"EQUITY"}, {"ETF"}) else "UNKNOWN"


def _positive(value):
    return type(value) in (int, float) and isfinite(value) and value > 0


class _LedgerWindows:
    """Read the published account and benchmark. A cutoff can never move their marks."""

    def __init__(self, source):
        self.source = source
        self.panel = source["panel"]
        self.times = self.panel.contract.observed_at
        self.frame = self.panel.frame
        self.result = source["result"]
        self.ledger = self.result["ledger"] or {}
        self.benchmark = self.result["benchmark"] or {}
        self.members = source["benchmark_instruments"]
        self.publication_clock = (None if source["data_mode"] in HISTORICAL_FIXTURES
                                  else panel_time(source["published_at"]))
        self.marks = {r["timestamp"]: r for r in self.ledger.get("marks", ())}
        self.bench_marks = {r["timestamp"]: r for r in self.benchmark.get("marks", ())}
        self.decisions = {r["observed_at"]: r for r in self.result["decisions"]}
        self.fills = self.ledger.get("fills", ())
        self.orders = self.ledger.get("orders", ())
        self.native = native_observation_index(source["snapshot"], self.panel.contract.timezones)
        self.releases = self._settlement_times()

    def _settlement_times(self):
        # The account's resale locks and sale receivables advance on that asset's
        # explicitly sourced sessions only. A future calendar entry with no actual
        # account mark cannot certify a release.
        sessions = defaultdict(list)
        for row in self.panel.audit["selections"]:
            time, asset = row["observed_at"], row["instrument"]
            if (row["field"] == "is_session" and time in self.marks
                    and row["attachment_ref"] == self.panel.contract.calendar_ref
                    and _boolean(self.frame.at[(panel_time(time), asset), "is_session"], True)):
                sessions[asset].append(time)
        releases = {}
        for fill in self.fills:
            delay, booked = fill.get("settlement_sessions"), fill["timestamp"]
            release = None
            if type(delay) is int and delay >= 0:
                dates = sessions[fill["instrument"]]
                index = bisect_right(dates, booked) + delay - 1
                if delay == 0:
                    release = booked
                elif index < len(dates):
                    release = dates[index]
            releases[fill["order_id"]] = release
        return releases

    def _mark_reasons(self, stamps, orders, cutoff):
        reasons = set()
        zones = self.panel.contract.timezones
        assets = set(self.members) | {row["instrument"] for row in orders}
        for ts in stamps:
            assets.update(self.marks.get(ts, {}).get("position_marks", {}))
        evidence_cutoff = min(cutoff, self.source["snapshot"].as_of)
        for stamp in stamps:
            time = panel_time(stamp)
            for asset in assets:
                if asset not in zones:
                    reasons.add("MISSING_PRICE")
                    continue
                quote = native_quote_at(self.native, asset, time, zones[asset], self.source["snapshot"].as_of)
                if quote is None:
                    day = time.tz_convert(zones[asset]).date()
                    disclosed_late = any(
                        r.timestamp <= time and r.available_at is not None and time < r.available_at <= evidence_cutoff
                        for r in self.native.get((asset, day), ()))
                    reasons.add("LATE_PRICE" if disclosed_late else "MISSING_PRICE")
                elif quote.suspended is True:
                    reasons.add("SUSPENDED")
                elif quote.suspended is None:
                    reasons.add("UNKNOWN_TRADEABILITY")
        reasons = sorted(reasons)
        if not self.ledger or not all(_positive(self.marks.get(ts, {}).get("equity")) for ts in stamps):
            reasons.append("LEDGER_MARK_UNAVAILABLE")
            failure = self.ledger.get("failure")
            if failure and failure["timestamp"] <= stamps[-1]:
                reasons.append(failure["reason"])
        if (not self.members or self.benchmark.get("instruments") != self.members
                or any(self.benchmark.get(k) != v for k, v in self.source["benchmark_definition"].items())
                or not all(_positive(self.bench_marks.get(ts, {}).get("equity")) for ts in stamps)):
            reasons.append("BENCHMARK_UNAVAILABLE")
            if self.benchmark.get("reason"):
                reasons.append(self.benchmark["reason"])
        return reasons

    def _window_settlements(self, start, end, cutoff):
        """Include new bookings and any prior inventory or proceeds still locked at the start."""
        settlements = []
        for fill in self.fills:
            booked, release = fill["timestamp"], self.releases[fill["order_id"]]
            if booked > end or (booked <= start and release is not None and release <= start):
                continue
            settled = release is not None and panel_time(release) <= cutoff
            settlements.append({
                "order_id": fill["order_id"], "instrument": fill["instrument"], "side": fill["side"],
                "booked_at": booked, "settlement_sessions": fill.get("settlement_sessions"),
                "settled_at": release if settled else None, "status": "SETTLED" if settled else "UNSETTLED",
            })
        return settlements

    def _window(self, stamps, cutoff):
        start, end = stamps[0], stamps[-1]
        window = {"start_at": start, "end_at": end}
        if len(stamps) < HORIZON + 1 or panel_time(end) > cutoff:
            return {**window, "end_at": end if len(stamps) == HORIZON + 1 else None,
                    "status": "WINDOW_NOT_MATURE", "reasons": ["WINDOW_NOT_MATURE"]}
        policy_at = self.source["policy_available_at"]
        if policy_at and panel_time(start) < panel_time(policy_at):
            return {**window, "status": "POLICY_NOT_EFFECTIVE", "reasons": ["POLICY_NOT_EFFECTIVE"]}
        if self.source["execution_mode"] != "PAPER_LONG_ONLY":
            return {**window, "status": "RESEARCH_ONLY", "reasons": ["RESEARCH_ONLY"]}
        fills = [row for row in self.fills if start < row["timestamp"] <= end]
        orders = [row for row in self.orders if start < row["timestamp"] <= end]
        reasons = self._mark_reasons(stamps, orders, cutoff)
        order_reasons = sorted({row["reason"] for row in orders if row["reason"]})
        suppressed = [
            {"decision_at": ts, "status": r["status"], "reason": r.get("reason"),
             "gates": [rule for rule in [*r.get("risk", ()), *r.get("approved_policy_checks", ())]
                       if rule["action"] == "GATE" and rule["status"] != "PASS"]}
            for ts, r in self.decisions.items() if start <= ts < end and r["status"] in {"BLOCKED_RISK", "NEEDS_DATA"}
        ]
        if not fills:
            statuses = {r["status"] for r in suppressed}
            if "SUSPENDED" in order_reasons and "SUSPENDED" not in reasons:
                reasons.append("SUSPENDED")
            if "BLOCKED_RISK" in statuses:
                reasons.append("RISK_SUPPRESSED")
            if "NEEDS_DATA" in statuses:
                reasons.append("DECISION_NEEDS_DATA")
            reasons.append("NO_FILLS")

        settlements = self._window_settlements(start, end, cutoff)
        if any(row["status"] == "UNSETTLED" for row in settlements):
            reasons.append("UNSETTLED_TRADES")
        decision_times = sorted({f["decision_at"] for f in fills})
        if any(ts not in self.decisions or self.decisions[ts]["status"] != "SCHEDULED"
               or not self.decisions[ts].get("risk") for ts in decision_times):
            reasons.append("DECISION_UNVERIFIED")
        window.update(fill_count=len(fills), order_count=len(orders), execution_reasons=order_reasons,
                      settlements=settlements, suppressed_decisions=suppressed)
        if reasons:
            return {**window, "status": reasons[0], "reasons": reasons}

        risk = [
            {"decision_at": ts, "status": self.decisions[ts]["status"],
             "target_weights": self.decisions[ts]["target_weights"], "rules": self.decisions[ts]["risk"],
             "approved_policy_checks": self.decisions[ts].get("approved_policy_checks", [])}
            for ts in decision_times
        ]
        opening, closing = (self.marks[ts]["equity"] for ts in (start, end))
        bench_open, bench_close = (self.bench_marks[ts]["equity"] for ts in (start, end))
        net, benchmark_return = closing / opening - 1, bench_close / bench_open - 1
        available = max(end, *(row["settled_at"] for row in settlements))
        if self.publication_clock is not None:
            available = max(panel_time(available), self.publication_clock).isoformat()
        # Identify the economic decision and market interval independently of the
        # submitted job, file/snapshot, requested cutoff and realized outcomes.
        sample_ref = stable_digest({
            "method": METHOD, "data_mode": ECONOMIC_MODE[self.source["data_mode"]], "currency": self.ledger["currency"],
            "instruments": self.members, "start_at": start, "end_at": end,
            "signals": [[d["decision_at"], d["target_weights"]] for d in risk],
        })
        return {**window, "status": "SETTLED", "sample_ref": sample_ref, "available_at": available,
                "opening_equity": opening, "closing_equity": closing,
                "benchmark_opening_equity": bench_open, "benchmark_closing_equity": bench_close,
                "net_return": net, "benchmark_return": benchmark_return, "excess_return": net - benchmark_return,
                "fees": sum(f["fees"] for f in fills), "slippage": sum(f["slippage"] for f in fills),
                "fill_order_ids": [f["order_id"] for f in fills], "fills_digest": stable_digest(fills), "risk": risk}

    def at(self, cutoff):
        if self.publication_clock is not None and self.publication_clock > cutoff:
            return [], [], ["SOURCE_NOT_YET_AVAILABLE"]
        samples, excluded = [], []
        for i in range(0, max(1, len(self.times) - 1), HORIZON):
            if i and panel_time(self.times[i]) >= cutoff:
                break
            row = self._window(self.times[i:i+HORIZON+1], cutoff)
            (samples if row["status"] == "SETTLED" else excluded).append(row)
        reasons = sorted({r for row in excluded for r in row["reasons"]})
        return samples, excluded, reasons


class StrategySettlementService:
    def __init__(self, app):
        self.app = app
        with app.queue._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS strategy_observations (
                    scope_id TEXT NOT NULL, observation_ref TEXT NOT NULL,
                    payload TEXT NOT NULL, payload_digest TEXT NOT NULL,
                    PRIMARY KEY(scope_id, observation_ref));
                CREATE TABLE IF NOT EXISTS strategy_economic_samples (
                    scope_id TEXT NOT NULL, sample_ref TEXT NOT NULL,
                    first_observation_ref TEXT NOT NULL, outcome_digest TEXT NOT NULL,
                    PRIMARY KEY(scope_id, sample_ref));
            """)

    def _sources(self, run_id=None):
        query = """SELECT j.job_id FROM jobs j JOIN request_snapshots r ON r.task_ref=j.task_ref
                   WHERE j.status=? AND json_extract(r.payload,'$.research_plan.validation_spec.scope_id')=?
                   AND json_type(r.payload,'$.research_plan.validation_spec.strategy_study')='object'"""
        args = [JobStatus.COMPLETED.value, self.app.scope_id]
        if run_id is not None:
            query += " AND json_extract(r.payload,'$.run_id')=?"
            args.append(_ref(run_id, "run_id"))
        with self.app.queue._connect() as db:
            return [row[0] for row in db.execute(query + " ORDER BY j.created_at DESC,j.job_id", args)]

    def _publication(self, job_id):
        from .application_service import verify_research_receipt

        job = self.app.queue.get(job_id)
        if job.status is not JobStatus.COMPLETED:
            raise ValueError("策略需要已完成并发布的报告")
        publication = verify_research_receipt(self.app.root, self.app.queue, job, job.result_ref)
        request, manifest = publication["request"], publication["manifest"]
        spec = request.research_plan.validation_spec
        study = manifest["source_snapshot"].get("strategy_study") or {}
        binding, result = study.get("binding"), study.get("result")
        if (spec["scope_id"] != self.app.scope_id or not binding or not result
                or binding != spec.get("strategy_study") or study.get("paper_only") is not True
                or study.get("production_write") is not False or result.get("paper_only") is not True
                or result.get("production_write") is not False or study.get("snapshot_ref") != request.snapshot_ref
                or binding["contract"]["snapshot_ref"] != request.snapshot_ref
                or result.get("template_id") != binding["template_id"] or result.get("parameters") != binding["parameters"]
                or result.get("panel_fingerprint") != binding["panel_ref"]
                or result.get("policy_fingerprint") != binding["policy_digest"]
                or binding["policy_ref"] != request.previous_policy_fingerprint
                or binding["policy_digest"] != spec.get("policy_fingerprint")
                or study.get("fingerprint") != stable_digest({"binding": binding, "result": result})):
            raise ValueError("策略发布结果与原始队列、模板、面板或政策绑定不一致")
        snapshot = self.app.snapshots.load(request.snapshot_ref)
        if study.get("data_mode") != snapshot.mode:
            raise ValueError("策略发布结果的数据来源模式不一致")
        template = StrategyCatalog().template(binding["template_id"])
        if (binding["template_digest"] != stable_digest(template)
                or result["template_fingerprint"] != template["fingerprint"]
                or result["rule_fingerprints"] != template["risk_rule_fingerprints"]):
            raise ValueError("策略报告与冻结的模板或风险规则指纹不一致")
        classes = snapshot.provenance.get("asset_classes", {})
        universe = template["benchmark"]["universe"]
        members = [a for a in binding["contract"]["instrument_ids"] if universe == "all" or classes.get(a) == universe.upper()]
        available_day = request.as_of
        if snapshot.mode not in HISTORICAL_FIXTURES:
            available_day = max(available_day, panel_time(manifest["created_at"]).date())
        summary = {
            "job_id": job_id, "run_id": request.run_id, "template_id": binding["template_id"],
            "asset_scope": _observed_scope(members, classes), "instruments": binding["contract"]["instrument_ids"],
            "benchmark_instruments": members, "benchmark_definition": template["benchmark"],
            "execution_mode": template["execution_mode"], "as_of": request.as_of.isoformat(),
            "observation_date": available_day.isoformat(),
            "published_at": manifest["created_at"], "data_mode": snapshot.mode,
            "availability_basis": "HISTORICAL_FIXTURE" if snapshot.mode in HISTORICAL_FIXTURES else "PUBLISHED_REPORT",
            "policy_ref": binding["policy_ref"], "snapshot_ref": snapshot.fingerprint,
            "report_digest": stable_digest(manifest), "ledger_digest": stable_digest(result["ledger"]),
            "report_url": f"/research/{request.run_id}/report/complete", "strategy_status": result["status"],
        }
        return summary, binding, result, snapshot

    def list_sources(self):
        sources = []
        for job_id in self._sources():
            try:
                sources.append(self._publication(job_id)[0])
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return sources

    def replay_inputs(self, run_id):
        ids = self._sources(run_id)
        if len(ids) != 1:
            raise ValueError("当前工作区缺少此运行的已发布策略报告")
        summary, binding, result, snapshot = self._publication(ids[0])
        policy = self.app.learning.store.policy(binding["policy_ref"], as_of=summary["as_of"])
        panel, classes = load_bound_strategy_panel(self.app.snapshots, binding, snapshot, policy)
        return {**summary, "binding": binding, "result": result, "snapshot": snapshot, "panel": panel,
                "asset_classes": classes, "policy": policy["effective"], "policy_available_at": policy.get("available_at")}

    def _identity(self, run_id, cutoff, method=METHOD):
        return stable_digest([method, self.app.scope_id, run_id, cutoff])

    def _evidence(self, run_id, as_of, evaluated_at):
        source = self.replay_inputs(run_id)
        cutoff = min(panel_time(as_of), panel_time(evaluated_at))
        samples, excluded, reasons = _LedgerWindows(source).at(cutoff)
        fields = ("run_id", "job_id", "template_id", "asset_scope", "instruments", "data_mode", "policy_ref",
                  "snapshot_ref", "report_digest", "ledger_digest", "report_url", "published_at",
                  "benchmark_instruments", "benchmark_definition", "availability_basis")
        return {**{key: source[key] for key in fields},
                "scope_id": self.app.scope_id, "method": METHOD, "source_as_of": source["as_of"],
                "observation_ref": self._identity(run_id, as_of), "as_of": as_of, "evaluated_at": evaluated_at,
                "cutoff_at": cutoff.isoformat(), "horizon_intervals": HORIZON, "minimum_samples": MIN_SAMPLES,
                "samples": samples, "excluded_windows": excluded, "reasons": reasons, "paper_only": True}

    def _classify(self, db, evidence, *, register):
        ref, scope = evidence["observation_ref"], self.app.scope_id
        selected, excluded = [], list(evidence["excluded_windows"])
        reasons = set(evidence["reasons"])
        for sample in evidence["samples"]:
            key = sample["sample_ref"]
            # The publication date, order IDs and report-specific diagnostics can
            # differ between reports of the same economic decision and outcome.
            outcome = stable_digest({k: sample[k] for k in ("opening_equity", "closing_equity",
                "benchmark_opening_equity", "benchmark_closing_equity", "fees", "slippage", "fill_count")})
            if register:
                db.execute("INSERT OR IGNORE INTO strategy_economic_samples VALUES (?,?,?,?)",
                           (scope, key, ref, outcome))
            owner = db.execute("""SELECT first_observation_ref,outcome_digest FROM strategy_economic_samples
                                  WHERE scope_id=? AND sample_ref=?""", (scope, key)).fetchone()
            if not owner:
                raise ValueError("经济样本登记缺失")
            if owner["outcome_digest"] != outcome:
                reason = "CONFLICTING_ECONOMIC_SAMPLE"
                excluded.append({k: sample[k] for k in ("sample_ref", "start_at", "end_at", "fill_count")}
                                | {"status": reason, "reasons": [reason]})
                reasons.add(reason)
                continue
            selected.append({**sample, "duplicate": owner["first_observation_ref"] != ref,
                             "first_observation_ref": owner["first_observation_ref"]})
        duplicates = sum(s["duplicate"] for s in selected)
        count = len(selected)
        if duplicates:
            reasons.add("DUPLICATE_SOURCE")
        if count < MIN_SAMPLES:
            reasons.add("INSUFFICIENT_SAMPLES")
        if "SOURCE_NOT_YET_AVAILABLE" in reasons:
            status = "SOURCE_NOT_YET_AVAILABLE"
        elif count and duplicates == count:
            status = "DUPLICATE_SOURCE"
        else:
            status = "SETTLED" if count >= MIN_SAMPLES else "INSUFFICIENT_SAMPLES"
        return {**evidence, "status": status, "reasons": sorted(reasons), "samples": selected,
                "excluded_windows": excluded, "sample_count": count, "new_sample_count": count-duplicates,
                "duplicate_count": duplicates}

    def observe(self, run_id, as_of):
        now = pd.Timestamp(datetime.now(UTC))
        as_of = _cutoff(as_of, now)
        ref = self._identity(_ref(run_id, "run_id"), as_of)
        try:
            record = self.get(ref)
        except KeyError:
            record = None
        if record is not None:
            return self.verify(ref)
        evidence = self._evidence(run_id, as_of, now.isoformat())
        with self.app.queue._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM strategy_observations WHERE scope_id=? AND observation_ref=?",
                                  (self.app.scope_id, ref)).fetchone()
            if existing:
                return self._checked(existing)
            record = self._classify(db, evidence, register=True)
            db.execute("INSERT INTO strategy_observations VALUES (?,?,?,?)",
                       (self.app.scope_id, ref, _json(record), stable_digest(record)))
        return record

    def _checked(self, row):
        record = _decode(row)
        if (record["scope_id"] != self.app.scope_id or record["observation_ref"] != row["observation_ref"]
                or record["observation_ref"] != self._identity(record["run_id"], record["as_of"], record["method"])):
            raise ValueError("冻结的策略观察身份不一致")
        return record

    def get(self, observation_ref):
        with self.app.queue._connect() as db:
            row = db.execute("SELECT * FROM strategy_observations WHERE scope_id=? AND observation_ref=?",
                             (self.app.scope_id, _ref(observation_ref, "observation_ref"))).fetchone()
        if row is None:
            raise KeyError("策略观察不存在")
        return self._checked(row)

    def list_observations(self):
        with self.app.queue._connect() as db:
            return [self._checked(row) for row in db.execute(
                "SELECT * FROM strategy_observations WHERE scope_id=? ORDER BY rowid DESC", (self.app.scope_id,))]

    def observed_fills(self, observation_ref):
        """Read the actual bookings referenced by the receipt, including carried-in locks."""
        receipt = self.get(observation_ref)
        source, _, result, _ = self._publication(receipt["job_id"])
        if any(receipt[k] != source[k] for k in ("run_id", "report_digest", "ledger_digest")):
            raise ValueError("成交来源与已冻结的报告或账本不一致")
        fills = (result["ledger"] or {}).get("fills", ())
        by_order = {row["order_id"]: row for row in fills}
        selected = set()
        for window in (*receipt["samples"], *receipt["excluded_windows"]):
            # Older receipts can identify the bookings in a selected window by
            # their order IDs and the digest of the original ordered fill list.
            if "fill_order_ids" in window:
                booked = [by_order.get(key) for key in window["fill_order_ids"]]
                if None in booked or stable_digest(booked) != window["fills_digest"]:
                    raise ValueError("窗口成交明细与原始账本摘要不一致")
                selected.update(window["fill_order_ids"])
            for settlement in window.get("settlements", ()):
                fill = by_order.get(settlement["order_id"])
                if (fill is None or fill["timestamp"] != settlement["booked_at"]
                        or fill["instrument"] != settlement["instrument"]):
                    raise ValueError("结算资产、成交时间或原始订单不一致")
                selected.add(fill["order_id"])
        return [row for row in fills if row["order_id"] in selected]

    def verify(self, observation_ref):
        """Rebuild decisions, marks, settlement and duplicate claims from their original sources."""
        record = self.get(observation_ref)
        if record["method"] != METHOD or panel_time(record["evaluated_at"]) > pd.Timestamp.now(tz="UTC"):
            raise ValueError("策略观察方法版本或证据登记时间不可核验")
        evidence = self._evidence(record["run_id"], record["as_of"], record["evaluated_at"])
        with self.app.queue._connect() as db:
            current = self._classify(db, evidence, register=False)
        if current != record:
            raise ValueError("策略观察与原始发布账本或经济样本登记不一致")
        return record

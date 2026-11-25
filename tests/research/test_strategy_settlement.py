"""Settled strategy windows can be traced to the actual queued report and account."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime

import pandas as pd
import pytest

from finahinking.data.panel_contracts import PanelContract, PanelFieldAttachment
from finahinking.data.research_fields import ResearchFieldStore
from finahinking.research.application_service import ResearchApplicationService
from finahinking.research.contracts import stable_digest
from finahinking.research.strategy_sample import strategy_sample
from finahinking.research.strategy_settlement import HORIZON, StrategySettlementService


def publish(app, inputs, template, key):
    job = app.run_strategy_template({**inputs, "template_id": template, "idempotency_key": key})
    deadline = time.monotonic() + 110
    while job["status"] not in {"completed", "failed", "cancelled"} and time.monotonic() < deadline:
        time.sleep(.15)
        job = app.get_job(job["job_id"])
    assert (job["status"], job["report_status"]) == ("completed", "AVAILABLE"), job
    return job


@pytest.fixture(scope="module")
def published(tmp_path_factory):
    root = tmp_path_factory.mktemp("strategy-settlement")
    app = ResearchApplicationService(root, allow_model_calls=False)
    sample = app.create_strategy_sample()
    inputs = {k: sample[k] for k in ("contract", "attachment_refs")}
    full = PanelContract.from_dict(sample["contract"])
    assets = ["EQA", "EQB", "EQC", "EQD"]
    stock = PanelContract(full.snapshot_ref, assets, full.observed_at, full.field_units, full.scope_id,
                          {a: full.currencies[a] for a in assets}, {a: full.timezones[a] for a in assets},
                          full.calendar_ref, full.universe_ref, full.adjustment_ref, full.trade_rule_ref)
    app.start()
    try:
        jobs = {t: publish(app, inputs, t, "source-" + t) for t in ("etf-relative-strength", "equal-weight")}
        jobs["short-term-reversal"] = publish(app, {**inputs, "contract": stock.to_dict()},
                                              "short-term-reversal", "source-stock-reversal")
        yield app, jobs, inputs
    finally:
        app.close()


@pytest.mark.parametrize(("template", "scope"), [
    ("short-term-reversal", "EQUITY"), ("etf-relative-strength", "ETF"), ("equal-weight", "MIXED"),
])
def test_published_paper_windows_reconcile_with_original_marks_fills_and_risk(published, template, scope):
    app, jobs, inputs = published
    service = StrategySettlementService(app)
    job = jobs[template]
    manifest_path = app.runs.get_run(job["run_id"])["bundle"] / "manifest.json"
    original_manifest = manifest_path.read_bytes()
    queued = app.queue.get(job["job_id"])
    original_request = app._request(queued)
    original_policy = app.learning.store.policy(job["policy_ref"])
    cutoff = inputs["contract"]["observed_at"][-1]
    receipt = service.observe(job["run_id"], cutoff)
    assert receipt["status"] == "SETTLED", (template, receipt["reasons"], receipt["sample_count"])
    assert receipt["sample_count"] >= 3
    assert receipt["asset_scope"] == scope
    assert receipt["data_mode"] == "SAMPLE"
    assert receipt["policy_ref"] == job["policy_ref"] and receipt["snapshot_ref"] == job["snapshot_ref"]
    result = job["strategy_study"]["result"]
    ledger = result["ledger"]
    marks = {r["timestamp"]: r for r in ledger["marks"]}
    bench = {r["timestamp"]: r for r in result["benchmark"]["marks"]}
    decisions = {r["observed_at"]: r for r in result["decisions"]}
    assert receipt["report_digest"] == stable_digest(app.runs.get_run(job["run_id"])["manifest"])
    assert receipt["ledger_digest"] == stable_digest(ledger)
    assert receipt["benchmark_instruments"] == result["benchmark"]["instruments"]
    assert len({r["sample_ref"] for r in receipt["samples"]}) == receipt["sample_count"]
    for prior, current in zip(receipt["samples"], receipt["samples"][1:]):
        assert prior["end_at"] <= current["start_at"]
    for sample in receipt["samples"]:
        start, end = sample["start_at"], sample["end_at"]
        assert inputs["contract"]["observed_at"].index(end) - inputs["contract"]["observed_at"].index(start) == HORIZON
        assert end <= sample["available_at"] <= cutoff
        fills = [f for f in ledger["fills"] if start < f["timestamp"] <= end]
        assert sample["fill_count"] == len(fills) > 0
        assert sample["net_return"] == pytest.approx(marks[end]["equity"] / marks[start]["equity"] - 1)
        assert sample["benchmark_return"] == pytest.approx(bench[end]["equity"] / bench[start]["equity"] - 1)
        assert sample["excess_return"] == pytest.approx(sample["net_return"] - sample["benchmark_return"])
        assert sample["fees"] == pytest.approx(sum(f["fees"] for f in fills))
        assert sample["slippage"] == pytest.approx(sum(f["slippage"] for f in fills))
        for evidence in sample["risk"]:
            assert decisions[evidence["decision_at"]]["risk"] == evidence["rules"]
    assert service.verify(receipt["observation_ref"]) == receipt
    assert manifest_path.read_bytes() == original_manifest
    assert app._request(app.queue.get(job["job_id"])) == original_request
    assert app.learning.store.policy(job["policy_ref"]) == original_policy


def test_identical_economic_windows_deduplicate_across_runs_cutoffs_retries_and_restart(published):
    app, jobs, inputs = published
    service = StrategySettlementService(app)
    source = jobs["equal-weight"]
    stored = service.observe(source["run_id"], inputs["contract"]["observed_at"][-1])
    snapshot, (fields,), contract, _ = strategy_sample()
    snapshot = replace(snapshot, mode="SYNTHETIC")
    app.snapshots.save(snapshot)
    ref = ResearchFieldStore(app.snapshots).save(PanelFieldAttachment(
        snapshot.fingerprint, fields.units, fields.records, fields.source_declaration))
    alias_contract = PanelContract(snapshot.fingerprint, contract.instrument_ids, contract.observed_at,
                                  contract.field_units, contract.scope_id, contract.currencies,
                                  contract.timezones, ref, ref, ref, ref)
    different_run = publish(app, {"contract": alias_contract.to_dict(), "attachment_refs": [ref]},
                            "equal-weight", "same-economic-strategy")
    assert source["run_id"] != different_run["run_id"]
    with ThreadPoolExecutor(max_workers=2) as threads:
        duplicates = list(threads.map(lambda _: service.observe(different_run["run_id"], "2025-05-01"), range(2)))
    duplicate = duplicates[0]
    assert duplicate == duplicates[1]
    assert duplicate["status"] == "DUPLICATE_SOURCE"
    assert (stored["data_mode"], duplicate["data_mode"]) == ("SAMPLE", "SYNTHETIC")
    assert duplicate["new_sample_count"] == 0
    assert duplicate["duplicate_count"] == stored["sample_count"]
    assert {s["sample_ref"] for s in duplicate["samples"]} == {s["sample_ref"] for s in stored["samples"]}
    assert all(s["first_observation_ref"] == stored["observation_ref"] for s in duplicate["samples"])
    reopened = ResearchApplicationService(app.root, allow_model_calls=False)
    try:
        restarted = StrategySettlementService(reopened)
        assert restarted.verify(duplicate["observation_ref"]) == duplicate
        assert restarted.get(stored["observation_ref"]) == stored
        assert restarted.observe(source["run_id"], "2025-05-03")["new_sample_count"] == 0
        assert {s["asset_scope"] for s in restarted.list_sources()} == {"EQUITY", "ETF", "MIXED"}
        assert {r["observation_ref"] for r in restarted.list_observations()} >= {
            duplicate["observation_ref"], stored["observation_ref"]}
        with pytest.raises(ValueError, match="未来"):
            restarted.observe(source["run_id"], "2099-01-01")
        with pytest.raises(ValueError, match="带时区"):
            restarted.observe(source["run_id"], "2025-03-20T16:00:00")
    finally:
        reopened.close()


def test_incomplete_window_never_backfills_a_result(published):
    app, jobs, inputs = published
    service = StrategySettlementService(app)
    times = inputs["contract"]["observed_at"]
    early = service.observe(jobs["equal-weight"]["run_id"], times[2])
    assert early["status"] == "INSUFFICIENT_SAMPLES"
    assert early["sample_count"] == 0 and early["samples"] == []
    assert early["excluded_windows"] == [{"start_at": times[0], "end_at": times[5],
                                          "status": "WINDOW_NOT_MATURE", "reasons": ["WINDOW_NOT_MATURE"]}]
    assert service.verify(early["observation_ref"]) == early


def test_late_quotes_rejected_orders_and_t_plus_sessions_keep_explicit_reasons(tmp_path):
    app = ResearchApplicationService(tmp_path, allow_model_calls=False)
    snapshot, (attachment,), contract, _classes = strategy_sample()
    times = contract.observed_at[:41]
    selected = [r for r in snapshot.observations if r.timestamp <= pd.Timestamp(times[-1])]
    # A suspended execution, a raw price available after its mark, and a buy
    # lock longer than the remaining asset sessions all come from worker input.
    selected = [
        replace(r, suspended=True) if r.instrument == "EQA" and r.timestamp == pd.Timestamp(times[11])
        else replace(r, available_at=r.timestamp + pd.Timedelta(hours=1))
        if r.instrument == "EQA" and r.timestamp == pd.Timestamp(times[35]) else r
        for r in selected
    ]
    snapshot = replace(snapshot, dataset_id="strategy-settlement-exclusions", observations=tuple(selected),
                       as_of=pd.Timestamp(times[-1]))
    records = [{**r, "settlement_days": 50 if r["instrument"] == "EQB" and r["timestamp"] < times[16] else 1}
               for r in attachment.records if r["timestamp"] <= times[-1]]
    attachment = PanelFieldAttachment(snapshot.fingerprint, attachment.units, records, "Frozen exclusions")
    app.snapshots.save(snapshot)
    ref = ResearchFieldStore(app.snapshots).save(attachment)
    c = PanelContract(snapshot.fingerprint, contract.instrument_ids, times, contract.field_units,
                      contract.scope_id, contract.currencies, contract.timezones, ref, ref, ref, ref)
    app.start()
    try:
        job = publish(app, {"contract": c.to_dict(), "attachment_refs": [ref]}, "equal-weight", "exclusions")
        service = StrategySettlementService(app)
        before_disclosure = service.observe(job["run_id"], times[35])
        assert "MISSING_PRICE" in before_disclosure["reasons"] and "LATE_PRICE" not in before_disclosure["reasons"]
        receipt = service.observe(job["run_id"], times[-1])
        assert receipt["status"] == "INSUFFICIENT_SAMPLES"
        assert {"SUSPENDED", "LATE_PRICE", "UNSETTLED_TRADES"} <= set(receipt["reasons"])
        assert any(row["status"] == "UNSETTLED" and row["settled_at"] is None
                   for window in receipt["excluded_windows"] for row in window.get("settlements", ()))
        assert any(window.get("suppressed_decisions") for window in receipt["excluded_windows"])
        assert any(r["reason"] == "SUSPENDED" for r in job["strategy_study"]["result"]["ledger"]["orders"])
        assert not any(s["start_at"] <= times[35] <= s["end_at"] for s in receipt["samples"])
        assert service.observe(job["run_id"], "2025-08-01")["sample_count"] == receipt["sample_count"]
    finally:
        app.close()


def test_window_settles_only_on_the_declared_asset_session_and_keeps_original_return(tmp_path):
    app = ResearchApplicationService(tmp_path, allow_model_calls=False)
    snapshot, (attachment,), contract, _classes = strategy_sample()
    times = contract.observed_at[:30]
    records = [{**row, "settlement_days": 8,
                "is_session": not (row["instrument"] == "EQA" and row["timestamp"] == times[14])}
               for row in attachment.records if row["timestamp"] <= times[-1]]
    attachment = PanelFieldAttachment(snapshot.fingerprint, attachment.units, records, "Eight asset sessions")
    app.snapshots.save(snapshot)
    ref = ResearchFieldStore(app.snapshots).save(attachment)
    c = PanelContract(snapshot.fingerprint, contract.instrument_ids, times, contract.field_units,
                      contract.scope_id, contract.currencies, contract.timezones, ref, ref, ref, ref)
    app.start()
    try:
        job = publish(app, {"contract": c.to_dict(), "attachment_refs": [ref]}, "equal-weight", "asset-clock")
        service = StrategySettlementService(app)
        prior = service.observe(job["run_id"], times[19])
        assert next(r for r in prior["excluded_windows"] if r["end_at"] == times[15])["status"] == "UNSETTLED_TRADES"
        matured = service.observe(job["run_id"], times[20])
        sample = next(r for r in matured["samples"] if r["end_at"] == times[15])
        assert sample["available_at"] == times[20] and sample["fill_count"] > 0
        assert next(row["settled_at"] for row in sample["settlements"] if row["instrument"] == "EQA") == times[20]
        following = next(row for row in [*matured["samples"], *matured["excluded_windows"]]
                         if row["start_at"] == times[15] and row["end_at"] == times[20])
        assert any(row["booked_at"] < following["start_at"] for row in following["settlements"])
        actual = job["strategy_study"]["result"]["ledger"]["fills"]
        selected_ids = {row["order_id"] for window in [*matured["samples"], *matured["excluded_windows"]]
                        for row in window.get("settlements", ())}
        assert service.observed_fills(matured["observation_ref"]) == [f for f in actual if f["order_id"] in selected_ids]
        marks = job["strategy_study"]["result"]["ledger"]["marks"]
        assert sample["net_return"] == pytest.approx(marks[15]["equity"] / marks[10]["equity"] - 1)
        assert service.get(prior["observation_ref"]) == prior
    finally:
        app.close()


def test_worker_uses_only_bound_assets_and_dates_even_when_snapshot_contains_later_quotes(tmp_path, monkeypatch):
    app = ResearchApplicationService(tmp_path, allow_model_calls=False)
    source, (rules,), original, _ = strategy_sample()
    close_time = pd.Timedelta(hours=16)
    times = [(pd.Timestamp(t) + close_time).isoformat() for t in original.observed_at[6:71]]
    observations = [replace(q, timestamp=q.timestamp + close_time, available_at=q.available_at + close_time)
                    for q in source.observations]
    records = [{**r, "timestamp": (pd.Timestamp(r["timestamp"])+close_time).isoformat(),
                "available_at": (pd.Timestamp(r["available_at"])+close_time).isoformat()} for r in rules.records]
    assets = ["EQA", "ETFA"]
    first, last = pd.Timestamp(times[0]), pd.Timestamp(times[-1])
    cutoff = pd.Timestamp(times[12])
    timely_at = cutoff - pd.Timedelta(minutes=5)
    rows = tuple(replace(q, timestamp=timely_at, available_at=timely_at)
                 if q.instrument == "EQA" and q.timestamp == cutoff else q for q in observations)
    source = replace(source, mode="CAPTURED", dataset_id="test-captured-market-availability", observations=rows,
                     as_of=source.as_of+close_time)
    changed = []
    for quote in source.observations:
        if quote.instrument in assets and first <= quote.timestamp <= last:
            changed.append(quote)
        else:
            price = float(40 + quote.timestamp.day * 30)
            changed.append(replace(quote, open=price, high=price, low=price, close=price,
                                   amount=price*quote.volume, available_at=None))
    # The 15:55 mark was available on the selected session. The later 16:00
    # suspended tick was first disclosed the following week.
    timely = next(q for q in changed if q.instrument == "EQA" and q.timestamp == timely_at)
    changed.append(replace(timely, timestamp=cutoff, available_at=cutoff + pd.Timedelta(days=7),
                           open=900, high=900, low=900, close=900, suspended=True))
    altered = replace(source, mode="LIVE", dataset_id="strategy-outside-window-changed",
                      observations=tuple(sorted(changed, key=lambda q: (q.instrument, q.timestamp))))
    reports, run_ids = [], []
    lineage = app.learning.lineage

    def legacy_lineage(policy, snapshot, instrument, *, rerun_of, strategy_decision_at):
        # Previously queued v1 requests used snapshot-wide lineage and did not
        # carry a strategy_decision_at field.
        return lineage(policy, snapshot, instrument, rerun_of=rerun_of)

    app.start()
    try:
        for key, snapshot in (("legacy", source), ("scoped", altered)):
            app.snapshots.save(snapshot)
            attachment = PanelFieldAttachment(snapshot.fingerprint, rules.units, records, "Frozen calendar")
            ref = ResearchFieldStore(app.snapshots).save(attachment)
            c = PanelContract(snapshot.fingerprint, assets, times, original.field_units, original.scope_id,
                              {a: original.currencies[a] for a in assets}, dict.fromkeys(assets, "America/New_York"),
                              ref, ref, ref, ref)
            with monkeypatch.context() as m:
                if key == "legacy":
                    m.setattr(app.learning, "lineage", legacy_lineage)
                job = publish(app, {"contract": c.to_dict(), "attachment_refs": [ref]}, "equal-weight", key)
            if key == "legacy":
                retry = {"contract": c.to_dict(), "attachment_refs": [ref], "template_id": "equal-weight",
                         "idempotency_key": key}
                original_request = app._request(app.queue.get(job["job_id"]))
                reopened = ResearchApplicationService(app.root, allow_model_calls=False)
                try:
                    assert reopened.run_strategy_template(retry)["job_id"] == job["job_id"]
                    with pytest.raises(ValueError, match="idempotency key is already bound"):
                        reopened.run_strategy_template({**retry, "parameters": {"rebalance_every": 2}})
                    with pytest.raises(ValueError, match="secret or endpoint"):
                        reopened.run_strategy_template({**retry, "question": "America/New_York",
                                                        "idempotency_key": "unrelated-timezone-string"})
                    assert reopened._request(reopened.queue.get(job["job_id"])) == original_request
                finally:
                    reopened.close()
            report = app.runs.get_run(job["run_id"])["manifest"]["source_snapshot"]
            assert report["reproduction"]["panel_scope"] == {
                "instrument_ids": assets, "timezones": c.timezones, "observed_at": times}
            assert report["quant_evidence"]["coverage"]["per_asset"] == {asset: len(times) for asset in assets}
            assert report["quant_evidence"]["exposure"]["as_of"] == times[-1]
            assert [mark["timestamp"] for mark in report["strategy_study"]["result"]["ledger"]["marks"]] == times
            reports.append(report)
            run_ids.append(job["run_id"])
        baseline, different_tail = reports
        assert "strategy_decision_at" not in baseline["policy_lineage"]
        assert different_tail["policy_lineage"]["strategy_decision_at"] == times[-1]
        assert baseline["dataset_fingerprint"] != different_tail["dataset_fingerprint"]
        assert baseline["quant_analytics"]["metrics"] == different_tail["quant_analytics"]["metrics"]
        for name in ("variance", "exposure", "tail_risk", "stress"):
            assert baseline["quant_evidence"][name] == different_tail["quant_evidence"][name]
        for name in ("ledger", "benchmark", "decisions"):
            assert baseline["strategy_study"]["result"][name] == different_tail["strategy_study"]["result"][name]
        service = StrategySettlementService(app)
        before_publication = service.observe(job["run_id"], times[-1])
        assert before_publication["status"] == "SOURCE_NOT_YET_AVAILABLE" and before_publication["samples"] == []
        today = datetime.now(UTC).date().isoformat()
        assert {row["observation_date"] for row in service.list_sources()} == {today}
        current = service.observe(job["run_id"], today)
        assert current["sample_count"] >= 3
        assert not {"MISSING_PRICE", "LATE_PRICE", "SUSPENDED"}.intersection(current["reasons"])
        assert all(pd.Timestamp(row["available_at"]) >= pd.Timestamp(current["published_at"])
                   for row in current["samples"])
        same_market_window = service.observe(run_ids[0], today)
        assert (current["data_mode"], same_market_window["data_mode"]) == ("LIVE", "CAPTURED")
        assert same_market_window["status"] == "DUPLICATE_SOURCE"
        assert same_market_window["snapshot_ref"] != current["snapshot_ref"]
        assert {r["sample_ref"] for r in same_market_window["samples"]} == {r["sample_ref"] for r in current["samples"]}
    finally:
        app.close()


def test_damaged_source_rejects_new_learning_but_leaves_frozen_receipt_readable(tmp_path):
    app = ResearchApplicationService(tmp_path, allow_model_calls=False)
    sample = app.create_strategy_sample()
    app.start()
    try:
        job = publish(app, {k: sample[k] for k in ("contract", "attachment_refs")}, "equal-weight", "corruption")
        service = StrategySettlementService(app)
        original = service.observe(job["run_id"], sample["contract"]["observed_at"][-1])
        source = app.runs.get_run(job["run_id"])
        manifest_file = source["bundle"] / "manifest.json"
        manifest = json.loads(manifest_file.read_text())
        manifest["source_snapshot"]["strategy_study"]["result"]["ledger"]["total_fees"] += 1
        manifest_file.write_text(json.dumps(manifest))
        with pytest.raises(ValueError, match="manifest identity"):
            service.verify(original["observation_ref"])
        with pytest.raises(ValueError, match="manifest identity"):
            service.observed_fills(original["observation_ref"])
        with pytest.raises(ValueError, match="manifest identity"):
            service.observe(job["run_id"], "2025-06-01")
        assert service.get(original["observation_ref"]) == original
    finally:
        app.close()

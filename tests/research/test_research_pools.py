import importlib.util

import pytest

from finahinking.p8_2.contracts import DatasetSnapshot, MarketObservation
from finahinking.research.job_queue import JobQueue

DECISION = "2026-01-02T20:00:00Z"
DATES = ["2026-01-02T20:00:00Z", "2026-01-05T20:00:00Z", "2026-01-06T20:00:00Z"]
ORIGINS = ("AUTO_SCAN", "QUANT_BASELINE", "USER", "MODEL", "PUBLIC_METHOD")


def store(path, scope="test"):
    assert importlib.util.find_spec("finahinking.research.research_pools"), "Research pool store is missing"
    from finahinking.research.research_pools import ResearchPoolStore

    return ResearchPoolStore(JobQueue(path), scope_id=scope)


def snapshot(prices=(10, 11, 12), *, instrument="AAA", suspended=False):
    return DatasetSnapshot(
        "pool-fixture",
        tuple(
            MarketObservation(instrument, date, price, price, price, price, volume=100,
                              available_at=date, suspended=suspended and i == 1)
            for i, (date, price) in enumerate(zip(DATES, prices, strict=False))
        ),
        provenance={"adjustment": "RAW", "units": {"price_currency": "CNY"}},
    )


def entry(data, origin="USER", **changes):
    return {
        "pool_ref": "first",
        "instrument": "AAA",
        "decision_origin": origin,
        "stage": "DECISION",
        "decision_at": DECISION,
        "available_at": DECISION,
        "policy_ref": "policy-v1",
        "snapshot_ref": data.fingerprint,
        "signal_fingerprint": "a" * 64,
        "source_ref": f"run-{origin.lower()}",
        **changes,
    }


def test_same_signal_across_origins_is_one_economic_sample(tmp_path):
    path = tmp_path / "queue.sqlite"
    pool = store(path)
    data = snapshot()
    for origin in ORIGINS:
        payload = entry(data, origin)
        assert pool.record_pool_entry(payload, snapshot=data) == pool.record_pool_entry(payload, snapshot=data)
    cohort = pool.freeze_cohort("first", decision_at=DECISION)
    assert len(cohort["samples"]) == 1
    assert set(cohort["samples"][0]["origins"]) == set(ORIGINS)
    assert len(store(path).list_entries("first")) == 5
    assert store(path, "different").list_entries("first") == []
    with pytest.raises(KeyError):
        store(path, "different").get_cohort(cohort["cohort_ref"])


def test_late_entry_and_revision_do_not_improve_frozen_cohort(tmp_path):
    pool = store(tmp_path / "queue.sqlite")
    data = snapshot()
    pool.record_pool_entry(entry(data), snapshot=data)
    cohort = pool.freeze_cohort("first", decision_at=DECISION)
    revised = snapshot((9, 100, 200))
    with pytest.raises(ValueError, match="immutable"):
        pool.record_pool_entry(entry(revised, "MODEL"), snapshot=revised)
    pool.record_pool_entry(entry(data, "MODEL"), snapshot=data)
    assert pool.freeze_cohort("first", decision_at=DECISION) == cohort
    result = pool.observe_pool(cohort["cohort_ref"], data, horizons=(1,))
    assert result["horizons"][0]["samples"][0]["price_return"] == pytest.approx(0.10)
    assert pool.observe_pool(cohort["cohort_ref"], revised, horizons=(1,)) == result


def test_horizons_count_observations_not_calendar_days(tmp_path):
    pool = store(tmp_path / "queue.sqlite")
    data = snapshot()
    pool.record_pool_entry(entry(data), snapshot=data)
    cohort = pool.freeze_cohort("first", decision_at=DECISION)
    suspended = pool.observe_pool(cohort["cohort_ref"], snapshot(suspended=True), horizons=(1, 5))
    first, fifth = suspended["horizons"]
    assert first["target_at"].startswith("2026-01-05")
    assert first["status"] == fifth["status"] == "NEEDS_DATA"
    result = pool.observe_pool(cohort["cohort_ref"], data, horizons=(1,))
    assert result["horizons"][0]["status"] == "READY"
    assert result["horizons"][0]["samples"][0]["price_return"] == pytest.approx(0.10)
    assert result["settlement_eligible"] is False
    assert result["policy_updates"] == []


def test_stages_keep_origin_and_reject_regression_or_late_information(tmp_path):
    pool = store(tmp_path / "queue.sqlite")
    data = snapshot()
    watch = pool.record_pool_entry(entry(data, stage="WATCH"), snapshot=data)
    with pytest.raises(ValueError, match="stage"):
        pool.record_pool_entry(entry(data), snapshot=data)
    with pytest.raises(ValueError, match="available"):
        pool.record_pool_entry(entry(data, stage="CANDIDATE", available_at=DATES[1]), snapshot=data)
    pool.record_pool_entry(entry(data, stage="CANDIDATE"), snapshot=data)
    decision = pool.record_pool_entry(entry(data), snapshot=data)
    assert watch["sample_ref"] == decision["sample_ref"]
    assert len(pool.list_entries("first")) == 3


def test_later_horizon_cannot_replace_observation_grid(tmp_path):
    from datetime import UTC, datetime, timedelta

    pool = store(tmp_path / "queue.sqlite")
    baseline = snapshot()
    pool.record_pool_entry(entry(baseline), snapshot=baseline)
    cohort = pool.freeze_cohort("first", decision_at=DECISION)
    cohort_ref = cohort["cohort_ref"]
    assert pool.observe_pool(cohort_ref, baseline, horizons=(1,))["horizons"][0]["status"] == "READY"

    original = datetime(2026, 1, 6, 20, tzinfo=UTC)
    later_rows = tuple(
        MarketObservation("AAA", original + timedelta(days=i), 13, 13, 13, 13, volume=100,
                          available_at=original + timedelta(days=i), suspended=False)
        for i in range(1, 59)
    )
    extended = DatasetSnapshot("extended", (*baseline.observations, *later_rows),
                               provenance=baseline.provenance)
    revised = DatasetSnapshot("revised", (baseline.observations[0], *extended.observations[2:]),
                              provenance=baseline.provenance)
    blocked = pool.observe_pool(cohort_ref, revised, horizons=(1, 5))
    assert blocked["horizons"][0]["status"] == "READY"
    assert blocked["horizons"][1]["reason"] == "FROZEN_OBSERVATION_GRID_MISMATCH"
    observed = pool.observe_pool(cohort_ref, extended)
    assert [h["horizon"] for h in observed["horizons"]] == [1, 5, 20, 60]
    assert [h["status"] for h in observed["horizons"]] == ["READY"] * 4
    assert observed["horizons"][-1]["target_at"] == later_rows[-1].timestamp.isoformat()
    assert len(observed["review_suggestions"]) == 4


def test_mixed_asset_coverage_uses_one_shared_mark(tmp_path):
    pool = store(tmp_path / "queue.sqlite")
    equity, etf = snapshot(), snapshot((20, 22, 25), instrument="ETF1")
    data = DatasetSnapshot("mixed", (*equity.observations, *etf.observations), provenance=equity.provenance)
    pool.record_pool_entry(entry(data), snapshot=data)
    pool.record_pool_entry(entry(data, "MODEL", instrument="ETF1"), snapshot=data)
    cohort = pool.freeze_cohort("first", decision_at=DECISION)
    partial = DatasetSnapshot(
        "missing", (*equity.observations, etf.observations[0], etf.observations[2]),
        provenance=equity.provenance,
    )
    first = pool.observe_pool(cohort["cohort_ref"], partial, horizons=(1,))["horizons"][0]
    assert first["status"] == "NEEDS_DATA"
    assert first["samples"] == []
    assert first["target_at"].startswith("2026-01-05")
    complete = pool.observe_pool(cohort["cohort_ref"], data, horizons=(1,))["horizons"][0]
    assert complete["unique_sample_count"] == 2
    assert {row["instrument"] for row in complete["samples"]} == {"AAA", "ETF1"}
    assert all(row["price_return"] == pytest.approx(0.10) for row in complete["samples"])


def test_delayed_price_cannot_resolve_frozen_horizon(tmp_path):
    from dataclasses import replace

    pool = store(tmp_path / "queue.sqlite")
    data = snapshot()
    pool.record_pool_entry(entry(data), snapshot=data)
    cohort = pool.freeze_cohort("first", decision_at=DECISION)
    cohort_ref = cohort["cohort_ref"]

    unavailable = DatasetSnapshot(
        "unavailable",
        (data.observations[0], replace(data.observations[1], suspended=True)),
        as_of=DATES[1],
        provenance=data.provenance,
    )
    first = pool.observe_pool(cohort_ref, unavailable, horizons=(1,))["horizons"][0]
    assert first["status"] == "NEEDS_DATA"

    delayed = DatasetSnapshot(
        "delayed",
        (data.observations[0], replace(data.observations[1], available_at=DATES[2])),
        as_of=DATES[2],
        provenance=data.provenance,
    )
    result = pool.observe_pool(cohort_ref, delayed, horizons=(1,))["horizons"][0]
    assert result["status"] == "NEEDS_DATA"
    assert result["target_at"] == first["target_at"]
    assert result["samples"] == []

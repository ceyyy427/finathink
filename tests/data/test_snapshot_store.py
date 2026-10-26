"""Replay and integrity boundaries for user-selected research datasets."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from finahinking.data.snapshot_store import SnapshotStore
from finahinking.data.user_api_contracts import DataBatch, fingerprint_records
from finahinking.p8_2.data_sources import FixtureMarketDataSource, snapshot_from_batch


def batch(*, available=True, missing=()):
    records = [{
        "instrument": "AAA", "timestamp": "2026-01-02T00:00:00Z",
        "open": 100, "high": 103, "low": 99, "close": 102, "volume": 1000,
        **({"available_at": "2026-01-02T01:00:00Z"} if available else {}),
    }]
    for field in missing:
        records[0].pop(field, None)
    return DataBatch(
        records=tuple(records), connection_id="my-feed",
        retrieved_at=datetime(2026, 1, 3, tzinfo=UTC),
        source_declaration="user-owned dataset",
        data_fingerprint=fingerprint_records(records),
        pit_available="AVAILABLE" if available else "UNKNOWN",
    )


def test_same_snapshot_is_content_addressed_and_reloads_after_restart(tmp_path):
    original = FixtureMarketDataSource().snapshot("AAA")
    store = SnapshotStore(tmp_path)
    ref = store.save(original)
    assert ref == original.fingerprint
    assert store.save(original) == ref
    loaded = SnapshotStore(tmp_path).load(ref)
    assert loaded.to_dict() == original.to_dict()
    assert loaded.observations[0].close == 99.75


def test_altered_snapshot_is_rejected_instead_of_silently_replaced(tmp_path):
    store = SnapshotStore(tmp_path)
    snapshot = FixtureMarketDataSource().snapshot("AAA")
    ref = store.save(snapshot)
    path = tmp_path / f"{ref}.json"
    path.write_text(path.read_text().replace('"close":99.75', '"close":99.5'))
    with pytest.raises(ValueError, match="fingerprint|integrity"):
        store.load(ref)
    with pytest.raises(ValueError, match="fingerprint|integrity"):
        store.save(snapshot)


@pytest.mark.parametrize("ref", ["../private", "a" * 63, "Z" * 64, "/tmp/data"])
def test_snapshot_lookup_rejects_non_content_references(tmp_path, ref):
    with pytest.raises(ValueError, match="fingerprint"):
        SnapshotStore(tmp_path).load(ref)


def test_user_batch_keeps_quality_declarations_and_no_connection_endpoint():
    snapshot = snapshot_from_batch(batch())
    assert snapshot.mode == "CAPTURED"
    assert snapshot.observations[0].close == 102
    assert snapshot.provenance["connection_id"] == "my-feed"
    assert snapshot.provenance["timezone"] == "UTC"
    assert snapshot.provenance["adjustment"] == "UNKNOWN"
    assert snapshot.provenance["calendar"] == "UNKNOWN"
    assert snapshot.provenance["quality_issues"] == []
    assert "endpoint" not in snapshot.provenance


def test_missing_availability_stays_unknown_including_after_roundtrip(tmp_path):
    snapshot = snapshot_from_batch(batch(available=False))
    assert snapshot.provenance["pit_status"] == "UNKNOWN"
    assert snapshot.observations[0].available_at is None
    assert snapshot.pit_available() is False
    assert snapshot.available_observations() == ()
    store = SnapshotStore(tmp_path)
    loaded = store.load(store.save(snapshot))
    assert loaded.observations[0].available_at is None
    assert loaded.pit_available() is False


def test_missing_ohlcv_is_an_actionable_error_not_fabricated_prices():
    with pytest.raises(ValueError, match=r"missing.*high.*volume"):
        snapshot_from_batch(batch(missing=("high", "volume")))


def test_changed_prices_produce_a_different_snapshot_reference(tmp_path):
    first = snapshot_from_batch(batch())
    second_batch = batch()
    second_batch.records[0]["close"] = 101
    second_batch = replace(second_batch, data_fingerprint=fingerprint_records(second_batch.records))
    second = snapshot_from_batch(second_batch)
    store = SnapshotStore(tmp_path)
    assert store.save(first) != store.save(second)


def test_unknown_lookup_is_not_replaced_with_sample_data(tmp_path):
    with pytest.raises(FileNotFoundError):
        SnapshotStore(tmp_path).load("a" * 64)


def test_batch_fingerprint_must_match_its_records():
    with pytest.raises(ValueError, match="fingerprint"):
        snapshot_from_batch(replace(batch(), data_fingerprint="0" * 64))


def test_snapshot_store_rejects_symlink_reference(tmp_path):
    store = SnapshotStore(tmp_path / "snapshots")
    original = FixtureMarketDataSource().snapshot("AAA")
    outside = tmp_path / "unrelated.json"
    outside.write_text(original.to_json())
    (store.root / f"{original.fingerprint}.json").symlink_to(outside)
    with pytest.raises(ValueError, match="integrity"):
        store.load(original.fingerprint)
    assert outside.read_text() == original.to_json()


def test_listed_snapshots_are_verified_and_returned_as_independent_values(tmp_path):
    store = SnapshotStore(tmp_path)
    ref = store.save(FixtureMarketDataSource().snapshot("AAA"))
    snapshots = store.list_snapshots()
    assert len(snapshots) == 1 and snapshots[0].fingerprint == ref
    snapshots[0].provenance["source"] = "changed-in-memory"
    assert store.load(ref).provenance["source"] == "fixture-market"


def test_api_capture_explicitly_fetches_and_saves_only_normalized_prices(tmp_path):
    import json

    from finahinking.data.connection_settings import InMemoryDataCredentialStore
    from finahinking.data.user_api import JsonApiConnector, TransportResponse
    from finahinking.data.user_api_contracts import DataConnectionConfig, DataRequest

    record = batch().records[0]

    class Transport:
        def request(self, url, *, headers, timeout, allow_redirects=False):
            return TransportResponse(200, {"content-type": "application/json"}, json.dumps([record]).encode())

    config = DataConnectionConfig("my-feed", "User feed", "https://example.test/prices", None, "no_auth", {key: key for key in record})
    connector = JsonApiConnector(config, InMemoryDataCredentialStore(), Transport(), resolver=lambda host, port: ("93.184.216.34",))
    store = SnapshotStore(tmp_path)
    ref = connector.capture(DataRequest("prices", ("AAA",)), store)
    saved = store.load(ref)
    assert saved.observations[0].close == 102
    assert saved.provenance["connection_id"] == "my-feed"
    assert "https://example.test" not in saved.to_json()

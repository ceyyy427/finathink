"""Typed optional fields extend, never rewrite, the original market snapshot."""

import importlib
import importlib.util
import json
from dataclasses import replace

import pytest

from finahinking.data.snapshot_store import SnapshotStore
from finahinking.p8_2.data_sources import FixtureMarketDataSource


def api():
    name = "finahinking.data.research_fields"
    assert importlib.util.find_spec(name) is not None, "typed research fields are not implemented"
    return importlib.import_module(name)


def attachment(snapshot, **changes):
    values = {"snapshot_ref": snapshot.fingerprint, "market": "CN",
              "units": {"turnover_rate": "ratio_free_float"},
              "records": [{"instrument": snapshot.instruments[0],
                           "timestamp": snapshot.observations[0].timestamp.isoformat(),
                           "available_at": snapshot.observations[0].timestamp.isoformat(),
                           "turnover_rate": 0.02}], "source_declaration": "fixture field observations"}
    return api().ResearchFieldAttachment(**{**values, **changes})


def test_attachment_roundtrip_is_immutable_and_keeps_original_snapshot_bytes(tmp_path):
    module = api()
    snapshot = FixtureMarketDataSource(points=4).snapshot("AAA")
    original = snapshot.to_json()
    store = SnapshotStore(tmp_path)
    store.save(snapshot)
    field_store = module.ResearchFieldStore(store)
    value = attachment(snapshot)
    reference = field_store.save(value)
    restored = field_store.load(reference)
    assert restored.fingerprint == value.fingerprint == reference
    assert restored.snapshot_ref == snapshot.fingerprint
    assert restored.units == {"turnover_rate": "ratio_free_float"}
    copy = restored.to_dict()
    copy["records"][0]["turnover_rate"] = 999
    assert restored.records[0]["turnover_rate"] == 0.02
    assert field_store.save(value) == reference
    assert store.load(snapshot.fingerprint).to_json() == original


def test_attachment_cannot_claim_another_or_unavailable_main_snapshot(tmp_path):
    module = api()
    original = FixtureMarketDataSource(points=4).snapshot("AAA")
    other = replace(original, dataset_id="different-snapshot")
    snapshots = SnapshotStore(tmp_path)
    snapshots.save(original)
    with pytest.raises(ValueError):
        module.ResearchFieldStore(snapshots).save(attachment(other))


@pytest.mark.parametrize("units", [{"turnover_rate": "volume"}, {"turnover_rate": "ratio"}, {}])
def test_turnover_units_need_an_explicit_real_share_denominator(units):
    snapshot = FixtureMarketDataSource(points=4).snapshot("AAA")
    with pytest.raises(ValueError):
        attachment(snapshot, units=units)


def test_attachment_path_traversal_and_corruption_fail_closed(tmp_path):
    module = api()
    snapshot = FixtureMarketDataSource(points=4).snapshot("AAA")
    snapshots = SnapshotStore(tmp_path)
    snapshots.save(snapshot)
    store = module.ResearchFieldStore(snapshots)
    ref = store.save(attachment(snapshot))
    path = snapshots.root / "research-fields" / f"{ref}.json"
    payload = json.loads(path.read_text())
    payload["records"][0]["turnover_rate"] = 0.9
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        store.load(ref)
    with pytest.raises(ValueError):
        store.load("../another-user")


def test_generic_data_mapping_can_create_explicit_field_attachment():
    api()
    from finahinking.data.field_mapping import normalize_records

    snapshot = FixtureMarketDataSource(points=4).snapshot("AAA")
    timestamp = snapshot.observations[0].timestamp.isoformat()
    batch = normalize_records([{"code": "AAA", "day": timestamp, "known": timestamp, "turn": 2}],
        {"instrument": "code", "timestamp": "day", "available_at": "known", "turnover_rate": "turn"},
        "research_fields")
    result = batch.to_research_attachment(snapshot.fingerprint, market="CN",
                                           units={"turnover_rate": "percent_free_float"})
    assert result.records[0]["turnover_rate"] == 2
    assert result.units["turnover_rate"] == "percent_free_float"
    assert result.source_declaration == batch.source_declaration


def test_revision_records_are_not_silently_collapsed_by_generic_mapping():
    api()
    from finahinking.data.field_mapping import normalize_records

    rows = [{"instrument": "CN:AAA", "timestamp": "2025-03-31", "available_at": known,
             "profit_yoy": value} for known, value in [("2025-04-20", 0.1), ("2025-05-20", 0.2)]]
    mapping = {name: name for name in rows[0]}
    batch = normalize_records(rows, mapping, "research_fields")
    assert len(batch.records) == 2
    assert not any("duplicate" in item for item in batch.quality_issues)


def test_explicit_json_connector_field_fetch_reuses_the_existing_transport_boundary():
    api()

    from finahinking.data.connection_settings import InMemoryDataCredentialStore
    from finahinking.data.user_api import JsonApiConnector
    from finahinking.data.user_api_contracts import DataRequest
    from tests.data.test_user_api import PUBLIC_RESOLVER, FakeTransport, config, response

    source = FixtureMarketDataSource(points=4).snapshot("AAA")
    timestamp = source.observations[0].timestamp.isoformat()
    settings = replace(config(), field_mapping={"instrument": "ticker", "timestamp": "time",
        "available_at": "known", "turnover_rate": "turn"})
    transport = FakeTransport([response({"data": [{"ticker": "AAA", "time": timestamp,
                                                   "known": timestamp, "turn": 2}]})])
    connector = JsonApiConnector(settings, InMemoryDataCredentialStore(), transport, resolver=PUBLIC_RESOLVER)
    result = connector.fetch_research_fields(DataRequest("research_fields", ("AAA",)),
        snapshot_ref=source.fingerprint, market="CN", units={"turnover_rate": "percent_free_float"})
    assert result.records[0]["turnover_rate"] == 2
    assert len(transport.calls) == 1
    assert transport.calls[0][3] is False


def test_nested_unit_values_are_rejected_as_schema_errors():
    source = FixtureMarketDataSource(points=4).snapshot("AAA")
    with pytest.raises(ValueError):
        attachment(source, units={"turnover_rate": ["ratio_free_float"]})

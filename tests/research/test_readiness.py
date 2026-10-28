"""Read-only preparation diagnoses actual local inputs, never runs research."""
import json
from dataclasses import replace

import pandas as pd
import pytest

from finahinking.local_app import LocalAppConfig, LocalApplication
from finahinking.p8_2.data_sources import FixtureMarketDataSource
from finahinking.research.application_service import ResearchApplicationService
from tests.research.test_application_service import payload


@pytest.fixture
def service(tmp_path):
    instance = ResearchApplicationService(tmp_path / "research")
    yield instance
    instance.close()


def inspect(service, snapshot=None, **changes):
    return service.readiness({**payload(service, snapshot=snapshot), **changes})


def checks(result):
    return {item["code"]: item for item in result["checks"]}


def test_missing_snapshot_has_a_repair_path_and_no_job(service):
    result = inspect(service, snapshot_ref="0" * 64)
    assert result["schema_version"] == "research-readiness.v1"
    assert result["can_submit"] is False
    assert checks(result)["snapshot"]["status"] == "BLOCKED"
    assert checks(result)["snapshot"]["action"]["href"] == "/research/jobs#prepare-data"
    assert service.list_jobs()["total"] == 0
    assert not (service.root / "model-inputs").exists()


def test_corrupted_snapshot_is_not_a_validated_input(service):
    values = payload(service)
    (service.snapshots.root / f'{values["snapshot_ref"]}.json').write_text('{"password":"never-echo-this"}')
    result = service.readiness(values)
    assert not result["can_submit"]
    assert "never-echo-this" not in json.dumps(result)
    assert str(service.root) not in json.dumps(result)


def test_known_minimum_data_and_unknown_market_declarations_are_distinct(service):
    snapshot = FixtureMarketDataSource(points=4).snapshot("AAA")
    result = inspect(service, snapshot)
    assert result["can_submit"] is True and result["status"] == "NEEDS_REVIEW"
    assert result["coverage"]["records"] == 4
    assert checks(result)["ohlcv"]["status"] == "PASS"
    assert checks(result)["units"]["status"] == "REVIEW"
    assert checks(result)["tradability"]["status"] == "REVIEW"
    assert checks(result)["method"]["required_fields"] == ["open", "high", "low", "close", "volume", "available_at"]
    assert result["data_mode"] == "SAMPLE"
    assert service.list_jobs()["total"] == 0


@pytest.mark.parametrize("kind, code", [("short", "ohlcv"), ("intraday", "frequency"), ("unknown", "pit"), ("late", "history_availability"), ("future", "pit"), ("other-future", "pit")])
def test_unusable_data_is_caught_before_running(service, kind, code):
    snapshot = FixtureMarketDataSource(points=4).snapshot("AAA")
    rows = list(snapshot.observations)
    if kind == "short":
        rows = rows[:3]
    elif kind == "intraday":
        rows[1] = replace(rows[1], timestamp=rows[0].timestamp + pd.Timedelta(hours=1), available_at=rows[0].timestamp + pd.Timedelta(hours=1))
    elif kind == "unknown":
        rows[0] = replace(rows[0], available_at=None)
    elif kind == "late":
        rows[0] = replace(rows[0], available_at=rows[2].timestamp)
    elif kind == "future":
        rows[-1] = replace(rows[-1], available_at="2026-10-02T00:00:00Z")
    else:
        rows.append(replace(rows[-1], instrument="ZZZ", available_at="2026-10-02T00:00:00Z"))
    result = inspect(service, replace(snapshot, observations=tuple(rows)))
    assert result["can_submit"] is False
    assert checks(result)[code]["status"] == "BLOCKED"
    assert checks(result)[code]["action"]["href"].startswith("/research/jobs#")
    assert service.list_jobs()["total"] == 0


@pytest.mark.parametrize("change, code", [({"instrument": "MISSING"}, "instrument"), ({"as_of": "invalid"}, "cutoff"), ({"question": "password=not-for-output"}, "question"), ({"budget": {"max_wall_seconds": 301}}, "budget"), ({"model_config_ref": "absent"}, "model"), ({"materials": [{"bad": "value"}]}, "materials")])
def test_invalid_configuration_is_explained_without_echoing_input(service, change, code):
    result = inspect(service, **change)
    assert not result["can_submit"] and checks(result)[code]["status"] == "BLOCKED"
    assert "not-for-output" not in json.dumps(result)


def test_changed_intent_changes_readiness_binding(service):
    first = inspect(service)
    second = inspect(service, question="复核成本")
    assert first["input_digest"] != second["input_digest"]
    assert len(first["input_digest"]) == 64


def test_unit_declaration_is_validated_before_any_transport(tmp_path):
    from finahinking.data.connection_settings import InMemoryDataCredentialStore
    from finahinking.data.snapshot_store import SnapshotStore
    from finahinking.data.user_api import JsonApiConnector
    from tests.data.test_user_api import PUBLIC_RESOLVER, FakeTransport, config, request

    transport = FakeTransport([])
    connector = JsonApiConnector(config(), InMemoryDataCredentialStore(), transport, resolver=PUBLIC_RESOLVER)
    with pytest.raises(ValueError):
        connector.capture(request(), SnapshotStore(tmp_path), units={"price_currency": "USD", "volume_unit": "lots"})
    assert transport.calls == []


def test_capture_retains_explicit_units_without_converting_data(service):
    from finahinking.data.field_mapping import normalize_records
    from finahinking.p8_2.data_sources import snapshot_from_batch

    rows = [row.to_dict() for row in FixtureMarketDataSource(points=4).snapshot("AAA").observations]
    batch = normalize_records(rows, {name: name for name in ("instrument", "timestamp", "available_at", "open", "high", "low", "close", "volume")}, "prices")
    snapshot = snapshot_from_batch(batch, units={"price_currency": "CNY", "volume_unit": "shares"})
    assert snapshot.observations[0].volume == rows[0]["volume"]
    assert checks(inspect(service, snapshot))["units"]["status"] == "PASS"


def test_readiness_route_checks_csrf_and_never_queues(tmp_path):
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "local.sqlite"), offline=True))
    try:
        body = payload(app.research_service)
        assert app.route("POST", "/api/research/readiness", body=body)[0] == 403
        status, _, result = app.route("POST", "/api/research/readiness", body={**body, "_csrf": app.csrf_token})
        assert status == 200 and result["can_submit"]
        assert app.research_service.list_jobs()["total"] == 0
        assert app.route("GET", "/api/research/readiness")[0] == 405
    finally:
        app.close()

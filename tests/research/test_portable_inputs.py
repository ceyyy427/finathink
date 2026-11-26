"""Publication scopes are enforced again when reading an externally supplied archive."""

import copy

import pytest

from finahinking.data.panel_contracts import PanelContract, PanelFieldAttachment
from finahinking.p8_2.contracts import DatasetSnapshot, MarketObservation
from finahinking.research.portable_inputs import freeze_inputs, rebuild_inputs


def price(asset, day, hour):
    stamp = f"2025-01-{day:02d}T{hour:02d}:00:00Z"
    return MarketObservation(asset, stamp, 10, 10, 10, 10, 100, available_at=stamp)


@pytest.fixture
def selection():
    quotes = (price("AAA", 1, 9), price("AAA", 1, 15), price("AAA", 2, 15), price("BBB", 2, 15))
    snapshot = DatasetSnapshot("selected", quotes, as_of="2025-01-02T16:00:00Z", provenance={"adjustment": "RAW"})
    t1, t2 = "2025-01-01T16:00:00Z", "2025-01-02T16:00:00Z"
    units = {"is_session": "boolean", "trade_allowed": "boolean"}
    revisions = [
        {"instrument": "AAA", "timestamp": t1, "available_at": t1, "is_session": True, "trade_allowed": True},
        {"instrument": "AAA", "timestamp": t2, "available_at": t2, "is_session": True},
        {"instrument": "AAA", "timestamp": "2025-01-01T10:00:00Z", "available_at": t1, "trade_allowed": False},
    ]
    attachment = PanelFieldAttachment(snapshot.fingerprint, units, revisions, "frozen")
    contract = PanelContract(snapshot.fingerprint, ["AAA"], [t1, t2], {"close": "USD"}, "selected",
                             {"AAA": "USD"}, {"AAA": "UTC"}, calendar_ref=attachment.fingerprint)
    return freeze_inputs(snapshot, [attachment], instruments=["AAA"], through=t2, contract=contract)


def test_projected_inputs_contain_only_selected_native_quotes_and_field_revisions(selection):
    prices = selection["observations"]
    fields = selection["attachments"][0]["records"]
    assert len(prices) == len(fields) == 2
    assert all(r["instrument"] == "AAA" and "T15:" in r["timestamp"] for r in prices)
    assert all(r["timestamp"].endswith("T16:00:00+00:00") for r in fields)
    snapshot, projected, panel = rebuild_inputs(selection, with_panel=True)
    assert snapshot.fingerprint != selection["original_snapshot"]
    assert projected[0].fingerprint == panel.contract.calendar_ref
    assert all(row["attachment_ref"] == projected[0].fingerprint for row in panel.audit["selections"])


def test_import_rejects_extra_prices_unselected_revisions_and_broadened_cutoff(selection):
    unused = copy.deepcopy(selection)
    earlier = {**unused["observations"][0], "timestamp": "2025-01-01T09:00:00Z"}
    unused["observations"].insert(0, earlier)
    with pytest.raises(ValueError, match="未选择"):
        rebuild_inputs(unused)

    revision = copy.deepcopy(selection)
    item = {**revision["attachments"][0]["records"][0], "timestamp": "2025-01-01T10:00:00Z"}
    revision["attachments"][0]["records"].append(item)
    with pytest.raises(ValueError, match="未选择"):
        rebuild_inputs(revision)

    after_close = {**selection, "through": "2025-01-02T23:59:59Z"}
    with pytest.raises(ValueError, match="超出冻结面板"):
        rebuild_inputs(after_close)


@pytest.mark.parametrize("field,value", [("units", {"price_currency": {"nested": "USD"}}),
                                         ("asset_classes", {"AAA": ["ETF"]}), ("adjustment", ["RAW"])])
def test_provenance_declarations_have_bounded_domain_types(selection, field, value):
    with pytest.raises(ValueError):
        rebuild_inputs({**selection, field: value})

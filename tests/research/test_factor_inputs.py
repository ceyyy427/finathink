"""Asset-local timing, unit and optional-input boundaries."""

import importlib
import importlib.util

import pandas as pd
import pytest

from finahinking.p8_2.contracts import DatasetSnapshot, MarketObservation
from tests.data.test_research_fields import attachment


def api():
    name = "finahinking.research.factor_inputs"
    assert importlib.util.find_spec(name) is not None, "auditable factor panels are not implemented"
    return importlib.import_module(name)


def snapshot():
    rows = []
    for asset, prices in [("CN:000001", [10, 11, 12]), ("US:000001", [100, 90, 81])]:
        for day, price in enumerate(prices, 1):
            time = f"2025-01-0{day}T16:00:00Z"
            rows.append(MarketObservation(asset, time, price, price + 1, price - 1, price,
                                          volume=1000, amount=price * 1000, available_at=time))
    return DatasetSnapshot("panel-fixture", tuple(rows), mode="SAMPLE")


def test_ohlcv_without_attachment_preserves_full_ids_and_asset_return_boundaries():
    module = api()
    source = snapshot()
    panel = module.build_factor_panel(source, None, as_of="2025-01-03",
        universe=module.UniverseSpec("MULTI", source.instruments))
    frame = panel.to_frame()
    assert set(frame["asset"]) == {"CN:000001", "US:000001"}
    grouped = frame.groupby(frame["asset"])
    assert grouped["return_1d"].first().loc["CN:000001"] == pytest.approx(0.1)
    assert grouped.head(1)["return_1d"].isna().all()
    assert panel.snapshot_ref == source.fingerprint
    assert panel.attachment_ref is None
    assert panel.missing_inputs(("turnover_rate",)) == ("turnover_rate",)
    assert frame["amount"].notna().all()


def test_percent_turnover_is_normalized_without_using_volume_or_filling_other_days():
    module = api()
    source = snapshot()
    fields = attachment(source, market="MULTI", units={"turnover_rate": "percent_free_float"})
    panel = module.build_factor_panel(source, fields, as_of="2025-01-03",
        universe=module.UniverseSpec("MULTI", source.instruments))
    frame = panel.to_frame()
    known = frame["turnover_rate"].dropna()
    assert len(known) == 1
    assert known.iloc[0] == pytest.approx(0.0002)
    assert panel.units["turnover_rate"] == "ratio_free_float"
    assert panel.attachment_ref == fields.fingerprint


def test_wrong_snapshot_or_market_attachment_is_rejected():
    module = api()
    source = snapshot()
    universe = module.UniverseSpec("MULTI", source.instruments)
    with pytest.raises(ValueError):
        module.build_factor_panel(source, attachment(source, snapshot_ref="a" * 64, market="MULTI"),
                                  as_of="2025-01-03", universe=universe)
    with pytest.raises(ValueError):
        module.build_factor_panel(source, attachment(source, market="CN"),
                                  as_of="2025-01-03", universe=universe)


def test_unavailable_and_future_field_records_do_not_enter_historical_panel():
    module = api()
    source = snapshot()
    record = {"instrument": "CN:000001", "timestamp": "2025-01-01T16:00:00Z",
              "available_at": "2025-01-04T16:00:00Z", "turnover_rate": 0.1}
    fields = attachment(source, market="MULTI", records=[record])
    panel = module.build_factor_panel(source, fields, as_of="2025-01-03",
        universe=module.UniverseSpec("MULTI", source.instruments))
    assert panel.missing_inputs(("turnover_rate",)) == ("turnover_rate",)
    assert len(panel.to_frame()) == 6


def test_daily_field_date_can_bind_to_same_utc_signal_day_but_never_next_day():
    module = api()
    source = snapshot()
    record = {"instrument": "CN:000001", "timestamp": "2025-01-01",
              "available_at": "2025-01-01T15:00:00Z", "turnover_rate": 0.03}
    fields = attachment(source, market="MULTI", records=[record])
    panel = module.build_factor_panel(source, fields, as_of="2025-01-03",
        universe=module.UniverseSpec("MULTI", source.instruments))
    values = panel.to_frame()["turnover_rate"].dropna()
    assert values.tolist() == [0.03]


def test_panel_returned_frame_mutation_does_not_rewrite_frozen_inputs():
    module = api()
    source = snapshot()
    panel = module.build_factor_panel(source, None, as_of="2025-01-03",
        universe=module.UniverseSpec("MULTI", source.instruments))
    digest = panel.fingerprint
    frame = panel.to_frame()
    frame.loc[:, "close"] = 999
    assert panel.fingerprint == digest
    assert panel.to_frame()["close"].iloc[0] == 10


def test_asof_selects_past_rows_without_relabeling_the_primary_snapshot():
    module = api()
    source = snapshot()
    panel = module.build_factor_panel(source, None, as_of=pd.Timestamp("2025-01-02T23:00:00Z"),
        universe=module.UniverseSpec("MULTI", source.instruments))
    assert len(panel.to_frame()) == 4
    assert panel.snapshot_ref == source.fingerprint


def test_factor_spec_is_definition_metadata_bound_to_an_existing_implementation():
    from finahinking.research import asset_contracts

    assert hasattr(asset_contracts, "FactorSpec"), "typed factor definitions are missing"
    spec = asset_contracts.FactorSpec(
        factor_id="finathink-return-volatility", version="1.0.0",
        formula="rolling_std(return(close,1),5)", required_fields=["close"],
        units="ratio-not-annualized", direction="LOWER", window=5, frequency="daily",
        availability="available-at-or-before-signal", missing_policy="propagate",
        implementation_ref="recipe:return_dispersion@1.0.0", implementation_fingerprint="a" * 64,
        source_resource_ids=["finathink-native-dsl.v1"],
    )
    assert spec.required_fields == ("close",)
    assert len(spec.fingerprint) == 64
    assert spec.to_dict()["implementation_ref"] == "recipe:return_dispersion@1.0.0"
    with pytest.raises((AttributeError, TypeError)):
        spec.formula = "arbitrary code"


def test_application_attachment_entry_is_explicit_and_user_scoped(tmp_path):
    api()
    from finahinking.research.application_service import ResearchApplicationService
    from tests.research.test_application_service import payload

    owner = ResearchApplicationService(tmp_path, scope_id="owner", allow_model_calls=False)
    other = ResearchApplicationService(tmp_path, scope_id="other", allow_model_calls=False)
    try:
        job = owner.submit(payload(owner))
        stored = owner.snapshots.load(job["snapshot_ref"])
        values = attachment(stored).to_dict()
        result = owner.attach_research_fields(job["job_id"], values)
        assert result["snapshot_ref"] == job["snapshot_ref"]
        assert result["attachment_ref"] == values["fingerprint"]
        assert result["status"] == "SAVED_NOT_EVALUATED"
        assert owner.list_jobs()["total"] == 1
        with pytest.raises(KeyError):
            other.attach_research_fields(job["job_id"], values)
    finally:
        owner.close()
        other.close()


def test_announced_revisions_do_not_rewrite_past_or_newer_financial_periods():
    module = api()
    source = snapshot()
    common = {"instrument": "CN:000001"}
    initial = {**common, "timestamp": "2024-12-31", "available_at": "2025-01-01T12:00:00Z", "profit_yoy": 0.1}
    future = {**common, "timestamp": "2024-12-31", "available_at": "2025-01-03T17:00:00Z", "profit_yoy": 99}
    older = {**common, "timestamp": "2024-09-30", "available_at": "2025-01-02T12:00:00Z", "profit_yoy": 55}
    universe = module.UniverseSpec("MULTI", source.instruments)
    first = attachment(source, market="MULTI", units={"profit_yoy": "ratio"}, records=[initial])
    revised = attachment(source, market="MULTI", units={"profit_yoy": "ratio"}, records=[initial, future, older])
    before = module.build_factor_panel(source, first, as_of="2025-01-03", universe=universe)
    after = module.build_factor_panel(source, revised, as_of="2025-01-03", universe=universe)
    pd.testing.assert_series_equal(before.to_frame()["profit_yoy"], after.to_frame()["profit_yoy"])
    assert after.to_frame()["profit_yoy"].dropna().tolist() == [0.1] * 3

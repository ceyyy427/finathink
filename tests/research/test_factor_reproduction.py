"""Hand arithmetic fixtures prove definitions, never source financial efficacy."""

from dataclasses import replace

import pandas as pd
import pytest

from finahinking.factors.dsl import evaluate_expression
from finahinking.research import factor_recipes
from finahinking.research.factor_inputs import UniverseSpec, build_factor_panel
from tests.data.test_research_fields import attachment
from tests.research.test_factor_inputs import snapshot


def specs(window=2):
    assert hasattr(factor_recipes, "pilot_factor_specs"), "audited pilot bindings are not implemented"
    return {item.factor_id: item for item in factor_recipes.pilot_factor_specs(window)}


def panel(*, amount_unit="CNY", turnover_unit="ratio_free_float"):
    source = snapshot()
    records = [{"instrument": row.instrument, "timestamp": row.timestamp.isoformat(),
                "available_at": row.timestamp.isoformat(), "turnover_rate": (index % 3 + 1) / 100}
               for index, row in enumerate(source.observations)]
    extra = attachment(source, market="MULTI", records=records,
        units={"turnover_rate": turnover_unit, "amount": amount_unit, "volume": "shares"})
    return build_factor_panel(source, extra, as_of="2025-01-03",
                               universe=UniverseSpec("MULTI", source.instruments))


@pytest.mark.parametrize("factor_id,expected", [
    ("finathink-turnover-freefloat-mean", 0.025),
    ("finathink-turnover-freefloat-dispersion", 0.005),
    ("finathink-return-dispersion", (1 / 10 - 1 / 11) / 2),
    ("finathink-close-reversal", -0.2),
    ("finathink-amihud-cny-unscaled", (0.1 / 11000 + (1 / 11) / 12000) / 2),
])
def test_each_selected_pilot_matches_hand_arithmetic(factor_id, expected):
    selected = specs()[factor_id]
    values = factor_recipes.evaluate_factor_spec(selected, panel())
    current = values.xs("CN:000001", level="asset").iloc[-1]
    assert current == pytest.approx(expected)
    assert len(values.xs("CN:000001", level="asset")) == 3
    assert values.xs("CN:000001", level="asset").iloc[0] != values.xs("CN:000001", level="asset").iloc[0]


def test_pilot_metadata_is_small_distinct_and_has_no_inherited_source_metrics():
    selected = specs(5)
    assert len(selected) == 5
    assert len({item.factor_id for item in selected.values()}) == 5
    assert not any("f_score" in name or "sue" in name or "turn_mid" in name for name in selected)
    for item in selected.values():
        recipe = factor_recipes.resolve_factor_spec(item)
        assert item.implementation_fingerprint == recipe.implementation_fingerprint
        assert item.formula == recipe.expression(5)
        assert "icir" not in item.to_dict()


def test_forged_formula_or_implementation_binding_never_executes():
    value = specs()["finathink-close-reversal"]
    for changed in [replace(value, formula="return(close,2)"),
                    replace(value, implementation_fingerprint="f" * 64)]:
        with pytest.raises(ValueError):
            factor_recipes.evaluate_factor_spec(changed, panel())


def test_missing_real_turnover_or_wrong_amount_currency_is_blocked_not_proxied():
    source = snapshot()
    missing = build_factor_panel(source, None, as_of="2025-01-03",
                                 universe=UniverseSpec("MULTI", source.instruments))
    for name in ("finathink-turnover-freefloat-mean", "finathink-amihud-cny-unscaled"):
        with pytest.raises(ValueError, match="BLOCKED_DATA"):
            factor_recipes.evaluate_factor_spec(specs()[name], missing)
    with pytest.raises(ValueError, match="BLOCKED_DATA"):
        factor_recipes.evaluate_factor_spec(specs()["finathink-amihud-cny-unscaled"], panel(amount_unit="USD"))
    with pytest.raises(ValueError, match="BLOCKED_DATA"):
        factor_recipes.evaluate_factor_spec(specs()["finathink-turnover-freefloat-mean"], panel(turnover_unit="ratio_total_shares"))


def test_constant_and_missing_observations_are_not_fabricated_as_nonzero_signals():
    frame = pd.DataFrame({"close": [10.0, 10.0, 10.0, None, 10.0]},
                         index=pd.date_range("2025-01-01", periods=5, tz="UTC"))
    values = evaluate_expression("rolling_std(return(close,1),2)", frame)
    assert values.iloc[2] == 0
    assert values.iloc[3:].isna().all()
    assert evaluate_expression("zscore(close,2)", frame).isna().all()


def test_panel_dsl_rolling_lag_rank_and_winsorize_are_asset_local_and_past_only():
    frame = panel().to_frame()
    for expression in ("return(close,1)", "lag(close,1)", "rolling_mean(close,2)",
                       "rank(close)", "winsorize(close,0.1,0.9)"):
        actual = evaluate_expression(expression, frame)
        for asset in frame["asset"].unique():
            single = frame.xs(asset, level="asset").drop(columns="asset")
            expected = evaluate_expression(expression, single)
            pd.testing.assert_series_equal(actual.xs(asset, level="asset"), expected, check_names=False)
        prefix = frame.loc[frame["timestamp"] < pd.Timestamp("2025-01-03T00:00:00Z")]
        pd.testing.assert_series_equal(evaluate_expression(expression, prefix), actual.loc[prefix.index], check_names=False)


def test_safe_abs_division_keeps_zero_amount_missing_and_window_warmup():
    frame = pd.DataFrame({"signal_return": [-0.1, 0.2, -0.3], "amount": [100, 0, 300]},
                         index=pd.date_range("2025-01-01", periods=3, tz="UTC"))
    values = evaluate_expression("divide(abs(signal_return),amount)", frame)
    assert values.iloc[0] == pytest.approx(0.001)
    assert pd.isna(values.iloc[1])
    assert values.iloc[2] == pytest.approx(0.001)
    rolled = evaluate_expression("rolling_mean(divide(abs(signal_return),amount),2)", frame)
    assert rolled.isna().all()

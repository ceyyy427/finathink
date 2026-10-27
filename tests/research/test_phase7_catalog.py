import pandas as pd
import pytest

from finahinking.factors.dsl import evaluate_expression
from finahinking.research import factor_catalog_loader


def test_curated_recipes_have_original_formulas_sources_fields_and_reproduction():
    recipes = factor_catalog_loader.load_research_recipes()
    assert 20 <= len(recipes) <= 30
    assert len({item.recipe_id for item in recipes}) == len(recipes)
    assert len({item.expression(5) for item in recipes}) == len(recipes)
    for item in recipes:
        payload = item.to_dict()
        assert payload["implementation_license"] == "MIT" and payload["source_ids"]
        assert payload["required_fields"] and payload["formula"] and payload["version"]
        assert payload["effect_status"] == "NOT_EVALUATED_ON_USER_DATA"
        assert payload["reproduction"]["finite_observations"] > 0
        assert len(payload["reproduction"]["dataset_fingerprint"]) == 64
        assert len(payload["reproduction"]["result_fingerprint"]) == 64


def test_every_recipe_is_prefix_causal_on_real_dsl_evaluation():
    index = pd.date_range("2026-01-01", periods=30, tz="UTC")
    frame = pd.DataFrame({name: [100 + n + n % 4 for n in range(30)] for name in ("open", "high", "low", "close", "volume")}, index=index)
    # Explicit synthetic optional fields, not substitutes inferred in production.
    frame["turnover_rate"] = [0.01 + (n % 7) / 1000 for n in range(30)]
    frame["amount"] = [10000 + 100 * n for n in range(30)]
    changed = frame.copy()
    changed.iloc[20:] *= 100
    for item in factor_catalog_loader.load_research_recipes():
        first = evaluate_expression(item.expression(5), frame)
        second = evaluate_expression(item.expression(5), changed)
        pd.testing.assert_series_equal(first.iloc[:20], second.iloc[:20])


def test_recipe_lookup_and_parameters_fail_closed():
    with pytest.raises(KeyError):
        factor_catalog_loader.get_research_recipe("arbitrary-python")
    recipe = factor_catalog_loader.load_research_recipes()[0]
    with pytest.raises(ValueError):
        recipe.expression(-1)

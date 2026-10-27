"""Original bounded DSL recipes; references are concepts, not copied code.

The implementation is Finathink MIT. User data licensing is separate. A
synthetic reproduction proves formula execution, never investment efficacy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import pandas as pd

from finahinking.factors.dsl import evaluate_expression, parse_factor_expression

from .contracts import stable_digest


@dataclass(frozen=True, slots=True)
class FactorRecipe:
    recipe_id: str
    title: str
    formula: str
    units: str
    concept: str
    version: str = "1.0.0"

    def expression(self, window=5):
        if type(window) is not int or not 1 <= window <= 252:
            raise ValueError("recipe window must be an integer in 1..252")
        return self.formula.format(window=window)

    @property
    def required_fields(self):
        return parse_factor_expression(self.expression(), ("open", "high", "low", "close", "volume")).fields

    def to_dict(self):
        return {"recipe_id": self.recipe_id, "title": self.title, "formula": self.formula, "required_fields": list(self.required_fields), "units": self.units, "version": self.version, "windowed": "{window}" in self.formula, "default_window": 5, "source_ids": ["finathink-native-dsl.v1", self.concept], "implementation_license": "MIT", "data_license": "USER_SUPPLIED_NOT_VERIFIED", "pit_semantics": "historical-prefix features; shifted one observation before label scoring", "effect_status": "NOT_EVALUATED_ON_USER_DATA", "production_write": False, "reproduction": dict(_reproduce(self.recipe_id))}


# Each entry is a distinct formula, not a list of lookback aliases counted as
# separate factors. Mean/rank/dispersion concepts also appear in Qlib Alpha158.
_RECIPES = (
    ("close_momentum", "收盘动量", "return(close,{window})", "ratio", "qlib-alpha158:ROC-concept"),
    ("close_reversal", "收盘均值回归", "negate(return(close,{window}))", "ratio", "finathink:reversal"),
    ("close_mean", "收盘滚动均值", "rolling_mean(close,{window})", "price", "qlib-alpha158:MA-concept"),
    ("close_dispersion", "收盘价格离散度", "rolling_std(close,{window})", "price", "qlib-alpha158:STD-concept"),
    ("close_zscore", "价格标准化偏离", "zscore(close,{window})", "standardized", "finathink:trailing-zscore"),
    ("close_prefix_rank", "价格历史分位", "rank(close)", "percentile", "qlib-alpha158:RANK-concept"),
    ("close_winsorized", "历史缩尾价格", "winsorize(close,0.05,0.95)", "price", "finathink:prefix-winsorization"),
    ("volume_momentum", "成交量变化", "return(volume,{window})", "ratio", "finathink:volume-change"),
    ("volume_mean", "成交量滚动均值", "rolling_mean(volume,{window})", "volume", "qlib-alpha158:VMA-concept"),
    ("volume_dispersion", "成交量离散度", "rolling_std(volume,{window})", "volume", "qlib-alpha158:VSTD-concept"),
    ("volume_zscore", "成交量异常程度", "zscore(volume,{window})", "standardized", "finathink:volume-zscore"),
    ("volume_prefix_rank", "成交量历史分位", "rank(volume)", "percentile", "finathink:volume-rank"),
    ("price_volume_rank", "量价分位合成", "combine(rank(close),rank(volume))", "percentile", "finathink:price-volume-rank"),
    ("price_volume_zscore", "量价标准化合成", "combine(zscore(close,{window}),zscore(volume,{window}))", "standardized", "finathink:price-volume-zscore"),
    ("price_volume_divergence", "量价标准化背离", "combine(zscore(close,{window}),negate(zscore(volume,{window})))", "standardized", "finathink:price-volume-divergence"),
    ("return_mean", "收益滚动均值", "rolling_mean(return(close,1),{window})", "ratio", "finathink:return-mean"),
    ("return_dispersion", "观察期收益波动", "rolling_std(return(close,1),{window})", "ratio-not-annualized", "finathink:return-dispersion"),
    ("return_zscore", "收益标准化异常", "zscore(return(close,1),{window})", "standardized", "finathink:return-zscore"),
    ("lagged_return", "滞后单期收益", "lag(return(close,1),{window})", "ratio", "finathink:lagged-return"),
    ("open_momentum", "开盘动量", "return(open,{window})", "ratio", "finathink:open-momentum"),
    ("high_momentum", "最高价动量", "return(high,{window})", "ratio", "finathink:high-momentum"),
    ("low_momentum", "最低价动量", "return(low,{window})", "ratio", "finathink:low-momentum"),
    ("close_open_momentum_gap", "收开动量差的一半", "combine(return(close,{window}),negate(return(open,{window})))", "ratio", "finathink:close-open-momentum-gap"),
    ("momentum_dispersion_balance", "标准化动量与波动平衡", "combine(zscore(return(close,1),{window}),negate(zscore(rolling_std(return(close,1),{window}),{window})))", "standardized", "finathink:momentum-dispersion-balance"),
)


@lru_cache(maxsize=1)
def research_recipes():
    return tuple(FactorRecipe(*values) for values in _RECIPES)


@lru_cache(maxsize=24)
def _reproduce(recipe_id):
    recipe = next(item for item in research_recipes() if item.recipe_id == recipe_id)
    index = pd.date_range("2025-01-01", periods=96, tz="UTC")
    close = [100 + n * 0.07 + math.sin(n / 4) * 2 for n in range(96)]
    opened = [value + 0.2 * math.cos(n) for n, value in enumerate(close)]
    frame = pd.DataFrame({"close": close, "open": opened, "high": [max(a, b) + 1 for a, b in zip(close, opened)], "low": [min(a, b) - 1 for a, b in zip(close, opened)], "volume": [1000 + 3 * n + 50 * math.cos(n / 3) for n in range(96)]}, index=index)
    values = evaluate_expression(recipe.expression(5), frame).dropna()
    payload = {"recipe_id": recipe.recipe_id, "window": 5, "finite_observations": len(values), "first": round(float(values.iloc[0]), 12), "last": round(float(values.iloc[-1]), 12)}
    return {**payload, "dataset_fingerprint": stable_digest({"timestamps": [value.isoformat() for value in index], "data": frame.to_dict(orient="list")}), "result_fingerprint": stable_digest(payload), "data_mode": "SAMPLE", "purpose": "deterministic mathematical reproduction, not evidence of market efficacy"}

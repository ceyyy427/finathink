"""Offline authoring only: fixed-source extraction and scalar reference arithmetic.
No Finathink, pandas, numpy, upstream modules or production evaluator are imported.
"""

import ast
import hashlib
import json
import math
import statistics
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if len(sys.argv) != 2:
    raise SystemExit(
        "Usage: python3 docs/research/phase24-build-content.py PATH_TO_PINNED_REFERENCE_ROOT"
    )
REF = Path(sys.argv[1]).resolve()
SOURCES = {
    "pyanomaly": (
        "https://github.com/chulwoohan/pyanomaly",
        "59758c913a4146bbfb9d3c056668ccdc5231c8c8",
        "pyanomaly/characteristics.py",
        "LICENSE.txt",
    ),
    "qlib": (
        "https://github.com/microsoft/qlib",
        "54355232463878d2eebb91fe0ee5fa7fa1f5976c",
        "qlib/contrib/data/loader.py",
        "LICENSE",
    ),
    "QUANTAXIS": (
        "https://github.com/yutiansut/QUANTAXIS",
        "232aa7ac9869966108f02a8fff85f1f7b09a9ce2",
        "QUANTAXIS/QAIndicator/indicators.py",
        "LICENSE",
    ),
    "deepseek-harness-quant": (
        "https://github.com/yuanwang589-dev/deepseek-harness-quant",
        "9f72ab7e64799aecfc79cf3b913359acea1dd9b9",
        "factors/classic_indicators.py",
        "LICENSE",
    ),
}
FILES = {}
CARDS = {}
FORMULAS = []
CASES = []


def file(source, path=None):
    _repo, commit, default, _licensepath = SOURCES[source]
    path = path or default
    if (source, path) not in FILES:
        raw = subprocess.check_output(["git", "-C", str(REF / source), "show", f"{commit}:{path}"])
        assert (REF / source / path).read_bytes() == raw, "source drift"
        FILES[source, path] = (raw.decode("utf-8-sig"), hashlib.sha256(raw).hexdigest())
    return path, FILES[source, path]


def card(source, symbol, path=None, part=None):
    path, (text, sha) = file(source, path)
    nodes = [
        n
        for n in ast.walk(ast.parse(text))
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == symbol
    ]
    assert nodes, (source, path, symbol)
    node = max(nodes, key=lambda n: n.end_lineno - n.lineno)
    if part and source == "qlib":
        choices = [
            n
            for n in ast.walk(node)
            if isinstance(n, ast.If)
            and isinstance(n.test, ast.Call)
            and any(isinstance(a, ast.Constant) and a.value == part for a in n.test.args)
        ]
        if not choices and part in {"KMID", "KUP", "KLOW", "KSFT", "KLEN"}:
            choices = [
                n for n in ast.walk(node) if isinstance(n, ast.If) and "kbar" in ast.unparse(n.test)
            ]
        assert choices, part
        node = choices[0]
    key = (
        source.lower()
        + "-"
        + symbol.replace("_", "-").lower()
        + ("-" + part.lower() if part else "")
    )
    if key not in CARDS:
        repo, commit, _, licensepath = SOURCES[source]
        # Formula statements are captured, not imported/executed. Helpers remain explicitly source-reported.
        exprs = [
            ast.unparse(n)
            for n in ast.walk(node)
            if isinstance(n, (ast.Assign, ast.AugAssign, ast.Return))
        ]
        original = "\n".join(exprs)
        CARDS[key] = {
            "card_id": key,
            "title": (ast.get_docstring(node) if isinstance(node, ast.FunctionDef) else part)
            or symbol,
            "source_id": source.lower(),
            "repository": repo,
            "commit": commit,
            "path": path,
            "sha256": sha,
            "license_id": "MIT",
            "license_path": licensepath,
            "read_date": "2026-11-01",
            "symbol": symbol + (f"::{part}" if part else ""),
            "line_start": node.lineno,
            "line_end": node.end_lineno,
            "original_formula": original,
            "formula_status": "EXPLICIT" if original else "UNRESOLVED",
            "adaptation_status": "REFERENCE_ONLY",
            "evidence_status": "SOURCE_REPORTED",
            "factor_id": None,
            "source_url": f"{repo}/blob/{commit}/{path}#L{node.lineno}",
            "reading_note": "来源代码的定义记录；辅助函数、预处理和实证结论未据此升级为本地验证。",
        }
    return key


# Small, intentionally non-monotone fixture. Values are synthetic, not market observations.
A = {
    "close": [10, 11, 9, 12, 10, 13, 11, 14],
    "open": [9, 10, 10, 11, 11, 12, 12, 13],
    "high": [12, 13, 12, 14, 13, 15, 14, 17],
    "low": [8, 9, 7, 10, 8, 10, 9, 11],
    "volume": [100, 120, 90, 140, 110, 160, 130, 180],
    "amount": [1000, 1320, 810, 1680, 1100, 2080, 1430, 2520],
    "turnover_rate": [0.01, 0.012, 0.009, 0.014, 0.011, 0.016, 0.013, 0.018],
    "total_return": [0.01, 0.105, -0.17, 0.34, -0.16, 0.305, -0.15, 0.28],
    "strategy_return": [0.01, -0.02, 0.03, 0.01, -0.01, 0.04, -0.02, 0.03],
    "benchmark_return": [0.005, -0.01, 0.02, 0.015, -0.02, 0.025, -0.015, 0.02],
    "bond_yield_long": [0.04, 0.041, 0.039, 0.042, 0.04, 0.038, 0.037, 0.039],
    "bond_yield_short": [0.025, 0.026, 0.028, 0.027, 0.026, 0.025, 0.024, 0.026],
    "social_finance": [100, 120, 90, 140, 110, 160, 130, 180],
    "industry": ["IND-A"] * 8,
}
FIN = {
    "total_assets": 1000,
    "book_equity": 400,
    "net_profit": 50,
    "market_cap": 1200,
    "revenue": 600,
    "operating_cash_flow": 70,
    "capital_expenditure": 30,
    "research_expense": 12,
    "total_debt": 250,
    "cash": 90,
    "enterprise_book_value": 650,
    "dividends": 10,
    "share_repurchases": 8,
    "share_issuance": 20,
    "ebitda": 110,
    "ebit": 85,
    "gross_profit": 220,
    "operating_expense": 330,
    "operating_accruals": -20,
    "current_assets": 320,
    "current_liabilities": 160,
    "inventory": 75,
    "receivables": 95,
    "ppe": 280,
    "depreciation": 25,
    "long_term_investment": 80,
    "short_term_investment": 40,
    "total_liabilities": 500,
    "long_term_debt": 180,
    "short_term_debt": 70,
    "preferred_stock": 25,
    "debt_issuance": 45,
    "debt_repayment": 25,
    "short_term_debt_change": 5,
    "employees": 100,
    "shares_outstanding": 100,
}
for i, (name, base) in enumerate(FIN.items()):
    A[name] = [base + (j * j + (i % 5 + 1) * j) * (1 if base >= 0 else -1) for j in range(8)]
UNITS = {
    f: (
        "CNY"
        if f not in {"employees", "shares_outstanding"}
        else "count"
        if f == "employees"
        else "shares"
    )
    for f in FIN
}
UNITS.update({f: "CNY" for f in ("close", "open", "high", "low", "amount", "social_finance")})
UNITS.update(
    volume="shares",
    turnover_rate="ratio_free_float",
    total_return="ratio",
    strategy_return="ratio",
    benchmark_return="ratio",
    bond_yield_long="ratio",
    bond_yield_short="ratio",
    industry="historical_classification",
)
B = {}
C = {}
for f, values in A.items():
    if f == "industry":
        B[f] = values.copy()
        C[f] = ["IND-B"] * 8
    else:
        i = list(A).index(f)
        B[f] = [
            v * (1.25 + (i % 3) * 0.1) + (j % 3) * 0.03 * (i % 4 + 1) for j, v in enumerate(values)
        ]
        C[f] = [v * (0.65 + (i % 4) * 0.07) + (j % 2) * 0.02 for j, v in enumerate(values)]
for f in ("open", "high", "low", "close"):
    B[f] = [2 * x for x in A[f]]
    C[f] = [3 * x for x in A[f]]
DATA = {"AAA": A, "BBB": B, "CCC": C}


def v(field, lag=0, asset="AAA"):
    return DATA[asset][field][-1 - lag]


def avg(xs):
    return sum(xs) / len(xs)


def sd(xs):
    return statistics.pstdev(xs)


def cov(xs, ys):
    return avg([(x - avg(xs)) * (y - avg(ys)) for x, y in zip(xs, ys)])


def ret(field="close", n=1, lag=0):
    return v(field, lag) / v(field, lag + n) - 1


def arr(field, n=3):
    return A[field][-n:]


def returns(n=3):
    return [
        A["close"][j] / A["close"][j - 1] - 1 for j in range(len(A["close"]) - n, len(A["close"]))
    ]


def diffs(field, n=3):
    return [A[field][j] - A[field][j - 1] for j in range(len(A[field]) - n, len(A[field]))]


def slope(xs):
    x = list(range(len(xs)))
    return cov(x, xs) / cov(x, x)


def ratio(n, d):
    return f"divide({n},{d})"


def sub(a, b):
    return f"subtract({a},{b})"


def add(a, b):
    return f"add({a},{b})"


def mul(a, b):
    return f"multiply({a},{b})"


def mean(a, w=3):
    return f"ts_mean({a},{w},{w})"


def std(a, w=3):
    return f"ts_std({a},{w},{w})"


def lag(a, n=1):
    return f"ts_lag({a},{n})"


def diff(a, n=1):
    return f"ts_diff({a},{n})"


def growth(a):
    return f"ts_return({a},1)"


def define(
    family,
    name,
    title,
    expr,
    expected,
    source,
    symbol,
    meaning,
    reason,
    *,
    path=None,
    part=None,
    unit="ratio",
    window=3,
    scope="ts",
    note="",
    refs=None,
):
    key = card(source, symbol, path, part)
    fid = f"lib-{family}-{name}"
    fields = sorted(
        {
            n.id
            for n in ast.walk(ast.parse(expr, mode="eval"))
            if isinstance(n, ast.Name) and n.id in UNITS
        }
    )
    assert len(expr) <= 512, (fid, len(expr))
    assert fields, (fid, expr)
    CARDS[key]["adaptation_status"] = "INDEPENDENT_IMPLEMENTATION"
    CARDS[key]["evidence_status"] = "DEFINITION_CHECKED"
    if CARDS[key]["factor_id"] is None:
        CARDS[key]["factor_id"] = fid
    else:
        # Separate outputs retain the exact same source span and are not independent source evidence.
        newkey = key + "-" + name
        CARDS[newkey] = {
            **CARDS[key],
            "card_id": newkey,
            "factor_id": fid,
            "symbol": CARDS[key]["symbol"] + "::" + name,
        }
        key = newkey
    common = "独立实现定义；窗口为示例研究参数，不继承上游胜率、收益或方向。缺失不填0，零分母/非有限结果无值，统计标准差采用总体ddof=0。"
    notes = common + note
    pit = "每行先按available_at选择当时已知版本；禁止未来披露或修订回填历史，市场字段使用RAW原始价格。"
    if family in {"growth", "industry", "event"}:
        pit += "财务差分按面板观察间隔，非年度标签；年度研究须输入相邻可比年度报告的已知观测，不把日频前向延续值当年度增长。"
    counter = "缺少任一声明字段、单位错误或发布晚于决策时点时不产生可用研究值。"
    if "divide" in expr:
        counter += "分母为0时返回缺失；负分母的代数值只作研究观察，不能直接解释为便宜或优质。"
    explanation = f"{title}：{meaning}。" + note
    ex = {
        "fixture": "numerical-goldens.json",
        "case_id": fid,
        "instrument": "AAA",
        "observed_at": "2026-01-09T16:00:00Z",
        "expected": float(expected),
        "calculation_reason": reason,
        "inputs": {
            "times": [f"2026-01-0{d}T16:00:00Z" for d in range(2, 10)][-window:],
            "field_units": {field: UNITS[field] for field in fields},
            "instruments": {
                asset: {field: DATA[asset][field][-window:] for field in fields}
                for asset in (list(DATA) if scope in {"cs", "mixed"} else ["AAA"])
            },
        },
    }
    FORMULAS.append(
        {
            "factor_id": fid,
            "title": title,
            "family": family,
            "version": "1.0.0",
            "formula": expr,
            "required_fields": fields,
            "field_units": {f: UNITS[f] for f in fields},
            "units": unit,
            "window": window,
            "scope": scope,
            "knowledge_refs": refs or ["point-in-time", "units", "missing-data"],
            "source_card_ids": [key],
            "numeric_example": ex,
            "counterexample": counter,
            "direction_scope": "方向待按用户样本、市场制度、持有期和成本研究；本目录不批准买卖或固定多空方向。",
            "explanation": explanation,
            "economic_definition": meaning,
            "deduplication_reason": meaning
            + "；独立性来自经济量和计算结构，不来自窗口、符号或别名。",
            "adaptation_notes": notes,
            "pit_policy": pit,
            "missing_policy": "STRICT_REQUIRED_FIELDS; NO_ZERO_FILL; ZERO_DENOMINATOR_TO_NULL",
            "adjustment_policy": "RAW；公司行动单独保留，不自动前复权。",
            "evidence_level": "DEFINITION_CHECKED",
            "usage_scope": "RESEARCH_ONLY",
        }
    )
    CASES.append(
        {
            "factor_id": fid,
            "expected": float(expected),
            "instrument": "AAA",
            "observed_at": "2026-01-09T16:00:00Z",
            "absolute_tolerance": 1e-10,
            "relative_tolerance": 1e-10,
            "calculation_reason": reason,
            "independent_method": "Python标准库标量算术/总体矩，无生产DSL、pandas或上游执行。",
            "source_card_ids": [key],
        }
    )


# Price trend: genuinely distinct published definitions (no window/sign clones).
def trend(name, title, expr, expected, symbol, meaning, reason, source="qlib", part=None, **kw):
    define(
        "trend",
        name,
        title,
        expr,
        expected,
        source,
        symbol,
        meaning,
        reason,
        part=part,
        refs=["momentum", "moving-average"],
        **kw,
    )


trend(
    "roc",
    "区间价格动量",
    growth("close"),
    ret(),
    "roc",
    "本期价格相对上一观察价格的比例变化",
    "14/11-1",
    source="deepseek-harness-quant",
    window=2,
)
trend(
    "ma-spread",
    "短长均线强度",
    sub(ratio(mean("close", 3), mean("close", 6)), 1),
    avg(arr("close", 3)) / avg(arr("close", 6)) - 1,
    "ma_bull",
    "短均线相对长均线的价格趋势",
    "mean(13,11,14)/mean(9,12,10,13,11,14)-1",
    source="deepseek-harness-quant",
    window=6,
    note="上游20/60观察窗口改为3/6，属于一个参数化定义。",
)
trend(
    "slope",
    "归一化价格斜率",
    ratio("ts_slope(close,3,3)", "close"),
    slope(arr("close")) / v("close"),
    "get_feature_config",
    "价格线性拟合每观察期斜率相对当前价格",
    "OLS slope([13,11,14])=0.5; 0.5/14",
    part="BETA",
    unit="ratio/session",
)
trend(
    "body",
    "日内实体收益",
    ratio(sub("close", "open"), "open"),
    (v("close") - v("open")) / v("open"),
    "get_feature_config",
    "开盘到收盘的有符号实体比例",
    "(14-13)/13",
    part="KMID",
    window=1,
)
trend(
    "upper-shadow",
    "上影线比例",
    ratio(sub("high", "maximum(open,close)"), "open"),
    (v("high") - max(v("open"), v("close"))) / v("open"),
    "get_feature_config",
    "当期最高价超过实体上沿的比例",
    "(17-max(13,14))/13",
    part="KUP",
    window=1,
)
trend(
    "lower-shadow",
    "下影线比例",
    ratio(sub("minimum(open,close)", "low"), "open"),
    (min(v("open"), v("close")) - v("low")) / v("open"),
    "get_feature_config",
    "实体下沿超过当期最低价的比例",
    "(min(13,14)-11)/13",
    part="KLOW",
    window=1,
)
trend(
    "close-location",
    "收盘相对区间中点",
    ratio(sub(sub(mul(2, "close"), "high"), "low"), "open"),
    (2 * v("close") - v("high") - v("low")) / v("open"),
    "get_feature_config",
    "收盘相对高低区间中点的有符号距离",
    "(2*14-17-11)/13",
    part="KSFT",
    window=1,
)
trend(
    "aroon-spread",
    "高低点时序差",
    ratio(sub("ts_argmax(high,3,3)", "ts_argmin(low,3,3)"), 3),
    (arr("high").index(max(arr("high"))) - arr("low").index(min(arr("low")))) / 3,
    "get_feature_config",
    "窗口最高价出现位置与最低价出现位置的差",
    "argmax([15,14,17])=2,argmin([10,9,11])=1; (2-1)/3",
    part="IMXD",
    unit="sessions",
    note="位置统一为窗口内最早并列值的零基索引。",
)
# Reversal definitions, not negative momentum aliases.
d = diff("close")
gains = mean(f"maximum({d},0)")
losses = mean(f"maximum(negate({d}),0)")
define(
    "reversal",
    "rsi",
    "相对强弱指标",
    sub(100, ratio(100, add(1, ratio(gains, losses)))),
    100
    - 100
    / (1 + avg([max(x, 0) for x in diffs("close")]) / avg([max(-x, 0) for x in diffs("close")])),
    "deepseek-harness-quant",
    "rsi",
    "上涨均幅相对下跌均幅的振荡强度",
    "最近差分[3,-2,3]；gain=2,loss=2/3；100-100/(1+3)=75",
    window=4,
    refs=["rsi", "mean-reversion"],
)
define(
    "reversal",
    "boll-lower",
    "布林下轨偏离",
    sub(ratio("close", sub(mean("close"), mul(2, std("close")))), 1),
    v("close") / (avg(arr("close")) - 2 * sd(arr("close"))) - 1,
    "deepseek-harness-quant",
    "boll_lower_dev",
    "当前价格相对均值减两倍波动的下轨偏离",
    "14/(mean([13,11,14])-2*pstdev([13,11,14]))-1",
    note="上游样本标准差改为总体标准差，并明确记录而非声称逐值复制。",
    refs=["bollinger", "mean-reversion"],
)
define(
    "reversal",
    "williams",
    "威廉区间位置",
    "negate("
    + mul(100, ratio(sub("ts_max(high,3,3)", "close"), sub("ts_max(high,3,3)", "ts_min(low,3,3)")))
    + ")",
    -100 * (max(arr("high")) - v("close")) / (max(arr("high")) - min(arr("low"))),
    "deepseek-harness-quant",
    "wr",
    "收盘到窗口高点的距离相对高低区间",
    "-100*(17-14)/(17-9)=-37.5",
    note="要求真实high/low，移除上游缺失时用close±1%补造的分支。",
    refs=["mean-reversion"],
)
tp = ratio(add(add("high", "low"), "close"), 3)
tpall = [(h + l + c) / 3 for h, l, c in zip(A["high"], A["low"], A["close"])]
devs = [abs(tpall[j] - avg(tpall[j - 2 : j + 1])) for j in range(5, 8)]
define(
    "reversal",
    "cci",
    "典型价格偏离指标",
    ratio(sub(tp, mean(tp)), mul(0.015, mean(f"abs({sub(tp, mean(tp))})"))),
    (tpall[-1] - avg(tpall[-3:])) / (0.015 * avg(devs)),
    "deepseek-harness-quant",
    "cci",
    "典型价格相对均值按历史滚动绝对偏离尺度标准化",
    "TP=(H+L+C)/3；最近三个abs(TP_t-mean(TP[t-2:t]))再求均值；当前偏离除以0.015倍该尺度",
    window=5,
    note="保留此来源的两层滚动绝对偏离定义，区别于以当前窗口均值统一计算MAD的经典CCI。",
    refs=["mean-reversion", "robust-statistics"],
)
# Volatility/tails.
define(
    "volatility",
    "return-std",
    "收益波动",
    std("ts_return(close,1)"),
    sd(returns()),
    "pyanomaly",
    "c_retvol",
    "窗口内简单收益率总体离散度",
    "pstdev([13/10-1,11/13-1,14/11-1])",
    window=4,
    note="上游月内样本统计改为末端滚动总体标准差。",
    refs=["volatility", "standard-deviation"],
)
define(
    "volatility",
    "maximum-return",
    "极端单期收益",
    "ts_max(ts_return(close,1),3,3)",
    max(returns()),
    "pyanomaly",
    "c_rmax1_21d",
    "近期最大的单期上涨尾部收益",
    "max([0.3,-2/13,3/11])=0.3",
    window=4,
    refs=["downside-risk"],
)
define(
    "volatility",
    "intraday-range",
    "日内振幅",
    ratio(sub("high", "low"), "open"),
    (v("high") - v("low")) / v("open"),
    "qlib",
    "get_feature_config",
    "当期高低价跨度相对开盘价格",
    "(17-11)/13",
    part="KLEN",
    window=1,
    refs=["volatility"],
)
tr = "maximum(subtract(high,low),maximum(abs(subtract(ts_lag(close,1),high)),abs(subtract(ts_lag(close,1),low))))"
trs = [
    max(
        A["high"][j] - A["low"][j],
        abs(A["close"][j - 1] - A["high"][j]),
        abs(A["close"][j - 1] - A["low"][j]),
    )
    for j in range(5, 8)
]
define(
    "volatility",
    "atr",
    "平均真实波幅",
    mean(tr),
    avg(trs),
    "QUANTAXIS",
    "QA_indicator_ATR",
    "日内区间与隔夜跳空共同决定的平均真实价格波幅",
    "TR最后三项[5,5,6]，平均16/3",
    unit="CNY",
    window=4,
    refs=["atr"],
)
define(
    "volatility",
    "price-dispersion",
    "价格水平离散度",
    ratio(std("close"), "close"),
    sd(arr("close")) / v("close"),
    "qlib",
    "get_feature_config",
    "价格水平标准差相对现价，与收益率波动不同",
    "pstdev([13,11,14])/14",
    part="STD",
    refs=["volatility"],
)
wv = mul("abs(ts_return(close,1))", "volume")
wvals = [abs(r) * vol for r, vol in zip(returns(), arr("volume"))]
define(
    "volatility",
    "volume-weighted-move",
    "成交量加权波动变异系数",
    ratio(std(wv), mean(wv)),
    sd(wvals) / avg(wvals),
    "qlib",
    "get_feature_config",
    "绝对价格变动乘成交股数后的相对离散度",
    "先算abs([0.3,-2/13,3/11])*[160,130,180]，再取总体标准差/均值",
    part="WVMA",
    window=4,
    refs=["volume-price", "volatility"],
)
# Liquidity definitions have separate economic denominators / units.
define(
    "liquidity",
    "amihud",
    "单位成交额价格冲击",
    mean(ratio("abs(ts_return(close,1))", "amount")),
    avg([abs(r) / a for r, a in zip(returns(), arr("amount"))]),
    "pyanomaly",
    "c_ami_126d",
    "每单位成交金额对应的绝对价格变动",
    "mean(abs([0.3,-2/13,3/11])/[2080,1430,2520])",
    unit="ratio/CNY",
    window=4,
    note="原式辅助滚动实现另见同文件roll_126d；此处以明确成交额amount替代供应商美元量。",
    refs=["liquidity"],
)
define(
    "liquidity",
    "turnover",
    "平均流通换手",
    mean("turnover_rate"),
    avg(arr("turnover_rate")),
    "pyanomaly",
    "c_turnover_126d",
    "按同一自由流通股口径计算的成交占比",
    "mean([.016,.013,.018])",
    unit="ratio_free_float",
    note="原式总股本换手，适配为明确声明的自由流通股口径；不混用百分数。",
    refs=["turnover-ratio"],
)
define(
    "liquidity",
    "turnover-volatility",
    "换手率波动",
    std("turnover_rate"),
    sd(arr("turnover_rate")),
    "pyanomaly",
    "c_std_turn",
    "流通换手需求在观察期内的不稳定程度",
    "pstdev([.016,.013,.018])",
    unit="ratio_free_float",
    note="自由流通股口径且使用ddof=0。",
    refs=["turnover-ratio", "liquidity"],
)
define(
    "liquidity",
    "dollar-volume",
    "平均成交金额",
    mean("amount"),
    avg(arr("amount")),
    "pyanomaly",
    "c_dolvol_126d",
    "成交容量的金额尺度",
    "(2080+1430+2520)/3",
    unit="CNY",
    refs=["liquidity", "capacity"],
)
define(
    "liquidity",
    "price-volume-correlation",
    "价格与对数成交量相关",
    "ts_corr(close,log(add(volume,1)),3,3)",
    cov(arr("close"), [math.log(x + 1) for x in arr("volume")])
    / (sd(arr("close")) * sd([math.log(x + 1) for x in arr("volume")])),
    "qlib",
    "get_feature_config",
    "价格水平与对数成交量同步程度",
    "corr([13,11,14],log([161,131,181]))，直接用标量总体协方差/标准差",
    part="CORR",
    note="成交量必须以股（shares）为基准；log(volume+1)中的1表示一股平滑，不能把手数或千股直接代入，否则平滑尺度改变。",
    refs=["correlation", "volume-price"],
)
pr = ratio("close", lag("close"))
vr = "log(add(divide(volume,ts_lag(volume,1)),1))"
pvals = [r + 1 for r in returns()]
vvals = [math.log(A["volume"][j] / A["volume"][j - 1] + 1) for j in range(5, 8)]
define(
    "liquidity",
    "change-volume-correlation",
    "价格变化与量比相关",
    f"ts_corr({pr},{vr},3,3)",
    cov(pvals, vvals) / (sd(pvals) * sd(vvals)),
    "qlib",
    "get_feature_config",
    "价格相邻比值与对数量比的同步性",
    "corr([13/10,11/13,14/11],log([160/110+1,130/160+1,180/130+1]))",
    part="CORD",
    note="当前与滞后成交量都必须先统一为股（shares）；log(volume/lag(volume)+1)中的1是无量纲量比平滑，与log(volume+1)的一股平滑不同；不得将一端股数与另一端手数或千股混用。",
    window=4,
    refs=["volume-price", "correlation"],
)
# Financial values: each published numerator is an economically different claim.
valdefs = [
    (
        "book-market",
        "账面市值比",
        "book_equity",
        "market_cap",
        "c_be_me",
        "普通股账面权益相对股权市值",
    ),
    ("assets-market", "资产市值比", "total_assets", "market_cap", "c_at_me", "总资产相对股权市值"),
    (
        "earnings-yield",
        "盈利收益率",
        "net_profit",
        "market_cap",
        "c_ni_me",
        "可比期间净利润相对股权市值",
    ),
    (
        "sales-yield",
        "销售市值比",
        "revenue",
        "market_cap",
        "c_sale_me",
        "可比期间销售收入相对股权市值",
    ),
    (
        "cashflow-yield",
        "经营现金流收益率",
        "operating_cash_flow",
        "market_cap",
        "c_ocf_me",
        "经营现金流相对股权市值",
    ),
    ("rd-yield", "研发市值比", "research_expense", "market_cap", "c_rd_me", "研发费用相对股权市值"),
    ("debt-market", "债务市值比", "total_debt", "market_cap", "c_debt_me", "有息债务相对股权市值"),
]
for name, title, n, d, sym, meaning in valdefs:
    define(
        "value",
        name,
        title,
        ratio(n, d),
        v(n) / v(d),
        "pyanomaly",
        sym,
        meaning,
        f"{n}={v(n)} / {d}={v(d)}",
        window=1,
        refs=["book-to-market", "earnings-yield"],
    )


def value(name, title, expr, expected, sym, meaning, reason):
    define(
        "value",
        name,
        title,
        expr,
        expected,
        "pyanomaly",
        sym,
        meaning,
        reason,
        window=1,
        refs=["book-to-market", "cashflow-quality"],
    )


value(
    "free-cashflow-yield",
    "自由现金流收益率",
    ratio(sub("operating_cash_flow", "capital_expenditure"), "market_cap"),
    (v("operating_cash_flow") - v("capital_expenditure")) / v("market_cap"),
    "c_fcf_me",
    "经营现金流扣资本开支后相对市值",
    "(经营现金流-资本支出)/市值；全部金额单位一致",
)
ev = add("market_cap", sub("total_debt", "cash"))
evv = v("market_cap") + v("total_debt") - v("cash")
value(
    "enterprise-book-market",
    "企业账面市场比",
    ratio("enterprise_book_value", ev),
    v("enterprise_book_value") / evv,
    "c_bev_mev",
    "净经营资产账面值相对企业市场价值",
    "企业价值=market_cap+total_debt-cash；账面企业价值/企业价值",
)
value(
    "payout-yield",
    "股东支付收益率",
    ratio(add("dividends", "share_repurchases"), "market_cap"),
    (v("dividends") + v("share_repurchases")) / v("market_cap"),
    "c_eqpo_me",
    "现金分红与股份回购合计相对市值",
    "(dividends+share_repurchases)/market_cap",
)
value(
    "enterprise-ebitda-yield",
    "企业息税折旧摊销前收益率",
    ratio("ebitda", ev),
    v("ebitda") / evv,
    "c_ebitda_mev",
    "息税折旧摊销前利润相对企业价值",
    "ebitda/(market_cap+total_debt-cash)",
)
value(
    "netdebt-market",
    "净债务市值比",
    ratio(sub("total_debt", "cash"), "market_cap"),
    (v("total_debt") - v("cash")) / v("market_cap"),
    "c_netdebt_me",
    "扣现金后的债务相对股权市值",
    "(total_debt-cash)/market_cap，现金缺失不默认为0",
)
# Quality 16 definitions.
qdefs = [
    ("operating-margin", "经营利润率", "ebit", "revenue", "c_ebit_sale", "息税前利润相对销售收入"),
    (
        "gross-profit-assets",
        "毛利资产比",
        "gross_profit",
        "total_assets",
        "c_gp_at",
        "毛利润相对总资产",
    ),
    ("roe", "净资产收益率", "net_profit", "book_equity", "c_ni_be", "净利润相对账面权益"),
    (
        "return-enterprise",
        "净经营资产回报",
        "ebit",
        "enterprise_book_value",
        "c_ebit_bev",
        "息税前利润相对净经营资产",
    ),
    (
        "accrual-assets",
        "经营应计资产比",
        "operating_accruals",
        "total_assets",
        "c_oaccruals_at",
        "经营应计金额相对总资产",
    ),
    (
        "operating-leverage",
        "经营费用资产比",
        "operating_expense",
        "total_assets",
        "c_opex_at",
        "经营成本相对资产规模",
    ),
    (
        "enterprise-turnover",
        "经营资产周转",
        "revenue",
        "enterprise_book_value",
        "c_sale_bev",
        "销售收入相对净经营资产",
    ),
    ("rd-sales", "研发费用率", "research_expense", "revenue", "c_rd_sale", "研发投入相对销售收入"),
    ("cash-assets", "现金资产比", "cash", "total_assets", "c_cash_at", "现金相对总资产"),
    (
        "current-ratio",
        "流动比率",
        "current_assets",
        "current_liabilities",
        "c_currat",
        "流动资产对流动负债覆盖",
    ),
    (
        "receivables-turnover",
        "应收账款周转",
        "revenue",
        "receivables",
        "c_salerec",
        "销售收入对期末应收账款尺度",
    ),
    (
        "depreciation-ppe",
        "固定资产折旧率",
        "depreciation",
        "ppe",
        "c_depr",
        "折旧费用相对固定资产净额",
    ),
]
for name, title, n, d, sym, meaning in qdefs:
    define(
        "quality",
        name,
        title,
        ratio(n, d),
        v(n) / v(d),
        "pyanomaly",
        sym,
        meaning,
        f"{n}={v(n)} / {d}={v(d)}",
        window=1,
        refs=["gross-profitability", "roe", "accruals"],
    )


def quality(name, title, expr, expected, sym, meaning, reason, window=1):
    define(
        "quality",
        name,
        title,
        expr,
        expected,
        "pyanomaly",
        sym,
        meaning,
        reason,
        window=window,
        refs=["cashflow-quality", "accruals"],
    )


quality(
    "cash-profit-assets",
    "现金经营利润资产比",
    ratio(sub(add("ebitda", "research_expense"), "operating_accruals"), "total_assets"),
    (v("ebitda") + v("research_expense") - v("operating_accruals")) / v("total_assets"),
    "c_cop_at",
    "息税折旧摊销前利润加研发减应计后对资产的贡献",
    "(ebitda+research_expense-operating_accruals)/total_assets，研发缺失不补0",
)
quality(
    "accrual-profit",
    "经营应计利润比",
    ratio("operating_accruals", "abs(net_profit)"),
    v("operating_accruals") / abs(v("net_profit")),
    "c_oaccruals_ni",
    "经营应计金额相对净利润绝对规模",
    "operating_accruals/abs(net_profit)；以明确净利润替代来源nix口径",
)
quality(
    "asset-turnover",
    "平均资产周转",
    ratio("revenue", mean("total_assets", 2)),
    v("revenue") / avg(arr("total_assets", 2)),
    "c_at_turnover",
    "销售收入相对相邻两期平均资产",
    "revenue/mean(total_assets本期和上一观察期)",
    window=2,
)
quality(
    "quick-ratio",
    "速动比率",
    ratio(sub("current_assets", "inventory"), "current_liabilities"),
    (v("current_assets") - v("inventory")) / v("current_liabilities"),
    "c_quick",
    "排除存货后的流动资产对流动负债覆盖",
    "(current_assets-inventory)/current_liabilities；无替代字段回填",
)


# Growth/investment: actual separate balance sheet components, observed-period deltas.
def gr(name, title, expr, expected, sym, meaning, reason, window=2):
    define(
        "growth",
        name,
        title,
        expr,
        expected,
        "pyanomaly",
        sym,
        meaning,
        reason,
        window=window,
        note="原来源年度财务定义适配为相邻可比观察期；日报面板上不可称同比增长。所有组成字段显式提供，取消fillna(0)。",
        refs=["asset-growth", "publication-lag", "revisions"],
    )


for name, title, f, sym in [
    ("asset-growth", "资产观察期增长", "total_assets", "c_at_gr1"),
    ("sales-growth", "销售观察期增长", "revenue", "c_sale_gr1"),
    ("capex-growth", "资本支出观察期增长", "capital_expenditure", "c_capx_gr1"),
]:
    gr(
        name,
        title,
        growth(f),
        ret(f),
        sym,
        f"{title}衡量对应经济规模的扩张",
        f"{f}本期/上一观察期-1",
    )
for name, title, f, sym in [
    ("inventory-investment", "存货投资变化", "inventory", "c_inv_gr1a"),
    ("long-investment", "长期投资变化", "long_term_investment", "c_lti_gr1a"),
    ("short-investment", "短期投资变化", "short_term_investment", "c_sti_gr1a"),
]:
    gr(
        name,
        title,
        ratio(diff(f), "total_assets"),
        (v(f) - v(f, 1)) / v("total_assets"),
        sym,
        f"{title}相对当期总资产",
        f"({f}本期-{f}上期)/total_assets，明确采用来源JKP当期分母分支",
    )
components = [
    (
        "current-operating-assets",
        "流动经营资产变化",
        sub("current_assets", "cash"),
        lambda l: v("current_assets", l) - v("cash", l),
        "c_coa_gr1a",
        "流动资产扣现金后经营资本的变化",
    ),
    (
        "current-operating-liabilities",
        "流动经营负债变化",
        sub("current_liabilities", "short_term_debt"),
        lambda l: v("current_liabilities", l) - v("short_term_debt", l),
        "c_col_gr1a",
        "流动负债扣短债后经营融资的变化",
    ),
    (
        "working-capital",
        "非现金营运资本变化",
        sub(sub("current_assets", "cash"), sub("current_liabilities", "short_term_debt")),
        lambda l: (
            v("current_assets", l)
            - v("cash", l)
            - v("current_liabilities", l)
            + v("short_term_debt", l)
        ),
        "c_cowc_gr1a",
        "经营流动资产减经营流动负债的净投入变化",
    ),
    (
        "noncurrent-operating-assets",
        "非流动经营资产变化",
        sub(sub("total_assets", "current_assets"), "long_term_investment"),
        lambda l: v("total_assets", l) - v("current_assets", l) - v("long_term_investment", l),
        "c_ncoa_gr1a",
        "资产扣流动资产和长期金融投资后的变化",
    ),
    (
        "noncurrent-operating-liabilities",
        "非流动经营负债变化",
        sub(sub("total_liabilities", "current_liabilities"), "long_term_debt"),
        lambda l: v("total_liabilities", l) - v("current_liabilities", l) - v("long_term_debt", l),
        "c_ncol_gr1a",
        "负债扣流动负债与长期金融债后的变化",
    ),
    (
        "net-noncurrent-operating",
        "净非流动经营资产变化",
        sub(
            sub(sub("total_assets", "current_assets"), "long_term_investment"),
            sub(sub("total_liabilities", "current_liabilities"), "long_term_debt"),
        ),
        lambda l: (
            v("total_assets", l)
            - v("current_assets", l)
            - v("long_term_investment", l)
            - v("total_liabilities", l)
            + v("current_liabilities", l)
            + v("long_term_debt", l)
        ),
        "c_nncoa_gr1a",
        "非流动经营资产扣经营负债后的净变化",
    ),
    (
        "financial-liabilities",
        "金融负债变化",
        add("total_debt", "preferred_stock"),
        lambda l: v("total_debt", l) + v("preferred_stock", l),
        "c_fnl_gr1a",
        "债务加优先股金融融资余额变化",
    ),
    (
        "net-financial-assets",
        "净金融资产变化",
        sub(
            add("short_term_investment", "long_term_investment"),
            add("total_debt", "preferred_stock"),
        ),
        lambda l: (
            v("short_term_investment", l)
            + v("long_term_investment", l)
            - v("total_debt", l)
            - v("preferred_stock", l)
        ),
        "c_nfna_gr1a",
        "金融投资扣金融负债后的净持有变化",
    ),
]
for name, title, expr, f, sym, meaning in components:
    gr(
        name,
        title,
        ratio(diff(expr), "total_assets"),
        (f(0) - f(1)) / v("total_assets"),
        sym,
        meaning,
        meaning + "：先分别计算本期/上期组成金额，差值除当期总资产",
    )
rd = "research_expense"
rdstock = add(
    add(add(add(rd, mul(0.8, lag(rd, 1))), mul(0.6, lag(rd, 2))), mul(0.4, lag(rd, 3))),
    mul(0.2, lag(rd, 4)),
)
gr(
    "rd-capital",
    "研发资本化存量",
    ratio(rdstock, "total_assets"),
    sum(w * v(rd, l) for l, w in enumerate([1, 0.8, 0.6, 0.4, 0.2])) / v("total_assets"),
    "c_rd5_at",
    "最近五个观察期研发投入按线性折耗组成的资本存量",
    "[1,.8,.6,.4,.2]分别乘当前及前四期研发后求和，再除总资产",
    window=5,
)
cs = ratio("capital_expenditure", "revenue")
pastcs = [v("capital_expenditure", l) / v("revenue", l) for l in [1, 2, 3]]
gr(
    "abnormal-capex",
    "异常资本支出强度",
    sub(ratio(cs, mean(lag(cs), 3)), 1),
    (v("capital_expenditure") / v("revenue")) / avg(pastcs) - 1,
    "c_capex_abn",
    "本期资本支出收入比相对前三期均值的异常程度",
    "本期capex/revenue除以前三期capex/revenue均值再减1",
    window=4,
)
# Industry neutralization uses actual historical classification and >=2 valid members.
for name, title, expr, expected, sym, meaning, unit, window in [
    (
        "book-market",
        "行业相对账面市值比",
        ratio("book_equity", "market_cap"),
        v("book_equity") / v("market_cap")
        - (
            v("book_equity") / v("market_cap")
            + v("book_equity", asset="BBB") / v("market_cap", asset="BBB")
        )
        / 2,
        "c_bm_ia",
        "同业账面市值比均值之外的相对估值",
        "ratio",
        1,
    ),
    (
        "cashflow-market",
        "行业相对现金流收益率",
        ratio("operating_cash_flow", "market_cap"),
        v("operating_cash_flow") / v("market_cap")
        - (
            v("operating_cash_flow") / v("market_cap")
            + v("operating_cash_flow", asset="BBB") / v("market_cap", asset="BBB")
        )
        / 2,
        "c_cfp_ia",
        "同业现金流收益率均值之外的现金回报差",
        "ratio",
        1,
    ),
    (
        "employment-growth",
        "行业相对雇员增长",
        growth("employees"),
        ret("employees")
        - (ret("employees") + v("employees", asset="BBB") / v("employees", 1, asset="BBB") - 1) / 2,
        "c_chempia",
        "同业就业扩张均值之外的雇员规模变化",
        "ratio",
        2,
    ),
    (
        "size",
        "行业相对市值",
        "market_cap",
        v("market_cap") - (v("market_cap") + v("market_cap", asset="BBB")) / 2,
        "c_mve_ia",
        "股权规模相对同业均值的金额差",
        "CNY",
        1,
    ),
]:
    define(
        "industry",
        name,
        title,
        f"group_demean({expr},industry,2)",
        expected,
        "pyanomaly",
        sym,
        meaning,
        "AAA与BBB同属IND-A，先各自计算定义值，AAA减两者算术均值；CCC的单成员行业不产生值",
        scope="mixed" if window > 1 else "cs",
        unit=unit,
        window=window,
        note="来源行业代码/会计年度分组适配为同一决策时点的历史有效行业分类；不回填缺失。",
        refs=["neutralization", "universe-bias"],
    )


# Event-related financing actions (no fabricated institutional/news fields).
def evdef(name, title, expr, expected, sym, meaning, reason, window=1):
    define(
        "event",
        name,
        title,
        expr,
        expected,
        "pyanomaly",
        sym,
        meaning,
        reason,
        window=window,
        note="观察已披露融资行为，保持企业行动与拆股口径一致；不把融资余额当机构交易。",
        refs=["publication-lag", "adjustment"],
    )


eq = sub(sub("share_issuance", "share_repurchases"), "dividends")
evdef(
    "equity-finance",
    "净股权融资强度",
    ratio(eq, "total_assets"),
    (v("share_issuance") - v("share_repurchases") - v("dividends")) / v("total_assets"),
    "c_eqnetis_at",
    "增发收入扣回购和分红后的外部股权融资",
    "(share_issuance-share_repurchases-dividends)/total_assets，采用来源非JKP分红扣除分支",
)
db = add(sub("debt_issuance", "debt_repayment"), "short_term_debt_change")
evdef(
    "debt-finance",
    "净债务融资强度",
    ratio(db, "total_assets"),
    (v("debt_issuance") - v("debt_repayment") + v("short_term_debt_change")) / v("total_assets"),
    "c_dbnetis_at",
    "债券发行扣偿还并加短债变化的融资流量",
    "(debt_issuance-debt_repayment+short_term_debt_change)/total_assets；不启用缺失回退",
)
evdef(
    "share-issuance",
    "股份净发行比例",
    growth("shares_outstanding"),
    ret("shares_outstanding"),
    "c_chcsho_12m",
    "拆股同口径股数的观察期增长",
    "shares_outstanding本期/上期-1",
    window=2,
)
evdef(
    "composite-issuance",
    "总收益剔除复合发行",
    sub(f"log({ratio('market_cap', lag('market_cap'))})", "log(add(1,total_return))"),
    math.log(v("market_cap") / v("market_cap", 1)) - math.log(1 + v("total_return")),
    "c_eqnpo_60m",
    "市值变化扣含现金分红总收益后的复合融资变化",
    "log(market_cap本期/上期)-log(1+total_return)；总收益是用户声明的已实现含分红收益",
    window=2,
)
# ETF/macro: source outputs independently adapted with explicit boundary fixes.
x = arr("strategy_return")
y = arr("benchmark_return")
define(
    "etf-macro",
    "correlation",
    "策略与ETF基准相关",
    "ts_corr(strategy_return,benchmark_return,3,3)",
    cov(x, y) / (sd(x) * sd(y)),
    "deepseek-harness-quant",
    "corr_stats",
    "已实现策略收益与ETF基准已实现收益的同步程度",
    "总体协方差([.04,-.02,.03],[.025,-.015,.02])除两者总体标准差",
    path="etf/etf_map.py",
    note="采用最近三个观察期；输入是已实现收益，不是未来收益标签。",
    refs=["etf-exposure", "correlation"],
)
define(
    "etf-macro",
    "beta",
    "策略对ETF基准敏感度",
    ratio(
        "ts_cov(strategy_return,benchmark_return,3,3)",
        mul(std("benchmark_return"), std("benchmark_return")),
    ),
    cov(x, y) / (sd(y) ** 2),
    "deepseek-harness-quant",
    "corr_stats",
    "策略收益对ETF基准收益的线性敏感度",
    "总体Cov(strategy,benchmark)/总体Var(benchmark)",
    path="etf/etf_map.py",
    note="修正来源cov(ddof=1)/var(ddof=0)尺度不一致，分子分母都用ddof=0；基准零方差返回缺失而非0。",
    refs=["beta", "etf-exposure"],
)
define(
    "etf-macro",
    "term-spread",
    "债券期限利差",
    sub("bond_yield_long", "bond_yield_short"),
    v("bond_yield_long") - v("bond_yield_short"),
    "deepseek-harness-quant",
    "load_panel",
    "长期债券收益率减短期债券收益率",
    "0.039-0.026=0.013，单位为比例百分点对应的ratio",
    path="factors/policy/policy_score.py",
    window=1,
    note="保留连续利差，不继承来源的政策评分和仓位档位。",
    refs=["units", "benchmark"],
)
define(
    "etf-macro",
    "social-finance-trend",
    "社融短长窗口强度",
    ratio(mean("social_finance", 3), mean("social_finance", 6)),
    avg(arr("social_finance", 3)) / avg(arr("social_finance", 6)),
    "deepseek-harness-quant",
    "macro_score",
    "近期社融增量均值相对较长窗口均值",
    "mean([160,130,180])/mean([90,140,110,160,130,180])",
    path="factors/policy/timing_system.py",
    window=6,
    note="提取原连续比值，不复制±6分阈值、regime回退或默认中性；按真实发布时点选择宏观版本，观察间隔须一致。",
    refs=["revisions", "publication-lag"],
)
assert len(FORMULAS) == 80, (len(FORMULAS), Counter(f["family"] for f in FORMULAS))
assert len({f["formula"] for f in FORMULAS}) == 80
# Additional real source definitions are reference-only, never counted as executable.
for source, syms in {
    "QUANTAXIS": [
        "QA_indicator_MA",
        "QA_indicator_MA_VOL",
        "QA_indicator_EMA",
        "QA_indicator_SMA",
        "QA_indicator_MACD",
        "QA_indicator_DMI",
        "QA_indicator_PBX",
        "QA_indicator_DMA",
        "QA_indicator_MTM",
        "QA_indicator_EXPMA",
        "QA_indicator_CHO",
        "QA_indicator_KDJ",
        "QA_indicator_BIAS",
        "QA_indicator_ROC",
        "QA_indicator_CCI",
        "QA_indicator_WR",
        "QA_indicator_OSC",
        "QA_indicator_RSI",
        "QA_indicator_ADTM",
        "QA_indicator_VR",
        "QA_indicator_VRSI",
        "QA_indicator_CR",
        "QA_indicator_ARBR",
        "QA_indicator_VSTD",
        "QA_indicator_ASI",
        "QA_indicator_PVT",
        "QA_indicator_OBV",
        "QA_indicator_VPT",
        "QA_indicator_BOLL",
        "QA_indicator_MIKE",
        "QA_indicator_BBI",
        "QA_indicator_MFI",
        "QA_indicator_SKDJ",
        "QA_indicator_DDI",
    ],
    "deepseek-harness-quant": ["ema", "macd_hist", "macd_golden_fresh", "kdj_j", "ma200_trend"],
    "pyanomaly": [
        "c_netis_at",
        "c_acc",
        "c_pctacc",
        "c_taccruals_at",
        "c_taccruals_ni",
        "c_noa_at",
        "c_cfp",
        "c_eqnpo_me",
        "c_enterprise_multiple",
        "c_ocf_at",
        "c_ocf_at_chg1",
        "c_ppeinv_gr1a",
        "c_lnoa_gr1a",
        "c_age",
        "c_dsale_dinv",
        "c_dsale_drec",
        "c_dgp_dsale",
        "c_dsale_dsga",
        "c_inv_gr1",
        "c_be_gr1a",
        "c_op_at",
        "c_pi_nix",
        "c_op_atl1",
        "c_ope_be",
        "c_operprof",
        "c_ope_bel1",
        "c_gp_atl1",
        "c_cop_atl1",
        "c_at_be",
        "c_aliq_at",
        "c_aliq_mat",
        "c_tangibility",
        "c_eq_dur",
        "c_f_score",
        "c_ps",
        "c_o_score",
        "c_z_score",
        "c_kz_index",
        "c_intrinsic_value",
        "c_sale_emp_gr1",
        "c_emp_gr1",
        "c_earnings_variability",
    ],
}.items():
    for symbol in syms:
        card(source, symbol)
FAMILY_MAP = {
    "trend": "trend_momentum",
    "volatility": "volatility_tail",
    "liquidity": "liquidity_turnover",
    "quality": "quality_profitability_accrual",
    "growth": "growth_investment",
    "industry": "industry_style",
    "event": "event_institutional",
    "etf-macro": "etf_macro",
}
for f in FORMULAS:
    f["family"] = FAMILY_MAP.get(f["family"], f["family"])
COUNTEREXAMPLES = {
    "trend-roc": "拆股日原始收盘从20降到10会给出-50%，但不代表企业价值腰斩；需单独识别公司行动。",
    "trend-ma-spread": "持续上涨结束后的横盘仍可使短均线高于长均线，正值不等于下一期继续上涨。",
    "trend-slope": "价格[10,100,10]的OLS斜率为0，不能据此声称路径平稳或低风险。",
    "trend-body": "开盘缺口下跌后盘中反弹形成阳线，实体收益为正但相对昨收仍可能亏损。",
    "trend-upper-shadow": "涨停制度限制最高价时上影线可能为0，不能据此认定抛压消失。",
    "trend-lower-shadow": "流动性极薄的一笔低价交易会制造长下影，不能视作真实承接资金。",
    "trend-close-location": "收盘恰在高低区间中点时值为0，即使当天振幅非常大，也不是无风险。",
    "trend-aroon-spread": "多个并列高点采用最早索引；最后一天再次触及高点不会被解释成新的突破日。",
    "reversal-rsi": "单边上涨导致下跌均幅为0时本实现返回缺失，不以100伪造可用反转信号。",
    "reversal-boll-lower": "均值较小而波动很大时下轨可为负，正负偏离不再有通常的价格支撑解释。",
    "reversal-williams": "跌停后的持续下跌可长期接近-100，超卖位置本身不能证明将反弹。",
    "reversal-cci": "本来源采用两层滚动偏离；若直接拿经典窗口MAD版CCI的阈值套用，信号不具有同一尺度。",
    "volatility-return-std": "三期收益均为-10%时波动率为0，但累计亏损很大；离散度不是损失上界。",
    "volatility-maximum-return": "单日并购跳涨把最大收益拉高，不代表一般交易日收益可复制。",
    "volatility-intraday-range": "高低价相同而开盘较昨收跳空20%时日内振幅为0，仍存在隔夜风险。",
    "volatility-atr": "股价拆分导致昨收与当前高低价跨口径，会将机械差额计作真实波幅。",
    "volatility-price-dispersion": "恒定比例增长使价格水平标准差随价格尺度变化，不能与收益波动直接等同。",
    "volatility-volume-weighted-move": "拆股后成交股数翻倍、价格变化尺度改变时，未统一股数口径会改变该加权指标。",
    "liquidity-amihud": "成交额极小的一笔交易会放大绝对收益/金额比值；这不是可直接外推到大订单的冲击曲线。",
    "liquidity-turnover": "自由流通股本改变而成交股数不变也会改变换手率；不能将变化全部解释为资金热度。",
    "liquidity-turnover-volatility": "每天都以很高换手交易会得到低换手波动，低值不代表交易成本低。",
    "liquidity-dollar-volume": "相同金额的ETF与小盘股可能拥有完全不同买卖价差；金额均值不是可成交深度。",
    "liquidity-price-volume-correlation": "价格和成交量受共同趋势驱动可高度相关，相关性不能识别买入或卖出因果。",
    "liquidity-change-volume-correlation": "量比含基期，前一期极低成交量会制造异常对数量比并主导短窗口相关。",
    "value-book-market": "账面权益为负时负账面市值比不能被当作极低估值。",
    "value-assets-market": "大量无收益或待减值资产可抬高资产市值比，并不代表可变现价值。",
    "value-earnings-yield": "一次性出售资产可抬高净利润收益率，但不能代表持续盈利。",
    "value-sales-yield": "亏损企业可能收入很高，销售市值比不包含利润率和现金回收。",
    "value-cashflow-yield": "临时延付供应商账款可提升经营现金流，但不一定改善长期盈利质量。",
    "value-rd-yield": "研发费用高可能源于失败项目；研发投入金额不是成功专利或未来现金流。",
    "value-debt-market": "市值骤跌会抬高债务市值比，不能把高值解释为低估而忽略偿债风险。",
    "value-free-cashflow-yield": "停止必要资本开支可以短期抬高自由现金流，却损害未来产能。",
    "value-enterprise-book-market": "净现金超过市值和债务时企业价值为负，传统估值倍数解释不适用。",
    "value-payout-yield": "借款融资回购会抬高股东支付收益率，同时增加杠杆。",
    "value-enterprise-ebitda-yield": "高资本消耗行业的EBITDA忽略维持性资本支出，不能当作可分配现金。",
    "value-netdebt-market": "大量受限现金不可偿债，直接扣全部现金会低估实际融资压力。",
    "quality-operating-margin": "不同收入确认准则可能让同类公司利润率不可比；高值不一定来自效率。",
    "quality-gross-profit-assets": "轻资产与重资产行业资产基础不同，不做行业比较会混淆经营模式和盈利能力。",
    "quality-roe": "回购或累计亏损压低权益后ROE可异常高，不能忽略负债和权益接近零。",
    "quality-return-enterprise": "净经营资产为负的预收款模式不适合套用常规正分母回报解释。",
    "quality-accrual-assets": "快速成长企业应收账款增加可形成高应计，不能仅凭此认定盈余操纵。",
    "quality-operating-leverage": "费用资产比高可能只是轻资产经营；该比率不是利润对销量的弹性估计。",
    "quality-enterprise-turnover": "薄利分销商周转很快但盈利可能很低，周转不等于资本回报。",
    "quality-rd-sales": "早期企业收入极少会导致研发费用率极高，不能与成熟企业直接排序。",
    "quality-cash-assets": "受限存款或用途受约束现金会抬高现金资产比，未必改善可用流动性。",
    "quality-current-ratio": "过期存货和难收回账款会抬高流动比率，账面覆盖不等于现金覆盖。",
    "quality-receivables-turnover": "年底集中保理可降低应收余额并抬高期末周转率，但未改变全年收款速度。",
    "quality-depreciation-ppe": "老旧资产净值接近零时折旧/净固定资产比率很高，不一定表示更新投资充分。",
    "quality-cash-profit-assets": "研发加回口径与会计资本化政策不同会造成跨公司不可比，不能直接视作真实现金流。",
    "quality-accrual-profit": "净利润接近零时应计利润比极端，即使应计绝对金额很小。",
    "quality-asset-turnover": "一次重大并购令期初期末平均资产失去期间代表性，周转变化可能是合并范围变化。",
    "quality-quick-ratio": "应收账款虽非存货但可能无法及时回收，速动比率不保证短债可偿付。",
    "growth-asset-growth": "并购并表可造成资产跳升，不能与内生投资扩张等同。",
    "growth-sales-growth": "更改收入总额法/净额法会产生销售增长突变，不能视为真实需求增长。",
    "growth-capex-growth": "资本支出基期接近零会得到极高增长率，绝对投资规模可能仍很小。",
    "growth-inventory-investment": "滞销导致存货堆积也会表现为正投资变化，并不自动支持增长论点。",
    "growth-long-investment": "金融资产公允价值上涨会抬高长期投资余额，不能全部解释为新增现金投资。",
    "growth-short-investment": "现金转入短期理财可抬高短投余额，未必改变总金融资产风险。",
    "growth-current-operating-assets": "应收恶化导致资产增加可与经营扩张同向，需区别回款和真实销售。",
    "growth-current-operating-liabilities": "延付供应商款会增加经营负债，这可能是融资压力而非议价能力。",
    "growth-working-capital": "大量预收款可以让营运资本降低，负变化不必然代表经营收缩。",
    "growth-noncurrent-operating-assets": "资产减值会导致该项下降，不能把负值一律解读成高效轻资产转型。",
    "growth-noncurrent-operating-liabilities": "长期应付义务改列流动项会改变指标，但总义务可能没有减少。",
    "growth-net-noncurrent-operating": "资产与负债等额同时增长可使净变化为0，仍可能扩大经营风险规模。",
    "growth-financial-liabilities": "债务换为优先股可能使合计不变，但现金流义务和清偿顺位不同。",
    "growth-net-financial-assets": "金融投资与金融负债等额增加会净额抵消，不能据此认定总杠杆未变。",
    "growth-rd-capital": "研发项目即时失败时固定线性折耗仍保留存量，本指标不是审计确认的无形资产价值。",
    "growth-abnormal-capex": "周期性大型项目会使本期资本支出强度远高于历史均值，不等于非理性投资。",
    "industry-book-market": "行业内仅有两家且其中一家负权益时行业均值偏离，结果不能替代可比公司筛选。",
    "industry-cashflow-market": "行业同样受季节性营运资本释放影响时相对中性，并不代表现金质量都正常。",
    "industry-employment-growth": "外包替代员工可使雇员增长低于同业，业务产能却可能上升。",
    "industry-size": "行业分类过宽时一家巨头会主导均值，负相对市值不等于具备小盘溢价。",
    "event-equity-finance": "高净股权融资可能为优质扩张融资，也可能为偿债救助，不能预设正负收益方向。",
    "event-debt-finance": "借新还旧可能净融资接近零，但短期再融资依赖仍然很高。",
    "event-share-issuance": "股份拆分若未对齐股数口径会被误认作净发行；增长本身不能区分员工激励和现金增发。",
    "event-composite-issuance": "若用未含现金分红的价格收益冒充total_return，会将派现与融资混淆；总收益与市值还须使用一致的拆股基准。",
    "etf-macro-correlation": "两个平稳收益序列在短窗口内也可能偶然高相关，相关不代表长期可替代持仓。",
    "etf-macro-beta": "基准收益方差接近零会使beta非常不稳定；比值有限也不代表估计可靠。",
    "etf-macro-term-spread": "长短期限债券信用风险不同会使利差混入信用成分，不能直接当同一国债期限结构。",
    "etf-macro-social-finance-trend": "春节月份的季节性融资峰值可能抬高短长均值比，不能把未季调比值直接当政策转向。",
}

assert len(COUNTEREXAMPLES) == 80
for f in FORMULAS:
    f["observation_frequency"] = "DAILY_DECISION_GRID"
    f["window_semantics"] = (
        "窗口单位为已知决策观察点，非自然日或会计年度；时序值只在同一资产内滚动。"
    )
    f["financial_period_policy"] = (
        "用户必须将利润/收入/现金流/分红/融资流量对齐到同一可比财务期间；资产负债表为该期时点余额。PIT面板状态前向延续不会创造新报告期，逐观察点差分仅表示已知状态更新，不宣称年度增长。"
    )
    f["counterexample"] = (
        COUNTEREXAMPLES[f["factor_id"].removeprefix("lib-")] + " " + f["counterexample"]
    )
# Reference-only names whose actual executable body delegates to unreviewed helpers remain unresolved.
for c in CARDS.values():
    if c["adaptation_status"] == "REFERENCE_ONLY":
        c["formula_status"] = "UNRESOLVED" if len(c["original_formula"]) < 55 else "EXPLICIT"
        c["title"] = "来源定义：" + c["symbol"]
        c["explanation"] = "保留原项目中的公式线索及代码位置，尚未完成Finathink适配与独立数值验证。"
    else:
        f = next(f for f in FORMULAS if f["factor_id"] == c["factor_id"])
        c["title"] = f["title"]
        c["explanation"] = f["explanation"]
        c["adaptation_notes"] = f["adaptation_notes"]
        c["original_formula"] = c["original_formula"][:20000]
for c in CARDS.values():
    source = next(k for k in SOURCES if k.lower() == c["source_id"])
    _, (_, license_sha) = file(source, c["license_path"])
    c["license_sha256"] = license_sha
    c["attribution"] = (
        "Normalized attributed source excerpt; copyright and MIT notice are retained in bundled third-party notices."
    )
    if source == "deepseek-harness-quant":
        c["license_id"] = "MIT_WITH_RESEARCH_NOTE"
# Resolve helper-based definitions with their actual arithmetic, not just wrapper names.
helper = card("pyanomaly", "crspd_roll_126d")
CARDS[helper].update(
    license_sha256=file("pyanomaly", "LICENSE.txt")[1][1],
    attribution="Normalized attributed source excerpt; MIT copyright notice retained.",
    explanation="Amihud、金额成交量与换手的实际滚动计算辅助函数；仅取数学定义，不执行上游模块。",
)
for fid in ["lib-liquidity-amihud", "lib-liquidity-turnover", "lib-liquidity-dollar-volume"]:
    f = next(f for f in FORMULAS if f["factor_id"] == fid)
    f["source_card_ids"].append(helper)
    next(c for c in CASES if c["factor_id"] == fid)["source_card_ids"].append(helper)
    if fid == "lib-liquidity-amihud":
        f["adaptation_notes"] += "上游乘1e6按百万金额缩放；本实现取消1e6，单位为每1CNY。"
# More than minimum cards because retaining all actual independent source definitions is honest.
assert len(CARDS) >= 120, len(CARDS)
out = ROOT / "src/finahinking/_package_data/research_assets/factors"
out.mkdir(exist_ok=True)
for path, payload in [
    (
        out / "library.json",
        {
            "schema_version": "factor-library-sources.v1",
            "version": "1.0.0",
            "cards": list(CARDS.values()),
        },
    ),
    (
        out / "formulas.json",
        {"schema_version": "factor-library-formulas.v1", "version": "1.0.0", "formulas": FORMULAS},
    ),
    (
        ROOT / "tests/fixtures/quant_factors/numerical-goldens.json",
        {
            "schema_version": "factor-library-numerics.v1",
            "reference_method": "Frozen scalar arithmetic authored independently of production DSL. Inputs are synthetic; values do not establish market efficacy.",
            "times": [f"2026-01-0{d}T16:00:00Z" for d in range(2, 10)],
            "units": UNITS,
            "input": DATA,
            "cases": CASES,
        },
    ),
]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
report = [
    "# Phase24 固定来源与独立数值记录",
    "",
    "实际读取：2026-11-01。只解析固定Git对象，不导入或执行上游模块。",
    "",
    f"本批次 {len(CARDS)} 张来源卡，80 个独立实现，十家族分布：`{dict(Counter(f['family'] for f in FORMULAS))}`。",
    "",
    "## 计数与适配边界",
    "",
    "每个窗口只登记一项；RSI的上涨/下跌互补项、Alpha158重复窗口、符号翻转不重复计数。上影、下影、实体和收盘位置是来源明确列出的不同OHLC几何量；经营资产、经营负债与净投入是文献明确区分的经济定义，不把单字段机械套算子作为新增定义。行业相对值改变研究比较对象，保留相应独立文献来源。",
    "",
    "所有财务币种为声明的同一CNY；这里的固定币种是明确参数合同，不能直接混用美元。经营现金流/净利润等由用户按相同财务期间提供。增长、发行和研发折耗沿观察序列计算；应使用可比财务期观测，日频前向延续值不构成年度增长证据。",
    "",
    "上游fillna(0)、伪造高低价、未来全样本标准化和未审查评分均未带入。标准差统一ddof=0。ETF beta修正原始cov/var的自由度不一致。来源样本胜率、来源论文结论与来源方向仅保留为来源主张，不属于本地验证。",
    "",
    "独立数值预期在调用生产模块前冻结：八观察点、三资产，按Python标准库标量算术、总体矩与明确行业组计算；无pandas/numpy/生产DSL/上游执行。生成脚本与冻结JSON一并保留以便复核，运行时不生成预期。",
    "",
    "## 文件级来源锁",
    "",
    "| 项目 | 固定提交 | 路径 | SHA-256 |",
    "|---|---|---|---|",
]
for (source, path), (text, sha) in FILES.items():
    report.append(f"| {source} | {SOURCES[source][1]} | `{path}` | `{sha}` |")
report += [
    "",
    "来源卡含经AST规范化的原始源码摘录，属于ATTRIBUTED_SOURCE_EXCERPTS而非纯原创叙述；四个MIT原文及版权声明随第三方许可目录保留，DeepSeek附加研究教育说明同时保留。Finathink DSL、中文解释与独立数值样例是本项目整理实现。",
    "",
    "所有文件的逐项起止行、原始公式片段、固定提交链接、许可证路径及读取日期保存在 `factors/library.json`。参考项没有 `factor_id`，只有已独立实现的项才绑定公式。",
    "",
    "## 80 项经济定义与差异",
    "",
    "| ID | 经济定义 | 来源卡 |",
    "|---|---|---|",
]
for f in FORMULAS:
    report.append(
        f"| `{f['factor_id']}` | {f['economic_definition']} | `{f['source_card_ids'][0]}` |"
    )
(ROOT / "docs/research/phase24-source-and-numerical-study.md").write_text("\n".join(report) + "\n")
print(len(CARDS), len(FORMULAS), dict(Counter(f["family"] for f in FORMULAS)))

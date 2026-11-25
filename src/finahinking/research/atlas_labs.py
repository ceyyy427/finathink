"""Read-only graph and formula projections over frozen inputs, with explicit study submission."""
from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from html import escape
from urllib.parse import urlencode

import pandas as pd

from finahinking.factors.dsl import evaluate_expression
from finahinking.p8_2.data_sources import FixtureMarketDataSource

from .contracts import stable_digest
from .factor_catalog_loader import get_research_recipe
from .factor_validation_sample import ValidationSample, discovery_sample_spec
from .factor_workbench import study_frame
from .temporal_validation import purged_walk_forward

EVIDENCE = "/research/atlas/evidence"
FACTORS = "/research/atlas/factors"
SOURCE = "/api/research/atlas/source"
SUBMIT = "/api/research/atlas/momentum-study"
RECEIPT = SUBMIT + "/receipt"
NODES = {"snapshot": "OHLCV 快照", "close": "收盘价", "lag": "滞后收盘价", "factor": "动量因子", "hypothesis": "研究假设"}
SAMPLES = {"SAMPLE", "SYNTHETIC"}


class LabInputError(ValueError):
    """A repair message that can be displayed in this local workspace."""


def _h(value):
    return escape(str(value), quote=True)


def _url(path, **values):
    return path + "?" + urlencode(values)


def _one(query, key, default=""):
    field = (query or {}).get(key)
    if field is None:
        return default
    if not isinstance(field, (list, tuple)) or len(field) != 1 or not isinstance(field[0], str) or len(field[0]) > 240:
        raise LabInputError("每个参数只能选取一个有效值。")
    return field[0]


@lru_cache(maxsize=1)
def _examples():
    trend = ValidationSample(discovery_sample_spec("trend_momentum")).anchor()
    long_series = FixtureMarketDataSource(points=240).snapshot("AAA")
    return {trend.fingerprint: (trend, "趋势样例"), long_series.fingerprint: (long_series, "日线样例")}


def _default_dataset():
    ref, (snapshot, _) = next(iter(_examples().items()))
    return ref + "~" + snapshot.instruments[0]


def _choices(service, selected):
    examples = _examples()
    result = [(ref + "~" + snapshot.instruments[0], f"静态示例 · {name}")
              for ref, (snapshot, name) in examples.items()]
    for snapshot in service.snapshots.list_snapshots():
        ref = snapshot.fingerprint
        if ref in examples:
            continue
        kind = "已存示例" if snapshot.mode in SAMPLES else "已存纸面" if snapshot.mode == "PAPER" else "已存快照"
        result.extend((ref + "~" + asset, f"{kind} · {asset} · {ref[:8]}") for asset in snapshot.instruments)
    if selected and all(value != selected for value, _ in result):
        result.append((selected, "所选快照不可用"))
    return "".join(f'<option value="{_h(value)}"{" selected" if value == selected else ""}>{_h(label)}</option>'
                   for value, label in result)


def _window(value):
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]{1,2}", str(value)) or not 2 <= int(value) <= 60:
        raise LabInputError("回看窗口须为 2 至 60 个观测期的整数。")
    return int(value)


@dataclass(frozen=True)
class Parameters:
    dataset: str
    short: int
    long: int
    fee: float

    @classmethod
    def parse(cls, values):
        short, long = _window(values["short"]), _window(values["long"])
        if short >= long:
            raise LabInputError("短窗口须小于长窗口。")
        try:
            fee = float(values["fee"])
        except (ValueError, TypeError, OverflowError):
            raise LabInputError("换手费用须为 0 至 100 个基点。") from None
        if isinstance(values["fee"], bool) or not math.isfinite(fee) or not 0 <= fee <= 100:
            raise LabInputError("换手费用须为 0 至 100 个基点。")
        return cls(values["dataset"], short, long, fee)

    def query(self):
        fee = str(int(self.fee)) if self.fee.is_integer() else repr(self.fee)
        return {"dataset": self.dataset, "short": self.short, "long": self.long, "fee": fee}


def _query_values(query):
    defaults = {"dataset": _default_dataset(), "short": "5", "long": "20", "fee": "5"}
    return {key: _one(query, key, value) for key, value in defaults.items()}


def _resolve(service, dataset):
    if not isinstance(dataset, str) or not re.fullmatch(r"[0-9a-f]{64}~[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", dataset):
        raise LabInputError("请选择列表中有完整指纹的快照和标的。")
    ref, instrument = dataset.split("~")
    sample = _examples().get(ref)
    snapshot = sample[0] if sample else service.snapshots.load(ref)
    if instrument not in snapshot.instruments:
        raise LabInputError("所选快照中没有这个标的。")
    return snapshot, ref, instrument


class Exploration:
    """One instrument and the same development interval used by the study engine."""

    def __init__(self, service, params):
        self.params = params
        self.snapshot, self.ref, self.instrument = _resolve(service, params.dataset)
        self.as_of = self.snapshot.as_of.tz_convert("UTC").date().isoformat()
        if not self.snapshot.pit_available() or self.snapshot.as_of > pd.Timestamp.now(tz="UTC"):
            raise LabInputError("快照的观测或可用时间尚未确定。请核对日期并重新保存。")
        rows = [record for record in self.snapshot.observations if record.instrument == self.instrument]
        if any(record.available_at < record.timestamp for record in rows):
            raise LabInputError("快照的可用时间早于观测时间。请核对原始记录。")
        try:
            full_frame = study_frame(self.snapshot, self.instrument, self.as_of)
            split = purged_walk_forward(full_frame.index, horizon=1, embargo=1, n_splits=1)
        except (TypeError, ValueError):
            raise LabInputError("请选择按时间排序、每天至多一条、可用时间完整的快照，最多支持 5000 条观测。") from None
        self.rows = rows[:split["validation_end"]]
        self.holdout_count = len(rows) - len(self.rows)
        if len(self.rows) <= params.long:
            raise LabInputError("研究区间短于当前回看窗口。请调小窗口或选择较长的快照。")
        self.frame = full_frame.iloc[:len(self.rows)]
        self.recipe = get_research_recipe("close_momentum")
        self.series = [evaluate_expression(self.recipe.expression(window), self.frame)
                       for window in (params.short, params.long)]
        self.mode_label = "示例数据" if self.snapshot.mode in SAMPLES else "纸面快照" if self.snapshot.mode == "PAPER" else "已保存数据"
        self.question = f"{self.instrument} 的 {params.short} 期与 {params.long} 期价格动量，与下一期收益有何关系？"

    def index(self, time=""):
        if time:
            for i, row in enumerate(self.rows):
                if row.timestamp.isoformat() == time:
                    return i
            raise LabInputError("该时间点不在所选快照的研究区间。")
        return max(self.params.long, round((len(self.rows) - 1) * .66))

    def link(self, path, **values):
        return _url(path, **self.params.query(), **values)

    def record(self, node, i):
        if node not in NODES:
            raise LabInputError("请选择图中的一个证据节点。")
        current = self.rows[i]
        previous = self.rows[i - self.params.short] if i >= self.params.short else None
        inputs = {"close": [current], "lag": [previous], "factor": [current, previous],
                  "snapshot": [current], "hypothesis": []}[node]
        result = {"kind": node, "snapshot_ref": self.ref, "dataset_id": self.snapshot.dataset_id,
                  "instrument": self.instrument, "data_mode": self.snapshot.mode, "as_of": self.snapshot.as_of.isoformat(),
                  "selected_at": current.timestamp.isoformat(), "records": [
                      {"observation_ref": row.fingerprint, "observation": row.to_dict()} for row in inputs if row is not None]}
        if node == "snapshot":
            result.update(provenance=dict(self.snapshot.provenance), limitations=list(self.snapshot.limitations))
        if node == "factor":
            value = self.series[0].iloc[i]
            result.update(recipe_id=self.recipe.recipe_id, recipe_version=self.recipe.version,
                          formula=self.recipe.expression(self.params.short), implementation_ref=self.recipe.implementation_fingerprint,
                          value=float(value) if pd.notna(value) else None)
        if node == "hypothesis":
            result.update(kind="research_question_draft", question=self.question, recipe_id=self.recipe.recipe_id,
                          windows=[self.params.short, self.params.long], fee_bps=self.params.fee, evaluation_status="NOT_RUN")
        return result


def atlas_labs_api(app, method, clean, query, body):
    if clean == RECEIPT:
        status, _, result = atlas_labs_api(app, method, SUBMIT, query, body)
        if status == 202:
            study_id = _h(result["study_id"])
            content = (f'<p>章程已保存在本地工作区。</p><code>{study_id}</code>'
                       f'<p><a href="{_h(result["study_url"])}">打开验证记录</a> · '
                       f'<a href="/api/research/experiments/{study_id}">读取原始进度</a></p>')
        else:
            message = "页面授权已失效。请返回因子实验后重新提交。" if status == 403 else result["error"]
            content = f'<p class="al-error" role="alert">{_h(message)}</p>'
        page = (f'<article class="al-page">{_title("因子实验", "提交结果")}{content}'
                f'<p><a href="{FACTORS}">返回因子实验</a></p></article>')
        return status, "text/html; charset=utf-8", app.render_shell(FACTORS, "因子实验", page, styles=("/assets/atlas-labs.css",))
    if clean not in {SOURCE, SUBMIT}:
        return None
    write = clean == SUBMIT
    if method != ("POST" if write else "GET"):
        return 405, "application/json", {"error": "请选择此操作对应的读取或提交方式。"}
    if write:
        denied = app._data_write_guard(body)
        if denied is not None:
            return denied
    try:
        service = app.research_service
        if not write:
            if set(query) - {"dataset", "short", "long", "fee", "node", "at"}:
                raise LabInputError("原始记录链接含未识别的参数。")
            lab = Exploration(service, Parameters.parse(_query_values(query)))
            return 200, "application/json", lab.record(_one(query, "node", "close"), lab.index(_one(query, "at")))
        if set(body) != {"_csrf", "dataset", "short", "long", "fee"}:
            raise LabInputError("请选择快照、两个回看窗口及费用后提交。")
        params = Parameters.parse(body)
        lab = Exploration(service, params)
        study_input = {"snapshot_ref": lab.ref, "instrument": lab.instrument, "as_of": lab.as_of,
                       "recipe_id": lab.recipe.recipe_id, "question": lab.question,
                       "windows": [params.short, params.long], "fee_bps": params.fee, "slippage_bps": 0,
                       "label_horizon": 1, "embargo": 1, "n_splits": 1}
        if lab.ref in _examples():
            service.snapshots.save(lab.snapshot)
        # The same immutable charter can be recovered after a network retry or duplicate click.
        study = service.create_factor_study({**study_input, "idempotency_key": "atlas-momentum-" + stable_digest(study_input)[:32]})
        return 202, "application/json", {"study_id": study["study_id"], "state": study["state"],
                                        "snapshot_ref": lab.ref, "study_url": _url("/research/experiments", study_id=study["study_id"])}
    except LabInputError as exc:
        return 422, "application/json", {"error": str(exc)}
    except (KeyError, FileNotFoundError):
        return 404, "application/json", {"error": "该快照或研究已不在当前工作区。"}
    except (ValueError, TypeError):
        return 422, "application/json", {"error": "快照完整性、样本长度或研究条件未通过校验。请核对所选快照及窗口。"}
    except (OSError, sqlite3.Error):
        return 503, "application/json", {"error": "本地研究存储暂时不可用。请检查磁盘后重试。"}


def _date(value):
    if value is None:
        return "尚未形成"
    stamp = value.isoformat()
    tail = "" if value == value.normalize() and value.utcoffset().total_seconds() == 0 else f'<small>{_h(stamp.partition("T")[2])}</small>'
    return f'<time datetime="{_h(stamp)}" title="{_h(stamp)}">{_h(value.date().isoformat())}{tail}</time>'


def _rows(pairs):
    return "".join(f'<div><dt>{_h(label)}</dt><dd>{value}</dd></div>' for label, value in pairs)


def _external_icon():
    return ('<svg viewBox="0 0 19 19" fill="none" width="17" height="17" aria-hidden="true">'
            '<path d="M10.5 3H16v5.5M15.7 3.4l-8 8M8 4H4a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1v-4" '
            'stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>')


def _inspector(lab, node, i):
    current = lab.rows[i]
    previous = lab.rows[i - lab.params.short] if i >= lab.params.short else None
    row = previous if node == "lag" else current
    units = lab.snapshot.provenance.get("units") or {}
    currency = units.get("price_currency", units.get("price", "未声明")) if isinstance(units, dict) else "未声明"
    times = []
    note = ""
    if node in {"close", "lag"}:
        tag = "数据来源 · OHLCV 快照"
        fields = [("字段", "<code>close</code>"), ("标的", _h(lab.instrument)),
                  ("观测值", f"{row.close:,.4f}" if row else "窗口尚未形成"), ("价格单位", _h(currency))]
        times = [("观测时间", _date(row.timestamp if row else None)), ("可用时间", _date(row.available_at if row else None))]
        if node == "lag":
            note = f"取所选日期往前 {lab.params.short} 个观测期的收盘价。"
    elif node == "snapshot":
        tag = "数据来源 · 冻结快照"
        fields = [("快照模式", _h(lab.snapshot.mode)), ("标的", _h(lab.instrument)),
                  ("观测数", str(len(lab.rows) + lab.holdout_count)), ("研究区间", f"{len(lab.rows)} 期")]
        times = [("起始观测", _date(lab.rows[0].timestamp)), ("快照截至", _date(lab.snapshot.as_of))]
    elif node == "factor":
        tag = "计算来源 · 价格动量"
        value = lab.series[0].iloc[i]
        fields = [("公式版本", _h(lab.recipe.version)), ("回看窗口", f"{lab.params.short} 期"),
                  ("观测值", f"{value:+.5f}" if pd.notna(value) else "窗口尚未形成"), ("单位", "比率")]
        known = max(current.available_at, previous.available_at) if previous else None
        times = [("观测时间", _date(current.timestamp)), ("可用时间", _date(known))]
    else:
        tag = "研究问题 · 待检验"
        fields = [("比较窗口", f"{lab.params.short} / {lab.params.long} 期"), ("数据范围", _h(lab.mode_label)),
                  ("换手费用", f"{lab.params.fee:g} bps"), ("问题状态", "草案")]
        note = lab.question
    record_url = lab.link(SOURCE, node=node, at=current.timestamp.isoformat())
    raw = lab.record(node, i)
    bindings = [("数据集", _h(lab.snapshot.dataset_id)), ("快照指纹", f'<code>{_h(lab.ref)}</code>')]
    if row and row.source != "unknown":
        bindings.append(("记录来源", _h(row.source)))
    if row and row.provider != "unknown":
        bindings.append(("提供方", _h(row.provider)))
    bindings.extend(("观测指纹", f'<code>{_h(record["observation_ref"])}</code>') for record in raw["records"])
    if raw.get("implementation_ref"):
        bindings.append(("公式指纹", f'<code>{_h(raw["implementation_ref"])}</code>'))
    source_label = "查看问题草案" if node == "hypothesis" else "查看原始记录"
    return (f'<h2>{NODES[node]}</h2><p class="al-source-tag">{tag}</p>'
            f'<dl class="al-fields">{_rows(fields)}</dl>'
            + (f'<dl class="al-fields al-times">{_rows(times)}</dl>' if times else "")
            + (f'<p class="al-inspector-note">{_h(note)}</p>' if note else "")
            + f'<a class="al-source-link" href="{_h(record_url)}" target="_blank" rel="noopener">{_external_icon()}{source_label}</a>'
              f'<details class="al-binding"><summary>来源指纹</summary><dl>{_rows(bindings)}</dl></details>')


# Coordinates are in the diagram viewBox. Ports sit exactly on node boundaries.
_WIDE_NODES = {"snapshot": (15, 157, 168, 72), "close": (258, 68, 176, 72), "lag": (258, 246, 176, 72),
               "factor": (510, 157, 157, 72), "hypothesis": (729, 157, 159, 72)}
_WIDE_EDGES = ("M183 193H219V104H258", "M219 193V282H258", "M434 104H470V193H510",
               "M434 282H470V193", "M667 193H729")
_NARROW_NODES = {"snapshot": (94, 12, 142, 49), "close": (11, 99, 143, 49), "lag": (176, 99, 143, 49),
                 "factor": (94, 187, 142, 49), "hypothesis": (94, 270, 142, 49)}
_NARROW_EDGES = ("M165 61V79H82.5V99", "M165 79H247.5V99", "M82.5 148V165H165V187",
                 "M247.5 148V165H165", "M165 236V270")


def _graph(lab, selected, i, *, narrow):
    positions, edges = (_NARROW_NODES, _NARROW_EDGES) if narrow else (_WIDE_NODES, _WIDE_EDGES)
    box = "0 0 330 332" if narrow else "0 0 905 386"
    nodes = []
    ports = set()
    for key, (x, y, w, h) in positions.items():
        label_y, detail_y = (20, 39) if narrow else (29, 56)
        href = lab.link(EVIDENCE, node=key, at=lab.rows[i].timestamp.isoformat())
        secondary = "待检验" if key == "hypothesis" else lab.mode_label
        nodes.append(f'<a class="al-graph-node al-node-{key}" data-atlas-node="{key}" href="{_h(href)}" '
                     f'aria-current="{"true" if selected == key else "false"}" aria-label="{NODES[key]}，{secondary}">'
                     f'<rect class="al-node-box" x="{x}" y="{y}" width="{w}" height="{h}"/>')
        if key == "snapshot":
            nodes.append(f'<rect class="al-node-band" x="{x+1}" y="{y+1}" width="{w-2}" height="{24 if narrow else 32}"/>')
        nodes.append(f'<text class="al-node-title" x="{x+14}" y="{y+label_y}">{NODES[key]}</text>'
                     f'<text class="al-node-detail" x="{x+14}" y="{y+detail_y}">{secondary}</text></a>')
        if key != "snapshot":
            ports.add((x + w / 2, y) if narrow else (x, y + h / 2))
        if key != "hypothesis":
            ports.add((x + w / 2, y + h) if narrow else (x + w, y + h / 2))
    paths = "".join(f'<path d="{edge}"/>' for edge in edges)
    circles = "".join(f'<circle cx="{x:g}" cy="{y:g}" r="{3.1 if narrow else 4.4}"/>' for x, y in sorted(ports))
    return (f'<svg class="al-graph-svg al-{"narrow" if narrow else "wide"}" viewBox="{box}" role="group" '
            f'aria-label="数据快照、原始价格、动量与研究假设的来源关系"><g class="al-edges">{paths}</g>'
            f'{"".join(nodes)}<g class="al-ports" aria-hidden="true">{circles}</g></svg>')


def _title(title, subtitle=""):
    return f'<header class="al-heading"><h1>{title}</h1>' + (f'<span class="al-heading-sub">{subtitle}</span>' if subtitle else "") + '</header>'


def _error_message(exc):
    if isinstance(exc, LabInputError):
        return str(exc)
    if isinstance(exc, FileNotFoundError):
        return "当前工作区中找不到所选快照。请选择其他已保存快照或静态示例。"
    return "所选快照无法通过完整性校验。请重新保存并选择该数据。"


def render_evidence(service, query=None):
    values = _query_values(query)
    options = _choices(service, values["dataset"])
    selector = ('<label>数据快照<select name="dataset">' + options + '</select></label>')
    try:
        lab = Exploration(service, Parameters.parse(values))
        selected = _one(query, "node", "close")
        if selected not in NODES:
            raise LabInputError("所选节点不存在。请选择这条证据链中的节点。")
        i = lab.index(_one(query, "at"))
    except (LabInputError, FileNotFoundError, ValueError) as exc:
        return (f'<article class="al-page" data-atlas-lab="evidence">{_title("证据溯源")}'
                f'<p class="al-error" role="alert">{_h(_error_message(exc))}</p><form class="al-source-form" method="get" '
                f'action="{EVIDENCE}">{selector}<button>读取快照</button></form></article>')
    templates = "".join(f'<template data-atlas-inspector-for="{key}">{_inspector(lab, key, i)}</template>' for key in NODES)
    date_options = "".join(f'<option value="{_h(row.timestamp.isoformat())}"{" selected" if n == i else ""}>'
                           f'{row.timestamp.date().isoformat()}</option>' for n, row in enumerate(lab.rows))
    hidden = "".join(f'<input type="hidden" name="{key}" value="{_h(value)}">' for key, value in
                     {**lab.params.query(), "node": selected}.items() if key != "dataset")
    return (f'<article class="al-page" data-atlas-lab="evidence">{_title("证据溯源")}'
            '<div class="al-evidence-grid"><section class="al-canvas" aria-label="证据关系图">'
            '<div class="al-zoom"><button type="button" data-atlas-zoom="-1" aria-label="缩小证据关系图"><b>−</b> 缩小</button>'
            '<button type="button" data-atlas-zoom="1" aria-label="放大证据关系图"><b>＋</b> 放大</button>'
            '<output data-atlas-zoom-label aria-live="polite"></output></div>'
            '<div class="al-graph-viewport" data-atlas-viewport><div class="al-graph-stage" data-atlas-stage>'
            f'{_graph(lab, selected, i, narrow=False)}{_graph(lab, selected, i, narrow=True)}</div></div>'
            f'<div class="al-canvas-meta"><span>{_h(lab.mode_label)} · {_h(lab.instrument)}</span>'
            f'<span>{lab.rows[i].timestamp.date().isoformat()}</span></div></section>'
            f'<aside class="al-inspector" aria-label="所选证据" data-atlas-inspector aria-live="polite">{_inspector(lab, selected, i)}</aside></div>'
            f'{templates}<div class="al-evidence-foot"><details><summary>切换快照或观测日</summary>'
            f'<form class="al-source-form" method="get" action="{EVIDENCE}">{selector}{hidden}'
            '<button type="submit">切换快照</button></form>'
            f'<form class="al-source-form" method="get" action="{EVIDENCE}">{hidden}'
            f'<input type="hidden" name="dataset" value="{_h(lab.params.dataset)}">'
            f'<label>观测日<select name="at" data-atlas-date>{date_options}</select></label>'
            '<button type="submit">读取观测日</button></form></details>'
            f'<a href="{_h(lab.link(FACTORS))}">在因子实验中比较窗口 <span aria-hidden="true">↗</span></a></div></article>')


def _chart_bounds(points):
    minimum = min(0., min(min(p[1:]) for p in points))
    maximum = max(0., max(max(p[1:]) for p in points))
    if minimum == maximum:
        return -.01, .01, .005
    width = (maximum - minimum) / 6
    base = 10. ** math.floor(math.log10(width))
    step = next(scale for scale in (1, 2, 2.5, 5, 10) if scale * base >= width) * base
    return math.floor(minimum / step) * step, math.ceil(maximum / step) * step, step


def _tick_label(value, step):
    if abs(value) < step * 1e-8:
        value = 0.
    if step < 1e-4 or step > 1e4:
        return f"{value:.2g}"
    decimals = len(f"{step:.8f}".rstrip("0").partition(".")[2])
    return f"{value:.{decimals}f}".replace("-", "−")


def _chart_svg(lab, points, initial, bounds, *, narrow):
    low, high, step = bounds
    width, height, left, top, right, bottom = (455, 310, 53, 18, 444, 267) if narrow else (860, 360, 63, 22, 844, 310)
    spacing = (right - left) / max(1, len(points) - 1)
    def y(value):
        return bottom - (value - low) / (high - low) * (bottom - top)
    grid, labels = [], []
    ticks = round((high - low) / step)
    for i in range(ticks + 1):
        value = low + i * step
        pos = y(value)
        grid.append(f'<path d="M{left} {pos:.2f}H{right}"/>')
        labels.append(f'<text x="{left-11}" y="{pos+4:.2f}" text-anchor="end">{_tick_label(value, step)}</text>')
    count = min(4 if narrow else 7, len(points))
    for i in range(count):
        index = round(i * (len(points) - 1) / max(1, count - 1))
        pos = left + index * spacing
        grid.append(f'<path d="M{pos:.2f} {top}V{bottom}"/>')
        label = points[index][0][5:10] if points[0][0][:4] == points[-1][0][:4] else points[index][0][:7]
        anchor = "start" if i == 0 else "end" if i == count - 1 else "middle"
        labels.append(f'<text class="al-axis-date" x="{pos:.2f}" y="{bottom+27}" text-anchor="{anchor}">{label}</text>')
    paths = []
    for series, name in [(1, "short"), (2, "long")]:
        path = " ".join(f'{"M" if i == 0 else "L"}{left + i * spacing:.2f},{y(point[series]):.2f}' for i, point in enumerate(points))
        paths.append(f'<path class="al-series al-series-{name}" d="{path}"/>')
    stamp, short_val, long_val = points[initial]
    cursor_x = left + initial * spacing
    tw, th = (135, 67) if narrow else (166, 80)
    tx = cursor_x + 12 if cursor_x + tw + 16 < right else cursor_x - tw - 12
    tx = max(left + 4, min(right - tw - 4, tx))
    box_y = top + 3
    date_y, first_y, second_y = (17, 37, 56) if narrow else (21, 45, 66)
    w1, w2 = lab.params.short, lab.params.long
    aria_values = f"{stamp[:10]}，{w1} 期动量 {short_val:.4f}，{w2} 期动量 {long_val:.4f}"
    return (f'<svg class="al-plot al-{"narrow" if narrow else "wide"}" role="slider" tabindex="0" aria-label="按日期查看价格动量" '
            f'aria-valuemin="1" aria-valuemax="{len(points)}" aria-valuenow="{initial+1}" aria-valuetext="{aria_values}" '
            f'viewBox="0 0 {width} {height}" data-atlas-plot data-left="{left}" data-right="{right}" data-top="{top}" '
            f'data-bottom="{bottom}" data-low="{low}" data-high="{high}" data-tooltip-width="{tw}">'
            '<title>两个回看窗口的价格动量</title><desc>横轴为观测日期，纵轴为动量比率。用左右方向键切换日期，可展开下方数值表。</desc>'
            f'<g class="al-grid-lines">{"".join(grid)}</g><g class="al-axis-lines"><path d="M{left} {top}V{bottom}H{right}"/>'
            f'<path class="al-zero" d="M{left} {y(0):.2f}H{right}"/></g><g class="al-ticks">{"".join(labels)}</g>{"".join(paths)}'
            f'<g class="al-cursor" aria-hidden="true"><line data-cursor-line x1="{cursor_x:.2f}" x2="{cursor_x:.2f}" y1="{top}" y2="{bottom}"/>'
            f'<circle class="al-short-marker" data-short-marker cx="{cursor_x:.2f}" cy="{y(short_val):.2f}" r="5"/>'
            f'<circle class="al-long-marker" data-long-marker cx="{cursor_x:.2f}" cy="{y(long_val):.2f}" r="5"/>'
            f'<g class="al-tooltip" data-tooltip transform="translate({tx:.2f},{box_y})"><rect width="{tw}" height="{th}" rx="3"/>'
            f'<text class="al-tooltip-date" data-tooltip-date x="11" y="{date_y}">{stamp[:10]}</text>'
            f'<path class="al-series al-series-short" d="M10 {first_y-4}h17"/><text x="34" y="{first_y}">{w1} 期动量</text>'
            f'<text data-tooltip-short x="{tw-10}" y="{first_y}" text-anchor="end">{short_val:.3f}</text>'
            f'<path class="al-series al-series-long" d="M10 {second_y-4}h17"/><text x="34" y="{second_y}">{w2} 期动量</text>'
            f'<text data-tooltip-long x="{tw-10}" y="{second_y}" text-anchor="end">{long_val:.3f}</text></g></g></svg>')


def _formula():
    return ('<div class="al-equation" role="math" aria-label="价格动量等于本期收盘价除以前推 k 期的收盘价，再减一">'
            '<span aria-hidden="true"><i>M</i><sub>t</sub></span><span aria-hidden="true">=</span>'
            '<span class="al-fraction" aria-hidden="true"><span><i>P</i><sub>t</sub></span>'
            '<span><i>P</i><sub>t−k</sub></span></span><span aria-hidden="true">− 1</span></div>')


def _plot(lab):
    points = [(row.timestamp.isoformat(), float(short), float(long)) for row, short, long in
              zip(lab.rows[lab.params.long:], lab.series[0].iloc[lab.params.long:], lab.series[1].iloc[lab.params.long:])]
    if any(not math.isfinite(value) for p in points for value in p[1:]):
        raise LabInputError("当前价格比率超出数值范围，请核对该快照的价格单位。")
    bounds = _chart_bounds(points)
    initial = lab.index() - lab.params.long
    point = points[initial]
    w1, w2 = lab.params.short, lab.params.long
    year = points[0][0][:4] if points[0][0][:4] == points[-1][0][:4] else points[0][0][:4] + "—" + points[-1][0][:4]
    ticks = _chart_svg(lab, points, initial, bounds, narrow=False) + _chart_svg(lab, points, initial, bounds, narrow=True)
    evidence_url = lab.link(EVIDENCE, node="factor", at=point[0])
    table_rows = "".join(f'<tr><th scope="row">{_h(stamp[:10])}</th><td>{lab.rows[k].close:,.4f}</td>'
                         f'<td>{v1:.6f}</td><td>{v2:.6f}</td></tr>'
                         for k, (stamp, v1, v2) in enumerate(points, start=lab.params.long))
    return (f'<div class="al-chart" data-atlas-chart data-short="{w1}" data-long="{w2}" data-initial="{initial}" '
            f'data-points="{_h(json.dumps(points, separators=(",", ":"), allow_nan=False))}">'
            f'<div class="al-chart-context"><span>{_h(lab.instrument)} · {year}</span><span>动量比率</span></div>{ticks}'
            f'<div class="al-legend" aria-hidden="true"><span><i></i>{w1} 期动量</span><span><i></i>{w2} 期动量</span></div></div>'
            f'<div class="al-chart-tools"><details><summary>查看 {len(points)} 个观测值</summary><div class="al-table-wrap" tabindex="0" aria-label="动量数值表">'
            f'<table><caption>两个窗口都有观测值的研究区间</caption><thead><tr><th scope="col">观测日</th><th scope="col">收盘价</th>'
            f'<th scope="col">{w1} 期动量</th><th scope="col">{w2} 期动量</th></tr></thead><tbody>{table_rows}</tbody></table></div></details>'
            f'<a href="{_h(evidence_url)}" data-atlas-evidence-link>追溯所选观测 <span aria-hidden="true">↗</span></a></div>')


def render_factors(service, csrf_token, query=None):
    values = _query_values(query)
    options = _choices(service, values["dataset"])
    try:
        lab = Exploration(service, Parameters.parse(values))
        plotted = _plot(lab)
        description = (f'{lab.mode_label} · {lab.instrument}。图中比较原始价格动量，'
                       f'末段 {lab.holdout_count} 个观测期留给样本外检验。')
    except (LabInputError, FileNotFoundError, ValueError) as exc:
        plotted = '<p role="alert" class="al-error">' + _h(_error_message(exc)) + '</p>'
        description = '选择数据快照及回看窗口以读取研究区间。'
    return (f'<article class="al-page" data-atlas-lab="factors">{_title("因子实验", "价格动量")}'
            '<div class="al-factor-grid"><section class="al-chart-panel" aria-label="价格动量公式与图表">'
            f'{_formula()}<div data-atlas-preview>{plotted}</div></section>'
            '<section class="al-params" aria-labelledby="al-parameter-title"><h2 id="al-parameter-title">实验参数</h2>'
            f'<form method="get" action="{FACTORS}" data-atlas-parameters>'
            '<div class="al-param-row"><span id="al-windows-label">回看窗口<span class="al-unit-label">（期）</span></span>'
            '<div class="al-window-pair" role="group" aria-labelledby="al-windows-label">'
            f'<input name="short" type="number" aria-label="短回看窗口" min="2" max="59" step="1" value="{_h(values["short"])}" required>'
            f'<input name="long" type="number" aria-label="长回看窗口" min="3" max="60" step="1" value="{_h(values["long"])}" required></div></div>'
            '<label class="al-param-row"><span>交易成本</span><span class="al-cost">'
            f'<input name="fee" aria-label="每单位换手的交易成本，基点" type="number" min="0" max="100" step="any" value="{_h(values["fee"])}" required>'
            '<span>bps</span></span></label>'
            f'<label class="al-param-row"><span>数据快照</span><select name="dataset">{options}</select></label>'
            '<div class="al-run-actions">'
            f'<button class="al-run" type="submit" name="_csrf" data-atlas-run="{SUBMIT}" value="{_h(csrf_token)}" formmethod="post" formaction="{RECEIPT}">运行实验</button>'
            '<button class="al-refresh" type="submit" data-atlas-refresh>更新图表</button></div>'
            '</form><div class="al-run-feedback" role="status" aria-live="polite" data-atlas-result></div>'
            '<p class="al-run-note">运行后保存实验章程，并以所设费用进行样本内验证。</p></section></div>'
            '<footer class="al-factor-foot"><p>比较不同回看窗口下的价格变化。</p>'
            f'<p data-atlas-source-note>{_h(description)}</p></footer></article>')

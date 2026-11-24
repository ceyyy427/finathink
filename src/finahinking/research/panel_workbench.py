"""Prepare and reuse audited panel inputs from local snapshots and attachments."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from finahinking.data.panel_contracts import (
    CURRENCIES,
    FIELD_DEFINITIONS,
    NATIVE_PRICE_FIELDS,
    PANEL_RULE_FIELDS,
    PanelContract,
    instrument,
    panel_time,
    reference,
)
from finahinking.data.research_fields import ResearchFieldStore, _label
from finahinking.data.research_panel import build_research_panel, field_clocks

from .contracts import stable_digest
from .factor_library import FactorLibrary
from .strategy_catalog import StrategyCatalog
from .strategy_runner import CORPORATE_ACTION_FIELDS, PRICE_LIMIT_FIELDS, strategy_input_fields

_VERSION = "saved-panel.v1"
_PRICE_COLUMNS = ("open", "high", "low", "close", "volume")
_CORE_FIELDS = set(_PRICE_COLUMNS) | {"suspended"} | {
    field for fields in PANEL_RULE_FIELDS.values() for field in fields
}
_MAX_BINDING_BYTES = 24_000  # Leave room in the research job's 32 KiB binding for method inputs.
_RULE_LABELS = {
    "calendar_ref": "交易日历",
    "universe_ref": "历史股票池",
    "adjustment_ref": "公司行动",
    "trade_rule_ref": "交易规则",
}
_REPAIR = {
    "calendar_ref": "核对日历在选定资产和决策时点的可用时间。",
    "universe_ref": "补齐各标的在历史时点的股票池成员声明。",
    "adjustment_ref": "补齐已知拆分比例，并核对行情快照的原始价格口径。",
    "trade_rule_ref": "补齐历史交易规则和行情中的显式停牌字段。",
    "currency": "核对行情币种及附件中金额、价格和每股字段的单位。",
    "missing_field": "核对行情覆盖和字段映射。晚到数据在原决策时点仍为空。",
}


def _date(value):
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("请选择有效的起止日期。")
    try:
        result = date.fromisoformat(value)
    except ValueError:
        raise ValueError("请选择有效的起止日期。") from None
    if result.isoformat() != value:
        raise ValueError("请选择有效的起止日期。")
    return result


def _target(kind, target_id, *, bounded_limits=False):
    if kind is None and target_id is None:
        return {}, None
    if not isinstance(target_id, str) or not target_id:
        raise ValueError("请选择需要检查的因子或策略。")
    if kind == "factor":
        factor = FactorLibrary().get(target_id)
        return factor["field_units"], factor["implementation_fingerprint"]
    if kind == "strategy":
        template = StrategyCatalog().template(target_id)
        return dict.fromkeys(strategy_input_fields(template, bounded_limits=bounded_limits)), template["fingerprint"]
    raise ValueError("请选择需要检查的因子或策略。")


def _calendar_grid(calendar, assets, start, end, snapshot, zone):
    # Every declared session must participate in the shared grid. Readiness
    # rejects a late/missing calendar observation instead of dropping its date.
    sessions, known_dates = set(), set()
    for row in calendar.records:
        if row["instrument"] not in assets or not isinstance(row.get("is_session"), bool):
            continue
        time, available = field_clocks(row)
        if not start <= time.date() <= end or time > snapshot.as_of:
            continue
        if row["is_session"]:
            sessions.add(time)
        if available is not None and available <= time and (not row.get("effective_until")
                                                            or time < panel_time(row["effective_until"])):
            known_dates.add((row["instrument"], time.tz_convert(zone).date()))
    quoted_dates = {(row.instrument, row.timestamp.tz_convert(zone).date()) for row in snapshot.observations
                    if row.instrument in assets and start <= row.timestamp.date() <= end and row.timestamp <= snapshot.as_of}
    return sorted(sessions), len(quoted_dates - known_dates)


def _setup(record):
    contract = record["contract"]
    return {
        "snapshot_ref": contract["snapshot_ref"], "panel_ref": stable_digest(record),
        "panel_fingerprint": record["panel_fingerprint"], "name": record["name"],
        "data_mode": record["data_mode"],
        "instrument_ids": contract["instrument_ids"], "observation_count": len(contract["observed_at"]),
        "first": contract["observed_at"][0], "last": contract["observed_at"][-1],
    }


class PanelWorkbench:
    def __init__(self, snapshots, scope_id):
        self.snapshots = snapshots
        self.scope_id = scope_id
        self.root = snapshots.root / "panels" / stable_digest(scope_id)

    @staticmethod
    def definitions():
        return {
            "fields": {name: sorted(spec[2]) for name, spec in FIELD_DEFINITIONS.items()
                       if name not in NATIVE_PRICE_FIELDS},
            "rules": {key: {"label": _RULE_LABELS[key], "fields": list(fields)}
                      for key, fields in PANEL_RULE_FIELDS.items()},
            "factors": [{"id": item["factor_id"], "label": item["title"]} for item in FactorLibrary().entries()],
            "strategies": [{"id": item["template_id"], "label": item["title"]} for item in StrategyCatalog().templates()],
        }

    def snapshot_options(self, ref):
        snapshot = self.snapshots.load(ref)
        rows = []
        for attachment in ResearchFieldStore(self.snapshots).list_for_snapshot(snapshot):
            units = {key: val for key, val in attachment.units.items() if key in FIELD_DEFINITIONS}
            if units:
                rows.append({
                    "attachment_ref": attachment.fingerprint, "units": units,
                    "row_count": len(attachment.records), "source_declaration": attachment.source_declaration,
                })
        return {
            "instruments": list(snapshot.instruments), "data_mode": snapshot.mode,
            "first": min(row.timestamp for row in snapshot.observations).date().isoformat(),
            "last": max(row.timestamp for row in snapshot.observations).date().isoformat(),
            "as_of": snapshot.as_of.isoformat(), "adjustment": snapshot.provenance.get("adjustment"),
            "extendable": snapshot.mode == "CAPTURED" and bool(snapshot.provenance.get("connection_ref")),
            "units": dict(snapshot.provenance.get("units", {})), "attachments": rows,
        }

    def _prepare(self, values):
        required = {"name", "snapshot_ref", "instrument_ids", "start", "end", "timezone", "attachment_refs", "rules"}
        if set(values) - required - {"target_kind", "target_id"} or not required <= values.keys():
            raise ValueError("请选择快照、标的、日期、时区和规则附件。")
        if (not isinstance(values["instrument_ids"], list) or not 1 <= len(values["instrument_ids"]) <= 100
                or not isinstance(values["attachment_refs"], list) or len(values["attachment_refs"]) > 32):
            raise ValueError("请选择有限数量的标的和字段附件。")
        assets = sorted(instrument(value) for value in values["instrument_ids"])
        refs = sorted(reference(value) for value in values["attachment_refs"])
        if len(set(assets)) != len(assets) or len(set(refs)) != len(refs):
            raise ValueError("标的或字段附件重复，请重新选择。")
        rules = values["rules"]
        if not isinstance(rules, dict) or set(rules) - PANEL_RULE_FIELDS.keys():
            raise ValueError("规则附件选择有误。")
        rules = {key: reference(ref) for key, ref in rules.items() if ref is not None}
        refs = sorted(set(refs) | set(rules.values()))
        if len(refs) > 32:
            raise ValueError("最多选用 32 个字段或规则附件。")
        starts, ends = _date(values["start"]), _date(values["end"])
        if starts > ends:
            raise ValueError("开始日期晚于截止日期。")
        try:
            zone = ZoneInfo(values["timezone"])
        except (TypeError, ValueError, ZoneInfoNotFoundError):
            raise ValueError("请选择有效的资产时区。可使用 UTC 或选取列表中的市场时区。") from None
        name = _label(values["name"], maximum=100)
        snapshot = self.snapshots.load(values["snapshot_ref"])
        if not set(assets) <= set(snapshot.instruments):
            raise ValueError("所选标的不在行情快照中，请重新选择。")
        required_fields, method = _target(values.get("target_kind"), values.get("target_id"))
        attachments = ResearchFieldStore(self.snapshots).load_for_snapshot(snapshot, refs)
        by_ref = {item.fingerprint: item for item in attachments}
        issues = [
            f"请选择包含 {'、'.join(fields)} 的{_RULE_LABELS[key]}附件。"
            for key, fields in PANEL_RULE_FIELDS.items()
            if key not in rules or not set(fields) <= by_ref[rules[key]].units.keys()
        ]
        declared = snapshot.provenance.get("units", {})
        currency = declared.get("price_currency")
        if currency not in CURRENCIES or declared.get("volume_unit") != "shares":
            issues.append("行情快照需要声明币种，成交量需要按股记录。请检查数据接口，再获取快照。")
        if snapshot.provenance.get("adjustment") != "RAW":
            issues.append("请获取明确标记为 RAW 的原始价格快照。")
        calendar = by_ref.get(rules.get("calendar_ref"))
        times, missing_calendar = _calendar_grid(calendar, set(assets), starts, ends, snapshot, zone) if calendar is not None else ([], 0)
        if calendar is not None and not times:
            issues.append("日期范围内没有决策时点。请检查交易日历、日期范围和快照截至时间。")
        if missing_calendar:
            issues.append(f"有 {missing_calendar} 个标的日期有行情但缺少当时可用的日历。请补齐历史会话声明。")
        if len({time.tz_convert(zone).date() for time in times}) != len(times):
            issues.append("同一个资产本地日期只能选择一个日频决策时点。请检查所选日历。")
        if len(times) * len(assets) > 10_000:
            raise ValueError("面板行数超限。请缩短日期范围或减少标的。")
        preview = {
            "status": "NEEDS_DATA", "data_mode": snapshot.mode, "row_count": len(assets) * len(times),
            "issues": issues, "target_issues": [],
            "fields": [{"field": field, "expected_unit": unit, "required": True,
                        "allowed_units": sorted(FIELD_DEFINITIONS[field][2])}
                       for field, unit in sorted(required_fields.items())],
        }
        if issues:
            return preview, None
        # Extra fields remain optional in the panel. Suitability for a target
        # is checked below, so an early missing financial value stays missing.
        units = {name: currency for name in _PRICE_COLUMNS if name != "volume"}
        units.update(volume="shares", suspended="boolean")
        selected_prices = [row for row in snapshot.observations
                           if row.instrument in assets and starts <= row.timestamp.date() <= ends]
        if selected_prices and all(row.amount is not None for row in selected_prices):
            units["amount"] = currency
        contract = PanelContract(
            snapshot.fingerprint, assets, times, units, self.scope_id,
            dict.fromkeys(assets, currency), dict.fromkeys(assets, values["timezone"]), **rules,
        )
        record = {"schema_version": _VERSION, "contract": contract.to_dict(), "data_mode": snapshot.mode,
                  "attachment_refs": refs, "name": name}
        if len(json.dumps(record, ensure_ascii=False).encode()) > _MAX_BINDING_BYTES:
            raise ValueError("面板超过后台研究输入上限。请缩短日期范围。")
        try:
            panel = build_research_panel(snapshot, attachments, contract, include_availability=True)
        except ValueError as exc:
            if str(exc) == "字段单位不一致":
                raise ValueError("所选附件的同名字段单位不一致。请只选用同一计量口径的附件。") from None
            raise
        audit = panel.audit
        if panel.status != "READY":
            issues.extend(dict.fromkeys(_REPAIR.get(reason.split("：", 1)[0], reason)
                                       for reason in audit["reasons"]))
        frame = panel.frame
        if not frame["is_session"].eq(True).all():
            issues.append("所选标的需要同一组日历决策时点。请选用一致的日历或分别保存面板。")
        if values.get("target_kind") == "strategy":
            limits = frame.get("limit_rule_version")
            if limits is not None and limits.dropna().ne("UNRESTRICTED").any():
                required_fields, _ = _target("strategy", values["target_id"], bounded_limits=True)
            for key, names in (("adjustment_ref", CORPORATE_ACTION_FIELDS), ("trade_rule_ref", PRICE_LIMIT_FIELDS)):
                expected = set(names) & required_fields.keys()
                if not expected <= by_ref[rules[key]].units.keys():
                    preview["target_issues"].append(f"所选{_RULE_LABELS[key]}附件需要提供 {'、'.join(sorted(expected))}。")
            classes = snapshot.provenance.get("asset_classes", {})
            if any(classes.get(asset) not in {"EQUITY", "ETF"} for asset in assets):
                preview["target_issues"].append("策略需要在行情快照中声明每个标的是股票还是 ETF。")
        fields = sorted(_CORE_FIELDS | set(audit["units"]) | set(required_fields))
        for field in required_fields:
            expected = required_fields[field]
            if not audit["coverage"].get(field):
                preview["target_issues"].append(f"{field} 在所选日期内无可用记录，请先补充对应附件。")
            elif expected is not None and audit["units"].get(field) != expected:
                preview["target_issues"].append(f"{field} 需要 {expected}，请检查附件或行情单位。")
            elif values.get("target_kind") == "strategy" and audit["coverage"][field] < preview["row_count"]:
                preview["target_issues"].append(f"{field} 只覆盖部分决策时点，请核对该策略的输入范围。")
        preview["fields"] = [{
            "field": field, "unit": audit["units"].get(field) or (
                "boolean" if field in {"is_session", "in_universe", "trade_allowed"} else None),
            "expected_unit": required_fields.get(field), "required": field in required_fields,
            "allowed_units": sorted(FIELD_DEFINITIONS[field][2]) if field in required_fields else [],
            "available_at": audit["availability"].get(field), "covered": audit["coverage"].get(field, 0),
        } for field in fields]
        record["panel_fingerprint"] = panel.fingerprint
        preview.update(
            status="NEEDS_DATA" if issues else "READY", panel_fingerprint=panel.fingerprint,
            first=times[0].isoformat(), last=times[-1].isoformat(),
            eligible_rows=int(frame["execution_eligible"].sum()),
        )
        if not issues:
            preview["preview_fingerprint"] = stable_digest({"record": record, "method": method,
                "target_kind": values.get("target_kind"), "target_id": values.get("target_id"),
                "period": [starts.isoformat(), ends.isoformat()], "preview": preview})
        return preview, record

    def preview(self, values):
        preview, _ = self._prepare(values)
        return preview

    def save(self, values, expected):
        preview, record = self._prepare(values)
        if not preview.get("preview_fingerprint") or preview["preview_fingerprint"] != expected:
            raise ValueError("面板输入已变化或数据未就绪。请修正后重新预览。")
        summary = _setup(record)
        if self.root.parent.is_symlink() or self.root.is_symlink():
            raise ValueError("面板保存目录无效，请检查工作区。")
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        target = self.root / f"{summary['panel_ref']}.json"
        with tempfile.NamedTemporaryFile(dir=self.root, prefix=".panel-", delete=False) as handle:
            temporary = handle.name
            try:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False).encode())
                handle.flush()
                os.fsync(handle.fileno())
                try:
                    os.link(temporary, target)
                except FileExistsError:
                    self.load(summary["panel_ref"])
            finally:
                os.unlink(temporary)
        return summary

    def load(self, ref):
        path = self.root / f"{reference(ref)}.json"
        if self.root.parent.is_symlink() or self.root.is_symlink() or path.is_symlink():
            raise ValueError("面板保存目录无效，请检查工作区。")
        with path.open("rb") as handle:
            raw = handle.read(_MAX_BINDING_BYTES + 1024)
        if len(raw) >= _MAX_BINDING_BYTES + 1024:
            raise ValueError("面板记录超过大小上限。")
        record = json.loads(raw)
        if (not isinstance(record, dict) or record.get("schema_version") != _VERSION
                or stable_digest(record) != ref or record["contract"]["scope_id"] != self.scope_id):
            raise ValueError("面板记录校验失败，请重新选择或恢复记录。")
        return record

    def list(self):
        items = []
        for path in sorted(self.root.glob("*.json")):
            try:
                items.append(_setup(self.load(path.stem)))
            except (OSError, TypeError, ValueError, KeyError, RecursionError):
                continue
        return sorted(items, key=lambda item: (item["last"], item["name"], item["panel_ref"]), reverse=True)

    def draft_for(self, ref):
        record = self.load(ref)
        contract = record["contract"]
        return {
            "name": record["name"], "snapshot_ref": contract["snapshot_ref"],
            "instrument_ids": contract["instrument_ids"], "start": contract["observed_at"][0][:10],
            "end": contract["observed_at"][-1][:10], "timezone": next(iter(contract["timezones"].values())),
            "rules": {key: contract[key] for key in PANEL_RULE_FIELDS}, "attachment_refs": record["attachment_refs"],
        }

    def expand_input(self, values):
        if not isinstance(values, dict) or "saved_panel_ref" not in values:
            return values
        if {"contract", "attachment_refs", "sample_spec"} & values.keys():
            raise ValueError("请选择已保存面板、样例或手工合同中的一种。")
        ref = values["saved_panel_ref"]
        try:
            record = self.load(ref)
        except FileNotFoundError:
            raise ValueError("所选面板已不可读。请重新选择面板。") from None
        return {**{key: value for key, value in values.items() if key != "saved_panel_ref"},
                "contract": record["contract"], "attachment_refs": record["attachment_refs"]}

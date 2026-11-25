"""Append new sessions to an immutable user price snapshot."""

from dataclasses import replace

from finahinking.p8_2.contracts import DatasetSnapshot

from .user_api_contracts import fingerprint_records


def _price_content(row):
    return {key: value for key, value in row.to_dict().items() if key != "retrieved_at"}


def extension_request(request, base, connection_ref, units, asset_classes, adjustment):
    provenance = base.provenance
    if (base.mode != "CAPTURED" or provenance.get("connection_ref") != connection_ref
            or provenance.get("units", {}) != units or provenance.get("asset_classes", {}) != asset_classes
            or provenance.get("adjustment") != adjustment or set(request.instruments) != set(base.instruments)):
        raise ValueError("增量需要同一连接映射、标的、单位和价格口径。请重新完整获取快照。")
    if request.start or request.as_of or not request.end or len(request.end) != 10:
        raise ValueError("增量自动从已保存快照的末端读取，请只填写新的截止日期。")
    last = {}
    for row in base.observations:
        last[row.instrument] = row.timestamp
    if request.end < max(time.date().isoformat() for time in last.values()):
        raise ValueError("增量截止日期早于现有快照，请选择更晚的日期。")
    return replace(request, start=min(last.values()).date().isoformat()), last


def append_prices(base, update, request, last, *, max_records):
    records = {(row.instrument, row.timestamp): row for row in base.observations}
    for row in update.observations:
        if row.instrument not in last or not request.start <= row.timestamp.date().isoformat() <= request.end:
            raise ValueError("数据接口返回了请求范围以外的记录，请检查标的与日期筛选。")
        key = (row.instrument, row.timestamp)
        if key in records:
            if _price_content(records[key]) != _price_content(row):
                raise ValueError("增量返回了历史修订，请完整获取新快照以保留两份历史版本。")
        elif row.timestamp <= last[row.instrument]:
            raise ValueError("增量返回了历史补录，请完整获取新快照。")
        else:
            records[key] = row
    if len(records) == len(base.observations):
        return base
    if len(records) > max_records:
        raise ValueError("合并后的快照记录太多，请缩小历史日期范围。")
    rows = tuple(records[key] for key in sorted(records))
    ref = fingerprint_records([_price_content(row) for row in rows])
    as_of = max(base.as_of, update.as_of)
    return DatasetSnapshot(
        dataset_id=f"user-{ref[:32]}", observations=rows, mode="CAPTURED", as_of=as_of,
        provenance={**update.provenance, "data_fingerprint": ref, "parent_snapshot_ref": base.fingerprint,
                    "pit_status": "AVAILABLE" if all(row.available_at_time(as_of) for row in rows) else "UNKNOWN"},
        limitations=base.limitations,
    )

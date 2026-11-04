"""Bindings for versioned strategy templates executed by the local worker."""

from __future__ import annotations

import json
from datetime import datetime

from finahinking.data.panel_contracts import PanelContract

from .contracts import stable_digest
from .strategy_catalog import StrategyCatalog, bind_parameters

VERSION = "strategy-study.v1"


def prepare_strategy(service, values):
    allowed = {"template_id", "contract", "attachment_refs", "parameters", "policy_ref",
               "idempotency_key", "question"}
    if not isinstance(values, dict) or set(values) - allowed:
        raise ValueError("策略研究输入包含未声明字段")
    contract = PanelContract.from_dict(values["contract"])
    snapshot = service.snapshots.load(contract.snapshot_ref)
    refs = values.get("attachment_refs", [])
    if not isinstance(refs, list) or len(set(refs)) != len(refs):
        raise ValueError("附件引用需要是无重复的数组")
    panel = service.snapshots.build_panel(contract, refs)
    catalog = StrategyCatalog()
    template = catalog.template(values["template_id"])
    parameters = values.get("parameters", {})
    as_of = datetime.fromisoformat(contract.observed_at[-1]).date().isoformat()
    policy = service.learning.store.policy(values.get("policy_ref"), as_of=as_of)
    if not isinstance(parameters, dict):
        raise TypeError("模板参数需要是对象")
    parameters = dict(parameters)
    for key in ("starting_cash", "fee_bps", "slippage_bps"):
        approved = policy["effective"][key]
        if key in parameters and parameters[key] != approved:
            raise ValueError("现金、费用与滑点使用批准政策")
        parameters[key] = approved
    parameters = bind_parameters(template, parameters)
    binding = {
        "version": VERSION, "template_id": values["template_id"],
        "template_digest": stable_digest(template), "risk_digest": stable_digest(catalog.risk_rules()),
        "contract": contract.to_dict(), "attachment_refs": sorted(refs),
        "parameters": parameters, "panel_ref": panel.fingerprint,
        "policy_ref": policy["policy_ref"], "policy_digest": stable_digest(policy["effective"]),
    }
    if len(json.dumps(binding, ensure_ascii=False, allow_nan=False).encode()) > 32768:
        raise ValueError("策略输入超过后台任务上下文上限")
    request = {"idempotency_key": values["idempotency_key"], "snapshot_ref": snapshot.fingerprint,
               "instrument": contract.instrument_ids[0], "as_of": as_of,
               "policy_ref": policy["policy_ref"],
               "question": values.get("question", "按冻结模板和风险规则验证纸面策略"),
               "budget": {"max_wall_seconds": 120, "max_bytes": 64 * 1024 * 1024}}
    return request, binding


def execute_bound_strategy(snapshots, binding, snapshot, policy_record):
    from .strategy_runner import execute_strategy

    catalog = StrategyCatalog()
    contract = PanelContract.from_dict(binding["contract"])
    if (binding["version"] != VERSION or contract.snapshot_ref != snapshot.fingerprint
            or binding["policy_ref"] != policy_record["policy_ref"]
            or binding["policy_digest"] != stable_digest(policy_record["effective"])
            or binding["template_digest"] != stable_digest(catalog.template(binding["template_id"]))
            or binding["risk_digest"] != stable_digest(catalog.risk_rules())):
        raise ValueError("策略模板、输入或批准政策已改变")
    panel = snapshots.build_panel(contract, binding["attachment_refs"])
    if panel.fingerprint != binding["panel_ref"]:
        raise ValueError("策略面板指纹不一致")
    classes = snapshot.provenance.get("asset_classes", {})
    result = execute_strategy(binding["template_id"], panel, binding["parameters"],
                              asset_classes={asset: classes.get(asset) for asset in contract.instrument_ids},
                              policy=policy_record["effective"])
    return {"binding": binding, "snapshot_ref": snapshot.fingerprint, "data_mode": snapshot.mode,
            "result": result, "fingerprint": stable_digest({"binding": binding, "result": result}),
            "paper_only": True, "production_write": False}

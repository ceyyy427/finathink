"""Local material previews and explicit, frozen admission to the research queue."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import replace

from .application_service import (
    WORKFLOW_VERSION,
    _model_input_digest,
    _read_json,
    _write_private_json,
)
from .context_bundle import ROLES, build_context_bundle
from .contracts import _unsafe_public_text, stable_digest
from .credentials import EnvironmentCredentialStore
from .job_queue import _ref, _snapshot_json
from .provider_adapters import grounded_wire_payload
from .provider_config import ProviderConfig, keychain_provider_ready
from .quality_cases import quality_case
from .readiness import inspect_snapshot

VERSION = "model-workbench.v1"
_DIGEST = re.compile(r"[0-9a-f]{64}")
_FIELDS = {"snapshot_ref", "instrument", "as_of", "question", "model_config_ref", "materials",
           "budget", "policy_ref", "idempotency_key"}
_REASONS = {
    "READY": "本地配置已具备，尚未试连。",
    "DISABLED": "该模型配置已停用。",
    "OUTBOUND_DISABLED": "当前工作区禁止外部模型调用。",
    "PROVIDER_MISSING": "请选择已保存的兼容模型配置。",
    "ENDPOINT_MISSING": "请先补全该模型的 HTTPS 接口。",
    "CREDENTIAL_MISSING": "当前进程无法使用配置所指的环境变量或钥匙串凭证。",
    "SNAPSHOT_UNAVAILABLE": "快照缺失或指纹校验失败，请重新选择完整快照。",
    "INSTRUMENT_MISSING": "请从所选快照选择研究标的。",
    "QUESTION_INVALID": "请填写不含私密地址或凭证的研究问题（1 至 2000 字）。",
    "BUDGET_INVALID": "请核对预算。外部模型只允许一次尝试。",
    "BUDGET_INSUFFICIENT": "已授权角色与研究汇总所需的调用数超出预算。",
    "MATERIALS_INVALID": "资料必须属于所选标的和角色，已获授权并在截止时点前可用。",
    "POLICY_UNAVAILABLE": "所选研究政策无法在截止时点使用。",
    "INSUFFICIENT_EVIDENCE": "所选标的缺少足量、时间可核验的日频观察。",
    "INPUT_INVALID": "研究输入未通过本地校验。",
}


class ModelWorkbenchError(ValueError):
    def __init__(self, code, status_code, message):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _blocked(code, *, message=None, projection=None):
    return {"schema_version": VERSION, "paper_only": True, "roles": [], "selection": {}, "budget": {},
            "expected_calls": 0, **(projection or {}), "status": "BLOCKED",
            "preview_ref": None, "preview_digest": None,
            "blockers": [{"code": code, "message": message or _REASONS[code]}]}


def _provider(config, allow_model_calls):
    if config is None:
        return {"provider_id": "offline", "adapter_kind": "offline", "model": "fixture-v1",
                "role_models": dict.fromkeys(ROLES, "fixture-v1"), "status": "READY",
                "endpoint_configured": False, "credential_ready": False, "enabled": True}
    ref = config.credential_ref
    ready = False
    if ref is not None:
        try:
            ready = (EnvironmentCredentialStore().has(ref) if ref.env_var is not None else keychain_provider_ready(ref))
        except (OSError, ValueError, RuntimeError):
            pass
    if not config.enabled or config.adapter_kind == "offline":
        status = "DISABLED"
    elif not allow_model_calls:
        status = "OUTBOUND_DISABLED"
    elif not config.endpoint:
        status = "ENDPOINT_MISSING"
    else:
        status = "READY" if ready else "CREDENTIAL_MISSING"
    return {"provider_id": config.provider_id, "adapter_kind": config.adapter_kind, "model": config.model,
            "role_models": {role: config.model_for_role(role) for role in ROLES}, "status": status,
            "endpoint_configured": config.endpoint is not None, "credential_ready": ready, "enabled": config.enabled}


def _snapshot_choice(snapshot, *, sample=False, as_of=None):
    return {"snapshot_ref": "sample" if sample else snapshot.fingerprint, "fingerprint": snapshot.fingerprint,
            "instruments": list(snapshot.instruments), "data_mode": snapshot.mode, "record_count": len(snapshot.observations),
            "as_of": as_of or (snapshot.as_of.date().isoformat() if snapshot.as_of is not None else None)}


def role_projection(request, snapshot, inputs, *, reports=(), discovery_backgrounds=None):
    """Render the exact bounded model inputs. Only synthesis may add later role reports."""
    roles = []
    config = ProviderConfig.from_mapping(inputs["provider"]) if inputs is not None else None
    for role in ROLES:
        item = {"role": role, "model": config.model_for_role(role) if config else "fixture-v1",
                "model_ref": config.model_ref_for_role(role) if config else "offline/fixture-v1",
                "dynamic_observations": bool(config and role == "research")}
        if config is None:
            roles.append({**item, "material_status": "LOCAL_ONLY", "materials": [], "evidence_refs": [],
                          "expected_call": False, "context": None, "context_fingerprint": None, "wire_fingerprint": None})
            continue
        discovery = request.research_plan.validation_spec.get("discovery")
        context_limit = discovery["blueprint"]["budget"]["max_context_bytes"] if discovery else 32768
        bundle = build_context_bundle(request, snapshot, role, materials=inputs["materials"],
                                      reports=reports if role == "research" else (), max_bytes=context_limit,
                                      discovery_knowledge=discovery_backgrounds[role] if discovery_backgrounds else None)
        context = bundle.to_dict()
        wire = grounded_wire_payload(item["model"], config.adapter_kind == "deepseek_compatible", bundle)
        roles.append({**item, "material_status": context["material_status"], "task": context["role_task"],
                      "materials": context["materials"], "evidence_refs": list(context["evidence_index"]),
                      "expected_call": bool(bundle.evidence_index), "context": context,
                      "context_fingerprint": bundle.fingerprint, "wire_fingerprint": _model_input_digest(wire),
                      "system_instructions": wire["messages"][0]["content"], "response_format": wire["response_format"],
                      "output_limit": {k: v for k, v in wire.items() if k not in {"messages", "model", "response_format"}}})
    return roles


def _projection(request, snapshot, inputs):
    spec = request.research_plan.validation_spec
    roles = role_projection(request, snapshot, inputs)
    selection = {"snapshot_ref": request.snapshot_ref, "instrument": request.instrument, "as_of": request.as_of.isoformat(),
                 "question": request.research_plan.hypotheses[0], "model_config_ref": spec["model_config_ref"],
                 "policy_ref": request.previous_policy_fingerprint, "policy_fingerprint": spec["policy_fingerprint"],
                 "data_mode": snapshot.mode, "record_count": sum(r.instrument == request.instrument for r in snapshot.observations)}
    return {"schema_version": VERSION, "paper_only": True, "execution_mode": spec["execution_mode"],
            "run_id": request.run_id, "selection": selection, "roles": roles, "budget": spec["budget"],
            "expected_calls": sum(r["expected_call"] for r in roles), "data_limitations": list(snapshot.limitations)}


def _preview_digest(request, projection):
    return _model_input_digest({"request_digest": stable_digest(request), "projection": projection})


def _receipt(root, scope, reference):
    if not isinstance(reference, str) or not _DIGEST.fullmatch(reference):
        raise ValueError("请使用有效的本地预览引用。")
    try:
        data = _read_json(root / "model-previews" / f"{reference}.json", max_bytes=131072)
    except FileNotFoundError:
        raise KeyError("预览不存在。") from None
    if _model_input_digest(data) != reference or data.get("schema_version") != VERSION or data.get("scope_id") != scope:
        raise ModelWorkbenchError("INVALID_PREVIEW", 409, "预览不属于当前工作区或其内容已经改变。")
    return data


def verify_bound_preview(root, request, snapshot, inputs):
    """Recompute the authorized wire contract before dispatch and when reading evidence."""
    spec = request.research_plan.validation_spec
    binding = spec["model_workbench"]
    receipt = _receipt(root, spec["scope_id"], binding["preview_ref"])
    original_spec = {key: value for key, value in spec.items() if key != "model_workbench"}
    original = replace(request, research_plan=replace(request.research_plan, validation_spec=original_spec),
                       config_digest=binding["base_config_digest"])
    projection = _projection(original, snapshot, inputs)
    expected = _preview_digest(original, projection)
    if (binding["preview_digest"] != expected or receipt["preview_digest"] != expected
            or binding["role_model_refs"] != {r["role"]: r["model_ref"] for r in projection["roles"] if inputs is not None}
            or request.config_digest != stable_digest([original.config_digest, binding])):
        raise ValueError("材料、模型、预算或发送合同与原授权不一致。")
    return projection


class ModelWorkbench:
    def __init__(self, service):
        self.service = service

    def settings(self):
        app = self.service
        sample = quality_case("sparse")
        choices = [_snapshot_choice(sample["snapshot"], sample=True, as_of=sample["as_of"])]
        unavailable = []
        for path in sorted(app.snapshots.root.glob("*.json")):
            if not _DIGEST.fullmatch(path.stem) or path.stem == sample["snapshot_ref"]:
                continue
            try:
                choices.append(_snapshot_choice(app.snapshots.load(path.stem)))
            except (OSError, ValueError, KeyError, TypeError):
                unavailable.append(path.stem)
        configs = [c for c in app.provider_configs.current_configs() if c.provider_id != "offline"]
        with app.queue._connect() as db:
            history = db.execute("""SELECT j.job_id, j.status, json_extract(r.payload,'$.run_id') run_id,
                json_extract(r.payload,'$.research_plan.validation_spec.model_config_ref') model_config_ref,
                json_extract(r.payload,'$.research_plan.validation_spec.execution_mode') execution_mode,
                json_extract(r.payload,'$.research_plan.hypotheses[0]') question,
                json_extract(r.payload,'$.research_plan.validation_spec.data_mode') data_mode
                FROM jobs j JOIN request_snapshots r ON j.task_ref=r.task_ref
                WHERE json_extract(r.payload,'$.workflow_version')=? AND json_extract(r.payload,'$.research_plan.validation_spec.scope_id')=?
                ORDER BY j.created_at DESC,j.job_id DESC LIMIT 50""", (WORKFLOW_VERSION, app.scope_id)).fetchall()
        return {"schema_version": VERSION, "paper_only": True, "allow_model_calls": app.allow_model_calls,
                "providers": [_provider(None, app.allow_model_calls), *[_provider(c, app.allow_model_calls) for c in configs]],
                "roles": list(ROLES), "snapshots": choices, "unavailable_snapshots": unavailable,
                "history": [dict(row) for row in history]}

    def _resolve(self, values):
        app = self.service
        reference = values.get("snapshot_ref")
        try:
            if reference == "sample":
                case = quality_case("sparse")
                snapshot, default_as_of = case["snapshot"], case["as_of"]
            else:
                snapshot = app.snapshots.load(reference)
                default_as_of = _snapshot_choice(snapshot)["as_of"]
        except (OSError, TypeError, ValueError, KeyError):
            return _blocked("SNAPSHOT_UNAVAILABLE"), None, None
        instrument = values.get("instrument", snapshot.instruments[0] if len(snapshot.instruments) == 1 else None)
        if instrument not in snapshot.instruments:
            return _blocked("INSTRUMENT_MISSING"), None, None
        question = values.get("question")
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 2000 or _unsafe_public_text(question):
            return _blocked("QUESTION_INVALID"), None, None
        as_of = values.get("as_of", default_as_of)
        checks = inspect_snapshot(snapshot, instrument, as_of)
        failed = next((c for c in checks if c["status"] == "BLOCKED"), None)
        if failed:
            return _blocked("INSUFFICIENT_EVIDENCE", message=failed["message"]), None, None
        provider = values.get("model_config_ref", "offline")
        if not isinstance(provider, str):
            return _blocked("PROVIDER_MISSING"), None, None
        config = (next((c for c in app.provider_configs.current_configs() if c.provider_id == provider), None)
                  if provider != "offline" else None)
        if config is None and provider != "offline":
            return _blocked("PROVIDER_MISSING"), None, None
        readiness = _provider(config, app.allow_model_calls)["status"]
        if readiness not in {"READY", "CREDENTIAL_MISSING"} or config is not None and config.credential_ref is None:
            return _blocked(readiness), None, None
        body = {**values, "snapshot_ref": snapshot.fingerprint, "as_of": as_of, "instrument": instrument,
                "model_config_ref": provider, "allow_model_calls": config is not None}
        try:
            prepared = app._prepare_submission(body, snapshot=snapshot, provider_config=config)
            _snapshot_json(prepared.request)
            projection = _projection(prepared.request, snapshot, prepared.model_inputs)
        except (ValueError, TypeError, KeyError) as error:
            message = str(error).lower()
            if any(k in message for k in ("budget", "max_", "one attempt", "resource limit")):
                code = "BUDGET_INVALID"
            elif any(k in message for k in ("material", "context")):
                code = "MATERIALS_INVALID"
            elif "policy_ref" in values:
                code = "POLICY_UNAVAILABLE"
            else:
                code = "INPUT_INVALID"
            return _blocked(code), None, None
        if readiness != "READY":
            return _blocked(readiness, projection=projection), None, None
        if projection["expected_calls"] > projection["budget"]["max_provider_calls"]:
            return _blocked("BUDGET_INSUFFICIENT", projection=projection), None, None
        return projection, prepared, snapshot

    def preview(self, values):
        if not isinstance(values, dict) or set(values) - _FIELDS:
            raise ValueError("材料预览包含未支持的字段。")
        if len(json.dumps(values, ensure_ascii=False, allow_nan=False).encode()) > 65536:
            raise ValueError("预览输入超出大小限制。")
        key = _ref(values["idempotency_key"], "idempotency_key") if "idempotency_key" in values else uuid.uuid4().hex
        # An app or client outside this workbench cannot accidentally acquire this queue key.
        values = {**values, "idempotency_key": "model-workbench-" + stable_digest(key)[:40]}
        projection, prepared, _ = self._resolve(values)
        if prepared is None:
            return projection
        digest = _preview_digest(prepared.request, projection)
        selection = projection["selection"]
        frozen = {k: selection[k] for k in ("question", "instrument", "as_of", "model_config_ref", "policy_ref")}
        frozen.update(snapshot_ref=values["snapshot_ref"], idempotency_key=values["idempotency_key"],
                      budget={k: v for k, v in projection["budget"].items() if k != "max_concurrency"},
                      materials=prepared.model_inputs["materials"] if prepared.model_inputs else [])
        receipt = {"schema_version": VERSION, "scope_id": self.service.scope_id, "values": frozen, "preview_digest": digest}
        reference = _model_input_digest(receipt)
        try:
            _write_private_json(self.service.root / "model-previews" / f"{reference}.json", receipt)
        except FileExistsError:
            _receipt(self.service.root, self.service.scope_id, reference)
        return {**projection, "status": "READY", "blockers": [], "preview_ref": reference, "preview_digest": digest}

    def authorize(self, values):
        if not isinstance(values, dict) or set(values) != {"preview_ref", "preview_digest", "authorized"} or values["authorized"] is not True:
            raise ModelWorkbenchError("AUTHORIZATION_REQUIRED", 403, "请先核对完整预览并明确授权本次任务。")
        app = self.service
        reference = values["preview_ref"]
        receipt = _receipt(app.root, app.scope_id, reference)
        if values["preview_digest"] != receipt["preview_digest"]:
            raise ModelWorkbenchError("STALE_PREVIEW", 409, "授权摘要与预览不一致，请重新预览。")
        frozen = receipt["values"]
        queue_key = "application:" + stable_digest({"scope": app.scope_id, "key": frozen["idempotency_key"]})[:40]
        with app.queue._connect() as db:
            row = db.execute("SELECT job_id FROM jobs WHERE idempotency_key=?", (queue_key,)).fetchone()
        if row:
            recorded = app._request(app.queue.get(row["job_id"])).research_plan.validation_spec.get("model_workbench") or {}
            if recorded.get("preview_digest") != values["preview_digest"] or recorded.get("preview_ref") != reference:
                raise ModelWorkbenchError("IDEMPOTENCY_CONFLICT", 409, "此任务标识已用于其他冻结输入，请重新预览新任务。")
            return app.get_job(row["job_id"])
        projection, prepared, snapshot = self._resolve(frozen)
        if prepared is None:
            blocker = projection["blockers"][0]
            raise ModelWorkbenchError(blocker["code"], 422, blocker["message"])
        if _preview_digest(prepared.request, projection) != receipt["preview_digest"]:
            raise ModelWorkbenchError("STALE_PREVIEW", 409, "模型、材料、预算或冻结版本已改变，请重新预览。")
        if frozen["snapshot_ref"] == "sample":
            app.snapshots.save(snapshot)
        binding = {"preview_ref": reference, "preview_digest": receipt["preview_digest"],
                   "base_config_digest": prepared.request.config_digest,
                   "role_model_refs": {r["role"]: r["model_ref"] for r in projection["roles"] if prepared.model_inputs is not None}}
        return app._enqueue_prepared(prepared, model_workbench=binding)

    def evidence(self, job_id):
        from .model_workbench_evidence import job_evidence

        return job_evidence(self.service, job_id)

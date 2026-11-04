"""Bridge a preview to the existing durable factor and strategy jobs."""
from dataclasses import replace
from datetime import datetime

from finahinking.data.panel_contracts import FIELD_PACKS, PanelContract

from .asset_catalog import ResearchAssetCatalog
from .contracts import AgentReport, stable_digest
from .discovery_planner import (
    DiscoveryBudget,
    StudyBlueprint,
    encode,
    open_knowledge,
    prepare_discovery,
)
from .drivers import DriverResult
from .job_queue import _ref
from .research_identity import execution_identity
from .snapshot_tools import local_research_policy
from .source_updates import SourceVersionStore, research_source_refs, source_policy
from .strategy_catalog import StrategyCatalog

_ROLE_TOPICS = {
    "fundamentals": ("fundamentals", "data-pit"),
    "technical": ("technical-volume", "returns-market", "factor-validation"),
    "sentiment": ("strategy-portfolio", "risk-review"),
    "news": ("data-pit", "fundamentals"),
    "learning": ("statistics", "factor-validation", "risk-review"),
}
_ROLE_BASE = {
    "fundamentals": ("publication-lag",),
    "technical": ("backtesting",),
    "sentiment": ("universe-bias",),
    "news": ("point-in-time",),
    "learning": ("oos",),
}
_ROLE_FOCUS = {
    "fundamentals": "检查历史财务披露口径及修订；需要单独的时点财务证据。",
    "technical": "优先检查固定样本、价格变动和费用后的方向稳定性。",
    "sentiment": "缺少独立情绪观测，情绪方向保持未知。",
    "news": "缺少独立新闻与发布时间证据，事件解释保持未知。",
    "learning": "若冻结测试方向改变或计费后失效，建议保留反例并复审。",
}


def _inputs(values):
    if not isinstance(values, dict) or set(values) - {"question", "profile", "budget", "contract", "attachment_refs", "policy_ref", "model_config_ref", "materials"}:
        raise ValueError("研究计划输入不符合合同")
    contract = PanelContract.from_dict(values["contract"]) if "contract" in values else None
    refs = values.get("attachment_refs", [])
    if not isinstance(refs, list) or len(refs) > 32 or len(set(refs)) != len(refs):
        raise ValueError("请提供去重的面板附件引用")
    if contract is None and refs:
        raise ValueError("附件需要绑定面板合同")
    return contract, refs


def provider_binding(config, materials):
    from .application_service import _model_input_digest

    return {"id": config.provider_id, "digest": _model_input_digest({
        "provider": config._stored(), "materials": materials,
    })}


def preview_discovery(service, values, *, frozen_provider=None):
    contract, attachments = _inputs(values)
    profile = values.get("profile", "balanced")
    question = values.get("question")
    provider = values.get("model_config_ref", "offline")
    if not isinstance(provider, str) or not provider:
        raise ValueError("模型供应商引用无效")
    if frozen_provider is None and provider != "offline":
        config = service.provider_configs.get(provider)
        if not service.allow_model_calls or config is None or not config.enabled or not config.endpoint or not config.credential_ref:
            raise ValueError("请选择已配置的模型供应商")
        frozen_provider = provider_binding(config, values.get("materials", []))
    elif provider == "offline" and values.get("materials"):
        raise ValueError("离线研究不接收模型资料")
    if contract is None:
        return prepare_discovery(question, profile, None, values.get("budget"), provider=frozen_provider).to_dict()
    panel = service.snapshots.build_panel(contract, attachments)
    as_of = datetime.fromisoformat(contract.observed_at[-1]).date().isoformat()
    policy = service.learning.store.policy(values.get("policy_ref"), as_of=as_of)
    frame = panel.frame
    fields = [field for field in frame.columns if frame[field].notna().all()]
    plan = prepare_discovery(question, profile, contract.snapshot_ref, values.get("budget"),
                             available_fields=fields, panel_ref=panel.fingerprint,
                             policy_ref=policy["policy_ref"], execution_ref=stable_digest(execution_identity(policy["effective"])),
                             risk_ref=stable_digest(StrategyCatalog().risk_rules()), provider=frozen_provider).to_dict()
    if plan["status"] == "READY" and SourceVersionStore(service.root).path.exists():
        ids = {ref["item_id"] for ref in plan["knowledge_refs"]}
        ids.update(item_id for defaults in _ROLE_BASE.values() for item_id in defaults)
        with open_knowledge(ResearchAssetCatalog.packaged()) as knowledge:
            bibliography = [source for item_id in sorted(ids)
                            for source in knowledge.resolve(item_id)["source_refs"]]
        sources = source_policy(service.root, [
            *research_source_refs(plan["target_kind"], plan["target_id"]), *bibliography,
        ])
        if sources["versions"]:
            for key in ("fingerprint", "blueprint_id", "schema_version"):
                plan.pop(key)
            source_budget = DiscoveryBudget(**plan.pop("budget"))
            plan["source_versions"] = sources["versions"]
            if sources["blocked"]:
                plan.update(status="SOURCE_REVIEW_REQUIRED", source_review_href="/research/workbench#qw-sources")
                plan.pop("candidates")
            plan = StudyBlueprint.freeze(plan, source_budget).to_dict()
    if frozen_provider and plan.get("target_kind") == "FACTOR":
        raise ValueError("冻结因子研究使用离线分析，以隔离样本外窗口")
    execution_gaps = []
    if plan.get("target_kind") == "FACTOR":
        missing_prices = sorted({"open", "high", "low", "volume"} - set(fields))
        execution_gaps.extend(f"{field}：缺少纸面检验所需的有效价格或成交字段" for field in missing_prices)
    if plan["status"] == "READY" and (panel.status != "READY" or len(contract.observed_at) < 50 or execution_gaps):
        for key in ("candidates", "fingerprint", "blueprint_id", "schema_version"):
            plan.pop(key, None)
        budget = DiscoveryBudget(**plan.pop("budget"))
        plan.update(status="NEEDS_DATA", repair_href="/settings/data-connections",
                    readiness_gaps=[*panel.audit["reasons"], *execution_gaps,
                                    *(["研究需要至少50个观察点"] if len(contract.observed_at) < 50 else [])])
        return StudyBlueprint.freeze(plan, budget).to_dict()
    return plan


def run_discovery(service, values):
    if not isinstance(values, dict):
        raise TypeError("研究提交需要对象")
    options = dict(values)
    if options.pop("authorized", None) is not True:
        raise ValueError("请明确授权运行此研究计划")
    model_authorization = options.pop("allow_model_calls", False)
    if options.get("model_config_ref", "offline") != "offline" and model_authorization is not True:
        raise ValueError("请明确授权模型调用")
    expected = options.pop("blueprint_fingerprint", None)
    key = _ref(options.pop("idempotency_key", None), "idempotency_key")
    blueprint = preview_discovery(service, options)
    if blueprint["fingerprint"] != expected:
        raise ValueError("数据、方法或政策已改变，请重新预览")
    if blueprint["status"] != "READY":
        return {"blueprint": blueprint, "job": None}
    # Revalidate the same serialized options on the worker. Caller data cannot
    # supply formulas, source references, or policy edits.
    options["policy_ref"] = blueprint["cost_policy_ref"]
    binding = {"blueprint": blueprint, "inputs": {key: value for key, value in options.items() if key != "materials"}}
    if len(encode(binding).encode()) > 32768:
        raise ValueError("冻结面板和计划超过提交预算，请缩小窗口")
    request_fields = {name: options[name] for name in ("contract", "question")}
    request_fields["attachment_refs"] = options.get("attachment_refs", [])
    request_fields["idempotency_key"] = key
    if blueprint["target_kind"] == "STRATEGY":
        from .strategy_jobs import prepare_strategy

        request, strategy = prepare_strategy(service, {
            **request_fields, "template_id": blueprint["target_id"], "policy_ref": blueprint["cost_policy_ref"],
        })
        if blueprint["provider"] is not None:
            request.update(model_config_ref=options["model_config_ref"], materials=options.get("materials", []),
                           allow_model_calls=True)
        job = service.submit(request, strategy_study=strategy, discovery=binding)
        return {"blueprint": blueprint, "job": job}
    if blueprint["cost_policy_ref"] != stable_digest(local_research_policy()):
        raise ValueError("冻结因子研究只接受基准政策")
    study = service.create_library_factor_study({
        **request_fields, "factor_id": blueprint["target_id"], "factor_version": blueprint["method_version"],
    }, discovery=binding)
    return {"blueprint": blueprint, "study_id": study["study_id"], "job": study["validation_job"]}


class DiscoveryDriver:
    """Deterministic role checks. Public methods are background, not observations."""

    def __init__(self, request, blueprint, index, *, available_fields=()):
        self.blueprint = blueprint
        self.packs = {}
        role_fields = {
            "fundamentals": FIELD_PACKS["financials"], "technical": FIELD_PACKS["prices"],
            "sentiment": ("sentiment_score",), "news": ("event_type", "news_count"), "learning": (),
        }
        for role, domains in _ROLE_TOPICS.items():
            ids = dict.fromkeys((*_ROLE_BASE[role], *(
                ref["item_id"] for ref in blueprint["knowledge_refs"] if ref["domain"] in domains
            )))
            entries = []
            for item_id in ids:
                item = index.resolve(item_id)
                entries.append({
                    "item_id": item_id, "digest": stable_digest(item), "source_ids": item["source_ref_ids"],
                    "counterexamples": [{"id": row["counterexample_id"], "correction": row["correction"]}
                                        for row in item["counterexamples"]], "title": item["title"],
                })
            pack = {"role": role, "items": entries,
                    "verified_field_names": sorted(set(available_fields).intersection(role_fields[role]))}
            if role in {"technical", "learning"}:
                pack["method"] = {"ids": blueprint["method_refs"], "digest": blueprint["method_manifest_digest"],
                                  "design": blueprint["split_spec"], "falsification": blueprint["falsification"]}

            pack["ref"] = "discovery-evidence:" + stable_digest({
                "run": request.run_id, "blueprint": blueprint["fingerprint"], "pack": pack})
            if len(encode(pack).encode()) > blueprint["budget"]["max_context_bytes"]:
                raise ValueError("角色知识包超过上下文预算")
            self.packs[role] = pack

    def background(self, role):
        if role == "research":
            return {"roles": list(self.packs.values()), "hypothesis": self.blueprint["hypothesis"],
                    "falsification": self.blueprint["falsification"], "focus": self.blueprint["profile_focus"]}
        return self.packs[role]

    def propose(self, request, context):
        role = context["analyst_role"]
        pack = self.packs[role]
        if len(encode({"context": context, "background": pack}).encode()) > self.blueprint["budget"]["max_context_bytes"]:
            from .contracts import FailureKind

            return DriverResult(failure_kind=FailureKind.RESOURCE_LIMIT)
        fields = pack["verified_field_names"]
        status = "READY" if fields or role == "learning" else "INSUFFICIENT_DATA"
        last = pack["items"][-1]
        focus = (f"已核对时点字段：{'、'.join(fields)}。仅能据此界定本轮分析范围。"
                 if fields and role in {"fundamentals", "news", "sentiment"} else _ROLE_FOCUS[role])
        if role == "learning":
            focus = pack["method"]["falsification"]
        observation = f"{focus} 对照「{last['title']}」的限制：{last['counterexamples'][0]['correction']}"
        return DriverResult(reports=(AgentReport(
            role=role, status=status, claims=(observation,), evidence_refs=(pack["ref"],),
            limitations=("引用用于设计与反证；属于来源定义，不能证实本次金融假设。",),
            model_ref="offline/discovery-v1",
        ),), research_plan=replace(request.research_plan, hypotheses=(self.blueprint["hypothesis"],)))

    def evidence(self, reports, product, *, calls=(), synthesis=None):
        roles = [{"role": row.role, "status": row.status, "observations": list(row.claims),
                  "evidence_refs": list(row.evidence_refs), "knowledge": self.packs[row.role]}
                 for row in reports]
        factor = self.blueprint["target_kind"] == "FACTOR"
        phase = product["phase"] if factor else "PAPER_SIMULATION"
        evidence_ref = product["fingerprint"]
        if factor:
            completed = product["library_validation"]["status"] == "COMPLETE"
            observation = product.get("test_evaluation") or {}
        else:
            completed = product["result"]["status"] == "COMPLETE"
            observation = product["result"]
        note = ("等待显式冻结和样本外测试。" if phase == "VALIDATION" and completed else
                "检验留有数据或方法缺口，保留为反例与复查建议。" if not completed else
                "复核计费后表现和反证条件，将纸面记录作为待审核知识建议。")
        return {"blueprint": self.blueprint, "roles": roles,
                "unresolved_views": [row["role"] for row in roles if row["status"] != "READY"],
                "hypotheses_used": 1 + len((synthesis or {}).get("hypotheses", ())),
                "outbound_model_calls": sum(row["dispatched"] for row in calls),
                "disagreements": list((synthesis or {}).get("disagreements", ())),
                "review_status": "REVIEW_REQUIRED",
                "product_ref": evidence_ref, "phase": phase,
                "paper_observation": {"status": observation.get("status", "NOT_YET_OBSERVED"),
                                      "product_ref": evidence_ref},
                "knowledge_suggestion": note}

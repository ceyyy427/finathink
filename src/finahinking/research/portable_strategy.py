"""Replay published paper strategies and frozen validation plans without source-workspace state."""

from datetime import date

from finahinking.data.panel_contracts import PanelContract, panel_time, reference
from finahinking.data.research_fields import ResearchFieldStore
from finahinking.data.research_panel import ResearchPanel

from .contracts import stable_digest
from .job_queue import _ref
from .portable_inputs import PortableUnavailable, freeze_inputs, rebuild_inputs
from .portable_knowledge import knowledge_items as _knowledge_items
from .portable_knowledge import knowledge_refs as _knowledge_refs
from .reproduction import _policy_supported
from .research_identity import execution_identity
from .source_updates import source_policy
from .strategy_catalog import StrategyCatalog, bind_parameters
from .strategy_jobs import VERSION as BINDING_VERSION
from .strategy_runner import VERSION as RUNNER_VERSION
from .strategy_runner import execute_strategy
from .strategy_settlement import HISTORICAL_FIXTURES, StrategySettlementService, _observed_scope
from .strategy_validation_service import STUDIO_FIELDS, StrategyValidationService, evaluate
from .strategy_validation_service import VERSION as STUDIO_VERSION


def _keys(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError("策略复算材料字段不一致")


def _refs(template, rules):
    refs = [*template["source_refs"], *(r for rule in rules for r in rule["source_refs"])]
    unique = {stable_digest(r): r for r in refs}
    return [unique[key] for key in sorted(unique)]


def freeze_source(app, run_id):
    """Resolve a scoped committed publication and project only its contract-bound inputs."""
    try:
        source = StrategySettlementService(app).replay_inputs(run_id)
    except (OSError, ValueError) as exc:
        cause = exc
        while cause is not None:
            if isinstance(cause, FileNotFoundError):
                raise PortableUnavailable("STRATEGY_SOURCE_MATERIAL_MISSING") from exc
            cause = cause.__cause__
        raise
    binding = source["binding"]
    snapshot, panel = source["snapshot"], source["panel"]
    contract = panel.contract
    attachments = ResearchFieldStore(app.snapshots).load_for_snapshot(snapshot, binding["attachment_refs"])
    data = freeze_inputs(snapshot, attachments, instruments=contract.instrument_ids,
                         through=contract.observed_at[-1], contract=contract)
    catalog = StrategyCatalog()
    template = catalog.template(binding["template_id"])
    refs = _refs(template, catalog.risk_rules())
    entry = app.runs.get_run(run_id)
    if entry["report_status"] != "AVAILABLE":
        raise PortableUnavailable("STRATEGY_REPORT_UNAVAILABLE")
    original = entry["manifest"]["source_snapshot"]
    reproduction = original.get("reproduction") or {}
    knowledge = _knowledge_items(original.get("discovery"))
    if (reproduction.get("policy") != source["policy"] or not reproduction.get("identity")
            or original.get("policy_lineage", {}).get("available_at") != source["policy_available_at"]):
        raise PortableUnavailable("STRATEGY_EXECUTION_IDENTITY_MISSING")
    return {
        "schema_version": "portable-strategy.v1", "binding": binding, "data": data,
        "publication": {k: source[k] for k in ("run_id", "job_id", "as_of", "published_at",
                                               "report_digest", "ledger_digest")},
        "runtime": reproduction["identity"], "runner_version": source["result"]["schema_version"],
        "template_version": template["version"], "source_refs": refs,
        "knowledge_items": knowledge, "source_review": source_policy(app.root, [*refs, *_knowledge_refs(knowledge)]),
        "policy": source["policy"], "policy_available_at": source["policy_available_at"],
        "panel_content_digest": stable_digest(panel.to_dict()),
        "result_digest": stable_digest(source["result"]),
    }


def _bindings(frozen):
    _keys(frozen, ("schema_version", "binding", "data", "publication", "runtime", "runner_version",
                   "template_version", "source_refs", "knowledge_items", "source_review", "policy", "policy_available_at",
                   "panel_content_digest", "result_digest"))
    if frozen["schema_version"] != "portable-strategy.v1":
        raise PortableUnavailable("STRATEGY_VERSION_MISMATCH")
    binding, pub = frozen["binding"], frozen["publication"]
    _keys(binding, ("version", "template_id", "template_digest", "risk_digest", "contract",
                    "attachment_refs", "parameters", "panel_ref", "policy_ref", "policy_digest"))
    _keys(pub, ("run_id", "job_id", "as_of", "published_at", "report_digest", "ledger_digest"))
    _ref(pub["run_id"], "run_id")
    _ref(pub["job_id"], "job_id")
    _ref(binding["template_id"], "template_id")
    for value in (pub["report_digest"], pub["ledger_digest"], binding["risk_digest"], binding["panel_ref"],
                  binding["template_digest"], binding["policy_digest"], binding["policy_ref"],
                  frozen["panel_content_digest"], frozen["result_digest"]):
        reference(value)
    contract = PanelContract.from_dict(binding["contract"])
    panel_time(pub["published_at"])
    if date.fromisoformat(pub["as_of"]) != panel_time(contract.observed_at[-1]).date():
        raise ValueError("策略报告日期与原面板截止时间不同")
    available = frozen["policy_available_at"]
    if available is not None:
        panel_time(available)
    data = frozen["data"]
    attachment_refs = binding["attachment_refs"]
    if (not isinstance(attachment_refs, list) or len(attachment_refs) > 32
            or attachment_refs != sorted({reference(ref) for ref in attachment_refs})
            or attachment_refs != [item["original_ref"] for item in data["attachments"]]
            or data["original_snapshot"] != contract.snapshot_ref or data["contract"] != binding["contract"]
            or stable_digest(frozen["policy"]) != binding["policy_digest"]
            or binding["policy_ref"] != frozen["policy"].get("learning_policy_ref", binding["policy_digest"])):
        raise ValueError("策略合同、附件或政策与原绑定不一致")
    identity = frozen["runtime"]
    _keys(identity, ("schema_version", "source_digest", "dependencies", "engine_version", "strategy_version"))
    _keys(identity["dependencies"], ("python", "numpy", "pandas"))
    reference(identity["source_digest"])
    for value in (identity["schema_version"], identity["engine_version"], identity["strategy_version"],
                  *identity["dependencies"].values(), binding["version"], frozen["runner_version"],
                  frozen["template_version"]):
        if not isinstance(value, str) or not 1 <= len(value) <= 100:
            raise ValueError("策略方法或依赖版本不明确")
    refs = frozen["source_refs"]
    if not isinstance(refs, list) or not 1 <= len(refs) <= 104:
        raise ValueError("策略来源引用无效")
    from .strategy_catalog import _validate_sources

    for ref in refs:
        _validate_sources([ref])
    knowledge = frozen["knowledge_items"]
    if not isinstance(knowledge, list) or len(knowledge) > 64:
        raise ValueError("策略知识来源数量无效")
    for item in knowledge:
        _keys(item, ("item_id", "digest"))
        _ref(item["item_id"], "item_id")
        reference(item["digest"])
    review = frozen["source_review"]
    _keys(review, ("versions", "blocked"))
    if (not isinstance(review["versions"], list) or len(review["versions"]) > 512
            or not isinstance(review["blocked"], list) or len(review["blocked"]) > 1024
            or any(not isinstance(item, str) or len(item) > 1024 for item in review["blocked"])):
        raise ValueError("策略来源审阅记录无效")
    for ref in review["versions"]:
        reference(ref)
    return contract


def validate_source(frozen):
    """Type-check the inert projection even when current methods or rights have changed."""
    _bindings(frozen)
    rebuild_inputs(frozen["data"])


def _strategy_panel(frozen, original):
    """Resolve original artifact references only after verifying the rebuilt rows and audit."""
    snapshot, fields, panel = rebuild_inputs(frozen["data"], with_panel=True)
    refs = frozen["binding"]["attachment_refs"]
    reverse = {item.fingerprint: ref for item, ref in zip(fields, refs, strict=True)}
    if len(reverse) != len(refs):
        raise PortableUnavailable("STRATEGY_ATTACHMENT_IDENTITY_AMBIGUOUS")
    audit = panel.audit
    audit["attachment_refs"] = sorted(reverse[ref] for ref in audit["attachment_refs"])
    for selection in audit["selections"]:
        selection["attachment_ref"] = reverse[selection["attachment_ref"]]
    original_ref = stable_digest({"contract": original.fingerprint, "attachments": sorted(refs),
                                  "semantics": "research-panel.v1"})
    if original_ref != frozen["binding"]["panel_ref"]:
        raise ValueError("原策略面板摘要与合同、附件不一致")
    rebound = ResearchPanel(panel.frame, audit, original, original_ref)
    if stable_digest(rebound.to_dict()) != frozen["panel_content_digest"]:
        raise PortableUnavailable("STRATEGY_PANEL_CONTENT_MISMATCH")
    return snapshot, rebound


def replay_source(app, frozen):
    """Re-execute the original account; never resolve state through a live policy or report store."""
    contract = _bindings(frozen)
    binding, pub, policy = frozen["binding"], frozen["publication"], frozen["policy"]
    if not _policy_supported(policy):
        raise PortableUnavailable("POLICY_UNSUPPORTED")
    if (binding["version"] != BINDING_VERSION or frozen["runner_version"] != RUNNER_VERSION
            or frozen["runtime"] != execution_identity(policy)):
        raise PortableUnavailable("STRATEGY_VERSION_MISMATCH")
    catalog = StrategyCatalog()
    template, rules = catalog.template(binding["template_id"]), catalog.risk_rules()
    if (binding["risk_digest"] != stable_digest(rules) or binding["template_digest"] != stable_digest(template)
            or frozen["template_version"] != template["version"]
            or frozen["source_refs"] != _refs(template, rules)):
        raise PortableUnavailable("STRATEGY_DEFINITION_MISMATCH")
    sources = [*frozen["source_refs"], *_knowledge_refs(frozen["knowledge_items"])]
    if frozen["source_review"]["blocked"] or source_policy(app.root, sources)["blocked"]:
        raise PortableUnavailable("SOURCE_WITHDRAWN")
    parameters = bind_parameters(template, binding["parameters"])
    if parameters != binding["parameters"]:
        raise ValueError("策略参数不完整")
    snapshot, panel = _strategy_panel(frozen, contract)
    classes = {asset: snapshot.provenance["asset_classes"].get(asset) for asset in contract.instrument_ids}
    result = execute_strategy(binding["template_id"], panel, parameters, asset_classes=classes, policy=policy)
    universe = template["benchmark"]["universe"]
    members = [asset for asset in contract.instrument_ids if universe == "all" or classes[asset] == universe.upper()]
    historical = snapshot.mode in HISTORICAL_FIXTURES
    observation_day = pub["as_of"] if historical else max(pub["as_of"], panel_time(pub["published_at"]).date().isoformat())
    return {
        **pub, "ledger_digest": stable_digest(result["ledger"]), "binding": binding,
        "snapshot_ref": contract.snapshot_ref, "snapshot": snapshot, "panel": panel, "template_id": binding["template_id"],
        "asset_scope": _observed_scope(members, classes), "instruments": contract.instrument_ids,
        "asset_classes": classes, "benchmark_instruments": members, "benchmark_definition": template["benchmark"],
        "execution_mode": template["execution_mode"], "data_mode": snapshot.mode, "result": result,
        "strategy_status": result["status"], "policy": policy, "policy_ref": binding["policy_ref"],
        "policy_available_at": frozen["policy_available_at"], "observation_date": observation_day,
        "availability_basis": "HISTORICAL_FIXTURE" if historical else "PUBLISHED_REPORT",
        "report_url": f"/research/{pub['run_id']}/report/complete",
    }


def _products(result):
    return {"strategy_ledger": result["ledger"], "strategy_benchmark": result["benchmark"],
            "strategy_decisions": result["decisions"], "strategy_result": {
                k: v for k, v in result.items() if k not in {"ledger", "benchmark", "decisions"}
            }}


def _validation_products(result):
    return {"strategy_validation_plan": result["plan"], "strategy_walkforward": result["walkforward"],
            "strategy_portfolio": result["portfolio"]}


def freeze_run(app, run_id, analytics, product):
    if product == "strategy_study":
        source = freeze_source(app, run_id)
        study = analytics[product]
        if (study["binding"] != source["binding"] or stable_digest(study["result"]) != source["result_digest"]
                or study["fingerprint"] != stable_digest({"binding": study["binding"], "result": study["result"]})):
            raise ValueError("策略产物与原报告不一致")
        return {"inputs": {"source": source}, "expected": _products(study["result"])}
    if product != "strategy_validation":
        raise ValueError("未知策略产物")
    evidence = analytics[product]
    plan = evidence["plan"]
    saved = StrategyValidationService(app).load(plan["plan_fingerprint"])
    if saved != plan:
        raise ValueError("验证报告与原工作区冻结方案不一致")
    from .application_service import verify_research_receipt
    from .research_differences import validation_comparison_summary

    with app.queue._connect() as db:
        jobs = db.execute(
            """SELECT j.job_id FROM jobs j JOIN request_snapshots r ON r.task_ref=j.task_ref
               WHERE j.status='completed' AND json_extract(r.payload,'$.run_id')=?
               AND json_extract(r.payload,'$.research_plan.validation_spec.scope_id')=?
               AND json_type(r.payload,'$.research_plan.validation_spec.strategy_validation')='object'""",
            (run_id, app.scope_id),
        ).fetchall()
    if len(jobs) != 1:
        raise PortableUnavailable("STRATEGY_STUDIO_PUBLICATION_MISSING")
    job = app.queue.get(jobs[0][0])
    verified = verify_research_receipt(app.root, app.queue, job, job.result_ref)
    request = verified["request"]
    frozen_job = request.research_plan.validation_spec[product]
    source = freeze_source(app, plan["run_id"])
    binding = source["binding"]
    if (verified["manifest"]["source_snapshot"].get(product) != validation_comparison_summary(plan)
            or request.snapshot_ref != plan["snapshot"] or request.previous_policy_fingerprint != binding["policy_ref"]
            or frozen_job != {"plan_ref": plan["plan_fingerprint"], "source_run": plan["run_id"],
                              "decision_at": binding["contract"]["observed_at"][-1]}):
        raise ValueError("冻结方案与原任务、已发表验证报告不一致")
    publication = {"run_id": request.run_id, "job_id": job.job_id,
                   "report_digest": stable_digest(verified["manifest"])}
    return {"inputs": {"source": source, "plan": plan, "original_scope": app.scope_id, "publication": publication},
            "expected": _validation_products(evidence)}


def _validate_plan(inputs):
    plan, scope, source = inputs["plan"], inputs["original_scope"], inputs["source"]
    _ref(scope, "original_scope")
    publication = inputs["publication"]
    _keys(publication, ("run_id", "job_id", "report_digest"))
    _ref(publication["run_id"], "run_id")
    _ref(publication["job_id"], "job_id")
    reference(publication["report_digest"])
    _keys(plan, STUDIO_FIELDS | {"method", "evaluation_kind", "template_id", "source", "options",
                                 "parameter", "parameter_values", "objective", "selection_rule",
                                 "stability_rule", "account_initialization", "warmup", "windows", "scenarios",
                                 "evaluation_units", "max_evaluation_rows"})
    _keys(plan["source"], ("run_id", "report_digest", "ledger_digest", "snapshot_ref", "policy_ref",
                           "data_mode", "panel_ref", "binding_digest", "policy_digest", "policy_available_at",
                           "template_digest", "asset_classes_digest", "observations", "assets"))
    _keys(plan["options"], ("train_size", "test_size", "stability_tolerance", "fee_bps", "slippage_bps",
                            "max_volume_participation", "parameter_values"))
    if not isinstance(plan["windows"], list) or len(plan["windows"]) != 3:
        raise ValueError("冻结验证窗口数量无效")
    for row in plan["windows"]:
        _keys(row, ("index", "train_start", "train_end", "test_start", "test_end", "train_observations",
                    "test_observations"))
    if not isinstance(plan["scenarios"], list) or not 1 <= len(plan["scenarios"]) <= 4:
        raise ValueError("冻结执行场景数量无效")
    for row in plan["scenarios"]:
        _keys(row, ("id", "starting_cash", "fee_bps", "slippage_bps", "max_volume_participation"))
    expected_ref = stable_digest([scope, {k: v for k, v in plan.items() if k != "plan_fingerprint"}])
    pub = source["publication"]
    if (plan["plan_fingerprint"] != expected_ref or plan["run_id"] != pub["run_id"]
            or plan["snapshot"] != source["data"]["original_snapshot"]
            or plan["original_report"] != pub["report_digest"] or plan["original_ledger"] != pub["ledger_digest"]):
        raise ValueError("验证方案与原策略来源不一致")
    from .strategy_attribution import normalize_portfolio_options

    if normalize_portfolio_options(plan["portfolio"]) != plan["portfolio"]:
        raise ValueError("验证方案的组合配置不完整")


def validate_run(inputs, product):
    if product not in {"strategy_study", "strategy_validation"}:
        raise ValueError("未知策略产物")
    _keys(inputs, ("source",) if product == "strategy_study" else ("source", "plan", "original_scope", "publication"))
    validate_source(inputs["source"])
    if product == "strategy_validation":
        _validate_plan(inputs)


def replay_run(app, inputs, product):
    if product not in {"strategy_study", "strategy_validation"}:
        raise ValueError("未知策略产物")
    _keys(inputs, ("source",) if product == "strategy_study" else ("source", "plan", "original_scope", "publication"))
    if product == "strategy_validation":
        _validate_plan(inputs)
        if inputs["plan"]["studio_version"] != STUDIO_VERSION:
            raise PortableUnavailable("STRATEGY_STUDIO_VERSION_MISMATCH")
    source = replay_source(app, inputs["source"])
    if product == "strategy_study":
        return _products(source["result"])
    if (source["ledger_digest"] != inputs["plan"]["original_ledger"]
            or stable_digest(source["result"]) != inputs["source"]["result_digest"]):
        raise PortableUnavailable("STRATEGY_ORIGINAL_LEDGER_MISMATCH")
    return _validation_products(evaluate(inputs["plan"], source))

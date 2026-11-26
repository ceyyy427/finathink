"""Replay a previously published frozen factor test from bounded portable evidence."""

from __future__ import annotations

import re
from types import SimpleNamespace

import pandas as pd

from finahinking.data.panel_contracts import PanelContract, panel_time, reference
from finahinking.data.research_fields import ResearchFieldStore
from finahinking.data.research_panel import build_research_panel

from .contracts import stable_digest, to_jsonable
from .factor_catalog_loader import get_research_recipe
from .factor_library import FactorLibrary
from .factor_library_workbench import VERSION as LIBRARY_VERSION
from .factor_study_suite import POLICY, run_factor_study_suite
from .factor_study_suite import VERSION as SUITE_VERSION
from .factor_validation import factor_validation_policy
from .factor_validation_sample import ValidationSample
from .factor_workbench import METHOD_VERSION, compute_study, study_frame
from .portable_inputs import PortableUnavailable, freeze_inputs, rebuild_inputs
from .portable_knowledge import knowledge_items, knowledge_refs
from .reports import redact_public_payload
from .snapshot_tools import SnapshotResearchTools
from .source_updates import source_policy
from .temporal_validation import purged_walk_forward

_VERSION = "portable-factor-test.v1"
_RECEIPT = {
    "study_id", "spec_ref", "snapshot_ref", "validation_run", "test_run", "validation_publication",
    "test_publication", "validation_result", "test_result", "candidate_id", "candidate_fingerprint",
    "formula_ref", "test_period", "sample_period", "schedule_ref", "data_ref",
    "policy_fingerprint", "experiment_ref", "settings_ref", "as_of", "knowledge_ref",
}
_PIN = ("card_id", "source_id", "commit", "path", "sha256", "license_sha256")
_EVALUATION = (
    "candidate_id", "instrument", "status", "oos_status", "sample_size", "coverage", "information_coefficient",
    "information_ratio", "quantile_returns", "long_short_return", "turnover", "transaction_cost", "decay",
    "oos_information_coefficient",
)


def _require(ok, message):
    if not ok:
        raise ValueError(message)


def _keys(mapping, names):
    _require(isinstance(mapping, dict) and set(mapping) == set(names), "冻结因子包的字段不完整或多余。")


def _identifier(value):
    _require(isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}", value),
             "冻结因子标识无效。")


def _pin(card):
    return {**{key: card[key] for key in _PIN}, "card_digest": stable_digest(card)}


def _library_formula(library, factor_id):
    factor = library.spec(factor_id)
    return {"id": factor_id, "expression": factor.formula, "version": factor.version, "window": factor.window,
            "implementation_ref": factor.implementation_fingerprint,
            "formula_fingerprint": library.get(factor_id)["formula_fingerprint"],
            "source_ids": list(factor.source_resource_ids)}


def _recipe_formula(recipe, window):
    return {"id": recipe.recipe_id, "expression": recipe.expression(window), "version": recipe.version,
            "window": window, "implementation_ref": recipe.implementation_fingerprint,
            "formula_fingerprint": stable_digest(recipe.expression(window)), "source_ids": recipe.to_dict()["source_ids"]}


def _resolve_formulas(inputs, root):
    formulas = inputs["formulas"]
    try:
        if inputs["workflow"] == METHOD_VERSION:
            expected = [_recipe_formula(get_research_recipe(row["id"]), row["window"]) for row in formulas]
            cards = []
        else:
            library = FactorLibrary()
            expected = [_library_formula(library, row["id"]) for row in formulas]
            ids = {source for row in expected for source in row["source_ids"]}
            cards = sorted((row for row in library.cards() if row["card_id"] in ids), key=lambda row: row["card_id"])
    except (KeyError, ValueError) as exc:
        raise PortableUnavailable("FACTOR_FORMULA_VERSION_UNAVAILABLE") from exc
    if expected != formulas:
        raise PortableUnavailable("FACTOR_FORMULA_VERSION_UNAVAILABLE")
    if [_pin(card) for card in cards] != inputs["sources"]:
        raise PortableUnavailable("FACTOR_SOURCE_VERSION_UNAVAILABLE")
    if source_policy(root, [*cards, *knowledge_refs(inputs["knowledge_items"])])["blocked"]:
        raise PortableUnavailable("SOURCE_WITHDRAWN")


def _published(app, study, phase):
    from .application_service import verify_research_receipt
    from .job_queue import JobStatus

    job = app.queue.get(study[f"{phase}_job_id"])
    _require(job.status is JobStatus.COMPLETED, "最终测试与验证都须有已发表回执。")
    verified = verify_research_receipt(app.root, app.queue, job, job.result_ref)
    report = verified["manifest"]["source_snapshot"]["factor_study"]
    _require(report["phase"] == phase.upper() and report["study_id"] == study["study_id"]
             and report["spec_ref"] == study["spec_ref"] and report["snapshot_ref"] == study["spec"]["snapshot_ref"]
             and report["charter"] == redact_public_payload(study["spec"]), "因子回执与研究章程不一致。")
    publication = verified["manifest"]["source_snapshot"]
    return report, verified["manifest"]["run_id"], job.result_ref.split(":", 1)[1], publication.get("discovery")


def _review(app, study, run_id):
    if study["review_decision"] is None:
        return None
    decision, reviewer, study_id = study["review_decision"], study["reviewer_id"], study["study_id"]
    event = None
    if "multi_factor_discovery" in study["spec"]:
        # Resolve the original immutable event even if this use scope was monitored or retired later.
        key = "study-review-" + stable_digest([study_id, decision, reviewer])[:32]
        event_ref = "factor-use-" + stable_digest([app.scope_id, key])
        with app.queue._connect() as db:
            row = db.execute("SELECT rowid AS sequence,* FROM factor_use_events WHERE scope_id=? AND event_ref=?",
                             (app.scope_id, event_ref)).fetchone()
            _require(row is not None, "原研究使用审核记录缺失。")
            event = app.factor_uses._decode(row)
    return {"study_id": study_id, "run_id": run_id, "candidate_id": study["candidate_id"],
            "decision": decision, "reviewer": reviewer, "use_event": event}


def _check_review(review, receipt):
    if review is None:
        return
    _keys(review, {"study_id", "run_id", "candidate_id", "decision", "reviewer", "use_event"})
    _require(review["study_id"] == receipt["study_id"] and review["run_id"] == receipt["test_run"]
             and review["candidate_id"] == receipt["candidate_id"] and review["decision"] in {"APPROVE_FOR_RESEARCH", "REJECT"},
             "审核记录与冻结研究不一致。")
    _identifier(review["reviewer"])
    event = review["use_event"]
    if event is not None:
        from .factor_lifecycle import FactorUseDecision

        _keys(event, {"scope_id", "event_ref", "sequence", "created_at", "decision"})
        decision = FactorUseDecision(**event["decision"])
        _identifier(event["scope_id"])
        panel_time(event["created_at"])
        _require(type(event["sequence"]) is int and event["sequence"] > 0
                 and event["event_ref"] == "factor-use-" + stable_digest([event["scope_id"], decision.idempotency_key])
                 and (decision.decision, decision.reviewer, decision.run_ref, decision.factor_id)
                 == (review["decision"], review["reviewer"], review["run_id"], review["candidate_id"])
                 and list(decision.time_domain) == receipt["test_period"], "归档审核事件未绑定原冻结结果。")


def _canonical_setup(study, validation, test):
    spec = study["spec"]
    record = next((row for row in validation["experiments"] if row["experiment_id"] == study["candidate_id"]), None)
    _require(record and record["digest"] == study["candidate_fingerprint"] and record["failure_reason"] is None
             and test["selected_candidate"] == study["candidate_id"], "最终测试未绑定已冻结的候选。")
    parameters = record["parameters"]
    if spec["version"] == METHOD_VERSION:
        recipe = get_research_recipe(spec["recipe_id"])
        formulas = [_recipe_formula(recipe, window) for window in spec["windows"]]
        chosen = _recipe_formula(recipe, parameters["window"])
        _require(parameters["expression"] == recipe.formula and recipe.version == spec["recipe_version"]
                 and chosen in formulas, "验证参数与已登记公式不一致。")
        if "snapshot_factor" in spec:
            pilot = spec["snapshot_factor"]
            _require((pilot["formula_fingerprint"], pilot["formula"]) ==
                     (chosen["implementation_ref"], chosen["expression"]), "自有快照的公式身份不一致。")
        settings = {key: spec[key] for key in ("label_horizon", "embargo", "n_splits", "cost")}
        settings["utc"] = "snapshot_factor" in spec
        return formulas, chosen, settings, [], parameters

    library = FactorLibrary()
    declared = spec["factors"] if spec["version"] == SUITE_VERSION else [spec["factor_spec"]]
    formulas = [_library_formula(library, row["factor_id"]) for row in declared]
    _require(all(row == library.spec(row["factor_id"]).to_dict() for row in declared), "已发表的公式版本不可用。")
    chosen = next((row for row in formulas if row["id"] == parameters["factor_id"]), None)
    _require(chosen is not None, "测试使用了未登记的公式。")
    settings = {"policy": spec["validation_policy"]}
    if spec["version"] == SUITE_VERSION:
        trial, = test["suite_validation"]["trials"]
        _require(trial["factor_id"] == chosen["id"] == test["suite_validation"]["frozen_test_ref"]
                 and trial["formula_fingerprint"] == chosen["formula_fingerprint"], "最终测试的试验与公式不一致。")
        settings.update(direction=trial.get("direction", settings["policy"]["direction"]),
                        data_mode=spec["data_mode"], sample=spec.get("sample_spec"),
                        sample_fingerprint=spec.get("sample_fingerprint"), sample_content=spec.get("sample_content_digest"))
        if "multi_factor_discovery" in spec:
            from .factor_discovery import frozen_discovery_candidate

            _require(frozen_discovery_candidate(study, validation) == test["freeze_receipt"], "多候选冻结回执不一致。")
    ids = {source for row in formulas for source in row["source_ids"]}
    cards = sorted((row for row in library.cards() if row["card_id"] in ids), key=lambda c: c["card_id"])
    if "multi_factor_discovery" in spec and cards != spec["multi_factor_discovery"]["sources"]:
        raise PortableUnavailable("FACTOR_SOURCE_VERSION_UNAVAILABLE")
    return formulas, chosen, settings, [_pin(card) for card in cards], parameters


def _evaluation(result):
    return {key: result[key] for key in _EVALUATION if key in result}


def _scorecard(trial):
    metrics = trial["metrics"]
    return {"factor_id": trial["factor_id"], "status": trial["status"], "failure_reason": trial["failure_reason"],
            "missing_reasons": trial["missing_reason"], "data_mode": trial["data_mode"],
            "scorecard": None if metrics is None else {
                key: value for key, value in metrics.items() if key not in {"panel_fingerprint", "fingerprint"}
            }}


def _products(result):
    if "suite_validation" in result:
        trial, = result["suite_validation"]["trials"]
        return {"factor_study": _scorecard(trial)}
    product = {"candidate_id": result["selected_candidate"], "evaluation": _evaluation(result["test_evaluation"]),
               "metrics": result["test_summary"]["metrics"], "failure_reason": result["test_summary"]["failure_reason"]}
    if "library_validation" in result:
        evidence = result["library_validation"]
        product.update(per_asset=[_evaluation(row) for row in evidence["evaluations"]],
                       missing_reasons=evidence["missing_reason"], status=evidence["status"])
        ledger = evidence["backtest"]
        if ledger is not None:
            product["backtest"] = {key: ledger[key] for key in ("metric_basis", "entries", "trades", "fee_total",
                                                                  "slippage_total", "fee_bps", "slippage_bps")}
            product["backtest"]["performance"] = ledger["performance"]["metrics"]
            product["backtest"]["train_thresholds"] = [
                {k: row[k] for k in ("asset", "threshold_scope", "threshold")} for row in ledger["quant_runs"]]
    return {"factor_study": to_jsonable(product)}


def freeze_run(app, run_id, analytics, product="factor_study"):
    test = analytics.get(product, {})
    if product != "factor_study" or test.get("phase") != "TEST":
        raise PortableUnavailable("FACTOR_TEST_NOT_PUBLISHED")
    study = app.factor_studies.get(test["study_id"])
    spec = study["spec"]
    if spec["version"] not in {METHOD_VERSION, SUITE_VERSION, LIBRARY_VERSION}:
        raise PortableUnavailable("FACTOR_WORKFLOW_DEPENDENCIES")
    validation, validation_run, validation_publication, _ = _published(app, study, "validation")
    verified, test_run, test_publication, discovery = _published(app, study, "test")
    _require(verified == test and test_run == run_id and study["test_key"] is not None, "最终测试回执不属于此报告。")
    formulas, chosen, settings, sources, parameters = _canonical_setup(study, validation, test)
    winner = next(row for row in validation["experiments"] if row["experiment_id"] == study["candidate_id"])
    candidate_refs = [ref for candidate in spec.get("multi_factor_discovery", {}).get("candidates", [])
                      for ref in candidate["knowledge_refs"]]
    knowledge = knowledge_items(discovery, candidate_refs)
    snapshot = app.snapshots.load(spec["snapshot_ref"])
    contract = PanelContract.from_dict(spec["panel_contract"]) if "panel_contract" in spec else None
    try:
        attachments = ResearchFieldStore(app.snapshots).load_for_snapshot(snapshot, spec.get("attachment_refs", []))
    except ValueError as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            raise PortableUnavailable("FACTOR_ATTACHMENT_MISSING") from exc
        raise
    if contract is not None:
        instruments = contract.instrument_ids
        times = pd.DatetimeIndex(contract.observed_at)
    elif spec["version"] == SUITE_VERSION:
        instruments = [spec["instrument"]]  # Generator specification and content hash bind the full synthetic panel.
        times = ValidationSample(settings["sample"]).times
    else:
        instruments = [spec["instrument"]]
        times = study_frame(snapshot, spec["instrument"], spec["as_of"], utc=settings["utc"]).index
    # The last daily close may have been published later that day. The source worker checked the report's as-of.
    through = (max(row.available_at for row in snapshot.observations if row.instrument in instruments)
               if spec["version"] == METHOD_VERSION else spec["test_period"][-1])
    data = freeze_inputs(snapshot, attachments, instruments=instruments, through=through, contract=contract)
    receipt = {
        "study_id": study["study_id"], "spec_ref": study["spec_ref"], "snapshot_ref": spec["snapshot_ref"], "as_of": spec["as_of"],
        "validation_run": validation_run, "test_run": test_run,
        "validation_publication": validation_publication, "test_publication": test_publication,
        "validation_result": validation["fingerprint"], "test_result": test["fingerprint"],
        "candidate_id": study["candidate_id"], "candidate_fingerprint": study["candidate_fingerprint"],
        "formula_ref": stable_digest(chosen), "test_period": spec["test_period"], "sample_period": spec["sample_period"],
        "schedule_ref": stable_digest([stamp.isoformat() for stamp in times]), "data_ref": stable_digest(data),
        "policy_fingerprint": spec["policy_fingerprint"], "experiment_ref": stable_digest(parameters),
        "settings_ref": stable_digest(settings), "knowledge_ref": stable_digest(knowledge),
    }
    inputs = {"version": _VERSION, "workflow": spec["version"], "receipt": receipt, "data": data,
              "settings": settings, "formulas": formulas, "parameters": parameters, "sources": sources,
              "winner": winner, "knowledge_items": knowledge, "review": _review(app, study, test_run)}
    _resolve_formulas(inputs, app.root)
    validate_run(inputs, product)
    return {"inputs": inputs, "expected": _products(test)}


def _decode(inputs, product):
    _keys(inputs, {"version", "workflow", "receipt", "data", "settings", "parameters", "formulas", "sources", "review", "winner", "knowledge_items"})
    workflow = inputs["workflow"]
    _require(product == "factor_study" and inputs["version"] == _VERSION
             and workflow in {METHOD_VERSION, SUITE_VERSION, LIBRARY_VERSION}, "不支持的冻结因子包版本。")
    receipt, settings, formulas = inputs["receipt"], inputs["settings"], inputs["formulas"]
    _keys(receipt, _RECEIPT)
    for key in ("study_id", "candidate_id", "validation_run", "test_run"):
        _identifier(receipt[key])
    for key in _RECEIPT - {"study_id", "candidate_id", "validation_run", "test_run", "sample_period", "test_period", "as_of"}:
        reference(receipt[key])
    for key in ("test_period", "sample_period"):
        period = receipt[key]
        _require(isinstance(period, list) and len(period) == 2 and panel_time(period[0]) < panel_time(period[1]),
                 "因子样本时段无效。")
    _require(receipt["validation_run"] != receipt["test_run"] and receipt["validation_publication"] != receipt["test_publication"]
             and receipt["validation_result"] != receipt["test_result"], "验证与最终测试须来自不同的发表记录。")
    winner = inputs["winner"]
    keys = {"experiment_id", "parameters", "evaluation", "digest", "ic", "failure_reason"}
    if workflow == METHOD_VERSION:
        keys.update({"dataset_fingerprint", "config_digest", "research_fingerprint", "split", "ir", "turnover",
                     "decay", "folds", "p_value", "adjusted_p_value", "sample_ids", "limitations", "paper_only"})
    elif workflow == SUITE_VERSION:
        keys.add("hypothesis_status")
    _keys(winner, keys)
    _require(isinstance(winner, dict) and winner.get("experiment_id") == receipt["candidate_id"]
             and winner.get("digest") == receipt["candidate_fingerprint"] and winner["failure_reason"] is None
             and winner["parameters"] == inputs["parameters"], "原验证的冻结候选与参数不一致。")
    if workflow == METHOD_VERSION:
        # The v1 parameter grid hashes these exact inputs before adding display metrics and adjusted p-values.
        preimage = {key: winner[key] for key in ("parameters", "evaluation", "folds", "p_value", "dataset_fingerprint",
                                                  "config_digest", "research_fingerprint")}
        preimage = {"raw": stable_digest(preimage), "adjusted_p_value": winner["adjusted_p_value"],
                    "family_size": len(formulas)}
        _require(winner["evaluation"]["candidate_id"] == receipt["candidate_id"], "冻结试验编号不一致。")
    else:
        preimage = {key: value for key, value in winner.items() if key != "digest"}
    _require(stable_digest(preimage) == receipt["candidate_fingerprint"], "原验证的冻结候选摘要与内容不符。")
    knowledge = inputs["knowledge_items"]
    _require(isinstance(knowledge, list) and len(knowledge) <= 128 and stable_digest(knowledge) == receipt["knowledge_ref"],
             "冻结研究的知识条目不完整。")
    for item in knowledge:
        _keys(item, {"item_id", "digest"})
        _identifier(item["item_id"])
        reference(item["digest"])
    _check_review(inputs["review"], receipt)
    ref = inputs["data"]["original_snapshot"]
    _require(ref == receipt["snapshot_ref"] and receipt["data_ref"] == stable_digest(inputs["data"])
             and receipt["experiment_ref"] == stable_digest(inputs["parameters"])
             and receipt["settings_ref"] == stable_digest(settings), "冻结因子输入与回执不一致。")
    _require(isinstance(formulas, list) and 1 <= len(formulas) <= 24, "因子公式数量超限。")
    for formula in formulas:
        _keys(formula, {"id", "expression", "version", "window", "implementation_ref", "formula_fingerprint", "source_ids"})
        _identifier(formula["id"])
        _identifier(formula["version"])
        reference(formula["implementation_ref"])
        reference(formula["formula_fingerprint"])
        _require(type(formula["window"]) is int and 1 <= formula["window"] <= 252
                 and isinstance(formula["expression"], str) and 1 <= len(formula["expression"]) <= 4096
                 and isinstance(formula["source_ids"], list) and 1 <= len(formula["source_ids"]) <= 32,
                 "因子公式参数无效。")
        for source_id in formula["source_ids"]:
            _identifier(source_id)
    ids = [(row["id"], row["window"]) for row in formulas]
    _require(len(ids) == len(set(ids)), "冻结因子公式重复。")
    chosen = next((row for row in formulas if stable_digest(row) == receipt["formula_ref"]), None)
    _require(chosen is not None, "冻结候选缺少原公式。")
    pins = inputs["sources"]
    _require(isinstance(pins, list) and len(pins) <= 128, "来源引用超限。")
    for pin in pins:
        _keys(pin, {*_PIN, "card_digest"})
        for key in ("card_digest", "sha256", "license_sha256"):
            reference(pin[key])
        _identifier(pin["card_id"])
        _identifier(pin["source_id"])
        _require(isinstance(pin["commit"], str) and re.fullmatch("[0-9a-f]{40}", pin["commit"])
                 and isinstance(pin["path"], str) and re.fullmatch(r"[A-Za-z0-9_./+-]{1,256}", pin["path"])
                 and not pin["path"].startswith("/") and ".." not in pin["path"].split("/"), "来源路径或版本无效。")
    snapshot, attachments, contract = rebuild_inputs(inputs["data"])
    end = panel_time(receipt["test_period"][1])
    as_of = SnapshotResearchTools._boundary(receipt["as_of"])
    limit = as_of if workflow == METHOD_VERSION else min(end, as_of)
    _require(panel_time(inputs["data"]["through"]) <= limit, "冻结输入截止时间超出最终测试范围。")
    _require(all(row.timestamp <= end and row.available_at is not None and row.available_at <= limit
                 for row in snapshot.observations), "因子快照超出已发表的样本边界。")
    parameters = inputs["parameters"]
    candidate = receipt["candidate_id"]
    if workflow == METHOD_VERSION:
        _keys(settings, {"label_horizon", "embargo", "n_splits", "cost", "utc"})
        _keys(settings["cost"], {"basis", "fee_bps", "slippage_bps"})
        _keys(parameters, {"expression", "window"})
        _require(type(settings["utc"]) is bool and not pins and not attachments and contract is None
                 and len(snapshot.instruments) == 1 and len(formulas) <= 8
                 and all(row["id"] == chosen["id"] for row in formulas)
                 and chosen["window"] == parameters["window"] and type(parameters["window"]) is int
                 and isinstance(parameters["expression"], str) and len(parameters["expression"]) <= 4096
                 and candidate == f"experiment-{formulas.index(chosen) + 1:04d}", "冻结参数与原候选不一致。")
        for key, low, high in (("label_horizon", 1, 20), ("embargo", 0, 20), ("n_splits", 1, 4)):
            _require(type(settings[key]) is int and low <= settings[key] <= high, "因子测试切分参数越界。")
        for key in ("fee_bps", "slippage_bps"):
            _require(type(settings["cost"][key]) in (float, int) and 0 <= settings["cost"][key] <= 100, "费用超界。")
        _require(settings["cost"]["basis"] == "ex-post rank-turnover diagnostic, not portfolio net P&L",
                 "因子费用口径不匹配。")
        times = study_frame(snapshot, snapshot.instruments[0], receipt["as_of"], utc=settings["utc"]).index
    else:
        _require({pin["card_id"] for pin in pins} == {sid for row in formulas for sid in row["source_ids"]}
                 and len({pin["card_id"] for pin in pins}) == len(pins), "公式来源不完整。")
        if workflow == LIBRARY_VERSION:
            _keys(settings, {"policy"})
            _keys(parameters, {"factor_id", "window"})
            _require(contract is not None and len(formulas) == 1 and candidate == "experiment-library"
                     and parameters == {"factor_id": chosen["id"], "window": chosen["window"]},
                     "面板因子测试缺少冻结参数或面板。")
            _keys(settings["policy"], factor_validation_policy())
            factor_validation_policy(**settings["policy"])
        else:
            _keys(settings, {"policy", "direction", "data_mode", "sample", "sample_fingerprint", "sample_content"})
            _require(settings["policy"] == POLICY and type(settings["direction"]) is int
                     and settings["direction"] in (-1, 1) and candidate == chosen["id"]
                     and parameters.get("factor_id") == chosen["id"]
                     and len({row["id"] for row in formulas}) == len(formulas), "预登记因子组参数不匹配。")
            if set(parameters) != {"factor_id"}:
                _keys(parameters, {"factor_id", "direction", "version", "window", "trial_ref"})
                reference(parameters["trial_ref"])
                _require((parameters["direction"], parameters["window"], parameters["version"])
                         == (settings["direction"], chosen["window"], chosen["version"]), "多候选冻结参数不一致。")
            _require((settings["sample"] is None) == (contract is not None), "合成样例与用户面板不能混用。")
            if contract is None:
                _require(not attachments and settings["data_mode"] == "SAMPLE", "合成样例来源不一致。")
                sample = ValidationSample(settings["sample"])
                _require((settings["sample_fingerprint"], settings["sample_content"], ref)
                         == (sample.fingerprint, sample.content_digest, sample.anchor().fingerprint),
                         "合成样例与登记的数据摘要不一致。")
            else:
                mode = {"CAPTURED": "USER_DATA", "LIVE": "USER_DATA", "SYNTHETIC": "SAMPLE", "SAMPLE": "SAMPLE"}
                _require(settings["sample_fingerprint"] is None and settings["sample_content"] is None
                         and settings["data_mode"] == mode.get(snapshot.mode, "UNKNOWN"), "面板数据类型不符。")
        times = pd.DatetimeIndex(contract.observed_at) if contract is not None else sample.times
    split = purged_walk_forward(times, horizon=settings.get("label_horizon", 1), embargo=settings.get("embargo", 1),
                                n_splits=settings.get("n_splits", 2))
    _require(receipt["schedule_ref"] == stable_digest([t.isoformat() for t in times])
             and receipt["test_period"] == [times[split["validation_end"]].isoformat(), times[-1].isoformat()]
             and receipt["sample_period"] == [times[0].isoformat(), times[-1].isoformat()],
             "因子数据与原冻结样本切分不一致。")
    return snapshot, attachments, contract, chosen


def validate_run(inputs, product="factor_study"):
    _decode(inputs, product)


def replay_run(app, inputs, product="factor_study"):
    snapshot, attachments, contract, chosen = _decode(inputs, product)
    _resolve_formulas(inputs, app.root)
    workflow, receipt, settings = inputs["workflow"], inputs["receipt"], inputs["settings"]
    candidate, factor_id = receipt["candidate_id"], chosen["id"]
    if workflow == SUITE_VERSION:
        panel = (ValidationSample(settings["sample"]) if contract is None
                 else build_research_panel(snapshot, attachments, contract))
        policy = {**settings["policy"], "direction": settings["direction"]}
        suite = run_factor_study_suite(receipt["study_id"], [FactorLibrary().spec(factor_id)],
                                       panel, policy, phase="test", frozen_factor=factor_id,
                                       snapshot_ref=snapshot.fingerprint, policy_fingerprint=receipt["policy_fingerprint"],
                                       spec_ref=receipt["spec_ref"], data_mode=settings["data_mode"])
        return _products({"suite_validation": suite})

    instrument = snapshot.instruments[0] if contract is None else contract.instrument_ids[0]
    spec = {"version": workflow, "snapshot_ref": snapshot.fingerprint, "instrument": instrument,
            "question": "复算已发表的冻结因子测试", "as_of": receipt["as_of"],
            "sample_period": receipt["sample_period"], "test_period": receipt["test_period"],
            "split": {"train": 0.6, "validation": 0.2, "test": 0.2}, "policy_fingerprint": receipt["policy_fingerprint"]}
    if workflow == METHOD_VERSION:
        recipe = get_research_recipe(factor_id)
        _require(inputs["parameters"]["expression"] == recipe.formula, "原参数表达式不属于冻结公式。")
        spec.update({key: settings[key] for key in ("cost", "label_horizon", "embargo", "n_splits")})
        spec.update(recipe_id=factor_id, recipe_version=chosen["version"])
        if settings["utc"]:
            spec["snapshot_factor"] = {"formula_fingerprint": chosen["implementation_ref"]}
    else:
        spec.update(factor_spec=FactorLibrary().spec(factor_id).to_dict(), source_card_ids=chosen["source_ids"],
                    panel_contract=contract.to_dict(), instrument_ids=list(contract.instrument_ids),
                    panel_fingerprint=build_research_panel(snapshot, attachments, contract).fingerprint,
                    attachment_refs=[row.fingerprint for row in attachments], validation_policy=settings["policy"],
                    cost={key: settings["policy"][key] for key in ("fee_bps", "slippage_bps")})
    # The historical receipt authorizes replay of this candidate only. No new reservation or workspace review is made.
    study = {"study_id": receipt["study_id"], "spec_ref": receipt["spec_ref"], "spec": spec,
             "candidate_id": candidate, "candidate_fingerprint": receipt["candidate_fingerprint"],
             "test_key": receipt["test_publication"], "state": "TESTING"}
    validation = {"spec_ref": receipt["spec_ref"], "snapshot_ref": snapshot.fingerprint, "experiments": [inputs["winner"]],
                  "summaries": [], "ranking": [], "limitations": []}
    field_store = SimpleNamespace(load={row.fingerprint: row for row in attachments}.__getitem__)
    return _products(compute_study(snapshot, study, phase="test", validation=validation, research_fields_store=field_store))

"""Offline replay of the published nine-stage test of an accepted method proposal."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import asdict
from types import SimpleNamespace

from finahinking.data.panel_contracts import panel_time, reference
from finahinking.data.research_fields import RESEARCH_FIELDS, ResearchFieldStore

from .asset_catalog import ResearchAssetCatalog
from .asset_contracts import FactorSpec
from .contracts import stable_digest
from .factor_families import audit_family_inputs, family_for_spec
from .factor_inputs import FactorPanel, UniverseSpec, build_factor_panel
from .factor_proposals import proposal_from_handoff
from .factor_recipes import resolve_factor_spec
from .factor_validation import (
    FactorValidationGateway,
    FactorValidationRun,
    factor_validation_policy,
    run_factor_validation,
)
from .method_context import LoadedMethods
from .portable_history import factor_publication_proof, verified_factor_scope
from .portable_inputs import PortableUnavailable, freeze_inputs, rebuild_inputs
from .reports import redact_public_payload
from .snapshot_tools import SnapshotResearchTools
from .source_updates import source_policy

WORKFLOW = "factor-workbench.pilot.v1"
_VERSION = "portable-method-factor-test.v1"
_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}")
_METHOD_BINDING = {"handoff_ref", "artifact_ref", "package_digest", "context_digest", "methods_digest", "method_manifest_digest"}
_RECEIPT = {"study_id", "candidate_id", "candidate_fingerprint", "spec_ref", "snapshot_ref", "panel_ref", "attachment_ref",
            "as_of", "test_period", "policy_fingerprint", "validation_run", "test_run", "validation_publication",
            "test_publication", "validation_result", "test_result", "validation_binding", "test_binding",
            "validation_evaluation", "test_evaluation", "method_ref"}
_UPSTREAM = re.compile(r"(https://github\.com/[\w.-]+/[\w.-]+)/blob/([0-9a-f]{40})/(.+)")


def _check(ok):
    if not ok:
        raise ValueError("九步因子测试与原研究、方法或数据绑定不符。")


def _neutral(binding):
    identity = dict(binding)
    identity.pop("final_test_usage")
    identity["runtime_policy"] = {k: v for k, v in identity["runtime_policy"].items() if k != "phase"}
    return stable_digest(identity)


def _projection(run):
    def metric(value):
        if isinstance(value, list):
            return [metric(item) for item in value]
        if isinstance(value, dict):
            # Derived run/metric hashes are archived in the publication. Compare their underlying metrics with tolerance.
            return {k: metric(v) for k, v in value.items() if "fingerprint" not in k and k not in {"run_id", "quant_run_ref", "candidate_artifact_ref"}}
        return value

    return {"factor_study": {"candidate_id": run["bindings"]["factor_id"], "status": run["status"],
            "scope": run["reproduction_scope"], "steps": [
                {**{k: row[k] for k in ("stage_id", "status", "reason")}, "metrics": metric(row["metrics"])}
                for row in run["steps"]]}}


def _resource_pins(catalog, ids):
    pins, upstream = [], []
    for key in sorted(set(ids)):
        item = catalog.get_resource(key)
        catalog.read_text(key)
        review = catalog._approved_review(item)
        pins.append({"resource_id": key, "resource_ref": stable_digest(asdict(item)), "rights_ref": stable_digest(asdict(review))})
        if item.source_url and item.source_path is not None:
            match = _UPSTREAM.fullmatch(item.source_url)
            if not match or match[3] != item.source_path or not item.source_sha256:
                raise PortableUnavailable("METHOD_SOURCE_VERSION_UNAVAILABLE")
            upstream.append({"repository": match[1], "commit": match[2], "path": item.source_path, "sha256": item.source_sha256})
    return pins, upstream


def _source_check(app, method, factor):
    catalog = ResearchAssetCatalog.packaged()
    manifest = method["manifest"]
    try:
        resources = [r["resource_id"] for r in manifest["loaded"]]
        # Use the installed bodies only to verify the public manifest and original method fingerprint.
        loaded = LoadedMethods.from_dict({"schema_version": "loaded-methods.v1", "status": "READY", "manifest": manifest,
                                          "bodies": {key: catalog.read_text(key) for key in resources}})
        pins, refs = _resource_pins(catalog, [*resources, *factor.source_resource_ids, "factor-validation-v1"])
        if loaded.fingerprint != method["binding"]["methods_digest"] or pins != method["resources"]:
            raise PortableUnavailable("METHOD_SOURCE_VERSION_UNAVAILABLE")
    except (KeyError, ValueError) as exc:
        raise PortableUnavailable("METHOD_SOURCE_VERSION_UNAVAILABLE") from exc
    if source_policy(app.root, refs)["blocked"]:
        raise PortableUnavailable("SOURCE_WITHDRAWN")


def _method_record(app, charter, factor):
    binding = charter["method_binding"]
    store = app._method_store()
    try:
        package = store.load(binding["handoff_ref"])
        artifact = next(row for row in store.artifacts(binding["handoff_ref"]) if row["artifact_ref"] == binding["artifact_ref"])
    except (FileNotFoundError, KeyError, StopIteration) as exc:
        raise PortableUnavailable("METHOD_ACCEPTED_ARTIFACT_UNAVAILABLE") from exc
    catalog = ResearchAssetCatalog.packaged()
    proposal = proposal_from_handoff(package, artifact, catalog=catalog, policy={"max_candidates": 1})
    loaded = LoadedMethods.from_dict(binding["loaded_methods"])
    _check(package.package_digest == binding["package_digest"] and package.context.fingerprint == binding["context_digest"]
           and proposal.expression == factor.formula and loaded.fingerprint == binding["methods_digest"]
           and loaded.manifest_fingerprint == binding["method_manifest_digest"])
    manifest = loaded.to_dict()["manifest"]
    resources, _ = _resource_pins(catalog, [*(r["resource_id"] for r in manifest["loaded"]),
                                           *factor.source_resource_ids, "factor-validation-v1"])
    return {"binding": {k: binding[k] for k in _METHOD_BINDING}, "manifest": manifest, "resources": resources,
            "accepted": {"expression": proposal.expression, "required_fields": list(proposal.required_fields)}}


def _verify_origin(inputs):
    proof, method, receipt = inputs["origin"], inputs["method"], inputs["receipt"]
    _check(set(proof) == {"request", "publication", "charter"})
    scope_id = verified_factor_scope(proof, receipt)
    manifest = method["manifest"]
    catalog = ResearchAssetCatalog.packaged()
    try:
        bodies = {r["resource_id"]: catalog.read_text(r["resource_id"]) for r in manifest["loaded"]}
        original = LoadedMethods.from_dict({"schema_version": "loaded-methods.v1", "status": "READY", "manifest": manifest, "bodies": bodies})
    except (KeyError, ValueError) as exc:
        raise PortableUnavailable("METHOD_SOURCE_VERSION_UNAVAILABLE") from exc
    charter = {**proof["charter"], "method_binding": {**method["binding"], "loaded_methods": original.to_dict()}}
    _check(original.fingerprint == method["binding"]["methods_digest"]
           and stable_digest({"scope_id": scope_id, "spec": charter}) == receipt["spec_ref"]
           and charter["version"] == WORKFLOW and charter["factor_spec"] == inputs["factor"]
           and charter["snapshot_ref"] == receipt["snapshot_ref"] and charter["attachment_ref"] == receipt["attachment_ref"]
           and charter["policy_fingerprint"] == receipt["policy_fingerprint"] and charter["universe"] == inputs["universe"]
           and charter["as_of"] == receipt["as_of"] and charter["test_period"] == receipt["test_period"]
           and charter["validation_policy"] == {**inputs["policy"], "phase": "validation"})


def _selected_records(data, rows):
    chosen = defaultdict(set)
    for row in rows:
        for name, source in row["field_provenance"].items():
            chosen[row["asset"], stable_digest(source)].add(name)
    if data["attachments"]:
        fields = data["attachments"][0]
        kept = []
        for record in fields["records"]:
            meta = {k: v for k, v in record.items() if k not in RESEARCH_FIELDS and k != "instrument"}
            names = chosen.get((record["instrument"], stable_digest(meta)), set()) & record.keys()
            if names:
                kept.append({k: v for k, v in record.items() if k not in RESEARCH_FIELDS or k in names})
        return kept
    return []


def _spec(raw):
    factor = FactorSpec(**{k: v for k, v in raw.items() if k != "schema_version"})
    _check(factor.to_dict() == raw)
    try:
        resolve_factor_spec(factor)
    except (KeyError, ValueError) as exc:
        raise PortableUnavailable("FACTOR_FORMULA_VERSION_UNAVAILABLE") from exc
    return factor


def freeze(app, study, validation, test, publications, review):
    charter = study["spec"]
    before, after = (FactorValidationRun.from_dict(doc["validation_run"]).to_dict() for doc in (validation, test))
    valid, held = before["bindings"], after["bindings"]
    winner, = validation["experiments"]
    factor = _spec(charter["factor_spec"])
    _check(before["status"] == "COMPLETE" and valid["final_test_usage"] == "HIDDEN" and before["final_test_metrics"] is None
           and held["final_test_usage"] == "RESERVED_FROZEN_TEST" and test["selected_candidate"] == study["candidate_id"]
           and winner["digest"] == study["candidate_fingerprint"] == _neutral(valid) == _neutral(held)
           and before["candidate_fingerprint"] == after["candidate_fingerprint"] == winner["digest"]
           and before["factor_spec"] == after["factor_spec"] == factor.to_dict())
    method = _method_record(app, charter, factor)
    snapshot = app.snapshots.load(charter["snapshot_ref"])
    try:
        attachment = ResearchFieldStore(app.snapshots).load(charter["attachment_ref"]) if charter["attachment_ref"] else None
    except (FileNotFoundError, KeyError) as exc:
        raise PortableUnavailable("METHOD_FACTOR_FIELD_ATTACHMENT_UNAVAILABLE") from exc
    universe = UniverseSpec(held["universe"]["market"], tuple(held["universe"]["instruments"]))
    panel = build_factor_panel(snapshot, attachment, as_of=charter["as_of"], universe=universe)
    _check(panel.fingerprint == held["panel_fingerprint"] and charter["test_period"] == held["final_test_window"])
    data = freeze_inputs(snapshot, [attachment] if attachment else [], instruments=universe.instruments, through=charter["test_period"][1])
    if attachment:
        data["attachments"][0]["records"] = _selected_records(data, panel.to_dict()["rows"])
    receipt = {**publications, **{k: study[k] for k in ("study_id", "spec_ref", "candidate_id", "candidate_fingerprint")},
               **{k: charter[k] for k in ("as_of", "snapshot_ref", "attachment_ref", "policy_fingerprint", "test_period")},
               "panel_ref": panel.fingerprint, "validation_result": validation["fingerprint"], "test_result": test["fingerprint"],
               "validation_binding": before["binding_digest"], "test_binding": after["binding_digest"],
               "validation_evaluation": before["fingerprint"], "test_evaluation": after["fingerprint"], "method_ref": stable_digest(method)}
    public_binding = {k: v for k, v in valid.items() if k != "family_audit"}
    public_charter = {k: v for k, v in charter.items() if k != "method_binding"}
    proof = {**factor_publication_proof(app, study, publications["test_publication"]), "charter": public_charter}
    if proof != redact_public_payload(proof):
        raise PortableUnavailable("METHOD_PUBLIC_CHARTER_UNAVAILABLE")
    inputs = {"origin": proof, "binding": public_binding, "version": _VERSION, "workflow": WORKFLOW, "receipt": receipt, "data": data, "method": method,
              "factor": factor.to_dict(), "winner": winner, "policy": after["policy"], "universe": universe.to_dict(), "review": review}
    decode(inputs)
    _source_check(app, method, factor)
    return {"inputs": inputs, "expected": _projection(after)}


def decode(inputs):
    from .portable_factors import _check_review

    _check(set(inputs) == {"origin", "binding", "version", "workflow", "receipt", "data", "method", "factor", "winner", "policy", "universe", "review"}
           and inputs["version"] == _VERSION and inputs["workflow"] == WORKFLOW)
    receipt, method, data = inputs["receipt"], inputs["method"], inputs["data"]
    _check(set(receipt) == _RECEIPT)
    ids = {"study_id", "test_run", "validation_run", "candidate_id"}
    for key in ids:
        _check(_ID.fullmatch(receipt[key]))
    for key in _RECEIPT - ids - {"as_of", "test_period", "attachment_ref"}:
        reference(receipt[key])
    if receipt["attachment_ref"] is not None:
        reference(receipt["attachment_ref"])
    for field in ("run", "result", "evaluation", "binding", "publication"):
        _check(receipt["test_" + field] != receipt["validation_" + field])
    factor = _spec(inputs["factor"])
    policy = factor_validation_policy(**inputs["policy"])
    _check(policy == inputs["policy"] and policy["phase"] == "test")
    winner = inputs["winner"]
    _check(set(winner) == {"experiment_id", "digest", "parameters", "failure_reason", "ic", "ir", "evaluation"}
           and winner["experiment_id"] == factor.factor_id == receipt["candidate_id"] and winner["failure_reason"] is None
           and winner["digest"] == receipt["candidate_fingerprint"]
           and winner["parameters"] == {"expression": factor.formula, "window": factor.window}
           and winner["evaluation"] == {"information_coefficient": winner["ic"], "status": "COMPLETE"} and winner["ir"] is None)
    _check(set(method) == {"binding", "manifest", "resources", "accepted"} and set(method["binding"]) == _METHOD_BINDING
           and stable_digest(method) == receipt["method_ref"] and stable_digest(method["manifest"]) == method["binding"]["method_manifest_digest"])
    binding, accepted = method["binding"], method["accepted"]
    for key in _METHOD_BINDING - {"handoff_ref", "artifact_ref"}:
        reference(binding[key])
    _check(re.fullmatch("method-handoff:[0-9a-f]{64}", binding["handoff_ref"])
           and re.fullmatch("artifact:method-[0-9a-f]{64}", binding["artifact_ref"])
           and set(accepted) == {"expression", "required_fields"} and accepted["expression"] == factor.formula
           and set(accepted["required_fields"]) == set(factor.required_fields))
    manifest = method["manifest"]
    _check(set(manifest) == {"schema_version", "methods", "loaded", "selection_digest", "input_gaps", "missing", "skipped"}
           and manifest["schema_version"] == "method-manifest.v1" and not manifest["missing"]
           and 1 <= len(manifest["loaded"]) <= 128 and len(method["resources"]) <= 160)
    for row in method["resources"]:
        _check(set(row) == {"resource_id", "resource_ref", "rights_ref"} and _ID.fullmatch(row["resource_id"]))
        reference(row["resource_ref"])
        reference(row["rights_ref"])
    _verify_origin(inputs)
    snapshot, attachments, contract = rebuild_inputs(data)
    _check(contract is None and len(attachments) <= 1 and (not attachments or data["attachments"][0]["kind"] == "research-fields.v1")
           and [row["original_ref"] for row in data["attachments"]] == ([receipt["attachment_ref"]] if receipt["attachment_ref"] else [])
           and receipt["snapshot_ref"] == data["original_snapshot"] and len(data["instruments"]) == 1)
    period = receipt["test_period"]
    _check(len(period) == 2 and panel_time(period[0]) < panel_time(period[1]) == panel_time(data["through"])
           and panel_time(data["through"]) <= SnapshotResearchTools._boundary(receipt["as_of"]))
    universe = UniverseSpec(inputs["universe"]["market"], tuple(data["instruments"]))
    _check(inputs["universe"] == universe.to_dict())
    local = build_factor_panel(snapshot, attachments[0] if attachments else None, as_of=receipt["as_of"], universe=universe).to_dict()
    _check(not local["issues"] and len(local["rows"]) == len(data["observations"]) and
           (not attachments or _selected_records(data, local["rows"]) == data["attachments"][0]["records"]))
    # Bind the rebuilt content to both original store references only when the full published panel hash verifies.
    local.update(snapshot_ref=receipt["snapshot_ref"], attachment_ref=receipt["attachment_ref"])
    panel = FactorPanel(json.dumps(local, ensure_ascii=False, allow_nan=False))
    _check(panel.fingerprint == receipt["panel_ref"])
    valid = dict(inputs["binding"])
    family = family_for_spec(factor)
    if family:
        valid["family_audit"] = audit_family_inputs(family, panel, {"factor_spec": factor.to_dict()}).to_dict()
    keys = {"snapshot_ref", "attachment_ref", "panel_fingerprint", "as_of", "universe", "factor_id", "factor_version",
            "factor_spec_fingerprint", "formula", "implementation_fingerprint", "execution_config", "cost_bps",
            "benchmark", "runtime_policy", "source_policy_fingerprint", "code_version", "train_window", "oos_window",
            "final_test_window", "final_test_usage"}
    if family:
        keys.update({"family_audit", "baseline_factor_spec_fingerprint"})
    _check(set(valid) == keys and valid["snapshot_ref"] == panel.snapshot_ref and valid["attachment_ref"] == panel.attachment_ref
           and valid["panel_fingerprint"] == panel.fingerprint and valid["as_of"] == local["as_of"]
           and valid["universe"] == universe.to_dict() and valid["final_test_usage"] == "HIDDEN"
           and valid["factor_id"] == factor.factor_id and valid["factor_spec_fingerprint"] == factor.fingerprint
           and valid["formula"] == factor.formula and valid["factor_version"] == factor.version
           and valid["implementation_fingerprint"] == factor.implementation_fingerprint
           and valid["runtime_policy"] == {**policy, "phase": "validation"} and valid["final_test_window"] == period)
    held = {**valid, "runtime_policy": policy, "final_test_usage": "RESERVED_FROZEN_TEST"}
    _check(_neutral(valid) == receipt["candidate_fingerprint"] and stable_digest(valid) == receipt["validation_binding"]
           and stable_digest(held) == receipt["test_binding"])
    _check_review(inputs["review"], receipt, {
        "scope_id": inputs["origin"]["request"]["research_plan"]["validation_spec"]["scope_id"],
        "factor_ref": factor.fingerprint, "evaluation_fingerprint": receipt["test_evaluation"],
        "market": universe.market, "universe_ref": stable_digest(universe.to_dict()),
        "purpose": "FORMULA_RESEARCH" if data["mode"] in {"SAMPLE", "SYNTHETIC"} else "PAPER_RESEARCH",
        "policy_fingerprint": stable_digest({"research_policy": receipt["policy_fingerprint"],
                                             "validation_policy": {k: v for k, v in policy.items() if k != "phase"}}),
    })
    return panel, factor, policy


def replay(app, inputs, *, published_identity=None):
    panel, factor, policy = decode(inputs)
    receipt = inputs["receipt"]
    _source_check(app, inputs["method"], factor)
    asset, = inputs["data"]["instruments"]
    frozen = {"state": "TEST_RESERVED", "test_key": receipt["test_publication"], "candidate_fingerprint": receipt["candidate_fingerprint"],
              "spec": {"snapshot_ref": receipt["snapshot_ref"], "instrument": asset, "test_period": receipt["test_period"]}}
    store = SimpleNamespace(get={receipt["study_id"]: frozen}.__getitem__)

    class PublishedTestGateway(FactorValidationGateway):
        def authorize_test(self, candidate_fingerprint, bindings, policy):
            held = {**bindings, "final_test_usage": "RESERVED_FROZEN_TEST"}
            valid = {**held, "final_test_usage": "HIDDEN", "runtime_policy": {**policy, "phase": "validation"}}
            return (stable_digest(held) == receipt["test_binding"] and stable_digest(valid) == receipt["validation_binding"]
                    and super().authorize_test(candidate_fingerprint, bindings, policy))

    gateway = PublishedTestGateway(study_store=store, study_ids={asset: receipt["study_id"]})
    run = run_factor_validation(factor, panel, policy, gateway=gateway, published_identity=published_identity).to_dict()
    _check(run["bindings"]["final_test_usage"] == "RESERVED_FROZEN_TEST")
    if run["status"] == "BLOCKED_CONTROL":
        raise PortableUnavailable("METHOD_FACTOR_CODE_VERSION_MISMATCH")
    return _projection(run)

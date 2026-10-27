"""Read existing source and verified report facts without promoting either."""

from __future__ import annotations

import json
import re
from dataclasses import asdict

from .asset_contracts import FactorEvidenceView, FactorSpec
from .contracts import stable_digest, to_jsonable
from .factor_recipes import resolve_factor_spec
from .factor_validation import FactorValidationRun

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SCOPE = ("as_of", "universe", "train_window", "oos_window", "final_test_window",
          "cost_bps", "execution_config", "benchmark", "code_version", "snapshot_ref", "attachment_ref")


def _spec(value):
    return FactorSpec(**{key: item for key, item in value.items() if key != "schema_version"})


def _definition(catalog, factor_id):
    data = json.loads(catalog.read_text("source-definitions"))
    for value in (*data["definitions"], *data.get("finathink_pilots", ()), *data.get("family_pilots", ())):
        if value.get("definition_id", value.get("factor_id")) == factor_id:
            return value
    raise ValueError("unknown factor definition")


def _local(entry, raw, requested):
    run = FactorValidationRun.from_dict(raw).to_dict()
    spec = _spec(run["factor_spec"])
    bindings = run["bindings"]
    if (spec.fingerprint != bindings["factor_spec_fingerprint"] or spec.factor_id != bindings["factor_id"]
            or spec.version != bindings["factor_version"] or spec.formula != bindings["formula"]):
        raise ValueError("factor definition binding is corrupt")
    try:
        resolve_factor_spec(spec)
        matches = requested is None or requested.fingerprint == spec.fingerprint
    except ValueError:
        matches = False
    charter = entry["manifest"]["source_snapshot"]["factor_study"].get("charter", {})
    policy = {key: value for key, value in run["policy"].items() if key != "phase"}
    return {"run_ref": entry["run_id"], "status": run["status"], "definition_matches": matches,
        "factor_ref": spec.fingerprint, "factor_id": spec.factor_id, "version": spec.version,
        "formula": spec.formula, "factor_spec": spec.to_dict(), "phase": run["policy"]["phase"],
        "evaluation_fingerprint": run["fingerprint"], "candidate_fingerprint": run["candidate_fingerprint"],
        "scope": {key: bindings.get(key) for key in _SCOPE},
        "policy_fingerprint": stable_digest({"research_policy": charter.get("policy_fingerprint"),
                                             "validation_policy": policy}),
        "source_policy_fingerprint": bindings["source_policy_fingerprint"],
        "reproduction_scope": run["reproduction_scope"],
        "local_metric": {step["stage_id"]: step["metrics"] for step in run["steps"]},
        "stages": [{key: step[key] for key in ("stage_id", "status", "reason")} for step in run["steps"]],
        "financial_falsification": False, "settled": run["policy"]["phase"] == "test" and run["status"] == "COMPLETE"}


def load_factor_evidence(factor_ref, *, catalog, run_repository) -> FactorEvidenceView:
    requested = factor_ref if isinstance(factor_ref, FactorSpec) else None
    factor_id = requested.factor_id if requested else factor_ref
    if not isinstance(factor_id, str) or not _ID.fullmatch(factor_id):
        raise ValueError("factor reference is invalid")
    definition = _definition(catalog, factor_id)
    claims = [asdict(item) for item in catalog.claims_for(factor_id)]
    missing, conflicts, local = [], [], []
    for resource_id in definition.get("source_resource_ids", ()):
        resource = catalog.get_resource(resource_id)
        if resource.availability != "PACKAGED":
            missing.append({"code": resource.availability, "resource_id": resource_id})
    if claims:
        missing.append({"code": "UPSTREAM_EXPERIMENT_NOT_REPRODUCED", "resource_id": None})
    for reference in run_repository.list_run_refs(limit=200):
        try:
            entry = run_repository.get_run(reference)
        except (ValueError, KeyError, OSError):
            missing.append({"code": "INDEX_RECORD_CORRUPT", "run_ref": reference})
            continue
        study = entry.get("manifest", {}).get("source_snapshot", {}).get("factor_study")
        if not isinstance(study, dict) or not isinstance(study.get("validation_run"), dict):
            continue
        raw = study["validation_run"]
        if raw.get("bindings", {}).get("factor_id") != factor_id:
            continue
        try:
            if entry["report_status"] != "AVAILABLE":
                raise ValueError("report bundle unavailable")
            record = _local(entry, raw, requested)
        except (ValueError, TypeError, KeyError, OSError):
            record = {"run_ref": reference, "status": "CORRUPT", "local_metric": None,
                      "definition_matches": False, "evaluation_fingerprint": None}
            missing.append({"code": "LOCAL_ARTIFACT_UNAVAILABLE_OR_CORRUPT", "run_ref": reference})
        local.append(record)
        if not record["definition_matches"]:
            conflicts.append({"code": "DEFINITION_MISMATCH", "run_ref": reference})
        if claims and record["local_metric"] is not None:
            conflicts.append({"code": "SOURCE_LOCAL_NOT_DIRECTLY_COMPARABLE", "run_ref": reference,
                "source_claim_ids": [claim["claim_id"] for claim in claims],
                "reason": "Source scope/formula/costs are not authenticated against this local evaluation."})
    result = {"schema_version": "factor-evidence.v1", "factor_id": factor_id,
        "factor_ref": requested.fingerprint if requested else factor_id, "definition": definition,
        "source_claims": claims, "local_runs": local, "conflicts": conflicts, "missing_dependencies": missing,
        "reproduction_scope": sorted({row["reproduction_scope"] for row in local if row["local_metric"] is not None}),
        "locally_reproduced_claim_count": 0, "truncated": run_repository.count_runs() > 200,
        "requires_human_admission": True, "production_write": False}
    return FactorEvidenceView(json.dumps(to_jsonable(result), ensure_ascii=False, allow_nan=False, sort_keys=True))

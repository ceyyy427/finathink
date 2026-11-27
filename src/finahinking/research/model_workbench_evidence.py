"""Check actual dispatch, published claims and their cited facts independently."""

from __future__ import annotations

import math
import re
import unicodedata

from .application_service import _model_input_digest, _read_json, verify_research_receipt
from .context_bundle import ROLES
from .contracts import stable_digest
from .job_queue import JobStatus
from .model_workbench import _DIGEST, VERSION, role_projection, verify_bound_preview
from .reports import redact_public_payload


def _usage(calls):
    sent = [c for c in calls if c["dispatched"]]
    known = [(c["usage"] or {}).get("total_units") for c in sent]
    missing = known.count(None)
    subtotal = sum(v for v in known if v is not None)
    return {"known_total_units": subtotal, "unknown_calls": missing, "total_units": None if missing else subtotal,
            "status": "NOT_CALLED" if not sent else "UNKNOWN" if missing else "RECORDED"}


def _protocol(calls, expected):
    sent = [c for c in calls if c["dispatched"]]
    if not sent:
        return "NOT_CALLED"
    if any(c["status"] in {"DISPATCHED", "UNKNOWN"} for c in sent):
        return "UNKNOWN"
    if any(c["status"] != "READY" for c in sent):
        return "REJECTED"
    return "ACCEPTED" if len(sent) == expected else "PARTIAL"


def _plain(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().strip(' "“”\'.。').split())


def _fact(claim, item, instrument):
    summary = item["summary"]
    if isinstance(summary, str):
        return "SOURCE_MATCHED" if _plain(claim) == _plain(summary) else None
    # Match only complete, dated close/volume statements. A number appearing
    # somewhere in a paragraph cannot certify the rest of that paragraph.
    asset = re.escape(instrument.casefold())
    fact = re.fullmatch(
        rf"(?:on )?(\d{{4}}-\d{{2}}-\d{{2}})[,，:：\s]*(?:{asset}[,，:：\s]*)?"
        r"(close(?:d)?(?: price)?|volume|收盘价|成交量)\s*(?:was|at|of|=|:|为|是)?\s*([+-]?\d+(?:\.\d+)?)",
        _plain(claim),
    )
    if not fact:
        return None
    day, label, number = fact.groups()
    key = "volume" if label in {"volume", "成交量"} else "close"
    for obs in summary["observations"]:
        if obs["timestamp"][:10] == day:
            return ("SOURCE_MATCHED" if math.isclose(float(number), obs[key], rel_tol=1e-12, abs_tol=1e-12)
                    else "SOURCE_MISMATCH")
    return None


def _claim_checks(claims, refs, view, instrument):
    materials = {item["evidence_ref"]: item for item in view["materials"]}
    references = list(dict.fromkeys(refs))
    checks = []
    for i, claim in enumerate(claims):
        citations = []
        for ref in references:
            verdict = _fact(claim, materials[ref], instrument) or "REVIEW_REQUIRED"
            citations.append({"evidence_ref": ref, "status": verdict})
        statuses = {c["status"] for c in citations}
        # A matching quotation cannot settle a claim contradicted by its cited prices.
        verdict = next(iter(statuses)) if len(statuses) == 1 else "REVIEW_REQUIRED"
        checks.append({"claim_index": i, "status": verdict, "evidence_refs": references,
                       "citation_checks": citations})
    return checks


def _inputs(app, request):
    spec = request.research_plan.validation_spec
    if spec["execution_mode"] != "provider-grounded":
        return None
    ref = spec["context_input_ref"]
    if not isinstance(ref, str) or not _DIGEST.fullmatch(ref):
        raise ValueError("模型输入引用无效。")
    value = _read_json(app.root / "model-inputs" / f"{ref}.json")
    if _model_input_digest(value) != ref or value.get("scope_id") != app.scope_id or value["provider"]["provider_id"] != spec["model_config_ref"]:
        raise ValueError("模型输入与冻结引用不一致。")
    return value


def _role_evidence(view, calls, source_report, *, offline, verified, conflict, instrument):
    role = view["role"]
    observed = [c for c in calls if c["role"] == role]
    expected = int(view.get("expected_call", False))
    claims = list(source_report.get("claims", ())) if verified else []
    refs = list(source_report.get("evidence_refs", ())) if verified else []
    checks = []
    semantics = "NOT_APPLICABLE" if offline else "NO_EVIDENCE"
    if not offline and verified and claims and _protocol(observed, expected) == "ACCEPTED":
        checks = _claim_checks(claims, refs, view, instrument)
        semantics = "SOURCE_MATCHED" if checks and all(c["status"] == "SOURCE_MATCHED" for c in checks) else "REVIEW_REQUIRED"
    return {"role": role, "model": view.get("model"), "model_ref": view.get("model_ref"),
            "observed_model_ref": source_report.get("model_ref") if verified else None,
            "material_status": view.get("material_status", "UNKNOWN"), "dynamic_observations": view.get("dynamic_observations", False),
            "role_status": source_report.get("status") if verified else None,
            "context_fingerprint": view.get("context_fingerprint"), "expected_call": bool(expected),
            "call_ids": [c["call_id"] for c in observed],
            "dispatch_status": "OFFLINE" if offline else "SENT" if any(c["dispatched"] for c in observed) else "NOT_SENT",
            "protocol_status": "NOT_APPLICABLE" if offline else "CONFLICT" if conflict else _protocol(observed, expected),
            "semantic_status": "CONFLICT" if conflict else semantics, "usage_status": _usage(observed)["status"],
            "claims": claims, "evidence_refs": refs, "limitations": list(source_report.get("limitations", ())) if verified else [],
            "semantic_checks": checks}


def _archived_backgrounds(request, source):
    """Old discovery reports froze the exact analyst packs before publication."""
    blueprint = request.research_plan.validation_spec["discovery"]["blueprint"]
    discovery = source["discovery"]
    if stable_digest(discovery["blueprint"]) != stable_digest(blueprint):
        raise ValueError("历史任务的研究计划与发表记录不一致。")
    packs = {r["role"]: r["knowledge"] for r in discovery["roles"]}
    for role in ROLES[:-1]:
        pack = packs[role]
        identity = {"run": request.run_id, "blueprint": blueprint["fingerprint"],
                    "pack": {k: v for k, v in pack.items() if k != "ref"}}
        if pack["role"] != role or pack["ref"] != "discovery-evidence:" + stable_digest(identity):
            raise ValueError("历史角色资料绑定不一致。")
    return {**packs, "research": {"roles": [packs[r] for r in ROLES[:-1]], "hypothesis": blueprint["hypothesis"],
                                  "falsification": blueprint["falsification"], "focus": blueprint["profile_focus"]}}


def _published_roles(study, views):
    results = {r["role"]: r for r in study.get("roles", ())}
    synthesis = study.get("synthesis") or {}
    if synthesis:
        results["research"] = {"claims": synthesis.get("manager_observations", []),
                               "evidence_refs": synthesis.get("evidence_refs", []),
                               "model_ref": views["research"].get("model_ref"), "status": "READY"}
    return results


def publication_accounting(request, snapshot, inputs, study, reports):
    """Freeze the original role/usage checks in the downloadable report."""
    projections = role_projection(request, snapshot, inputs, reports=reports)
    views = {v["role"]: v for v in projections}
    results = _published_roles(study, views)
    calls = study.get("calls", [])
    offline = inputs is None
    roles = []
    for view in projections:
        role = view["role"]
        row = _role_evidence(view, calls, results.get(role, {}), offline=offline, verified=True,
                             conflict=False, instrument=request.instrument)
        roles.append({k: row[k] for k in ("role", "model", "model_ref", "call_ids", "dispatch_status",
                                           "usage_status", "protocol_status", "semantic_status", "semantic_checks")})
    return {"preview_digest": request.research_plan.validation_spec["model_workbench"]["preview_digest"],
            "roles": roles, "dispatched_calls": sum(c["dispatched"] for c in calls), "usage": _usage(calls),
            "expected_calls": sum(v["expected_call"] for v in projections)}


def job_evidence(app, job_id):
    record = app.queue.get(job_id)
    request = app._request(record)
    spec = request.research_plan.validation_spec
    offline = spec["execution_mode"] != "provider-grounded"
    binding = spec.get("model_workbench")
    job = app.get_job(job_id)
    calls = (job.get("model_research") or {}).get("calls", [])
    archived = {}
    publication = None
    issues = []
    journal_status = (job.get("model_research") or {}).get("journal_status")
    if journal_status == "UNAVAILABLE" or (journal_status == "NOT_CREATED" and record.attempts and
            record.status in {JobStatus.CANCELLED, JobStatus.FAILED} and job["failure_kind"] not in {
                "OUTBOUND_DISABLED", "SNAPSHOT_INTEGRITY_FAILED", "CONTEXT_INTEGRITY_FAILED", "POLICY_CHANGED",
                "REQUEST_UNAVAILABLE", "WORKER_START_FAILED"}):
        issues.append("CALL_JOURNAL_UNAVAILABLE")
    if record.status is JobStatus.COMPLETED:
        try:
            publication = verify_research_receipt(app.root, app.queue, record, record.result_ref, verify_input=True)
            archived = publication["manifest"]["source_snapshot"].get("model_research", {})
        except (ValueError, TypeError, KeyError, OSError):
            issues.append("PUBLICATION_INVALID")
    views = [{"role": role, "model_ref": (binding or {}).get("role_model_refs", {}).get(role)} for role in ROLES]
    try:
        snapshot = app.snapshots.load(request.snapshot_ref)
        inputs = _inputs(app, request)
        if binding:
            verify_bound_preview(app.root, request, snapshot, inputs)
        reports = publication["result"].state.analyst_reports if publication else ()
        backgrounds = (_archived_backgrounds(request, publication["manifest"]["source_snapshot"])
                       if publication and not offline and "discovery" in spec else None)
        views = role_projection(request, snapshot, inputs, reports=reports, discovery_backgrounds=backgrounds)
        if offline and "discovery" in spec:
            for view in views:
                view.update(model="discovery-v1", model_ref="offline/discovery-v1")
    except (ValueError, TypeError, KeyError, OSError):
        issues.append("FROZEN_INPUT_INVALID")
    prior_unpublished_discovery = "discovery" in spec and not offline and publication is None
    by_role = {v["role"]: v for v in views}
    if not offline and not prior_unpublished_discovery and "FROZEN_INPUT_INVALID" not in issues:
        seen = set()
        for call in calls:
            role = call["role"]
            view = by_role.get(role)
            if (view is None or role in seen or not view["expected_call"] or view["model"] != call["model"]
                    or (role != "research" or publication) and view["context_fingerprint"] != call["context_fingerprint"]):
                issues.append("CALL_BINDING_CONFLICT")
                break
            seen.add(role)
    if publication and not offline:
        if not archived or archived.get("context_input_ref") != spec.get("context_input_ref"):
            issues.append("MODEL_PUBLICATION_MISSING")
        elif archived.get("calls") != calls:
            issues.append("CALL_JOURNAL_CONFLICT")
    results = _published_roles(archived, by_role)
    synthesis = archived.get("synthesis") or {}
    if offline and publication:
        results = {r.role: {"claims": list(r.claims), "evidence_refs": list(r.evidence_refs),
                            "limitations": list(r.limitations), "model_ref": r.model_ref, "status": r.status}
                   for r in publication["result"].state.analyst_reports}
    if publication and not offline and not issues:
        for role, view in by_role.items():
            result = results.get(role, {})
            refs = result.get("evidence_refs", ())
            sent = [c for c in calls if c["role"] == role and c["dispatched"]]
            if result.get("status") == "READY" and (not refs or not result.get("claims") or not set(refs) <= set(view["evidence_refs"])
                    or len(sent) != 1 or sent[0]["status"] != "READY"
                    or result.get("model_ref", view["model_ref"]) != view["model_ref"]):
                issues.append("ROLE_EVIDENCE_CONFLICT")
                break
            if (not view["expected_call"] and (sent or result.get("status") != "INSUFFICIENT_DATA" or refs)
                    or role == "research" and (not synthesis.get("hypotheses")
                        or synthesis.get("context_fingerprint") != view["context_fingerprint"])):
                issues.append("ROLE_EVIDENCE_CONFLICT")
                break
    conflict = any(reason.endswith("CONFLICT") for reason in issues)
    verified = publication is not None and not issues
    roles = [_role_evidence(v, calls, results.get(v["role"], {}), offline=offline,
                            verified=verified, conflict=conflict, instrument=request.instrument) for v in views]
    expected = sum(r["expected_call"] for r in roles)
    sent_count = sum(c["dispatched"] for c in calls)
    usage = _usage(calls)
    missing_journal = ("CALL_JOURNAL_UNAVAILABLE" in issues or "CALL_JOURNAL_CONFLICT" in issues) and not calls
    if missing_journal:
        usage = {"known_total_units": 0, "unknown_calls": None, "total_units": None, "status": "UNKNOWN"}
        for row in roles:
            if row["expected_call"] or row["material_status"] == "UNKNOWN":
                row.update(dispatch_status="UNKNOWN", usage_status="UNKNOWN",
                           protocol_status="CONFLICT" if conflict else "UNKNOWN")
    observed = [r for r in roles if r["semantic_checks"]]
    semantics = "NO_EVIDENCE"
    if observed:
        semantics = ("SOURCE_MATCHED" if len(observed) == expected and all(r["semantic_status"] == "SOURCE_MATCHED" for r in observed)
                     else "REVIEW_REQUIRED")
    selection = {"question": request.research_plan.hypotheses[0], "instrument": request.instrument, "as_of": request.as_of.isoformat(),
                 "snapshot_ref": request.snapshot_ref, "model_config_ref": spec["model_config_ref"], "data_mode": spec["data_mode"],
                 "policy_ref": request.previous_policy_fingerprint}
    return redact_public_payload({
        "schema_version": VERSION, "paper_only": True, "job_id": job_id, "run_id": request.run_id, "status": record.status.value,
        "created_at": job["created_at"], "updated_at": job["updated_at"], "result_ref": record.result_ref if verified else None,
        "execution_mode": spec["execution_mode"], "data_mode": spec["data_mode"], "selection": selection, "budget": spec["budget"],
        "attempts": record.attempts, "preview_ref": (binding or {}).get("preview_ref"), "preview_digest": (binding or {}).get("preview_digest"),
        "consent_status": "FROZEN_PREVIEW" if binding else "LEGACY_SUBMISSION", "request_digest": stable_digest(request),
        "dispatch_status": "OFFLINE" if offline else "UNKNOWN" if missing_journal else "SENT" if sent_count else "NOT_SENT",
        "dispatched_calls": None if missing_journal else sent_count, "expected_calls": expected, "usage_status": usage["status"], "usage": usage,
        "protocol_status": "NOT_APPLICABLE" if offline else "CONFLICT" if conflict else "UNKNOWN" if missing_journal else _protocol(calls, expected),
        "semantic_status": "NOT_APPLICABLE" if offline else "CONFLICT" if conflict else semantics,
        "semantic_summary": {"checked_claims": sum(len(r["semantic_checks"]) for r in roles),
                             "matched_claims": sum(c["status"] == "SOURCE_MATCHED" for r in roles for c in r["semantic_checks"]),
                             "scope": "LITERAL_SOURCE_OBSERVATIONS"},
        "roles": roles, "calls": calls, "synthesis": synthesis if verified else {}, "evidence_issues": issues,
        "failure_kind": job["failure_kind"] or (issues[0] if issues else None), "cancel_requested": record.cancel_requested,
        "report_status": job["report_status"] if publication or record.status is not JobStatus.COMPLETED else "UNAVAILABLE",
        "report_url": job["report_url"] if verified else None, "run_url": job["run_url"],
    })

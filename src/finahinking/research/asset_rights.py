"""File-specific intake proposals; never installation or financial-use approval."""

from __future__ import annotations

import json
import re
from datetime import date

from .source_contracts import source_path


def _review_valid(review, audit, artifact):
    try:
        return (
            isinstance(review, dict) and review["source_id"] == audit["source_id"]
            and review["commit"] == audit["commit"] and review["path"] == artifact["path"]
            and review["sha256"] == artifact["sha256"]
            and isinstance(review["reviewed_by"], str) and bool(review["reviewed_by"].strip())
            and isinstance(review["rationale"], str) and bool(review["rationale"].strip())
            and date.fromisoformat(review["reviewed_on"]).isoformat() == review["reviewed_on"]
            and isinstance(review["inspected_notices"], list)
            and len(review["inspected_notices"]) <= 64
        )
    except (KeyError, TypeError, ValueError):
        return False


def plan_asset_import(audit: dict, *, existing_catalog) -> dict:
    """Review metadata is supplied separately from raw source text; nothing is written."""
    if not isinstance(audit, dict) or audit.get("schema_version") != "source-audit.v1":
        raise ValueError("source-audit.v1 required")
    artifacts = audit.get("artifacts")
    reviews = audit.get("file_reviews", [])
    if (not isinstance(artifacts, list) or len(artifacts) > 2048
            or not isinstance(reviews, list) or len(reviews) > 2048):
        raise ValueError("bounded source artifacts and file reviews required")
    prefix = audit["repository"] + "/blob/" + audit["commit"] + "/"
    existing = {(r.source_path, r.source_sha256): r.resource_id
                for r in existing_catalog.resources() if r.source_path and r.source_sha256
                and r.source_url and r.source_url.startswith(prefix)}
    notices = {a["path"]: a for a in audit.get("license_artifacts", [])
               if isinstance(a, dict) and isinstance(a.get("path"), str)}
    by_path = {}
    for review in reviews:
        if not isinstance(review, dict):
            raise TypeError("file review must be an object")
        path = source_path(review.get("path"))
        if path in by_path:
            raise ValueError("duplicate file review")
        by_path[path] = review
    result = {"status": "PROPOSAL_ONLY", "source_id": audit["source_id"],
              "commit": audit["commit"], "eligible": [], "reference_only": [],
              "blocked": [], "unresolved": [], "required_notices": []}
    required = {}
    seen = set()
    for artifact in artifacts:
        path = source_path(artifact.get("path"))
        if path in seen:
            raise ValueError("duplicate source artifact")
        seen.add(path)
        item = {"path": path, "sha256": artifact.get("sha256"),
                "existing_resource_id": existing.get((path, artifact.get("sha256")))}

        def append(category, reason, item=item):
            result[category].append({**item, "reason": reason})

        if audit.get("status") != "VERIFIED" or audit.get("errors") or artifact.get(
            "status"
        ) != "VERIFIED":
            append("blocked", "SOURCE_INTEGRITY_NOT_VERIFIED")
            continue
        review = by_path.get(path)
        if review is None:
            append("unresolved", "FILE_REVIEW_REQUIRED")
            continue
        if not _review_valid(review, audit, artifact):
            append("blocked", "REVIEW_IDENTITY_MISMATCH")
            continue
        decision = review.get("decision")
        if decision in {"REFERENCE_ONLY", "BLOCKED"}:
            append("reference_only" if decision == "REFERENCE_ONLY" else "blocked",
                   review["rationale"])
            continue
        if decision != "ADAPTATION_CANDIDATE" or review.get("license_id") not in {
            "MIT", "Apache-2.0",
        }:
            append("blocked", "UNSUPPORTED_OR_UNKNOWN_RIGHTS")
            continue
        inspected = {}
        for evidence in review["inspected_notices"]:
            if not isinstance(evidence, dict):
                raise TypeError("notice reference must be an object")
            reference = source_path(evidence.get("path"))
            if reference in inspected:
                raise ValueError("duplicate inspected notice")
            inspected[reference] = evidence.get("sha256")
        applicable = artifact.get("license_candidates", [])
        if (not applicable or set(applicable) != set(inspected)
                or any(p not in notices or notices[p].get("status") != "VERIFIED"
                       or notices[p].get("sha256") != inspected[p] for p in applicable)):
            append("blocked", "UNREVIEWED_APPLICABLE_NOTICE")
            continue
        embedded = artifact.get("embedded_rights", [])
        identifiers = [identifier for line in embedded for identifier in re.findall(
            r"SPDX-License-Identifier:\s*([^\s*]+)", line)]
        if (any(i != review["license_id"] for i in identifiers)
                or any("commons clause" in line.casefold() for line in embedded)):
            append("blocked", "EMBEDDED_RIGHTS_CONFLICT")
            continue
        append("eligible", "REVIEWED_ADAPTATION_CANDIDATE")
        required.update(inspected)
    result["required_notices"] = [{"path": p, "sha256": sha}
                                  for p, sha in sorted(required.items())]
    return result


def reconcile_resources(catalog, baseline: dict) -> dict:
    """Keep unavailable resources and unresolved formulas visible without rewriting them."""
    resources = baseline.get("resources")
    if not isinstance(resources, list) or len(resources) > 4096:
        raise ValueError("resource reconciliation records required")
    seen, errors = set(), []
    for item in resources:
        rid = item["resource_id"]
        if rid in seen:
            raise ValueError("duplicate reconciliation resource")
        seen.add(rid)
        try:
            current = catalog.get_resource(rid)
            if (current.sha256 != item["previous_sha256"]
                    or current.availability != item["previous_availability"]):
                errors.append({"resource_id": rid, "code": "LEGACY_IDENTITY_CHANGED"})
            if current.availability == "PACKAGED":
                catalog.read_text(rid)
        except ValueError:
            errors.append({"resource_id": rid, "code": "LEGACY_RESOURCE_UNREADABLE"})
    definitions = {d["definition_id"]: d for d in json.loads(
        catalog.read_text("source-definitions"))["definitions"]}
    for previous in baseline.get("unresolved_definitions", []):
        current = definitions.get(previous["definition_id"])
        if current is None or current["definition_status"] != previous["status"]:
            errors.append({"definition_id": previous["definition_id"],
                           "code": "UNRESOLVED_DEFINITION_CHANGED"})
    return {"status": "BLOCKED" if errors else "VERIFIED", "resource_count": len(seen),
            "unresolved_definition_count": len(baseline.get("unresolved_definitions", [])),
            "errors": errors}

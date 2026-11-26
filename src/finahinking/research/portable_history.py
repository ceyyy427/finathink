"""Minimal witnesses from verified original queue publications for archived report bytes."""

import base64
import hashlib
import html

from finahinking.data.panel_contracts import reference

from .application_service import verify_research_receipt
from .contracts import stable_digest, to_jsonable
from .job_queue import JobStatus, _ref

_COMPLETE = "complete_report.html"
_MAX_REPORT_BYTES = 16 * 1024 * 1024


def freeze_report(app, run_id):
    """Verify the origin receipt; export only the HTML and its public identity links."""
    _ref(run_id, "run_id")
    entry = app.runs.get_run(run_id)
    if entry["report_status"] != "AVAILABLE":
        raise ValueError("历史报告未通过原工作区校验")
    with app.queue._connect() as db:
        rows = db.execute(
            """SELECT j.job_id FROM jobs j JOIN request_snapshots r ON r.task_ref=j.task_ref
               WHERE j.status=? AND json_extract(r.payload,'$.run_id')=?
               AND json_extract(r.payload,'$.research_plan.validation_spec.scope_id')=?""",
            (JobStatus.COMPLETED.value, run_id, app.scope_id),
        ).fetchall()
    if len(rows) != 1:
        raise ValueError("历史报告缺少唯一的原工作区发表回执")
    job = app.queue.get(rows[0][0])
    verified = verify_research_receipt(app.root, app.queue, job, job.result_ref)
    manifest = verified["manifest"]
    if verified["bundle"] != entry["bundle"] or manifest != entry["manifest"] or manifest["run_id"] != run_id:
        raise ValueError("历史报告的发表回执与索引不一致")
    path = verified["bundle"] / _COMPLETE
    if path.is_symlink():
        raise ValueError("历史报告路径无效")
    with path.open("rb") as handle:
        raw = handle.read(_MAX_REPORT_BYTES + 1)
    sha = hashlib.sha256(raw).hexdigest()
    if len(raw) > _MAX_REPORT_BYTES or sha != manifest["files"].get(_COMPLETE):
        raise ValueError("历史报告字节与发表清单不一致或超出范围")
    witness = {"run_id": run_id, "manifest_digest": stable_digest(manifest),
               "receipt_digest": job.result_ref.split(":", 1)[1], "report_sha256": sha}
    return {"name": run_id + ".html", "media_type": "text/html", "sha256": sha,
            "content_base64": base64.b64encode(raw).decode("ascii"),
            "publication": {**witness, "fingerprint": stable_digest(witness)}}


def factor_publication_proof(app, study, receipt_digest):
    from .application_service import _read_json
    from .reports import redact_public_payload

    job = app.queue.get(study["test_job_id"])
    proof = {"request": to_jsonable(app._request(job)), "publication": _read_json(app.root / "results" / (receipt_digest + ".json"))}
    if proof != redact_public_payload(proof):
        from .portable_inputs import PortableUnavailable

        raise PortableUnavailable("FACTOR_PUBLICATION_PROOF_UNAVAILABLE")
    return proof


def verified_factor_scope(proof, receipt):
    publication, request = proof["publication"], proof["request"]
    binding = request["research_plan"]["validation_spec"]
    if (stable_digest(publication) != receipt["test_publication"] or stable_digest(request) != publication["request_digest"]
            or request["run_id"] != publication["run_id"] or request["run_id"] != receipt["test_run"]
            or publication["snapshot_ref"] != request["snapshot_ref"] or request["snapshot_ref"] != receipt["snapshot_ref"]
            or binding["factor_study"] != {"phase": "test", "spec_ref": receipt["spec_ref"], "study_id": receipt["study_id"]}
            or binding["execution_mode"] != "local-deterministic" or binding["model_config_ref"] != "offline"):
        raise ValueError("冻结因子未绑定同一原发表回执和研究章程。")
    return _ref(binding["scope_id"], "scope_id")


def _factor_document(items, inputs):
    receipt = inputs["receipt"]
    documents = {row["publication"]["run_id"]: row for row in items}
    for phase in ("validation", "test"):
        run = receipt[phase + "_run"]
        if run not in documents:
            raise ValueError("缺少因子测试的原始发表报告。")
        document = documents[run]
        if "method" in inputs:
            markers = [f'<dt>{label}</dt><dd>{receipt[phase + suffix]}</dd>' for label, suffix in (
                ("validation_fingerprint", "_evaluation"), ("binding_digest", "_binding"))]
            if phase == "test" and inputs["origin"]["publication"]["manifest_digest"] != document["publication"]["manifest_digest"]:
                raise ValueError("方法测试回执引用的原发表清单不一致。")
        elif inputs.get("settings", {}).get("discovery"):
            frozen = inputs["settings"]["discovery"]
            trial = frozen["trial"]["fingerprint"] if phase == "test" else frozen["frozen"]["trial_ref"]
            if phase == "test" and frozen["origin"]["publication"]["manifest_digest"] != document["publication"]["manifest_digest"]:
                raise ValueError("因子回执引用的原发表清单不一致。")
            markers = [f'"digest": "{trial}"', f'"spec_ref": "{receipt["spec_ref"]}"', f'"family_ref": "{frozen["frozen"]["family_ref"]}"']
        else:
            return
        published = html.unescape(base64.b64decode(document["content_base64"], validate=True).decode("utf-8"))
        if not all(marker in published for marker in markers):
            raise ValueError("冻结评估指纹或试验家族与原始因子报告不一致。")


def validate_history(items, kind, source_id, inputs):
    """Bind the checked bytes to their archived origin and independently frozen workflow refs."""
    by_run = {}
    for item in items:
        witness = item["publication"]
        if not isinstance(witness, dict) or set(witness) != {
            "run_id", "manifest_digest", "receipt_digest", "report_sha256", "fingerprint",
        }:
            raise ValueError("历史报告的发表证明字段无效")
        run = _ref(witness["run_id"], "run_id")
        for key in ("manifest_digest", "receipt_digest", "report_sha256", "fingerprint"):
            reference(witness[key])
        body = {key: value for key, value in witness.items() if key != "fingerprint"}
        if (stable_digest(body) != witness["fingerprint"] or witness["report_sha256"] != item["sha256"]
                or item["name"] != run + ".html" or run in by_run):
            raise ValueError("历史文件字节、标识与发表证明不匹配")
        by_run[run] = witness
    if source_id not in by_run:
        raise ValueError("缺少原研究的历史报告")
    if not inputs:
        return
    expected = []
    if kind == "factor_study":
        if "method" in inputs or inputs.get("settings", {}).get("discovery"):
            _factor_document(items, inputs)
        for phase in ("validation", "test"):
            receipt = inputs["receipt"]
            expected.append((receipt[phase + "_run"], "receipt_digest", receipt[phase + "_publication"]))
    elif kind == "research_pair":
        for record in (inputs["anchor"], *inputs["children"].values()):
            expected.append((record["run_id"], "manifest_digest", record["manifest_digest"]))
    elif kind in {"strategy_study", "strategy_validation", "strategy_pair"}:
        for record in ([inputs["source"]["publication"], inputs["publication"]]
                       if kind == "strategy_validation" else [inputs["source"]["publication"]]):
            expected.append((record["run_id"], "manifest_digest", record["report_digest"]))
    for run, key, ref in expected:
        if run not in by_run or by_run[run][key] != ref:
            raise ValueError("历史文件与冻结工作流的原发表清单或回执不同")

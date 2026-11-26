"""Minimal witnesses from verified original queue publications for archived report bytes."""

import base64
import hashlib

from finahinking.data.panel_contracts import reference

from .application_service import verify_research_receipt
from .contracts import stable_digest
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

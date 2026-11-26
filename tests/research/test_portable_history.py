"""Archive only publication identities and verify their links to the workflow."""

import base64
import hashlib

import pytest

from finahinking.research.contracts import stable_digest
from finahinking.research.portable_history import freeze_report, validate_history
from tests.research.test_reproduction import original

__all__ = ["original"]


def test_published_history_checks_original_job_and_html(original):
    service, run_id = original
    item = freeze_report(service, run_id)
    entry = service.runs.get_run(run_id)
    raw = (entry["bundle"] / "complete_report.html").read_bytes()
    assert base64.b64decode(item["content_base64"]) == raw
    witness = item["publication"]
    sha = hashlib.sha256(raw).hexdigest()
    job = next(j for j in service.list_jobs()["jobs"] if j["run_id"] == run_id)
    receipt = service.queue.get(job["job_id"]).result_ref.split(":", 1)[1]
    assert witness["receipt_digest"] == receipt
    assert witness["manifest_digest"] == stable_digest(entry["manifest"])
    assert witness["report_sha256"] == sha == item["sha256"]
    validate_history([item], "research", run_id, None)
    with pytest.raises(ValueError, match="不匹配"):
        validate_history([{**item, "sha256": "0" * 64}], "research", run_id, None)
    with pytest.raises(ValueError, match="不匹配"):
        validate_history([{**item, "name": "other.html"}], "research", run_id, None)


@pytest.mark.parametrize("kind", ["strategy_study", "strategy_validation", "strategy_pair", "factor_study", "research_pair"])
def test_workflow_origin_substitution_is_rejected(kind):
    reports = []
    for run in ("source", "additional", "candidate"):
        witness = {"run_id": run, "receipt_digest": stable_digest([run, "receipt"]),
                   "manifest_digest": stable_digest([run, "manifest"]), "report_sha256": stable_digest([run, "html"])}
        reports.append({"name": run + ".html", "sha256": witness["report_sha256"],
                        "publication": {**witness, "fingerprint": stable_digest(witness)}})
    def record(i):
        w = reports[i]["publication"]
        return {"run_id": w["run_id"], "report_digest": w["manifest_digest"], "manifest_digest": w["manifest_digest"]}
    if kind == "factor_study":
        inputs = {"receipt": {"test_run": "source", "validation_run": "additional",
                              "test_publication": reports[0]["publication"]["receipt_digest"],
                              "validation_publication": reports[1]["publication"]["receipt_digest"]}}
    elif kind == "research_pair":
        inputs = {"anchor": record(0), "children": {"left": record(1), "right": record(2)}}
    elif kind == "strategy_validation":
        inputs = {"source": {"publication": record(1)}, "publication": record(0)}
    else:
        inputs = {"source": {"publication": record(0)}}
    validate_history(reports, kind, "source", inputs)
    substituted = dict(reports[0]["publication"])
    substituted.update(manifest_digest="0" * 64, receipt_digest="0" * 64)
    substituted["fingerprint"] = stable_digest({k: v for k, v in substituted.items() if k != "fingerprint"})
    with pytest.raises(ValueError, match="与冻结工作流"):
        validate_history([{**reports[0], "publication": substituted}, *reports[1:]], kind, "source", inputs)

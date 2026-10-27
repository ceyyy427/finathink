from finahinking.research.application_service import ResearchApplicationService
from finahinking.research.contracts import stable_digest
from finahinking.research.snapshot_tools import local_research_policy
from tests.research.test_application_service import payload, wait_for


def test_spawned_risk_and_allocation_evidence_survives_restart(tmp_path):
    service = ResearchApplicationService(tmp_path)
    try:
        body = payload(service)
        job = service.submit(body)
        service.start()
        completed = wait_for(service, job["job_id"])
        assert completed["status"] == "completed"
        risk = completed["risk_portfolio"]
        assert risk["run_id"] == completed["run_id"]
        assert risk["dataset_fingerprint"] == body["snapshot_ref"]
        assert risk["policy_fingerprint"] == stable_digest(local_research_policy())
        entry = service.runs.get_run(completed["run_id"])
        assert entry["manifest"]["source_snapshot"]["risk_portfolio"] == risk
        assert risk["paper_ledger"]["entries"][0]["event"] == "paper_pending"
        assert risk["paper_ledger"]["entries"][0]["quantity"] == 0
    finally:
        service.close()
    reopened = ResearchApplicationService(tmp_path)
    try:
        saved = reopened.get_job(job["job_id"])
        assert saved["report_status"] == "AVAILABLE"
        assert saved["risk_portfolio"] == risk
    finally:
        reopened.close()

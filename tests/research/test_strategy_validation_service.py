"""Exercise the saved studio plan through the same queue, report and public routes as the UI."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from finahinking.local_app import LocalAppConfig, LocalApplication
from finahinking.research.application_service import ResearchApplicationService
from finahinking.research.strategy_validation_routes import API, BASE
from finahinking.research.strategy_validation_service import StrategyValidationService
from tests.research.test_strategy_worker import completed


def test_frozen_studio_journey_through_worker(tmp_path):
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "studio.sqlite")))
    service = app.research_service

    def post(action, **values):
        return app.route("POST", API + "/" + action, body={"_csrf": app.csrf_token, **values})

    try:
        assert app.route("GET", API)[2]["sources"] == []
        assert app.route("GET", BASE)[0] == 200
        assert service.list_jobs()["total"] == 0
        assert app.route("POST", API + "/example", body={})[0] == 403
        assert app.route("GET", BASE + "/example")[0] == 405
        assert app.route("GET", BASE + "?run=absent")[0] == 404
        service.start()
        status, _, receipt = post("example")
        assert status == 202, receipt
        original = completed(service, receipt["source_job"])
        run = original["run_id"]
        assert app.route("GET", receipt["url"])[0] == 200
        assert post("example")[2] == receipt
        assert app.route("GET", API + "/progress?source_job=" + original["job_id"])[2] == {
            "status": "completed"
        }
        assert app.route("GET", API + "/progress?study=" + original["job_id"])[0] == 404
        assert app.route("GET", API + "?page=0")[0] == 422
        original_url = "/research/" + run + "/report/complete"
        status, _, original_html = app.route("GET", original_url)
        assert status == 200
        assert post("preview", run_id=run, train_size="3.2")[0] == 422
        assert post("preview", run_id=run, train_size=True)[0] == 422
        status, _, frozen = post("preview", run_id=run, parameter_values="4,5")
        assert status == 200, frozen
        ref = frozen["plan_fingerprint"]
        assert len(frozen["windows"]) == 3 and "walkforward" not in frozen
        assert post("preview", run_id=run, parameter_values=[5, 4])[2] == frozen
        assert (
            post("preview", run_id=run, parameter_values=[4, 5], fee_bps=9)[2]["plan_fingerprint"]
            != ref
        )
        assert post("run", run_id="other", plan_fingerprint=ref)[0] == 422
        assert post("run", run_id=run, plan_fingerprint="f" * 64)[0] == 404
        assert post("run", run_id=run, plan_fingerprint=ref, fee_bps=20)[0] == 422
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts = list(
                pool.map(lambda _: post("run", run_id=run, plan_fingerprint=ref), range(2))
            )
        for status, _, receipt in receipts:
            assert status == 202, receipt
        study_id = receipts[0][2]["study_id"]
        assert study_id == receipts[1][2]["study_id"]
        job = completed(service, study_id)
        status, _, result = app.route("GET", API + "?study=" + study_id)
        assert status == 200, result
        assert result["status"] == "completed" and result["plan_fingerprint"] == ref
        assert len(result["walkforward"]["windows"]) == 3
        assert result["portfolio"]["ledger_digest"] == frozen["original_ledger"]
        assert job["strategy_validation"]["run_id"] == run
        assert job["metrics"] == {} and job["risk_portfolio"] is None
        assert app.route("GET", API + "/progress?study=" + study_id)[2] == {"status": "completed"}
        assert app.route("GET", original_url)[2] == original_html
        status, _, page = app.route("GET", result["report_url"])
        assert status == 200 and "策略验证报告" in page and ref in page
        assert "<script" not in page and 'src="http' not in page
        assert app.route("GET", receipts[0][2]["url"])[0] == 200
        assert service.list_jobs()["total"] == 2
        outsider = ResearchApplicationService(
            service.root, scope_id="separate-session", allow_model_calls=False
        )
        try:
            other = StrategyValidationService(outsider)
            assert other.history() == [] and other.sources.list_sources() == []
            with pytest.raises(KeyError):
                other.load(ref)
            with pytest.raises(KeyError):
                other.get(study_id)
        finally:
            outsider.close()
        with pytest.raises(ValueError):
            service.learning._source(job["run_id"])
    finally:
        app.close()
    reopened = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "studio.sqlite")))
    try:
        assert reopened.route("GET", API + "?study=" + study_id)[2] == result
        with reopened.research_service.queue._connect() as db:
            db.execute(
                "UPDATE strategy_validation_plans SET payload='{}' WHERE fingerprint=?", (ref,)
            )
        assert reopened.route("GET", API + "?study=" + study_id)[0] == 422
    finally:
        reopened.close()

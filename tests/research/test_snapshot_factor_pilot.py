from dataclasses import replace

import pandas as pd
import pytest

from finahinking.local_app import LocalAppConfig, LocalApplication
from finahinking.p8_2.data_sources import FixtureMarketDataSource
from tests.research.test_factor_study_application import wait_study

PREVIEW = "/api/research/experiments/snapshot-factor/preview"
SUBMIT = "/api/research/experiments/snapshot-factor"


def captured_snapshot(*, points=120, mode="CAPTURED", change_first=None, cutoff=None):
    original = FixtureMarketDataSource(points=points).snapshot("AAA")
    rows = original.observations
    if change_first:
        rows = (change_first(rows[0]), *rows[1:])
    return replace(original, mode=mode, observations=rows, as_of=cutoff)


def inputs(reference, **changes):
    return {
        "snapshot_ref": reference,
        "instrument": "AAA",
        "recipe_id": "close_momentum",
        "question": "检验收盘动量与后续收益的样本外相关性",
        **changes,
    }


def post(app, path, values=None, *, csrf=True):
    body = dict(values or {})
    if csrf:
        body["_csrf"] = app.csrf_token
    status, _, response = app.route("POST", path, body=body)
    return status, response


@pytest.mark.parametrize(
    "options, changes, reason",
    [
        ({"mode": "SAMPLE"}, {}, "自有数据"),
        ({"points": 97}, {}, "100 条"),
        ({}, {"instrument": "BBB"}, "快照中包含的标的"),
        ({}, {"recipe_id": "volume_momentum"}, "收盘动量或收盘均值回归"),
        ({"change_first": lambda row: replace(row, available_at=None)}, {}, "available_at"),
        ({"change_first": lambda row: replace(row, available_at=row.timestamp + pd.Timedelta(days=2))},
         {}, "下一次观测"),
        ({"cutoff": "2026-01-02T00:00:00Z"}, {}, "截止时点"),
    ],
)
def test_preview_explains_unsupported_data_without_creating_a_job(tmp_path, options, changes, reason):
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "app.sqlite")))
    try:
        service = app.research_service
        reference = service.snapshots.save(captured_snapshot(**options))
        status, response = post(app, PREVIEW, inputs(reference, **changes))
        assert status == 422 and reason in response["error"]
        assert service.factor_studies.list() == []
        assert service.list_jobs()["total"] == 0
    finally:
        app.close()


def test_same_frozen_factor_result_replays_after_restart_without_new_job(tmp_path):
    config = LocalAppConfig(db_path=str(tmp_path / "app.sqlite"))
    app = LocalApplication(config)
    try:
        service = app.research_service
        snapshot = captured_snapshot(points=98)
        reference = service.snapshots.save(snapshot)
        values = inputs(reference)
        assert post(app, PREVIEW, values, csrf=False)[0] == 403
        status, preview = post(app, PREVIEW, values)
        assert status == 200
        assert preview["data_mode"] == "CAPTURED"
        assert preview["as_of"] == snapshot.as_of.isoformat()
        assert preview["formula"] == "return(close,5)"
        assert preview["record_count"] == 98
        assert service.list_jobs()["total"] == 0

        submission = {**values, "preview_ref": preview["preview_ref"], "idempotency_key": "captured-trial"}
        stale = post(app, SUBMIT, {**submission, "recipe_id": "close_reversal"})
        assert stale[0] == 422 and "重新检查" in stale[1]["error"]
        assert service.factor_studies.list() == []
        service.start()
        status, created = post(app, SUBMIT, submission)
        assert status == 202
        study_id = created["study_id"]
        action = f"/api/research/experiments/{study_id}"
        assert post(app, SUBMIT, submission)[1]["study_id"] == study_id
        assert post(app, f"{action}/replay")[0] == 422

        validation = wait_study(service, study_id, "validation")
        assert validation["state"] == "VALIDATED"
        assert validation["validation"]["test_evaluation"] is None
        candidate, = validation["validation"]["ranking"]
        assert post(app, f"{action}/freeze", {"candidate_id": candidate})[0] == 200
        assert post(app, f"{action}/test", {"idempotency_key": "captured-final"})[0] == 202
        tested = wait_study(service, study_id, "test")
        assert tested["state"] == "TEST_EVALUATED"
        assert tested["test_result"]["test_evaluation"]["oos_status"] == "PASS"
        expected = tested["test_result"]["test_summary"]["fingerprint"]
        bundle = service.runs.get_run(tested["test_job"]["run_id"])["bundle"]
        report = bundle / "complete_report.html"
        original_bytes = report.read_bytes()
        assert expected.encode() in original_bytes
        assert "冻结后的样本外结果".encode() in original_bytes
    finally:
        app.close()

    reopened = LocalApplication(config)
    try:
        assert post(reopened, f"{action}/replay", csrf=False)[0] == 403
        status, result = post(reopened, f"{action}/replay")
        assert status == 200 and result == {
            "status": "MATCH",
            "study_id": study_id,
            "snapshot_ref": reference,
            "expected_fingerprint": expected,
            "actual_fingerprint": expected,
        }
        assert reopened.research_service.list_jobs()["total"] == 2
        assert report.read_bytes() == original_bytes
        assert post(reopened, f"{action}/test", {"idempotency_key": "new-test"})[0] == 422

        report.write_bytes(b"changed")
        status, response = post(reopened, f"{action}/replay")
        assert status == 422 and "恢复报告" in response["error"]
    finally:
        reopened.close()

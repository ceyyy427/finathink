from types import SimpleNamespace

import pytest

from finahinking.research.application_service import ResearchApplicationService
from finahinking.research.job_maintenance import JobMaintenance
from tests.research.test_application_service import payload


def test_scoped_cursor_freezes_equal_timestamp_insertions_and_restarts(tmp_path):
    app = ResearchApplicationService(tmp_path)
    foreign = ResearchApplicationService(tmp_path, scope_id="foreign")
    try:
        app.queue._clock = lambda: 1000
        original = {app.submit(payload(app, key=f"item-{i}"))["job_id"] for i in range(5)}
        foreign.submit(payload(foreign))
        service = JobMaintenance(app)
        first = service.summaries(limit=2)
        assert first["total"] == 5 and first["next_cursor"]
        assert all(item["report_status"] == "NOT_CHECKED" and item["report_url"] is None for item in first["jobs"])
        app.submit(payload(app, key="later"))
        with pytest.raises(ValueError):
            JobMaintenance(foreign).summaries(cursor=first["next_cursor"])
        seen = [row["job_id"] for row in first["jobs"]]
        cursor = first["next_cursor"]
        reopened = ResearchApplicationService(tmp_path)
        try:
            while cursor:
                page = JobMaintenance(reopened).summaries(limit=2, cursor=cursor)
                assert page["total"] == 5
                seen.extend(row["job_id"] for row in page["jobs"])
                cursor = page["next_cursor"]
        finally:
            reopened.close()
        assert set(seen) == original and len(seen) == 5
        for bad in ("junk", "x"*4097):
            with pytest.raises(ValueError):
                service.summaries(cursor=bad)
        for limit in (0, 101, True):
            with pytest.raises(ValueError):
                service.summaries(limit=limit)
    finally:
        app.close()
        foreign.close()


def test_maintenance_is_scoped_read_only_with_unknown_metrics(tmp_path):
    app = ResearchApplicationService(tmp_path)
    foreign = ResearchApplicationService(tmp_path, scope_id="foreign")
    try:
        first = app.submit(payload(app))
        second = app.submit(payload(app, key="second"))
        foreign.submit(payload(foreign))
        app.queue.save_metrics(SimpleNamespace(run_id=first["job_id"], experiments=1, provider_calls=0, bytes_used=123, resource_failures=0, retry_count=0, stage_durations={"QUANT_VALIDATION": 1.25}))
        claimed = app.queue.claim("test-reader", job_id=second["job_id"])
        with app.queue._connect() as db:
            db.execute("UPDATE jobs SET lease_until=0 WHERE job_id=?", (claimed.job_id,))
        status = JobMaintenance(app).status()
        assert status["counts"] == {"queued": 1, "running": 1}
        assert status["expired_leases"] == 1
        assert status["resources"]["disk_free_bytes"] > 0
        assert status["resources"]["worker_rss_bytes"] is None
        assert status["recent"][0]["runtime_metrics"] is None  # Not zero.
        assert status["slow_stages"][0]["seconds"] == 1.25
        assert app.queue.get(claimed.job_id).status.value == "running"  # No recovery on GET.
        assert app.list_jobs()["total"] == 2
    finally:
        app.close()
        foreign.close()

"""Reproducible local SAMPLE workload; no provider calls or external data."""
import argparse
import json
import platform
import sqlite3
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def measure(action, repetitions):
    times = []
    for _ in range(repetitions):
        start = time.perf_counter()
        action()
        times.append((time.perf_counter()-start)*1000)
    return {"mean_ms": statistics.mean(times), "min_ms": min(times), "max_ms": max(times), "repetitions": repetitions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=1000)
    parser.add_argument("--repetitions", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 20 <= args.jobs <= 10000 or not 2 <= args.repetitions <= 100:
        parser.error("jobs: 20–10000; repetitions: 2–100")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from finahinking.p8_2.data_sources import FixtureMarketDataSource
    from finahinking.research.application_service import ResearchApplicationService
    from finahinking.research.contracts import AgentTask
    from finahinking.research.job_maintenance import JobMaintenance
    from finahinking.research.job_queue import JobQueue

    result = {"schema_version": 1, "platform": platform.platform(), "python": platform.python_version(), "sqlite": sqlite3.sqlite_version, "machine": platform.machine(),
        "workload": {"jobs": args.jobs, "page_size": 20, "repetitions": args.repetitions, "data_mode": "SAMPLE", "observations_per_snapshot": 20, "instrument_count": 1, "model_calls": 0},
        "limits": ["Local measurements, not a universal speed or capacity guarantee.", "History baseline uses the preserved full-detail API over queued jobs; report cold/warm reads are measured separately.", "Lease expiry uses an explicitly controlled clock; two-job spawn and reopen use actual processes."]}
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        snapshot = FixtureMarketDataSource(points=20).snapshot("AAA")
        def submit(app, key):
            return app.submit({"idempotency_key": key, "snapshot_ref": app.snapshots.save(snapshot), "question": "Measure local paper task runtime", "instrument": "AAA", "as_of": "2026-10-01", "model_config_ref": "offline"})
        app = ResearchApplicationService(root / "history", allow_model_calls=False)
        try:
            for i in range(args.jobs):
                submit(app, f"bench-{i}")
            service = JobMaintenance(app)
            result["full_history"] = measure(lambda: app.list_jobs(limit=20), args.repetitions)
            result["summary_history"] = measure(lambda: service.summaries(limit=20), args.repetitions)
            with ThreadPoolExecutor(max_workers=4) as pool:
                start = time.perf_counter()
                pages = list(pool.map(lambda _: service.summaries(limit=20), range(args.repetitions)))
                result["concurrent_reads"] = {"threads": 4, "requests": len(pages), "elapsed_ms": (time.perf_counter()-start)*1000, "all_pages_valid": all(len(page["jobs"]) == 20 for page in pages)}
            assert result["concurrent_reads"]["all_pages_valid"]
        finally:
            app.close()
        app = ResearchApplicationService(root / "spawn", allow_model_calls=False)
        try:
            jobs = [submit(app, f"spawn-{i}") for i in range(2)]
            start = time.perf_counter()
            app.start()
            peak, statuses = 0, []
            deadline = time.monotonic()+120
            while time.monotonic() < deadline:
                statuses = [app.get_job(job["job_id"]) for job in jobs]
                peak = max(peak, sum(job["status"] == "running" for job in statuses))
                if all(job["status"] in {"completed", "failed", "cancelled"} for job in statuses):
                    break
                time.sleep(.02)
            assert all(job["status"] == "completed" for job in statuses), [job["status"] for job in statuses]
            result["spawn"] = {"jobs": 2, "concurrency_limit": 2, "observed_running_peak": peak, "elapsed_ms": (time.perf_counter()-start)*1000}
            run_id = statuses[0]["run_id"]
            def cold():
                app.runs.verification_cache.clear()
                assert app.runs.get_run(run_id)["report_status"] == "AVAILABLE"
            result["cold_report"] = measure(cold, args.repetitions)
            assert app.runs.get_run(run_id)["report_status"] == "AVAILABLE"
            result["warm_report"] = measure(lambda: app.runs.get_run(run_id), args.repetitions)
            path = app.runs.get_run(run_id)["bundle"] / "complete_report.html"
            saved = path.read_bytes()
            path.write_bytes(b"X"+saved[1:])
            result["mutation_status"] = app.runs.get_run(run_id)["report_status"]
            assert result["mutation_status"] == "CORRUPT"
            path.write_bytes(saved)
            assert app.runs.get_run(run_id)["report_status"] == "AVAILABLE"
            result["cache"] = app.runs.verification_cache.statistics()
            result["maintenance"] = JobMaintenance(app).status()
        finally:
            app.close()
        reopened = ResearchApplicationService(root / "spawn", allow_model_calls=False)
        try:
            result["reopen"] = {"jobs": reopened.list_jobs()["total"], "reports_available": all(reopened.get_job(job["job_id"])["report_status"] == "AVAILABLE" for job in jobs)}
            assert result["reopen"] == {"jobs": 2, "reports_available": True}
        finally:
            reopened.close()
        clock = [1000.0]
        queue = JobQueue(root / "lease.sqlite", clock=lambda: clock[0], lease_seconds=1)
        job = queue.enqueue(AgentTask(role="technical", task_id="lease-probe", input_digest="sample-input", inputs={"dataset_ref": "sample"}), "lease-probe")
        assert queue.claim("benchmark", job_id=job.job_id) is not None
        clock[0] += 2
        recovered = queue.recover_expired()
        result["controlled_lease_recovery"] = {"clock": "SIMULATED", "recovered": len(recovered), "state": queue.get(job.job_id).status.value}
        assert len(recovered) == 1 and result["controlled_lease_recovery"]["state"] == "retryable"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("full_history", "summary_history", "cold_report", "warm_report", "spawn", "reopen", "controlled_lease_recovery")}, ensure_ascii=False))


if __name__ == "__main__":
    main()

import os
from concurrent.futures import ThreadPoolExecutor

from finahinking.research.report_verification_cache import ReportVerificationCache
from tests.research.test_run_repository import completed, repository


def test_cache_is_bounded_expires_and_never_caches_failure(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"original")
    now = [0.0]
    cache = ReportVerificationCache(capacity=2, ttl=2, clock=lambda: now[0])
    calls = []
    def verify():
        calls.append(1)
        return "AVAILABLE" if path.read_bytes() == b"original" else "CORRUPT"
    for _ in range(2):
        assert cache.check("one", [path], verify) == "AVAILABLE"
    assert len(calls) == 1
    now[0] = 2.01
    assert cache.check("one", [path], verify) == "AVAILABLE" and len(calls) == 2
    for key in ("two", "three"):
        cache.check(key, [path], verify)
    assert cache.statistics()["entries"] == 2
    before = path.stat()
    path.write_bytes(b"modified")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert cache.check("three", [path], verify) == "CORRUPT"
    count = len(calls)
    assert cache.check("three", [path], verify) == "CORRUPT" and len(calls) == count+1


def test_repository_cache_preserves_integrity_under_mutation_symlinks_and_threads(tmp_path, monkeypatch):
    import finahinking.research.run_repository as module
    repo = repository(tmp_path)
    result, manifest = completed(tmp_path / "reports")
    repo.record_result(result, manifest)
    repo.verification_cache.clear()
    original = module.verify_report_bundle
    calls = []
    def count(path):
        calls.append(1)
        return original(path)
    monkeypatch.setattr(module, "verify_report_bundle", count)
    assert repo.get_run("persisted-run")["report_status"] == "AVAILABLE"
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(value == "AVAILABLE" for value in pool.map(lambda _: repo.get_run("persisted-run")["report_status"], range(20)))
    assert len(calls) == 1
    report = tmp_path / "reports" / "persisted-run" / "complete_report.html"
    original_bytes, before = report.read_bytes(), report.stat()
    report.write_bytes(b"X" + original_bytes[1:])
    os.utime(report, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert repo.get_run("persisted-run")["report_status"] == "CORRUPT"
    report.write_bytes(original_bytes)
    assert repo.get_run("persisted-run")["report_status"] == "AVAILABLE"
    outside = tmp_path / "same.html"
    outside.write_bytes(original_bytes)
    report.unlink()
    report.symlink_to(outside)
    assert repo.get_run("persisted-run")["report_status"] == "CORRUPT"


def test_system_path_alias_above_report_root_remains_supported(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    repo = repository(alias)
    result, manifest = completed(alias / "reports")
    repo.record_result(result, manifest)
    assert repo.get_run("persisted-run")["report_status"] == "AVAILABLE"

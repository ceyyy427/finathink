from dataclasses import replace

from finahinking.research.source_contracts import SourceArtifact
from finahinking.research.source_updates import compare_sources
from tests.research.test_source_intake import _snapshot, source_tree

__all__ = ["source_tree"]


def test_license_change_and_deleted_source_require_review(source_tree):
    _, record, modules = source_tree
    old = _snapshot(record, modules)
    changed = SourceArtifact("LICENSE", "e" * 64, 35)
    new = replace(old, commit="f" * 40,
                  artifacts=(changed, *(x for x in old.artifacts if x.path not in {"LICENSE", "factors/mean.py"})))
    result = compare_sources(old, new, objects=[
        {"object_id": "factor:mean", "evidence": {"source_refs": [{
            "repository": old.repository, "path": "factors/mean.py"}]}}])
    assert result["removed"] == ["factors/mean.py"]
    assert result["rights_changed"] == ["LICENSE"]
    assert result["affected_items"] == ["factor:mean"]
    assert result["review_required"] is True


def _changed_checkout(tmp_path, source_tree, *, repository=None):
    import hashlib
    import shutil

    from tests.research.test_source_intake import _git

    old_root, record, modules = source_tree
    if repository:
        record["repository"] = repository
        _git(old_root, "remote", "set-url", "origin", repository)
    new_root = tmp_path / "source-new"
    shutil.copytree(old_root, new_root)
    body = b"MIT License\nCopyright new release holder\n"
    (new_root / "LICENSE").write_bytes(body)
    _git(new_root, "add", "LICENSE")
    _git(new_root, "commit", "-qm", "new license")
    artifacts = [{**item, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
                 if item["path"] == "LICENSE" else item for item in record["artifacts"]]
    new = {**record, "commit": _git(new_root, "rev-parse", "HEAD").decode().strip(), "artifacts": artifacts}
    return old_root, _snapshot(record, modules), new_root, _snapshot(new, modules)


def test_source_updates_require_git_bytes_and_persist_explicit_decision(tmp_path, source_tree):
    import pytest

    from finahinking.research.source_updates import SourceVersionStore, source_policy

    old_root, old, new_root, new = _changed_checkout(tmp_path, source_tree)
    path = tmp_path / "artifacts"
    store = SourceVersionStore(path)
    artifact = next(row for row in old.artifacts if row.path == "factors/mean.py")
    evidence = [{"repository": old.repository, "path": artifact.path, "sha256": artifact.sha256, "commit": old.commit}]
    with pytest.raises(ValueError, match="未通过验证"):
        store.stage(old, new, old_checkout=old_root, new_checkout=old_root)
    ref = store.stage(old, new, old_checkout=old_root, new_checkout=new_root)
    assert source_policy(path, evidence)["blocked"] == []
    store.review(ref, decision="WITHDRAW", reviewer="reviewer", rationale="许可变更，保留历史。")
    assert source_policy(path, evidence)["blocked"] == ["test-source:factors/mean.py"]
    again = SourceVersionStore(path)
    assert again.records()[0]["old"]["commit"] == old.commit
    assert again.records()[0]["new"]["commit"] == new.commit
    with pytest.raises(ValueError, match="已冻结"):
        again.review(ref, decision="APPROVE_REFERENCE", reviewer="reviewer", rationale="修改原审核")


def test_old_report_and_replay_still_resolve_old_content(tmp_path, source_tree):
    from finahinking.research.application_service import ResearchApplicationService
    from finahinking.research.reproduction import ReproductionService
    from finahinking.research.source_updates import SourceVersionStore
    from finahinking.research.strategy_catalog import StrategyCatalog
    from tests.research.test_strategy_worker import completed

    repository = StrategyCatalog().template("equal-weight")["source_refs"][0]["repository"]
    old_root, old, new_root, new = _changed_checkout(tmp_path, source_tree, repository=repository)
    app = ResearchApplicationService(tmp_path / "artifact-root", allow_model_calls=False)
    try:
        sample = app.create_strategy_sample()
        values = {key: sample[key] for key in ("contract", "attachment_refs")}
        values["question"] = "等权组合的集中风险如何？"
        preview = app.preview_discovery(values)
        job = app.run_discovery({**values, "authorized": True, "blueprint_fingerprint": preview["fingerprint"],
                                 "idempotency_key": "before-withdraw"})["job"]
        app.start()
        job = completed(app, job["job_id"])
        replay = ReproductionService(app)
        old_entry = app.runs.get_run(job["run_id"])
        report = old_entry["bundle"] / "complete_report.html"
        before = report.read_bytes()
        package = replay.export(job["run_id"], include_data=True)
        saved = replay.restore(package)
        original = replay.recompute(saved["package_id"])
        store = SourceVersionStore(app.root)
        ref = store.stage(old, new, old_checkout=old_root, new_checkout=new_root)
        assert "strategy:equal-weight" in store.records()[0]["difference"]["affected_items"]
        store.review(ref, decision="WITHDRAW", reviewer="local-user", rationale="撤回来源，仅保留历史。")
        blocked = app.preview_discovery(values)
        assert blocked["status"] == "SOURCE_REVIEW_REQUIRED" and "candidates" not in blocked
        assert blocked["source_versions"] == [ref]
        assert app.get_job(job["job_id"])["report_status"] == "AVAILABLE"
        assert report.read_bytes() == before
        assert replay.recompute(saved["package_id"]) == original
        assert replay.export(job["run_id"], include_data=True) == package
    finally:
        app.close()


def test_withdrawn_background_knowledge_blocks_even_when_definition_source_is_current(tmp_path, source_tree):
    from finahinking.research.application_service import ResearchApplicationService
    from finahinking.research.discovery_sample import create_discovery_sample
    from finahinking.research.source_updates import SourceVersionStore, research_source_refs

    app = ResearchApplicationService(tmp_path / "artifacts", allow_model_calls=False)
    try:
        question = "过去的价格动量能否解释下一观察点的收益？"
        sample = create_discovery_sample(app, question)
        values = {"question": question, **{name: sample[name] for name in ("contract", "attachment_refs")}}
        assert app.preview_discovery(values)["status"] == "READY"
        repository = "https://github.com/yutiansut/QUANTAXIS"
        assert all(row["repository"] != repository for row in research_source_refs("FACTOR", "lib-trend-roc"))
        old_root, old, new_root, new = _changed_checkout(tmp_path, source_tree, repository=repository)
        store = SourceVersionStore(app.root)
        ref = store.stage(old, new, old_checkout=old_root, new_checkout=new_root)
        store.review(ref, decision="WITHDRAW", reviewer="local-user", rationale="暂停使用参考资料。")
        assert app.preview_discovery(values)["status"] == "SOURCE_REVIEW_REQUIRED"
        assert app.list_jobs()["total"] == 0
    finally:
        app.close()

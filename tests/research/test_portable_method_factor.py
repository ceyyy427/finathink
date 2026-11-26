"""An accepted external proposal yields a portable, independently evaluated nine-stage test."""
import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

import finahinking
from finahinking.data.research_fields import ResearchFieldAttachment
from finahinking.research.application_service import ResearchApplicationService
from finahinking.research.contracts import stable_digest
from finahinking.research.reproduction import ReproductionService
from finahinking.research.source_updates import SourceVersionStore
from tests.research.test_method_factor_journey import prepared, wait_study
from tests.research.test_portable_package import reseal
from tests.research.test_source_intake import source_tree
from tests.research.test_source_updates import _changed_checkout

__all__ = ["source_tree"]


def _independent_install(tmp_path, package, original_commit):
    root = tmp_path / "isolated-runtime"
    modules = root / "src"
    shutil.copytree(Path(finahinking.__file__).parent, modules / "finahinking",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if original_commit == "unknown":
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(root), "add", "src"], check=True)
        subprocess.run(["git", "-C", str(root), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "-qm", "Frozen runtime fixture"], check=True)
    (root / "package.json").write_text(json.dumps(package), encoding="utf-8")
    script = """
import json
from pathlib import Path
import finahinking
from finahinking.quant.runtime import _code_commit
from finahinking.research.application_service import ResearchApplicationService
from finahinking.research.reproduction import ReproductionService

assert Path(finahinking.__file__).resolve().is_relative_to((Path.cwd() / "src").resolve())
package = json.loads(Path("package.json").read_text())
recorded = json.loads(package["archive"])["inputs"]["policy"]["code_version"]
assert recorded != _code_commit() and "unknown" in {recorded, _code_commit()}
app = ResearchApplicationService(Path("target"), allow_model_calls=False)
try:
    service = ReproductionService(app)
    ref = service.restore(package)["package_id"]
    matched = service.recompute(ref)
    assert matched["products"]["factor_study"]["status"] == "REPRODUCED", matched
    with Path("src/finahinking/research/snapshot_tools.py").open("a") as stream:
        stream.write(chr(10) + "# Fixture source change" + chr(10))
    changed = service.recompute(ref)
    assert changed["products"]["factor_study"]["status"] == "BLOCKED" and "CODE_MISMATCH" in changed["missing_dependencies"]
    assert app.list_jobs()["total"] == 0
finally:
    app.close()
"""
    env = {**os.environ, "PYTHONPATH": str(modules)}
    env.pop("FINAHINKING_CODE_COMMIT", None)
    subprocess.run([sys.executable, "-c", script], env=env, cwd=root, check=True, timeout=90)


def test_method_pilot_only_replays_published_frozen_proposal_with_pinned_dependencies(tmp_path, monkeypatch, source_tree):
    monkeypatch.chdir(tmp_path)  # A fresh user or wheel installation has no checkout in its working directory.
    src = ResearchApplicationService(tmp_path / "original", allow_model_calls=False)
    clean = ResearchApplicationService(tmp_path / "target", scope_id="fresh", allow_model_calls=False)
    try:
        job, handoff, artifact, _, field = prepared(src)
        records = [{**r, "turnover_rate": r["turnover_rate"] * 100} for r in field.records]
        # A later same-day correction became available after that day's only observation.
        unused = {**records[0], "available_at": (pd.Timestamp(records[0]["timestamp"]) + pd.Timedelta(hours=1)).isoformat(),
                  "turnover_rate": 87.654321}
        field = ResearchFieldAttachment(field.snapshot_ref, field.market, {**field.units, "turnover_rate": "percent_free_float"},
                                        [*records, unused])
        src.attach_research_fields(job["job_id"], field)
        src.start()
        study = src.create_method_factor_study(job["job_id"], handoff, artifact["artifact_ref"],
                                               attachment_ref=field.fingerprint, idempotency_key="frozen-external")
        sid = study["study_id"]
        validation = wait_study(src, sid, "validation")
        dest, origin = ReproductionService(clean), ReproductionService(src)
        validation_run = validation["validation_job"]["run_id"]
        locked = origin.export(validation_run, include_data=True, data_scope="workflow")
        assert "FACTOR_TEST_NOT_PUBLISHED" in locked["missing_dependencies"]
        assert json.loads(locked["archive"])["inputs"] is None
        candidate = validation["validation"]["ranking"][0]
        src.freeze_factor_study(sid, candidate)
        src.test_factor_study(sid, "reserved-pilot")
        tested = wait_study(src, sid, "test")
        src.review_factor_study(sid, "REJECT", "reviewer")
        run = tested["test_job"]["run_id"]
        package = origin.export(run, include_data=True, data_scope="workflow")
        body = json.loads(package["archive"])
        inputs = body["inputs"]
        method = inputs["method"]
        assert method["accepted"]["expression"] == artifact["payload"]["expression"]
        assert method["binding"]["artifact_ref"] == artifact["artifact_ref"]
        assert method["binding"]["handoff_ref"] == handoff
        assert "bodies" not in method and "loaded_methods" not in package["archive"]
        assert inputs["review"]["use_event"]["decision"]["evaluation_fingerprint"] == inputs["receipt"]["test_evaluation"]
        assert inputs["receipt"]["test_binding"] == tested["test_result"]["validation_run"]["binding_digest"]
        assert inputs["data"]["attachments"][0]["units"]["turnover_rate"] == "percent_free_float"
        assert len(inputs["data"]["attachments"][0]["records"]) == len(records) and "87.654321" not in package["archive"]
        expected = {r["stage_id"]: r for r in body["expected"]["factor_study"]["steps"]}
        assert len(expected) == 9 and expected["portfolio_backtest"]["metrics"]["entries"]
        assert expected["information_coefficient"]["metrics"]["per_asset"][0]["samples"] > 0
        states = {r["name"] for r in body["history"]}
        assert states == {run + ".html", validation_run + ".html"}
        ref = dest.restore(package)["package_id"]
        monkeypatch.setattr(clean, "_method_store", lambda: pytest.fail("destination attempted to invoke a handoff"))
        first = dest.recompute(ref)
        matched = first["products"]["factor_study"]
        assert first["status"] == matched["status"] == "REPRODUCED", first["missing_dependencies"]
        assert matched["fields_checked"] > 100 and matched["expected_digest"] == matched["actual_digest"]
        assert matched["reproduction_scope"] == "NINE_STAGE_METHOD_TEST"
        for key in ("factor_ref", "evaluation_fingerprint", "universe_ref", "policy_fingerprint", "purpose", "market"):
            altered = copy.deepcopy(body)
            decision = altered["inputs"]["review"]["use_event"]["decision"]
            decision[key] = {"market": "HK", "purpose": "PAPER_SCREENING"}.get(key, "0" * 64)
            with pytest.raises(ValueError, match="审核事件"):
                dest.restore(reseal(package, altered))
        assert first["model_called"] is False and clean.list_jobs()["total"] == 0 and not clean.factor_studies.list()
        for key in ("policy", "data", "receipt"):
            altered = copy.deepcopy(body)
            material = altered["inputs"]
            if key == "policy":
                material["policy"]["fee_bps"] += 10
            elif key == "data":
                material["data"]["attachments"][0]["records"][0]["turnover_rate"] *= 2
            else:
                material["receipt"]["test_run"] = validation_run
            with pytest.raises(ValueError):
                dest.restore(reseal(package, altered))
        relabeled = copy.deepcopy(body)
        revid = "7" * 64
        relabeled["inputs"]["receipt"]["test_evaluation"] = revid
        relabeled["inputs"]["review"]["use_event"]["decision"]["evaluation_fingerprint"] = revid
        with pytest.raises(ValueError, match="原始因子报告"):
            dest.restore(reseal(package, relabeled))
        substituted = copy.deepcopy(body)
        original = substituted["inputs"]
        original["method"]["binding"].update(handoff_ref="method-handoff:" + "0" * 64, artifact_ref="artifact:method-" + "0" * 64)
        original["receipt"]["method_ref"] = stable_digest(original["method"])
        with pytest.raises(ValueError):
            dest.restore(reseal(package, substituted))
        _independent_install(tmp_path, package, inputs["policy"]["code_version"])
        changed_sources = copy.deepcopy(body)
        historical = changed_sources["inputs"]
        historical["method"]["resources"][0]["resource_ref"] = "0" * 64
        historical["receipt"]["method_ref"] = stable_digest(historical["method"])
        mismatched = dest.recompute(dest.restore(reseal(package, changed_sources))["package_id"])
        assert "METHOD_SOURCE_VERSION_UNAVAILABLE" in mismatched["missing_dependencies"]
        old_path, old, new_path, new = _changed_checkout(tmp_path, source_tree,
            repository="https://github.com/yuanwang589-dev/deepseek-harness-quant")
        updates = SourceVersionStore(clean.root)
        changed = updates.stage(old, new, old_checkout=old_path, new_checkout=new_path)
        updates.review(changed, decision="WITHDRAW", reviewer="auditor", rationale="暂停此版本的方法参考资料。")
        blocked = dest.recompute(ref)
        assert "SOURCE_WITHDRAWN" in blocked["missing_dependencies"]
        assert blocked["products"]["factor_study"]["status"] == "BLOCKED"
        assert dest.history(ref)["artifacts"] == body["history"] and clean.list_jobs()["total"] == 0
    finally:
        src.close()
        clean.close()

def test_native_method_code_identity_for_wheel_without_git_metadata(monkeypatch):
    from finahinking.research import factor_validation
    from finahinking.research.research_identity import execution_identity
    from finahinking.research.snapshot_tools import local_research_policy

    published = execution_identity(local_research_policy())
    recorded_git = "f" * 40
    monkeypatch.setattr(factor_validation, "_code_commit", lambda: "unknown")
    assert factor_validation._matches_code_version(recorded_git, published)
    assert not factor_validation._matches_code_version(recorded_git, None)
    assert not factor_validation._matches_code_version("non-git-version", published)
    assert not factor_validation._matches_code_version(recorded_git, {**published, "source_digest": "0" * 64})
    assert not factor_validation._matches_code_version(recorded_git, {**published, "dependencies": dict(published["dependencies"], numpy="other")})
    monkeypatch.setattr(factor_validation, "_code_commit", lambda: "e" * 40)
    assert not factor_validation._matches_code_version(recorded_git, published)
    assert factor_validation._matches_code_version("unknown", published)
    assert not factor_validation._matches_code_version("unknown", {**published, "source_digest": "0" * 64})

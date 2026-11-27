"""Published held-out factor receipts replay independently of the original queue."""

import copy
import json
from dataclasses import replace

import pandas as pd
import pytest

from finahinking.data.connection_settings import InMemoryDataCredentialStore
from finahinking.local_app import LocalAppConfig
from finahinking.p8_2.data_sources import FixtureMarketDataSource
from finahinking.research import portable_factors
from finahinking.research.application_service import ResearchApplicationService
from finahinking.research.contracts import stable_digest
from finahinking.research.factor_discovery import discovery_questions
from finahinking.research.factor_library import FactorLibrary
from finahinking.research.portable_inputs import PortableUnavailable
from finahinking.research.portable_knowledge import knowledge_refs
from finahinking.research.reproduction import ReproductionService
from tests.research.test_factor_library_journey import prepare
from tests.research.test_factor_study_application import values, wait_study
from tests.research.test_multi_factor_discovery import submit
from tests.research.test_saved_panel_journeys import (
    CAPTURE,
    ETFS,
    PANEL,
    STOCKS,
    TIMES,
    attach_request,
    capture_request,
    configure,
    draft_input,
    owned_feed,
    request,
    running_panel_app,
    utc_day,
)

__all__ = ["owned_feed"]


@pytest.mark.parametrize("kind", ["recipe", "snapshot", "panel", "discovery"])
def test_published_factor_replays_in_clean_workspace(tmp_path, kind, monkeypatch):
    source = ResearchApplicationService(tmp_path / "original", allow_model_calls=False)
    clean = ResearchApplicationService(tmp_path / "clean", allow_model_calls=False)
    try:
        source.start()
        if kind in {"recipe", "snapshot"}:
            snapshot = replace(FixtureMarketDataSource(points=120).snapshot("AAA"), mode="CAPTURED")
            if kind == "snapshot":
                last = snapshot.observations[-1]
                snapshot = replace(snapshot, observations=(*snapshot.observations[:-1],
                                   replace(last, available_at=last.timestamp + pd.Timedelta(minutes=30))), as_of=None)
            ref = source.snapshots.save(snapshot)
            if kind == "recipe":
                created = source.create_factor_study(values(ref))
            else:
                inputs = {"snapshot_ref": ref, "instrument": "AAA", "recipe_id": "close_momentum",
                          "question": "检验收盘动量与未来观察收益的相关性"}
                preview = source.preview_snapshot_factor(inputs)
                created = source.create_snapshot_factor({**inputs, "preview_ref": preview["preview_ref"],
                                                        "idempotency_key": "snapshot-test"})
        elif kind == "panel":
            created = source.create_library_factor_study(prepare(source, "lib-value-book-market"))
        else:
            _, created = submit(source, discovery_questions()[0], "original-multi")
        study_id = created["study_id"]
        valid = wait_study(source, study_id, "validation")
        validation = valid["validation"]
        with pytest.raises(PortableUnavailable, match="NOT_PUBLISHED"):
            portable_factors.freeze_run(source, valid["validation_job"]["run_id"], {"factor_study": validation})
        candidates = [row["experiment_id"] for row in validation["experiments"] if row["failure_reason"] is None]
        candidate = candidates[-1]
        source.freeze_factor_study(study_id, candidate)
        source.test_factor_study(study_id, "final")
        finished = wait_study(source, study_id, "test")
        if kind == "discovery":
            source.review_factor_study(study_id, "REJECT", "reviewer", use_scope=finished["review_scope"])
        run = finished["test_job"]["run_id"]
        recorded = finished["test_result"]
        frozen = portable_factors.freeze_run(source, run, {"factor_study": recorded})
        inputs = frozen["inputs"]
        assert inputs["receipt"]["candidate_id"] == candidate
        assert inputs["receipt"]["validation_result"] == validation["fingerprint"]
        assert inputs["receipt"]["test_result"] == recorded["fingerprint"]
        assert portable_factors.replay_run(clean, inputs) == frozen["expected"]
        assert clean.factor_studies.list() == [] and clean.list_jobs()["total"] == 0
        assert list(clean.snapshots.list_snapshots()) == []
        if kind == "recipe":
            assert len(inputs["formulas"]) == 3
            assert frozen["expected"]["factor_study"]["evaluation"]["sample_size"] > 0
            corrupt = copy.deepcopy(inputs)
            other = inputs["formulas"][0]
            corrupt["receipt"].update(candidate_id="experiment-0001", formula_ref=stable_digest(other))
            corrupt["parameters"]["window"] = other["window"]
            corrupt["receipt"]["experiment_ref"] = stable_digest(corrupt["parameters"])
            corrupt["winner"].update(experiment_id="experiment-0001", parameters=copy.deepcopy(corrupt["parameters"]))
            corrupt["winner"]["evaluation"]["candidate_id"] = "experiment-0001"
            with pytest.raises(ValueError, match="冻结候选摘要"):
                portable_factors.validate_run(corrupt)
            duplicates = copy.deepcopy(inputs)
            duplicates["receipt"]["test_run"] = duplicates["receipt"]["validation_run"]
            with pytest.raises(ValueError, match="不同的发表"):
                portable_factors.validate_run(duplicates)
        if kind == "panel":
            assert inputs["data"]["attachments"] and inputs["data"]["contract"]
            assert frozen["expected"]["factor_study"]["backtest"]["entries"]
        if kind == "discovery":
            assert candidate != validation["experiments"][0]["experiment_id"]
            assert inputs["review"]["use_event"]["decision"]["reviewer"] == "reviewer"
            assert inputs["review"]["use_event"]["decision"]["run_ref"] == run
            assert inputs["parameters"]["direction"] == recorded["freeze_receipt"]["direction"]
            assert len(inputs["formulas"]) == 3 and inputs["sources"]
            exported = ReproductionService(source).export(run, include_data=True, data_scope="workflow")
            destination = ReproductionService(clean)
            restored = destination.restore(exported)["package_id"]
            history = destination.history(restored)
            assert history["evidence"]["factor_review"] == inputs["review"]
            for name in ("factor_ref", "evaluation_fingerprint", "universe_ref", "policy_fingerprint"):
                tampered = copy.deepcopy(inputs)
                tampered["review"]["use_event"]["decision"][name] = "a" * 64
                with pytest.raises(ValueError, match="审核事件"):
                    portable_factors.validate_run(tampered)
            tampered = copy.deepcopy(inputs)
            frozen = tampered["settings"]["discovery"]["frozen"]
            frozen["family_ref"] = "a" * 64
            frozen["fingerprint"] = stable_digest({k: v for k, v in frozen.items() if k != "fingerprint"})
            tampered["receipt"]["settings_ref"] = stable_digest(tampered["settings"])
            with pytest.raises(ValueError, match="家族"):
                portable_factors.validate_run(tampered)
            assert len(history["artifacts"]) == 2
            assert destination.recompute(restored)["products"]["factor_study"]["status"] == "REPRODUCED"
            assert not clean.factor_studies.list() and not clean.factor_uses.list()
            formula_ids = {pin["card_id"] for pin in inputs["sources"]}
            formula_refs = {(row["repository"], row["path"]) for row in FactorLibrary().cards() if row["card_id"] in formula_ids}
            withdrawn = next((row["repository"], row["path"]) for row in knowledge_refs(inputs["knowledge_items"])
                             if (row["repository"], row["path"]) not in formula_refs)
            monkeypatch.setattr(portable_factors, "source_policy",
                                lambda root, refs: {"blocked": [r["path"] for r in refs if (r["repository"], r["path"]) == withdrawn]})
            with pytest.raises(PortableUnavailable, match="SOURCE_WITHDRAWN"):
                portable_factors.replay_run(clean, inputs)
        broken = copy.deepcopy(inputs)
        broken["receipt"]["test_period"][0] = broken["receipt"]["sample_period"][0]
        with pytest.raises(ValueError, match="样本切分|审核事件"):
            portable_factors.validate_run(broken)
        changed = copy.deepcopy(inputs)
        chosen = next(row for row in changed["formulas"] if stable_digest(row) == changed["receipt"]["formula_ref"])
        chosen["expression"] = "unsupported(close)"
        changed["receipt"]["formula_ref"] = stable_digest(chosen)
        with pytest.raises(PortableUnavailable, match="FORMULA_VERSION"):
            portable_factors.replay_run(clean, changed)
        report = source.runs.get_run(run)["bundle"] / "complete_report.html"
        report.write_bytes(b"damaged")
        with pytest.raises(ValueError):
            portable_factors.freeze_run(source, run, {"factor_study": recorded})
    finally:
        source.close()
        clean.close()


def test_captured_panel_discovery_package_scopes_prices_and_rule_revisions(tmp_path, owned_feed):
    transport, proof, calls, _, _ = owned_feed
    assets = (*STOCKS, *ETFS)
    config = LocalAppConfig(db_path=str(tmp_path / "source.sqlite"), offline=True)
    with running_panel_app(config, transport, InMemoryDataCredentialStore()) as (app, base):
        configure(app, base, proof, ["prices", "rules"])
        ref = request(app, base, CAPTURE, capture_request(assets), expected=201)["snapshot_ref"]
        rules = request(app, base, PANEL + "capture-fields", attach_request(ref, assets, "rules"), expected=201)
        draft = draft_input(ref, rules["attachment_ref"], assets, "stock")
        draft.update(start=utc_day(TIMES[5]), end=utc_day(TIMES[-10]))
        preview = request(app, base, PANEL + "preview", draft)
        panel = request(app, base, PANEL + "save", {**draft, "preview_fingerprint": preview["preview_fingerprint"]},
                        expected=201)
        original_calls = list(calls)
        service = app.research_service
        _, study = submit(service, discovery_questions()[0], "owned-multi", saved_panel_ref=panel["panel_ref"], market="CN_A")
        study_id = study["study_id"]
        valid = wait_study(service, study_id, "validation")
        candidate = valid["validation"]["ranking"][-1]
        service.freeze_factor_study(study_id, candidate)
        service.test_factor_study(study_id, "heldout")
        tested = wait_study(service, study_id, "test")
        run = tested["test_job"]["run_id"]
        package = ReproductionService(service).export(run, include_data=True, data_scope="workflow")
        data = json.loads(package["archive"])["inputs"]["data"]
        assert data["mode"] == "CAPTURED"
        assert [row["original_ref"] for row in data["attachments"]] == [rules["attachment_ref"]]
        assert all(TIMES[5] <= pd.Timestamp(row["timestamp"]) <= TIMES[-10] for row in data["observations"])
        assert all(pd.Timestamp(row["available_at"]) <= TIMES[-10] for row in data["attachments"][0]["records"])
        assert proof not in str(package)
        assert original_calls == calls
    clean = ResearchApplicationService(tmp_path / "clean", scope_id="fresh", allow_model_calls=False)
    try:
        destination = ReproductionService(clean)
        receipt = destination.recompute(destination.restore(package)["package_id"])
        assert receipt["products"]["factor_study"]["status"] == "REPRODUCED", receipt["missing_dependencies"]
        assert not clean.factor_studies.list() and clean.list_jobs()["total"] == 0
    finally:
        clean.close()

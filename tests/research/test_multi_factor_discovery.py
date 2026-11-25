"""Research questions through the existing spawn, evidence and review lifecycle."""

import pandas as pd
import pytest

from finahinking.data.connection_settings import InMemoryDataCredentialStore
from finahinking.local_app import LocalAppConfig
from finahinking.research.application_service import ResearchApplicationService
from finahinking.research.factor_discovery import _expand_candidates, discovery_questions
from finahinking.research.factor_library import (
    FAMILIES,
    FactorLibrary,
    formula_fingerprint,
    parse_library_expression,
)
from finahinking.research.factor_study_suite import POLICY, run_factor_study_suite
from finahinking.research.factor_validation_sample import ValidationSample, discovery_sample_spec
from finahinking.research.reproduction import ReproductionService
from tests.research.test_factor_study_application import wait_study
from tests.research.test_saved_panel_journeys import (
    CAPTURE,
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


@pytest.fixture
def service(tmp_path):
    application = ResearchApplicationService(tmp_path, allow_model_calls=False)
    try:
        yield application
    finally:
        application.close()


def submit(service, question, key, **inputs):
    options = {"question": question, **inputs}
    plan = service.preview_factor_discovery(options)
    study = service.create_factor_discovery({
        **options, "authorized": True, "idempotency_key": key, "preview_ref": plan["fingerprint"],
    })
    return plan, study


def test_ten_families_frozen_proposals_synonyms_and_dsl_boundary(service):
    plans = [service.preview_factor_discovery({"question": question}) for question in discovery_questions()]
    assert len(plans) == 10
    assert {plan["family"] for plan in plans} == set(FAMILIES)
    assert service.list_jobs()["total"] == 0
    assert not service.snapshots.list_snapshots()
    assert len({tuple(plan["evaluation"]["test_period"]) for plan in plans}) == 10
    for plan in plans:
        assert plan["status"] == "READY"
        assert len({c["formula_fingerprint"] for c in plan["candidates"]}) == 3
        assert plan["excluded"][0]["reason"] == "DUPLICATE_FORMULA"
        assert plan["budget"]["family_size"] == 80
        for candidate in plan["candidates"]:
            assert candidate["source_ids"] and candidate["counterexamples"]
            spec = candidate["factor_spec"]
            assert parse_library_expression(spec["formula"], spec["required_fields"]).expression == spec["formula"]
    alias = service.preview_factor_discovery({"question": "请研究区间动量"})
    assert alias["fingerprint"] == plans[0]["fingerprint"]
    assert service.preview_factor_discovery({"question": "动量因子和均值回归因子"})["status"] == "NEEDS_CLARIFICATION"
    with pytest.raises(ValueError):
        service.preview_factor_discovery({"question": plans[0]["question"], "formula": "unsupported(close)"})
    with pytest.raises(ValueError):
        parse_library_expression("unsupported(close)", ["close"])
    entries = {row["factor_id"]: row for row in FactorLibrary().entries()}
    original = entries["lib-trend-roc"]
    alias_formula = "negate(ts_return(close,3))"
    entries["version-alias"] = {**original, "factor_id": "version-alias", "formula": alias_formula,
        "formula_fingerprint": formula_fingerprint(alias_formula, original["units"], original["scope"])}
    retain, excluded = _expand_candidates(entries, "lib-trend-roc", ("version-alias", "lib-trend-slope", "lib-trend-ma-spread"))
    assert len(retain) == 3
    assert excluded[0]["retained_factor_id"] == "lib-trend-roc"


def test_stale_preview_and_cancel_preserve_registration(service):
    plan = service.preview_factor_discovery({"question": discovery_questions()[0]})
    with pytest.raises(ValueError, match="重新预览"):
        service.create_factor_discovery({"question": discovery_questions()[1], "authorized": True,
                                        "idempotency_key": "stale", "preview_ref": plan["fingerprint"]})
    _, study = submit(service, discovery_questions()[0], "cancel-discovery")
    study_id = study["study_id"]
    # A worker has begun one trial. A terminal job cancels both that row and the
    # rows still waiting in the pre-registered denominator.
    service.factor_studies.suite_trial_state(study_id, "validation", study["trials"][0]["factor_id"], "RUNNING")
    service.cancel(study["validation_job_id"])
    cancelled = service.factor_study(study_id)
    assert cancelled["validation_job"]["status"] == "cancelled"
    assert {row["state"] for row in cancelled["trials"]} == {"CANCELLED"}
    family = service.factor_suite_family(plan["evaluation"]["family_ref"])
    assert family["registered_count"] == 80
    assert family["published_count"] == 0
    assert all(value is None for value in family["holm"].values())
    _, replay = submit(service, "请研究区间动量", "cancel-discovery")
    assert replay["study_id"] == study_id
    with pytest.raises(ValueError, match="研究归属"):
        submit(service, "请研究区间动量", "new-attempt")


def test_calculation_errors_and_falsification_keep_the_same_denominator(monkeypatch):
    from finahinking.research import factor_study_suite

    signal = factor_study_suite._signal

    def evaluate(library, spec, panel, end):
        if spec.factor_id == "lib-trend-slope":
            raise ArithmeticError("fixture error")
        return signal(library, spec, panel, end)

    monkeypatch.setattr(factor_study_suite, "_signal", evaluate)
    library = FactorLibrary()
    ids = ["lib-trend-roc", "lib-trend-slope"]
    result = run_factor_study_suite("study-controls", [library.spec(factor_id) for factor_id in ids],
        ValidationSample(discovery_sample_spec("trend_momentum")), {**POLICY, "min_ic": 1})
    assert [trial["status"] for trial in result["trials"]] == ["FALSIFIED", "EXECUTION_FAILED"]
    assert result["multiple_testing"]["registered_family_size"] == 80
    assert result["multiple_testing"]["local_batch_adjusted_pvalues"]["lib-trend-slope"] is None


def test_source_review_revokes_preview_before_submission(service, monkeypatch):
    from finahinking.research import factor_discovery

    question = discovery_questions()[0]
    plan = service.preview_factor_discovery({"question": question})
    monkeypatch.setattr(factor_discovery, "source_policy", lambda *_: {"versions": ["unrelated-change"], "blocked": []})
    assert service.preview_factor_discovery({"question": question})["fingerprint"] == plan["fingerprint"]
    factor_discovery.verify_discovery_sources(service.root, plan)
    monkeypatch.setattr(factor_discovery, "source_policy", lambda _root, refs: {"versions": ["updated"], "blocked": [next(iter(refs))["card_id"]]})
    blocked = service.preview_factor_discovery({"question": question})
    assert blocked["status"] == "SOURCE_REVIEW_REQUIRED"
    assert blocked["fingerprint"] != plan["fingerprint"]
    with pytest.raises(ValueError, match="重新预览"):
        service.create_factor_discovery({"question": question, "authorized": True,
                                         "idempotency_key": "withdrawn", "preview_ref": plan["fingerprint"]})
    assert service.list_jobs()["total"] == 0


def test_saved_user_panel_retains_missing_candidate_fields(tmp_path, owned_feed):
    transport, proof, calls, _, rows = owned_feed
    fourth = "STKD"
    assets = (*STOCKS, fourth)
    for kind in ("prices", "rules"):
        rows[kind].extend({**row, "code": fourth} for row in list(rows[kind]) if row["code"] == "ETFA")
    rows["financial"].extend({**row, "code": fourth, "net_profit": row["net_profit"] + 12}
                             for row in list(rows["financial"]) if row["code"] == "STKA")
    regular_quote = next(row for row in rows["prices"] if row["code"] == STOCKS[0] and row["stamp"] == TIMES[30].isoformat())
    intraday = (TIMES[30] + pd.Timedelta(hours=12)).isoformat()
    rows["prices"].append({**regular_quote, "stamp": intraday, "known": intraday,
                           **{key: regular_quote[key] * 2 for key in ("o", "h", "l", "c", "value")}})
    config = LocalAppConfig(db_path=str(tmp_path / "app.sqlite3"), offline=True)
    with running_panel_app(config, transport, InMemoryDataCredentialStore()) as (app, base):
        configure(app, base, proof, ["prices", "rules", "financial"])
        snapshot = request(app, base, CAPTURE, capture_request(assets), expected=201)["snapshot_ref"]
        rules = request(app, base, PANEL + "capture-fields", attach_request(snapshot, assets, "rules"), expected=201)
        financial = request(app, base, PANEL + "capture-fields", attach_request(snapshot, assets, "financial"), expected=201)
        draft = draft_input(snapshot, rules["attachment_ref"], assets, "stock", (financial["attachment_ref"],))
        draft.update(start=utc_day(TIMES[5]), end=utc_day(TIMES[-10]))
        preview = request(app, base, PANEL + "preview", draft)
        panel = request(app, base, PANEL + "save", {**draft, "preview_fingerprint": preview["preview_fingerprint"]}, expected=201)
        calls_before = list(calls)
        options = {"question": discovery_questions()[4], "saved_panel_ref": panel["panel_ref"], "market": "CN_A"}
        endpoint = "/api/research/factor-suites/discovery"
        assert request(app, base, endpoint + "/preview", options, authorized=False, expected=403)
        plan = request(app, base, endpoint + "/preview", options)
        assert plan["evaluation"]["data_mode"] == "USER_DATA" and plan["panel_fingerprint"] == panel["panel_fingerprint"]
        assert all(candidate["field_issues"] for candidate in plan["candidates"])
        study = request(app, base, endpoint, {**options, "authorized": True,
                          "idempotency_key": "missing-financials", "preview_ref": plan["fingerprint"]}, expected=202)
        validated = wait_study(app.research_service, study["study_id"], "validation")
        trials = validated["validation"]["suite_validation"]["trials"]
        assert len(trials) == 3 and {trial["status"] for trial in trials} == {"NEEDS_DATA"}
        assert all(trial["missing_reason"] for trial in trials)
        assert not validated["validation"]["ranking"]
        family = app.research_service.factor_suite_family(plan["evaluation"]["family_ref"])
        assert family["registered_count"] == 80 and family["published_count"] == 3
        assert all(pvalue is None for pvalue in family["holm"].values())
        assert calls == calls_before
        quality, quality_study = submit(app.research_service, discovery_questions()[5], "declared-financials",
                                        saved_panel_ref=panel["panel_ref"], market="CN_A")
        primary, *comparators = quality["candidates"]
        assert primary["factor_id"] == "lib-quality-roe" and primary["field_issues"] == []
        assert all(row["field_issues"] for row in comparators)
        completed = wait_study(app.research_service, quality_study["study_id"], "validation")
        result = completed["validation"]
        assert result["ranking"] == ["lib-quality-roe"]
        assert [row["status"] for row in result["suite_validation"]["trials"]][1:] == ["NEEDS_DATA", "NEEDS_DATA"]
        reproducer = ReproductionService(app.research_service)
        package = reproducer.export(completed["validation_job"]["run_id"], include_data=True)
        assert package["product_coverage"]["factor_study"] == "REQUIRES_ORIGINAL_WORKFLOW"
        start, _ = quality["evaluation"]["sample_period"]
        cutoff = quality["evaluation"]["test_period"][0]
        assert all(start <= row["timestamp"] < cutoff and row["available_at"] < cutoff
                   for row in package["snapshot"]["observations"])
        observation, = [row for row in package["snapshot"]["observations"]
                        if row["timestamp"] == regular_quote["stamp"] and row["instrument"] == STOCKS[0]]
        assert observation["close"] == regular_quote["c"] and observation["available_at"] != intraday
        assert reproducer.recompute(reproducer.restore(package)["package_id"])["status"] == "PARTIAL"
        assert calls == calls_before


def test_six_questions_use_one_entry_for_real_validation_test_and_review(service):
    service.start()
    reviewed = []
    questions = discovery_questions()
    for index in (0, 1, 2, 3, 4, 9):
        plan, study = submit(service, questions[index], f"journey-{index}")
        study_id = study["study_id"]
        validated = wait_study(service, study_id, "validation")
        assert validated["state"] == "VALIDATED"
        assert validated["validation"]["test_evaluation"] is None
        assert "test_job" not in validated
        trials = validated["validation"]["suite_validation"]["trials"]
        assert len(trials) == len(plan["candidates"])
        for trial in trials:
            if trial["metrics"] is not None:
                assert trial["metrics"]["evaluation_end"] < plan["evaluation"]["test_period"][0]
        family = service.factor_suite_family(plan["evaluation"]["family_ref"])
        assert family["registered_count"] == 80
        assert family["published_count"] == len(trials)
        ranked = validated["validation"]["ranking"]
        assert ranked, trials
        selectable = [x for x in trials if x["status"] in {"PASS", "FALSIFIED"}]
        assert ranked == [x["factor_id"] for x in sorted(selectable, key=lambda x: (
            x["status"] != "PASS", -x["metrics"]["metrics"]["mean_ic"], x["factor_id"]))]
        frozen = service.freeze_factor_study(study_id, ranked[0])
        receipt = frozen["freeze_receipt"]
        assert receipt["formula_fingerprint"] and receipt["parameters"]["direction"] == receipt["direction"]
        assert receipt["cost"] == {"fee_bps": 5, "slippage_bps": 5}
        assert frozen["test_key"] is None
        if index == 0:
            validation_report = service.runs.get_run(validated["validation_job"]["run_id"])["bundle"] / "complete_report.html"
            before = validation_report.read_bytes()
            validation_report.write_bytes(b"tampered validation")
            assert service.factor_study(study_id)["validation"] is None
            with pytest.raises(ValueError, match="冻结证据"):
                service.test_factor_study(study_id, "reserved-by-invalid-report")
            assert service.factor_studies.get(study_id)["test_key"] is None
            validation_report.write_bytes(before)
        service.test_factor_study(study_id, f"test-once-{index}")
        tested = wait_study(service, study_id, "test")
        test_trial, = tested["test_result"]["suite_validation"]["trials"]
        assert test_trial["factor_id"] == ranked[0] and tested["test_result"]["freeze_receipt"] == receipt
        assert tested["review_scope"]["time_domain"] == plan["evaluation"]["test_period"]
        with pytest.raises(ValueError, match="once-only"):
            service.test_factor_study(study_id, f"second-test-{index}")
        for name, changed in (("market", "CN"), ("factor_ref", "a" * 64), ("universe_ref", "b" * 64),
                              ("time_domain", plan["evaluation"]["sample_period"]), ("purpose", "PAPER_SCREENING")):
            with pytest.raises(ValueError, match="逐项确认"):
                service.review_factor_study(study_id, "REJECT", "reviewer",
                                            use_scope={**tested["review_scope"], name: changed})
        decision = "APPROVE_FOR_RESEARCH" if test_trial["status"] == "PASS" else "REJECT"
        if decision == "APPROVE_FOR_RESEARCH" and all(item[2] != decision for item in reviewed):
            test_report = service.runs.get_run(tested["test_job"]["run_id"])["bundle"] / "complete_report.html"
            before = test_report.read_bytes()
            test_report.write_bytes(b"tampered test")
            with pytest.raises(ValueError, match="verified final-test"):
                service.review_factor_study(study_id, decision, "reviewer", use_scope=tested["review_scope"])
            assert service.factor_studies.get(study_id)["review_decision"] is None
            test_report.write_bytes(before)
        final = service.review_factor_study(study_id, decision, "reviewer", use_scope=tested["review_scope"])
        assert final["state"] == "REVIEWED" and final["admission"]["decision"]["decision"] == decision
        assert final["admission"]["decision"]["purpose"] == "FORMULA_RESEARCH"
        reviewed.append((study_id, ranked[0], decision))
    assert {decision for _, _, decision in reviewed} == {"APPROVE_FOR_RESEARCH", "REJECT"}
    for _, factor_id, decision in reviewed:
        lifecycle = service.factor_evidence(factor_id)["lifecycle"]
        assert lifecycle["state"] == ("RESEARCH_APPROVED" if decision == "APPROVE_FOR_RESEARCH" else "REJECTED")

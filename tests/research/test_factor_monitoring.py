"""A descriptive decay warning must never silently mutate weights or rules."""

import copy
import importlib
import importlib.util

import pandas as pd
import pytest

from tests.research.test_factor_lifecycle import evidence


def api():
    name = "finahinking.research.factor_monitoring"
    assert importlib.util.find_spec(name) is not None, "factor monitoring proposal is missing"
    return importlib.import_module(name)


def history(metrics=(.3, .02, -.1), *, mode="USER_DATA_EVALUATION"):
    records = []
    for number, metric in enumerate(metrics):
        row = evidence(mode=mode).to_dict()["local_runs"][0]
        start = pd.Timestamp("2024-01-01T00:00:00Z") + pd.Timedelta(days=number * 30)
        end = start + pd.Timedelta(days=20)
        row["run_ref"] = f"run-{number}"
        row["evaluation_fingerprint"] = f"{number + 1:064x}"
        row["scope"].update(as_of=end.isoformat(), final_test_window=[start.isoformat(), end.isoformat()])
        row["local_metric"]["information_coefficient"] = {"mean_oriented_time_series_ic": metric}
        records.append(row)
    return records


def policy(**changes):
    return {"as_of": "2024-04-01", **changes}


def test_three_nonoverlapping_final_windows_propose_review_not_automatic_changes():
    proposal = api().evaluate_factor_monitoring("a" * 64, history(), policy())
    value = proposal.to_dict()
    assert value["status"] == "REVIEW_RECOMMENDED"
    assert value["baseline_ic"] == .3
    assert value["recent_ic"] == pytest.approx(-.04)
    assert value["factor_weight_updates"] == value["risk_rule_updates"] == {}
    assert value["financial_falsification"] is False
    assert value["revisit_when"]
    assert len(value["evidence_refs"]) == 3
    value["evidence_refs"].clear()
    assert len(proposal.to_dict()["evidence_refs"]) == 3


@pytest.mark.parametrize("status", ["BLOCKED_DATA", "EXECUTION_ERROR", "COMPLETE_WITH_BLOCKS"])
def test_one_failure_or_data_gap_is_not_factor_decay(status):
    rows = history((.3,))
    broken = copy.deepcopy(rows[0])
    broken.update(status=status, run_ref="broken", evaluation_fingerprint="d" * 64)
    rows.append(broken)
    result = api().evaluate_factor_monitoring("a" * 64, rows, policy()).to_dict()
    assert result["status"] == "OBSERVE"
    assert result["baseline_ic"] is result["recent_ic"] is None
    assert result["ignored"]


@pytest.mark.parametrize("changed", ["factor_ref", "policy_fingerprint", "universe"])
def test_mixed_formula_policy_or_universe_cannot_be_averaged(changed):
    rows = history()
    if changed == "universe":
        rows[-1]["scope"]["universe"]["market"] = "US"
    else:
        rows[-1][changed] = "f" * 64
    result = api().evaluate_factor_monitoring("a" * 64, rows, policy()).to_dict()
    assert result["status"] == "INCOMPARABLE"
    assert "SCOPE_MISMATCH" in result["codes"]


def test_duplicate_overlap_future_and_unfinished_windows_do_not_inflate_evidence():
    rows = history()
    duplicate = api().evaluate_factor_monitoring("a" * 64, [rows[0]] * 3, policy()).to_dict()
    assert duplicate["status"] == "OBSERVE" and len(duplicate["evidence_refs"]) == 1
    overlapping = copy.deepcopy(rows)
    overlapping[-1]["scope"]["final_test_window"] = rows[0]["scope"]["final_test_window"]
    assert api().evaluate_factor_monitoring("a" * 64, overlapping, policy()).to_dict()["status"] == "INCOMPARABLE"
    assert api().evaluate_factor_monitoring("a" * 64, rows, policy(as_of="2024-01-25")).to_dict()["status"] == "OBSERVE"
    for row in rows:
        row["phase"] = "validation"
    assert api().evaluate_factor_monitoring("a" * 64, rows, policy()).to_dict()["status"] == "OBSERVE"


def test_one_bad_window_does_not_overrule_two_stable_windows_or_unknowns():
    stable = api().evaluate_factor_monitoring("a" * 64, history((.3, .3, -.1)), policy()).to_dict()
    assert stable["status"] == "OBSERVE"
    rows = history((None, None, None))
    unknown = api().evaluate_factor_monitoring("a" * 64, rows, policy()).to_dict()
    assert unknown["recent_ic"] is None and unknown["status"] == "OBSERVE"


def test_monitoring_draft_uses_existing_preview_review_versions_without_fake_settlement(tmp_path):
    from finahinking.research.application_service import ResearchApplicationService

    app = ResearchApplicationService(tmp_path, allow_model_calls=False)
    try:
        original = app.learning.store.initial()
        proposal = api().evaluate_factor_monitoring("a" * 64, history(), policy())
        review = app.learning.review_factor_monitoring(proposal)
        assert review["source_kind"] == "FACTOR_VALIDATION_MONITORING"
        assert review["settlement"] is None and review["proposal"]["settlement_digest"] is None
        assert review["status"] == "PENDING_REVIEW"
        assert review["preview"]["factor_weights"] == original["factor_weights"]
        assert review["preview"]["risk_rules"] == original["risk_rules"]
        assert len(app.learning.policies()) == 1
        values = {"decision": "APPROVED", "expected_preview_fingerprint": review["preview_fingerprint"],
                  "rationale": "复核连续样本外变化，仅记录观察建议"}
        approved = app.learning.decide(review["review_id"], values)
        assert approved["status"] == "APPROVED"
        changed = app.learning.store.policy(approved["policy_ref"])
        assert changed["version"] == 1
        assert changed["factor_weights"] == original["factor_weights"]
        assert changed["risk_rules"] == original["risk_rules"]
        assert "factor_monitoring" in changed["registry"]
        assert app.learning.store.policy()["policy_ref"] == original["policy_ref"]
        assert app.learning.review_factor_monitoring(proposal)["policy_ref"] == approved["policy_ref"]
        assert len(app.learning.policies()) == 2
        assert app.factor_uses.list() == []
        assert app.list_jobs()["total"] == 0
    finally:
        app.close()
    reopened = ResearchApplicationService(tmp_path, allow_model_calls=False)
    try:
        assert reopened.learning.get(review["review_id"])["policy_ref"] == approved["policy_ref"]
    finally:
        reopened.close()


def test_monitoring_policy_is_bounded_and_future_refresh_rejected():
    for changed in ({"min_windows": 1}, {"drop_threshold": -1}, {"unknown": "execute"},
                    {"as_of": "2999-01-01"}, {"min_windows": True}):
        with pytest.raises(ValueError):
            api().evaluate_factor_monitoring("a" * 64, history(), policy(**changed))
    with pytest.raises(ValueError):
        api().evaluate_factor_monitoring("a" * 64, history() * 100, policy())


def test_explicit_refresh_reads_owned_reports_not_caller_metrics_or_paid_calls(tmp_path):
    from finahinking.research.application_service import ResearchApplicationService

    app = ResearchApplicationService(tmp_path, allow_model_calls=False)
    try:
        result = app.refresh_factor_monitoring("finathink-known-book-to-market", policy())
        assert result["proposal"]["status"] == "OBSERVE" and result["learning_review"] is None
        with pytest.raises(ValueError):
            app.refresh_factor_monitoring("finathink-known-book-to-market", {**policy(), "metrics": history()})
        assert app.list_jobs()["total"] == 0 and app.learning.list_reviews() == []
    finally:
        app.close()


def test_new_scope_note_preserves_prior_registry_and_rejection_creates_no_version(tmp_path):
    from finahinking.research.application_service import ResearchApplicationService

    app = ResearchApplicationService(tmp_path, allow_model_calls=False)
    try:
        first = app.learning.review_factor_monitoring(api().evaluate_factor_monitoring("a" * 64, history(), policy()))
        approved = app.learning.decide(first["review_id"], {"decision": "APPROVED",
            "expected_preview_fingerprint": first["preview_fingerprint"], "rationale": "保留第一范围观察"})
        other = history()
        for row in other:
            row["factor_ref"] = "d" * 64
        proposal = api().evaluate_factor_monitoring("d" * 64, other, policy())
        second = app.learning.review_factor_monitoring(proposal, parent_policy_ref=approved["policy_ref"])
        assert len(second["preview"]["registry"]["factor_monitoring"]) == 2
        rejected = app.learning.decide(second["review_id"], {"decision": "REJECTED",
            "expected_preview_fingerprint": second["preview_fingerprint"], "rationale": "证据不足，保留原记录"})
        assert rejected["policy_ref"] is None
        assert len(app.learning.policies()) == 2
        assert len(app.learning.store.policy(approved["policy_ref"])["registry"]["factor_monitoring"]) == 1
    finally:
        app.close()

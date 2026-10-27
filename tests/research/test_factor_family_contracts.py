"""Five small executable definitions, each with its own input audit."""

import importlib
import importlib.util
import math
from dataclasses import replace

import pandas as pd
import pytest

from finahinking.data.research_fields import ResearchFieldAttachment
from finahinking.p8_2.contracts import DatasetSnapshot, MarketObservation
from finahinking.research.factor_inputs import UniverseSpec, build_factor_panel
from finahinking.research.factor_validation import (
    FactorValidationGateway,
    factor_validation_policy,
    run_factor_validation,
)


def api():
    name = "finahinking.research.factor_families"
    assert importlib.util.find_spec(name) is not None, "audited family calculations are missing"
    return importlib.import_module(name)


def family_panel(*, damage=None, points=100, revision=False, return_source=False):
    dates = pd.date_range("2024-01-01T16:00:00Z", periods=points)
    observations, records = [], []
    for number in range(2):
        for index, stamp in enumerate(dates):
            close = 100 + number * 10 + index / 20 + 3 * math.sin(index / (4 + number))
            opened = close - .2 * math.cos(index / 3)
            observations.append(MarketObservation(f"CN:A{number}", stamp, opened, max(opened, close) + 1,
                min(opened, close) - 1, close, volume=100000, amount=close * 100000,
                available_at=stamp, industry="sector-a", suspended=False))
            row = {"instrument": f"CN:A{number}", "timestamp": stamp.isoformat(), "available_at": stamp.isoformat(),
                "book_equity": 200 + index, "market_cap": 1000 + index * 2, "industry": "sector-a",
                "announcement_at": "2023-12-31T12:00:00Z", "report_period_end": "2023-12-30",
                "effective_from": "2023-12-31", "effective_until": "2025-01-01", "limit_up_price": 150 + number * 10,
                "limit_rule_version": "fixture-limit.v1", "institutional_net_buy": 1000 * math.sin(index / 3),
                "seat_identity_confidence": "SOURCE_VERIFIED_INSTITUTIONAL_AGGREGATE"}
            if damage == "announcement":
                row["announcement_at"] = "2025-01-01"
            if damage == "revision":
                row["revision_at"] = "2025-01-01"
            if damage == "industry":
                row.pop("effective_from")
            if damage == "limit":
                row.pop("limit_rule_version")
            if damage == "seat":
                row["seat_identity_confidence"] = "UNVERIFIED"
            if revision and index == 1:
                row["book_equity"] = 999
                row["available_at"] = dates[4].isoformat()
                row["revision_at"] = dates[4].isoformat()
            records.append(row)
    snapshot = DatasetSnapshot("family-fixture", tuple(observations), mode="SAMPLE")
    units = {"volume": "shares", "amount": "CNY", "book_equity": "CNY", "market_cap": "CNY",
        "industry": "historical_classification", "limit_up_price": "CNY", "limit_rule_version": "rule_version",
        "institutional_net_buy": "CNY", "seat_identity_confidence": "declared_confidence",
        "open": "CNY", "high": "CNY", "low": "CNY", "close": "CNY"}
    attachment = ResearchFieldAttachment(snapshot.fingerprint, "CN", units, records)
    if return_source:
        return snapshot, attachment
    return build_factor_panel(snapshot, attachment, as_of=dates[-1], universe=UniverseSpec("CN", snapshot.instruments))


@pytest.mark.parametrize("family", ["financial", "industry", "price-limit", "institutional", "alpha101"])
def test_each_adapted_family_has_actual_formula_and_nine_step_fixture(family):
    module = api()
    spec = next(value for value in module.family_factor_specs() if module.family_for_spec(value) == family)
    panel = family_panel()
    audit = module.audit_family_inputs(family, panel, {"factor_spec": spec.to_dict()}).to_dict()
    assert audit["status"] == "READY"
    values = module.evaluate_family_factor(spec, panel)
    assert values.notna().sum() >= 150
    frame = panel.to_frame()
    index = ("CN:A0", frame.loc["CN:A0"].index[20])
    row = frame.loc[index]
    expected = {"financial": row.book_equity / row.market_cap,
        "industry": row.return_1d - frame.xs(index[1], level="timestamp").return_1d.mean(),
        "price-limit": row.close / row.limit_up_price, "institutional": row.institutional_net_buy / row.amount,
        "alpha101": (row.close - row.open) / (row.high - row.low + .001)}[family]
    assert values.loc[index] == pytest.approx(expected)
    result = run_factor_validation(spec, panel, factor_validation_policy(), gateway=FactorValidationGateway()).to_dict()
    assert all(step["status"] == "COMPLETE" for step in result["steps"])
    assert result["reproduction_scope"] == "FORMULA_FIXTURE"
    assert result["bindings"]["family_audit"]["family_id"] == family
    assert result["financial_falsification"] is False


@pytest.mark.parametrize("family,damage,code", [("financial", "announcement", "ANNOUNCEMENT_NOT_KNOWN"),
    ("financial", "revision", "REVISION_NOT_KNOWN"), ("industry", "industry", "HISTORICAL_CLASSIFICATION_MISSING"),
    ("price-limit", "limit", "LIMIT_RULE_VERSION_MISSING"), ("institutional", "seat", "SEAT_IDENTITY_UNCERTAIN")])
def test_time_rules_and_identity_gaps_have_separate_data_reasons(family, damage, code):
    module = api()
    panel = family_panel(damage=damage)
    result = module.audit_family_inputs(family, panel, {}).to_dict()
    assert result["status"] == "BLOCKED_DATA"
    assert code in result["codes"]
    spec = next(value for value in module.family_factor_specs() if module.family_for_spec(value) == family)
    validation = run_factor_validation(spec, panel, factor_validation_policy(), gateway=FactorValidationGateway()).to_dict()
    assert validation["status"] == "BLOCKED_DATA"
    assert validation["steps"][4]["status"] == "NOT_RUN"


def test_alpha101_original_form_and_reuse_boundary_are_not_silently_changed():
    module = api()
    spec = next(value for value in module.family_factor_specs() if module.family_for_spec(value) == "alpha101")
    altered = replace(spec, formula="divide(subtract(close,open),add(subtract(high,low),0.01))")
    audit = module.audit_family_inputs("alpha101", family_panel(), {"factor_spec": altered.to_dict()}).to_dict()
    assert audit["status"] == "BLOCKED_DATA"
    assert "DEFINITION_MISMATCH" in audit["codes"]
    valid = module.audit_family_inputs("alpha101", family_panel(), {"factor_spec": spec.to_dict()}).to_dict()
    assert valid["source_reference"] == "https://arxiv.org/abs/1601.00991v3"
    assert valid["reuse_basis"] == "INDEPENDENT_MATH_IMPLEMENTATION_NO_UPSTREAM_CODE"
    assert "WorldQuant" in valid["limitations"][0]


def test_delayed_revision_cannot_replace_the_prior_signal_vintage():
    module = api()
    panel = family_panel(revision=True)
    frame = panel.to_frame().loc["CN:A0"]
    assert frame.iloc[1].book_equity == 200
    assert frame.iloc[2].book_equity == 202
    # A later revision of an older daily vintage cannot displace the newer one.
    assert frame.iloc[4].book_equity == 204
    assert module.audit_family_inputs("financial", panel, {}).to_dict()["status"] == "READY"


def test_financial_proposal_is_only_a_bounded_known_expression():
    import hashlib

    from finahinking.factors.dsl import parse_factor_expression
    from finahinking.research.factor_proposals import FactorProposal, validate_factor_proposal

    spec = next(value for value in api().family_factor_specs() if api().family_for_spec(value) == "financial")
    expression = parse_factor_expression(spec.formula, spec.required_fields)
    hypothesis = "研究已公告账面权益与当日市值的比值"
    digest = hashlib.sha256(f"{hypothesis}|{expression.expression}|{','.join(expression.fields)}".encode()).hexdigest()[:12]
    proposal = FactorProposal("factor-proposal-" + digest, spec.formula, hypothesis, spec.required_fields,
                              {"max_window": 252, "point_in_time": True, "paper_only": True})
    validate_factor_proposal(proposal)


def test_negative_capitalization_or_limit_price_is_data_error_not_a_factor_result():
    import json

    from finahinking.research.factor_inputs import FactorPanel

    for family, field in (("financial", "market_cap"), ("price-limit", "limit_up_price")):
        payload = family_panel().to_dict()
        payload["rows"][0][field] = -1
        audit = api().audit_family_inputs(family, FactorPanel(json.dumps(payload)), {}).to_dict()
        assert audit["status"] == "BLOCKED_DATA"
        assert "NONPOSITIVE_DENOMINATOR" in audit["codes"]


def test_fixed_definition_can_enter_the_versioned_method_charter_without_fake_window_sweep():
    from finahinking.research.factor_workbench import prepare_method_study

    snapshot, fields = family_panel(return_source=True)
    spec = next(value for value in api().family_factor_specs() if api().family_for_spec(value) == "financial")
    charter = prepare_method_study(snapshot, fields, instrument="CN:A0", as_of="2024-04-09",
        question="研究已知财务比值", factor_spec=spec, method_binding={}, policy_fingerprint="a" * 64)
    assert charter["factor_spec"]["window"] == 1
    assert charter["baseline_window"] == 5
    assert charter["parameterized"] is False

"""Real HTTP, worker and published evidence for the discovery entry point."""
import json
from urllib.request import Request, urlopen

import pytest

from tests.p7_5.test_phase4_browser import http_app
from tests.research.test_factor_library_journey import prepare
from tests.research.test_strategy_worker import completed

__all__ = ["http_app"]

CASES = [
    ("价格动量如何影响下一期收益？", "lib-trend-roc"),
    ("RSI 低位能否解释反转？", "lib-reversal-rsi"),
    ("收益波动率能否解释差异？", "lib-volatility-return-std"),
    ("账面市值比有没有样本外证据？", "lib-value-book-market"),
    ("ROE 和收益有何关联？", "lib-quality-roe"),
    ("总资产增长有持续性吗？", "lib-growth-asset-growth"),
    ("等权组合的集中风险如何？", "equal-weight"),
    ("ETF 相对强弱计费后怎样？", "etf-relative-strength"),
]


def call(app, base, path, values=None):
    if values is None:
        with urlopen(base + path, timeout=20) as response:
            return response.read()
    request = Request(base + path, data=json.dumps({**values, "_csrf": app.csrf_token}).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=20) as response:
        return json.load(response)


@pytest.mark.parametrize(("question", "target"), CASES, ids=[target for _, target in CASES])
@pytest.mark.parametrize("http_app", [False], indirect=True)
def test_eight_end_to_end_questions_produce_real_artifacts(http_app, question, target):
    app, base = http_app
    factor = target.startswith("lib-")
    sample = prepare(app.research_service, target) if factor else app.research_service.create_strategy_sample()
    values = {key: sample[key] for key in ("contract", "attachment_refs")}
    values.update(question=question, profile="defensive")
    preview = call(app, base, "/api/research/discovery/preview", values)
    assert preview["status"] == "READY", preview
    assert app.research_service.list_jobs()["total"] == 0
    submitted = call(app, base, "/api/research/discovery",
                     {**values, "blueprint_fingerprint": preview["fingerprint"],
                      "authorized": True, "idempotency_key": "fixture-discovery"})
    job = completed(app.research_service, submitted["job"]["job_id"])
    assert job["discovery"]["blueprint"]["target_id"] == target
    assert job["discovery"]["outbound_model_calls"] == 0
    if factor:
        study = submitted["study_id"]
        prefix = "/api/research/experiments/" + study
        assert job["factor_study"]["test_evaluation"] is None
        assert job["factor_study"]["ranking"] == ["experiment-library"]
        call(app, base, prefix + "/freeze", {"candidate_id": "experiment-library"})
        submitted_test = call(app, base, prefix + "/test", {"idempotency_key": "fixture-heldout"})
        job = completed(app.research_service, submitted_test["test_job"]["job_id"])
        assert job["discovery"]["blueprint"]["fingerprint"] == preview["fingerprint"]
        evidence = job["factor_study"]["library_validation"]
        assert evidence["phase"] == "test" and evidence["backtest"]["quant_runs"]
        reviewed = call(app, base, prefix + "/review", {
            "decision": "APPROVE_FOR_RESEARCH", "reviewer_id": "fixture-reviewer"})
        assert reviewed["state"] == "REVIEWED"
    else:
        result = job["strategy_study"]["result"]
        assert result["ledger"]["fills"]
        latest = next(item for item in reversed(result["decisions"]) if item["status"] == "SCHEDULED")
        entry = call(app, base, "/api/research/pools", {
            "job_id": job["job_id"], "pool_ref": "discovery-reviewed", "stage": "DECISION",
            "decision_origin": "USER", "instrument": next(iter(latest["target_weights"]))})
        cohort = call(app, base, "/api/research/pools/freeze", {
            "pool_ref": entry["pool_ref"], "decision_at": entry["decision_at"]})
        observed = call(app, base, "/api/research/pools/observe", {
            "cohort_ref": cohort["cohort_ref"], "snapshot_ref": sample["snapshot_ref"]})
        assert len(observed["horizons"]) == 4
    assert job["discovery"]["knowledge_suggestion"]
    assert job["discovery"]["product_ref"]
    assert "研究计划与分析师证据".encode() in call(app, base, job["report_url"])

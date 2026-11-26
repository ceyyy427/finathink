"""Strategy-only policy admission and replay use actual published account bindings."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest

from finahinking.local_app import LocalAppConfig, LocalApplication
from finahinking.research.contracts import stable_digest
from finahinking.research.strategy_learning import StrategyLearningService
from finahinking.research.strategy_learning_routes import BASE

from .test_strategy_worker import completed


def submit(app, path, **values):
    code, _, result = app.route("POST", BASE + path, body={"_csrf": app.csrf_token, **values})
    assert code in (200, 201), result
    return result


def test_strategy_application_requires_scoped_admission_and_freezes_actual_pair(tmp_path):
    config = LocalAppConfig(db_path=str(tmp_path / "research.sqlite"))
    app = LocalApplication(config)
    try:
        service = app.research_service
        service.start()
        sample = service.create_strategy_sample()
        inputs = {key: sample[key] for key in ("contract", "attachment_refs")}
        job = service.run_strategy_template({**inputs, "template_id": "equal-weight", "idempotency_key": "frozen-mixed"})
        original = completed(service, job["job_id"])
        study = original["strategy_study"]
        baseline = service.learning.store.policy(original["policy_ref"])
        assert app.route("POST", BASE + "/observations", body={})[0] == 403
        observation = submit(app, "/observations", run_id=original["run_id"], as_of=original["as_of"][:10])
        audit = submit(app, "/audits", observation_ref=observation["observation_ref"])
        assert audit["status"] == "READY" and audit["sample_count"] >= 3
        assert audit["scope"]["asset_scope"] == "MIXED"
        assert audit["scope"]["instruments"] == study["binding"]["contract"]["instrument_ids"]
        assert audit["mean_excess_return"] < 0 and audit["below_benchmark_count"] * 2 > audit["sample_count"]
        proposal = submit(app, f"/audits/{audit['audit_id']}/propose")
        path = f"/proposals/{proposal['proposal_id']}"
        assert proposal["status"] == "PENDING_REVIEW" and proposal["policy"] is None
        assert app.route("POST", BASE + path + "/pair", body={"_csrf": app.csrf_token})[0] == 422
        review = {"decision": "APPROVED", "rationale": "核对七个独立账本观察窗，测试较低的信号资本预算。",
                      "expected_preview_fingerprint": proposal["preview_fingerprint"]}
        wrong = {**review, "expected_preview_fingerprint": "0"*64}
        assert app.route("POST", BASE + path + "/decision", body={"_csrf": app.csrf_token, **wrong})[0] == 422
        with ThreadPoolExecutor(max_workers=2) as threads:
            decisions = list(threads.map(lambda _: submit(app, path + "/decision", **review), range(2)))
        assert decisions[0] == decisions[1]
        approved = decisions[0]
        policy = approved["policy"]
        assert policy["version"] == baseline["version"] + 1 and policy["parent_ref"] == baseline["policy_ref"]
        assert policy["scope"] == audit["scope"] and datetime.fromisoformat(policy["effective_at"]).tzinfo is not None
        assert {key for key in policy["effective"] if policy["effective"][key] != baseline["effective"].get(key)} == {"signal_budget_scale"}
        assert service.learning.store.policies() == [baseline]
        pair = submit(app, path + "/pair")
        assert pair["status"] == "AVAILABLE" and pair["run_id"] == original["run_id"] and pair["fills_changed"]
        left, right = [app.route("GET", BASE + f"/pairs/{pair['pair_id']}/{side}")[2] for side in ("baseline", "candidate")]
        assert left["result"] == study["result"] and left["binding"] == study["binding"]
        assert left["input_fingerprint"] == right["input_fingerprint"] == pair["frozen"]["input_fingerprint"]
        assert left["policy_fingerprint"] == baseline["runtime_fingerprint"]
        assert right["policy_fingerprint"] == policy["runtime_fingerprint"]
        assert left["result"]["ledger"]["fills"] != right["result"]["ledger"]["fills"]
        assert left["result"]["benchmark"] == right["result"]["benchmark"]
        equity = next(row for row in pair["metrics"] if row["metric"] == "final_equity")
        assert equity["delta"] == pytest.approx(right["result"]["ledger"]["marks"][-1]["equity"] - left["result"]["ledger"]["marks"][-1]["equity"])
        assert pair["series"][-1]["candidate"] == right["result"]["ledger"]["marks"][-1]["equity"]
        assert stable_digest(left) == pair["sides"]["baseline"]["evidence_digest"]
        assert stable_digest(right) == pair["sides"]["candidate"]["evidence_digest"]
        assert app.route("POST", BASE + path + "/apply", body={"_csrf": app.csrf_token, "run_id": original["run_id"]})[0] == 422
        assert submit(app, f"/audits/{audit['audit_id']}/propose")["proposal_id"] == proposal["proposal_id"]
        duplicate = submit(app, "/observations", run_id=original["run_id"], as_of="2025-05-01")
        dup_audit = submit(app, "/audits", observation_ref=duplicate["observation_ref"])
        assert dup_audit["status"] == "DUPLICATE_SOURCE" and dup_audit["sample_count"] == 0
        assert app.route("POST", BASE + f"/audits/{dup_audit['audit_id']}/propose", body={"_csrf": app.csrf_token})[0] == 422
        assert service.get_job(job["job_id"])["strategy_study"] == study
    finally:
        app.close()
    reopened = LocalApplication(config)
    try:
        assert submit(reopened, path + "/decision", **review) == {**approved, "pair_id": pair["pair_id"]}
        assert submit(reopened, path + "/pair") == pair
        assert reopened.route("GET", BASE + f"/policies/{policy['policy_ref']}")[2] == policy
        registry = StrategyLearningService(reopened.research_service)
        assert len(registry.store.list("policy")) == len(registry.store.list("pair")) == 1
        with reopened.research_service.queue._connect() as db:
            db.execute("UPDATE strategy_learning_records SET payload='{}' WHERE kind='pair-candidate'")
        with pytest.raises(ValueError, match="完整性"):
            registry.get_pair(pair["pair_id"])
    finally:
        reopened.close()


def test_rejected_etf_and_insufficient_stock_keep_independent_decisions(tmp_path):
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "research.sqlite")))
    try:
        service = app.research_service
        service.start()
        sample = service.create_strategy_sample()
        inputs = {key: sample[key] for key in ("contract", "attachment_refs")}
        learn = StrategyLearningService(service)
        for template in ("etf-relative-strength", "value-quality"):
            queued = service.run_strategy_template({**inputs, "template_id": template, "idempotency_key": template})
            job = completed(service, queued["job_id"])
            obs = learn.settlements.observe(job["run_id"], job["as_of"][:10])
            audit = learn.audit(obs["observation_ref"])
            if template == "value-quality":
                assert audit["status"] == "INSUFFICIENT_SAMPLES" and audit["sample_count"] < audit["minimum_samples"]
                with pytest.raises(ValueError, match="至少三个"):
                    learn.propose(audit["audit_id"])
            else:
                proposal = learn.propose(audit["audit_id"])
                fields = {"expected_preview_fingerprint": proposal["preview_fingerprint"], "rationale": "ETF 样本仍有风险。", "decision": "REJECTED"}
                frozen = learn.decide(proposal["proposal_id"], fields)
                assert frozen["status"] == "REJECTED" and frozen["policy"] is None
                assert learn.decide(proposal["proposal_id"], fields) == frozen
                with pytest.raises(ValueError, match="不可变"):
                    learn.decide(proposal["proposal_id"], {**fields, "decision": "APPROVED"})
                with pytest.raises(ValueError, match="明确批准"):
                    learn.pair(proposal["proposal_id"])
        assert learn.store.list("policy") == learn.store.list("pair") == []
    finally:
        app.close()


def test_stock_and_etf_pairs_and_explicit_eligible_post_effective_application(tmp_path, monkeypatch):
    from dataclasses import replace
    from datetime import UTC, timedelta

    import pandas as pd

    from finahinking.data.panel_contracts import PanelContract, PanelFieldAttachment
    from finahinking.data.research_fields import ResearchFieldStore
    from finahinking.research import strategy_learning, strategy_settlement
    from finahinking.research.strategy_sample import strategy_sample

    config = LocalAppConfig(db_path=str(tmp_path / "research.sqlite"))
    app = LocalApplication(config)
    try:
        service = app.research_service
        service.start()
        snapshot, (attachment,), original, _ = strategy_sample()
        times = original.observed_at
        learning = StrategyLearningService(service)

        def published(template, assets, times, key):
            first, last = pd.Timestamp(times[0]), pd.Timestamp(times[-1])
            selected = tuple(row for row in snapshot.observations if row.instrument in assets and first <= row.timestamp <= last)
            data = replace(snapshot, dataset_id=key, observations=selected, as_of=last)
            fields = [row for row in attachment.records if row["instrument"] in assets and times[0] <= row["timestamp"] <= times[-1]]
            service.snapshots.save(data)
            ref = ResearchFieldStore(service.snapshots).save(PanelFieldAttachment(data.fingerprint, attachment.units, fields,
                                                                                  "Frozen strategy learning fixture"))
            contract = PanelContract(data.fingerprint, assets, times, original.field_units, original.scope_id,
                                     {a: original.currencies[a] for a in assets}, {a: original.timezones[a] for a in assets},
                                     ref, ref, ref, ref)
            created = service.run_strategy_template({"contract": contract.to_dict(), "attachment_refs": [ref],
                                                      "template_id": template, "idempotency_key": key})
            return completed(service, created["job_id"])

        assets = original.instrument_ids
        stock = published("short-term-reversal", [asset for asset in assets if asset.startswith("EQ")], times, "stocks")
        ef = published("etf-relative-strength", assets, times[:51], "funds")
        late = published("etf-relative-strength", assets, times[51:], "subsequent-funds")
        approved_time = datetime.fromisoformat(times[50]).astimezone(UTC) + timedelta(minutes=30)

        class ReviewClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return approved_time.astimezone(tz) if tz else approved_time.replace(tzinfo=None)

        for job, last, scope in ((stock, times[-1], "EQUITY"), (ef, times[50], "ETF")):
            with monkeypatch.context() as clock:
                if scope == "ETF":
                    clock.setattr(strategy_settlement, "datetime", ReviewClock)
                    clock.setattr(strategy_learning, "datetime", ReviewClock)
                    # The first two filled windows can be observed before the third matures.
                    fills = job["strategy_study"]["result"]["ledger"]["fills"]
                    ends = [times[i+5] for i in range(0, 50, 5)
                            if any(times[i] < row["timestamp"] <= times[i+5] for row in fills)]
                    observed = learning.settlements.observe(job["run_id"], ends[1])
                    early = learning.audit(observed["observation_ref"])
                    assert early["status"] == "INSUFFICIENT_SAMPLES" and early["sample_count"] == 2
                receipt = learning.settlements.observe(job["run_id"], last)
                audit = learning.audit(receipt["observation_ref"])
                assert audit["scope"]["asset_scope"] == scope
                assert audit["status"] == "READY" and audit["sample_count"] >= 3
                if scope == "ETF":
                    assert receipt["duplicate_count"] == 2
                    assert audit["sample_count"] == receipt["new_sample_count"] + 2
                proposal = learning.propose(audit["audit_id"])
                approved = learning.decide(proposal["proposal_id"], {"decision": "APPROVED", "rationale": "复核纸面样本后比较风险预算。",
                    "expected_preview_fingerprint": proposal["preview_fingerprint"]})
            paired = learning.pair(proposal["proposal_id"])
            assert paired["status"] == "AVAILABLE" and paired["fills_changed"]
            assert paired["sides"]["baseline"]["ledger_digest"] == receipt["ledger_digest"]
            assert paired["sides"]["candidate"]["policy_fingerprint"] == approved["policy"]["runtime_fingerprint"]
            assert paired["frozen"]["report_digest"] == receipt["report_digest"]
        with pytest.raises(ValueError, match="不在批准范围"):
            learning.apply_to_run(proposal["proposal_id"], stock["run_id"])
        with pytest.raises(ValueError, match="生效时间"):
            learning.apply_to_run(proposal["proposal_id"], ef["run_id"])
        application = learning.apply_to_run(proposal["proposal_id"], late["run_id"])
        assert application["status"] == "COMPLETE" and application["run_id"] == late["run_id"]
        evidence = app.route("GET", application["evidence_url"])[2]
        assert evidence["result"]["ledger"]["fills"] != late["strategy_study"]["result"]["ledger"]["fills"]
        assert evidence["binding"]["contract"] == late["strategy_study"]["binding"]["contract"]
        assert evidence["policy_fingerprint"] == approved["policy"]["runtime_fingerprint"]
        assert evidence["binding"]["template_id"] == "etf-relative-strength"
        assert learning.apply_to_run(proposal["proposal_id"], late["run_id"]) == application
    finally:
        app.close()
    reopened = LocalApplication(config)
    try:
        restarted = StrategyLearningService(reopened.research_service)
        assert restarted.apply_to_run(proposal["proposal_id"], late["run_id"]) == application
        assert len(restarted.store.list("application")) == 1
    finally:
        reopened.close()

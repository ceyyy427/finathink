"""Scoped, explicitly admitted learning from settled strategy paper ledgers."""

from datetime import UTC, date, datetime
from math import isfinite
from statistics import fmean

from .contracts import stable_digest, to_jsonable
from .learning_manager import (
    AdmissionDecision,
    LearningAdmissionRecord,
    LearningManager,
    LearningUpdateProposal,
)
from .learning_reliability import instant
from .strategy_learning_store import StrategyLearningStore
from .strategy_settlement import ECONOMIC_MODE, MIN_SAMPLES, StrategySettlementService

METHOD = "strategy-settled-relative-budget.v1"
BUDGET_RULE = "signal_budget_scale"


def _id(kind, binding):
    return "strategy-" + kind + "-" + stable_digest(binding)


def _scope(receipt, source):
    binding = source["binding"]
    contract = binding["contract"]
    return {
        "template_id": binding["template_id"], "template_digest": binding["template_digest"],
        "parameters": binding["parameters"],
        "risk_digest": binding["risk_digest"], "asset_scope": receipt["asset_scope"],
        "instruments": contract["instrument_ids"], "asset_classes": source["asset_classes"],
        "strategy_assets": source["benchmark_instruments"], "data_mode": source["data_mode"],
        "execution_costs": {key: source["policy"][key] for key in ("starting_cash", "fee_bps", "slippage_bps", "max_volume_participation")},
        "market_scope": contract["scope_id"], "currencies": contract["currencies"],
        "timezones": contract["timezones"], "benchmark": {
            key: value for key, value in (source["result"].get("benchmark") or {}).items()
            if key not in {"marks", "status", "reason"}
        },
        "use": "PAPER_STRATEGY", "parent_policy_ref": receipt["policy_ref"],
    }


def _economic_scope(scope):
    """Use settlement's provenance equivalence only when grouping economic evidence."""
    return {**scope, "data_mode": ECONOMIC_MODE[scope["data_mode"]]}


def _frozen_inputs(receipt, source):
    binding = source["binding"]
    common = {key: value for key, value in binding.items() if key not in {"policy_ref", "policy_digest"}}
    if (source["report_digest"] != receipt["report_digest"] or source["ledger_digest"] != receipt["ledger_digest"]
            or binding["policy_ref"] != receipt["policy_ref"] or binding["policy_digest"] != stable_digest(source["policy"])
            or binding["contract"]["snapshot_ref"] != receipt["snapshot_ref"]
            or source["panel"].fingerprint != binding["panel_ref"]):
        raise ValueError("原策略报告、账本、政策与冻结面板证据不一致")
    return {
        "run_id": receipt["run_id"], "report_digest": receipt["report_digest"],
        "original_ledger_digest": receipt["ledger_digest"], "snapshot_ref": receipt["snapshot_ref"],
        "panel_ref": binding["panel_ref"], "binding_digest": stable_digest(binding),
        "input_fingerprint": stable_digest([common, source["asset_classes"]]),
        "original_result_digest": stable_digest(source["result"]),
        "baseline_runtime_fingerprint": binding["policy_digest"],
    }


def _audit_windows(receipt, reusable, claimed):
    selected, excluded, seen = [], [], set()
    cutoff = instant(receipt["as_of"], end=True)
    last_end = None
    for row in sorted(receipt["samples"], key=lambda sample: instant(sample["start_at"])):
        ref = row["sample_ref"]
        start, end, available = (instant(row[key]) for key in ("start_at", "end_at", "available_at"))
        reason = None
        if ref in claimed or (row["duplicate"] and ref not in reusable) or ref in seen:
            reason = "DUPLICATE_SOURCE"
        elif row["status"] != "SETTLED" or row["fill_count"] <= 0:
            reason = "UNSETTLED"
        elif not start < end <= available <= cutoff or (last_end is not None and start < last_end):
            reason = "INVALID_WINDOW"
        elif not all(type(row[k]) in (int, float) and isfinite(row[k]) for k in
                     ("net_return", "benchmark_return", "excess_return", "fees", "slippage")):
            reason = "INVALID_BENCHMARK"
        if reason:
            excluded.append({"sample_ref": ref, "start_at": row["start_at"], "end_at": row["end_at"], "status": reason})
            continue
        seen.add(ref)
        last_end = end
        proof = {key: row[key] for key in ("sample_ref", "start_at", "end_at", "available_at", "fill_count",
                                           "net_return", "benchmark_return", "excess_return", "fees", "slippage")}
        selected.append({**proof, "risk_digest": stable_digest(row["risk"]), "sample_digest": stable_digest(row)})
    return selected, [*receipt["excluded_windows"], *excluded]


class StrategyLearningService:
    def __init__(self, app):
        self.app = app
        self.settlements = StrategySettlementService(app)
        self.store = StrategyLearningStore(app)

    def audit(self, observation_ref):
        identity = _id("audit", [self.app.scope_id, METHOD, observation_ref])
        with self.app.queue._connect() as db:
            prior = self.store.find(db, "audit", identity)
        if prior is not None:
            return self.get_audit(identity)
        receipt = self.settlements.verify(observation_ref)
        source = self.settlements.replay_inputs(receipt["run_id"])
        frozen = _frozen_inputs(receipt, source)
        scope = _scope(receipt, source)
        claimed = self.store.claimed({row["sample_ref"] for row in receipt["samples"]})
        reusable = self._compatible_unclaimed(receipt, scope, claimed)
        selected, excluded = _audit_windows(receipt, reusable, claimed)
        parent = self.app.learning.store.policy(receipt["policy_ref"])
        if parent["runtime_fingerprint"] != frozen["baseline_runtime_fingerprint"]:
            raise ValueError("原政策的执行指纹与报告不匹配")
        scale = source["policy"].get(BUDGET_RULE, 1.0)
        baseline = {"version": parent["version"], "factor_weights": parent["factor_weights"],
                    "risk_rules": {**parent["risk_rules"], BUDGET_RULE: scale}, "registry": parent["registry"],
                    "ledger_digest": receipt["ledger_digest"], "dataset_digest": receipt["snapshot_ref"]}
        excess = [row["excess_return"] for row in selected]
        negatives = sum(value < 0 for value in excess)
        average = fmean(excess) if excess else None
        candidate = max(0.1, round(scale * 0.8, 8))
        status = "INSUFFICIENT_SAMPLES"
        if receipt["status"] == "DUPLICATE_SOURCE" or (not selected and claimed and all(row["sample_ref"] in claimed for row in receipt["samples"])):
            status = "DUPLICATE_SOURCE"
        elif "CONFLICTING_ECONOMIC_SAMPLE" in receipt["reasons"]:
            status = "EVIDENCE_CONFLICT"
        elif receipt["status"] == "SETTLED" and len(selected) >= MIN_SAMPLES:
            status = "READY" if average < 0 and negatives * 2 > len(selected) and candidate < scale else "KEEP_POLICY"
        payload = {
            "audit_id": identity, "observation_ref": observation_ref, "receipt_fingerprint": stable_digest(receipt),
            "scope": scope, "scope_fingerprint": stable_digest(scope), "frozen": frozen, "baseline": baseline,
            "as_of": receipt["as_of"], "source_status": receipt["status"], "source_reasons": receipt["reasons"],
            "status": status, "method": METHOD, "minimum_samples": MIN_SAMPLES, "sample_count": len(selected),
            "economic_set_fingerprint": stable_digest([_economic_scope(scope), sorted(row["sample_ref"] for row in selected)]),
            "selected_samples": selected, "excluded_windows": excluded, "below_benchmark_count": negatives,
            "mean_net_return": fmean(row["net_return"] for row in selected) if selected else None,
            "mean_benchmark_return": fmean(row["benchmark_return"] for row in selected) if selected else None,
            "mean_excess_return": average, "rule": BUDGET_RULE, "before": scale,
            "proposed": candidate if status == "READY" else None, "paper_only": True,
        }
        with self.app.queue._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.store.put(db, "audit", identity, payload)
        return self.get_audit(identity)

    def _compatible_unclaimed(self, receipt, scope, claimed):
        # Only a receipt introducing new observations can open another cohort.
        # The first observer records an economic identity; only a proposal consumes it.
        if receipt["status"] != "SETTLED" or not receipt["new_sample_count"]:
            return set()
        earlier = [row for row in receipt["samples"] if row["duplicate"] and row["sample_ref"] not in claimed]
        evidence_scope = _economic_scope(scope)
        matching = {receipt["run_id"]: True}
        owner_runs = {}
        for ref in {row["first_observation_ref"] for row in earlier}:
            owner = self.settlements.get(ref)
            run_id = owner["run_id"]
            owner_runs[ref] = run_id
            if run_id not in matching:
                frozen = self.settlements.replay_inputs(run_id)
                _frozen_inputs(owner, frozen)
                matching[run_id] = _economic_scope(_scope(owner, frozen)) == evidence_scope
        return {row["sample_ref"] for row in earlier if matching[owner_runs[row["first_observation_ref"]]]}

    def get_audit(self, identity):
        record = self.store.get("audit", identity)
        proposal_id = _id("proposal", [identity])
        with self.app.queue._connect() as db:
            linked = self.store.find(db, "proposal", proposal_id)
        return {**record, "proposal_id": proposal_id if linked else None}

    def _verify(self, audit):
        receipt = self.settlements.verify(audit["observation_ref"])
        if stable_digest(receipt) != audit["receipt_fingerprint"]:
            raise ValueError("策略观察与冻结审核证据不一致")
        return receipt

    def propose(self, identity):
        audit = self.get_audit(identity)
        if audit["proposal_id"]:
            return self.proposal(audit["proposal_id"])
        if audit["status"] != "READY":
            raise ValueError("提案需要至少三个已结算、互不重叠的可比窗口，并达到已登记的基准落后条件")
        self._verify(audit)
        sample_refs = [sample["sample_ref"] for sample in audit["selected_samples"]]
        proposal_id = _id("proposal", [identity])
        proposal = LearningUpdateProposal(
            "PROPOSAL_READY", audit["receipt_fingerprint"], instant(audit["as_of"]).date(),
            risk_rule_updates={BUDGET_RULE: audit["proposed"]}, ledger_digest=audit["frozen"]["original_ledger_digest"],
            dataset_digest=audit["frozen"]["snapshot_ref"], evidence_refs=(identity, *sample_refs),
        )
        preview = LearningManager().preview_proposal(proposal, audit["baseline"])
        value = {"proposal_id": proposal_id, "audit_id": identity, "scope": audit["scope"],
                 "scope_fingerprint": audit["scope_fingerprint"], "parent_policy_ref": audit["scope"]["parent_policy_ref"],
                 "baseline_runtime_fingerprint": audit["frozen"]["baseline_runtime_fingerprint"],
                 "preview_fingerprint": preview.fingerprint, "preview": to_jsonable(preview), "proposal": to_jsonable(proposal),
                 "rule": BUDGET_RULE, "before": audit["before"], "proposed": audit["proposed"], "paper_only": True}
        with self.app.queue._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.store.claim(db, sample_refs, proposal_id)
            self.store.put(db, "proposal", proposal_id, value)
        return self.proposal(proposal_id)

    def proposal(self, identity):
        value = self.store.get("proposal", identity)
        with self.app.queue._connect() as db:
            decision = self.store.find(db, "decision", identity)
            pair_id = _id("pair", [identity])
            pair = self.store.find(db, "pair", pair_id)
        return {**value, "status": decision["admission"]["decision"] if decision else "PENDING_REVIEW",
                "admission": decision["admission"] if decision else None, "policy": decision["policy"] if decision else None,
                "pair_id": pair_id if pair else None}

    def decide(self, identity, values):
        record = self.store.get("proposal", identity)
        if values["expected_preview_fingerprint"] != record["preview_fingerprint"]:
            raise ValueError("政策预览指纹已改变，请重新读取后再审核")
        decision = AdmissionDecision(values["decision"])
        rationale = values["rationale"]
        if not isinstance(rationale, str) or not 1 <= len(rationale.strip()) <= 500:
            raise ValueError("审核依据必须为一至五百字")
        requested_date = values.get("effective_on")
        if requested_date is not None and decision is not AdmissionDecision.APPROVED:
            raise ValueError("拒绝提案无需填写政策生效日")
        requested_date = date.fromisoformat(requested_date) if requested_date is not None else None
        audit = self.get_audit(record["audit_id"])
        with self.app.queue._connect() as db:
            existing = self.store.find(db, "decision", identity)
        def same(existing):
            admission = existing["admission"]
            if (admission["decision"] != decision.value or admission["rationale"] != rationale.strip()
                    or existing["requested_effective_on"] != (requested_date.isoformat() if requested_date else None)):
                raise ValueError("该提案已有不可变审核，请读取原决定")
        if existing:
            same(existing)
            return self.proposal(identity)
        receipt = self._verify(audit)
        source = self.settlements.replay_inputs(receipt["run_id"])
        if _frozen_inputs(receipt, source) != audit["frozen"]:
            raise ValueError("原策略输入与审核基线不一致")
        with self.app.queue._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = self.store.find(db, "decision", identity)
            if existing:
                same(existing)
            else:
                now = datetime.now(UTC)
                if now < instant(receipt["evaluated_at"]):
                    raise ValueError("审核时间早于冻结证据的记录时间，请核对本机时钟")
                if requested_date and requested_date < now.date():
                    raise ValueError("政策生效日期不能早于本次明确审核")
                admission = LearningAdmissionRecord(
                    admission_id=_id("decision", [identity, decision.value]), proposal_digest=record["proposal"]["proposal_digest"],
                    admitted_by="local-user", decision=decision, as_of=now.date(), rationale=rationale.strip(),
                )
                policy = None
                if decision is AdmissionDecision.APPROVED:
                    typed = LearningUpdateProposal(**record["proposal"])
                    manager = LearningManager()
                    admitted = manager.admit_update(typed, admission)
                    if manager.preview_apply(admitted, audit["baseline"]).fingerprint != record["preview_fingerprint"]:
                        raise ValueError("已审政策的参数与冻结预览不一致")
                    version = manager.apply(admitted, audit["baseline"])
                    effective = {**source["policy"], BUDGET_RULE: version.risk_rules[BUDGET_RULE]}
                    effective_at = max(now, instant(requested_date)) if requested_date else now
                    body = {"version": version.version, "typed_policy": to_jsonable(version), "parent_ref": record["parent_policy_ref"],
                            "proposal_id": identity, "scope": audit["scope"], "scope_fingerprint": audit["scope_fingerprint"],
                            "effective_at": effective_at.isoformat(), "effective": effective,
                            "runtime_fingerprint": stable_digest(effective), "paper_only": True}
                    policy = {**body, "policy_ref": _id("policy", body)}
                    self.store.put(db, "policy", policy["policy_ref"], policy)
                self.store.put(db, "decision", identity, {"admission": to_jsonable(admission), "decided_at": now.isoformat(),
                    "policy": policy, "requested_effective_on": requested_date.isoformat() if requested_date else None})
        return self.proposal(identity)

    def pair(self, proposal_id):
        from .strategy_learning_replay import create_pair

        identity = _id("pair", [proposal_id])
        with self.app.queue._connect() as db:
            prior = self.store.find(db, "pair", identity)
        if prior is None:
            proposal = self.proposal(proposal_id)
            if proposal["status"] != "APPROVED":
                raise ValueError("成对复跑需要已明确批准的策略政策")
            audit = self.get_audit(proposal["audit_id"])
            receipt = self._verify(audit)
            source = self.settlements.replay_inputs(receipt["run_id"])
            if _frozen_inputs(receipt, source) != audit["frozen"] or _scope(receipt, source) != proposal["policy"]["scope"]:
                raise ValueError("成对复跑的输入或政策范围与冻结报告不一致")
            result, sides = create_pair(identity, proposal, audit, source)
            with self.app.queue._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                for side, evidence in sides.items():
                    self.store.put(db, "pair-" + side, identity, evidence)
                self.store.put(db, "pair", identity, result)
        return self.get_pair(identity)

    def get_pair(self, identity):
        record = self.store.get("pair", identity)
        for side in ("baseline", "candidate"):
            self.side(identity, side, pair=record)
        return record

    def side(self, identity, side, *, pair=None):
        if side not in ("baseline", "candidate"):
            raise KeyError("策略对照侧不存在")
        record = pair or self.store.get("pair", identity)
        evidence = self.store.get("pair-" + side, identity)
        if stable_digest(evidence) != record["sides"][side]["evidence_digest"]:
            raise ValueError("成对复跑的账本证据与已冻结的对照侧不同")
        return evidence

    def apply_to_run(self, identity, run_id):
        """Explicitly replay a later, scope-compatible source under its admitted policy."""
        from .strategy_learning_replay import run_side

        application_id = _id("application", [identity, run_id])
        with self.app.queue._connect() as db:
            prior = self.store.find(db, "application", application_id)
        if prior:
            return prior
        proposal = self.proposal(identity)
        if proposal["status"] != "APPROVED":
            raise ValueError("后续运行必须选择已批准的策略政策")
        policy = proposal["policy"]
        source = self.settlements.replay_inputs(run_id)
        binding = source["binding"]
        if _scope(source, source) != policy["scope"]:
            raise ValueError("后续策略的模板、资产、市场、基准或原政策不在批准范围内")
        times = binding["contract"]["observed_at"]
        if instant(times[0]) < instant(policy["effective_at"]):
            raise ValueError("后续运行的首个观察早于政策生效时间，请选择生效后开始的报告")
        if instant(times[-1]) > datetime.now(UTC):
            raise ValueError("后续运行不能读取未来观察")
        frozen = _frozen_inputs(source, source)
        from .source_updates import require_active_research_source

        require_active_research_source(self.app.root, "STRATEGY", binding["template_id"])
        evidence = run_side("candidate", source, policy["effective"], policy["policy_ref"], frozen)
        value = {"application_id": application_id, "proposal_id": identity, "run_id": run_id,
                 "status": evidence["result"]["status"], "effective_at": policy["effective_at"],
                 "scope_fingerprint": policy["scope_fingerprint"], "policy_ref": policy["policy_ref"],
                 "policy_fingerprint": evidence["policy_fingerprint"], "input_fingerprint": frozen["input_fingerprint"],
                 "execution_digest": evidence["execution_digest"], "evidence_digest": stable_digest(evidence),
                 "evidence_url": "/api/research/learning/strategy/applications/" + application_id + "/evidence",
                 "source_report_url": f"/research/{run_id}/report/complete", "paper_only": True}
        with self.app.queue._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.store.put(db, "application-evidence", application_id, evidence)
            self.store.put(db, "application", application_id, value)
        return value

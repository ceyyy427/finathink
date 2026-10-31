# Phase 20 Learning Reliability and Maintenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans task-by-task. Native inline execution and one fresh final whole-branch reviewer.

**Goal:** Ground learning suggestions in separated settled samples, compare approved policies on frozen inputs, and measure/improve local task maintenance.
**Architecture:** Pure assessment over verified records, scoped immutable assessment/pair records in existing queue DB, existing LearningReviewStore and worker execution. Scoped cursor summaries and bounded report-verification cache feed existing native UI.
**Tech Stack:** Python >=3.11, SQLite, existing numpy/pandas, native JS, pytest/Playwright; no new runtime dependencies.
**Spec:** docs/superpowers/specs/2026-10-31-finathink-phase20-learning-maintenance-design.md

## Global Constraints
- Local/paper-only; user data interface; no provider SDK or automatic model calls.
- Assessment max 200 candidates, at least 20 nonoverlapping settled windows; fixed 95% Wilson lower loss bound >0.5 plus negative mean for 10% contraction; preserve 0.1 budget/0.01 CVaR floors.
- Frozen source/policy/mode/method identity; unknown and counterexamples remain visible; no automatic policy mutation.
- Explicit exploratory confirmation for single-outcome approvals; historical approved versions remain readable.
- Cursor pages 1–100, initial rowid watermark; cache 64 entries/2 seconds, invalidate all file stamps/path/symlink changes; no cache of failed integrity.
- Benchmark default 1000 SAMPLE jobs/20 read repetitions and actual two-job spawn/reopen; no fabricated speedup.

## Review Focus
- Duplicate economic signals, overlapping windows, late records, mixed identities and tampered stored evidence must not strengthen a recommendation.
- New aggregate proposals and legacy exploratory approvals must preserve explicit authority, immutable previews and existing policy recovery.
- Pair half-enqueue/restart, changed code, failed child and late UI selection must not claim a comparison of different inputs.
- Cursor stability under inserts, equal timestamps and foreign scopes; cached success after same-size/restored-mtime tampering or symlink replacement.
- Missing resource metrics, cache expiry/concurrency and cold/warm benchmark labels must not imply stronger health or performance evidence.

### Task 1: Typed cohort assessment and explanatory evidence
**Files:** Create `src/finahinking/research/learning_reliability.py`, `tests/research/test_learning_reliability.py`.
**Interfaces:** `assess_outcomes(records, *, scope, start, as_of)->dict` consumes verified normalized settled rows, returns bounded coverage/exclusions/statistics/counterexamples/recommendation and fingerprint; `loss_interval(losses,total)->list|None`.
- [x] Write failures for hand-computed Wilson/mean, 19 vs20 windows, mixed winners, zero/unknown, overlap/duplicates, future known-at, mixed mode/code/policy/costs, exhausted floors and >200 inputs.
- [x] Run `python3 -m pytest tests/research/test_learning_reliability.py -q` (Expected FAIL for missing behavior).
- [x] Implement stable chronological selection, fixed rule and explicit descriptive limits.
- [x] Run same command (Expected PASS), commit and native task gate.

### Task 2: Verified durable assessment and governed proposal
**Files:** Create `src/finahinking/research/learning_assessments.py`, `tests/research/test_learning_assessments.py`; modify `learning_review.py`, affected learning API/flow fixtures and frontend approval payload/copy.
**Interfaces:** `LearningAssessmentService(app).assess({anchor_review_id,start,as_of})`, `.get(id)`, `.list()`, `.propose(id)`; consumes Task1. Integrates original scoped settlement reconstruction, proposal preview/admission, `allow_exploratory` validation and audit mode.
- [x] Write failures for actual settled reports→assessment→proposal→approval, no mutation before approval, repeat/restart/cross-scope, stale/corrupt proof, rejected/insufficient samples and explicit exploratory approval with old admitted-policy compatibility.
- [x] Run `python3 -m pytest tests/research/test_learning_assessments.py -q` (Expected FAIL).
- [x] Add content-addressed persistence and bounded automatic selection; revalidate proof before proposal; adapt existing tests to explicit exploratory semantics.
- [x] Run `python3 -m pytest tests/research/test_learning_assessments.py tests/research/test_phase8_learning_flow.py tests/research/test_phase8_learning_api.py tests/research/test_learning_manager.py -q` (Expected PASS), commit and native gate.

### Task 3: Durable frozen paired policy replay
**Files:** Create `src/finahinking/research/learning_replays.py`, `tests/research/test_learning_replays.py`.
**Interfaces:** `LearningReplayService(app).create({review_id,snapshot_ref,as_of})`, `.get(id)`, `.resume(id)`, `.list()`; consumes approved Task2/legacy reviews, existing submit/worker/report manifests. Pair reservation is persisted before deterministic child enqueue; GET never queues.
- [x] Write failures for actual two-child spawn, policy/input binding, metric deltas, retrospective labeling, half-enqueue recovery, repeat/restart, foreign scope, failure/missing report/code mismatch and no implicit paid calls.
- [x] Run `python3 -m pytest tests/research/test_learning_replays.py -q` (Expected FAIL).
- [x] Implement explicit paired scheduling/recovery and conservative comparison eligibility.
- [x] Run `python3 -m pytest tests/research/test_learning_replays.py tests/research/test_phase8_learning_flow.py -q` (Expected PASS), commit and native gate.

### Task 4: Measured pagination, verification cache and maintenance
**Files:** Create `research/job_maintenance.py`, `research/report_verification_cache.py`, `tests/research/test_job_maintenance.py`, `tests/research/test_report_verification_cache.py`, `scripts/benchmark_phase20.py`; modify `application_service.py`, `run_repository.py`, `job_queue.py` only where measured.
**Interfaces:** `JobMaintenance(service).summaries(limit=20,cursor=None)`, `.status()`; content-stamp cache for RunRepository successful report checks; standalone JSON benchmark.
- [x] Capture original list/verification baseline before optimization; write failures for stable scoped cursor, concurrent inserts/equal time, partial metrics, TTL/size/symlink/content mutation and thread safety.
- [x] Run new tests (Expected FAIL for missing APIs/extra verification work).
- [x] Implement bounded summaries/cache/status, justified indexes and benchmark; no report AVAILABLE claim from unverified summaries.
- [x] Run `python3 -m pytest tests/research/test_job_maintenance.py tests/research/test_report_verification_cache.py tests/research/test_run_repository.py tests/research/test_job_queue.py tests/research/test_supervisor.py -q` plus `python3 scripts/benchmark_phase20.py --jobs 1000 --repetitions 20 --output <own-scratch>/benchmark.json` (Expected PASS and measured baseline/optimized/recovery results), commit and native gate.

### Task 5: Integrated native workspace and release evidence
**Files:** Create Phase20 route/page/JS modules, HTTP/browser/Node tests and Chinese guide/review; modify existing learning/jobs navigation, `frontend/src/research.js`, test script, bundles, CI, installed journey and roadmap/capability docs.
**Interfaces:** Scoped GET assessment/pair/maintenance views, CSRF POST assess/propose/create/resume; Task1–4 outputs only. Existing learning review owns approval; fresh selection invalidates late responses.
- [x] Write real HTTP/browser failures for insufficient/ready samples, explicit exploratory vs evidence approval, paired resume/reopen, maintenance/pagination, Chinese errors, inert GET, keyboard/reduced motion and 1440x900/1920x1080/375x812.
- [x] Run new tests (Expected FAIL); implement within existing shell and build/CI; read UI Audit skill for bounded design audit.
- [x] Run Node/build, required browsers, benchmark/install/notebook/lint/governance/secrets/dependencies and `python3 -m pytest -q -ra --tb=short` (Expected PASS), commit and native gate.
- [ ] One independent final reviewer; regrade by impact; one Critical/Important fix pass with RED→GREEN and full suite. Record all rulings/deferred minors.
- Release gate: PR exact-head CI, merge to main, verify merge SHA and main CI, archive evidence and remove only this plan scratch. User already authorized publication.

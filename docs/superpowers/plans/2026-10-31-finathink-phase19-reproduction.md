# Phase 19 Research Reproduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Native inline execution, one fresh final reviewer.

**Goal:** Explain research differences, restore deterministic products, and expose calibrated multi-asset evidence.

**Architecture:** Freeze execution identity in existing analytics; read verified run records; reuse snapshot tools for reproduction. Extend the existing comparison page and local CSRF API.

**Tech Stack:** Python >=3.11, numpy/pandas, native JS, pytest/Playwright.

**Spec:** docs/superpowers/specs/2026-10-31-finathink-phase19-reproduction-design.md

## Global Constraints
- Python >=3.11; no new runtime dependencies; existing blue-white shell.
- Local/paper-only, user-provided data, no provider SDK; no automatic model/network calls.
- UNKNOWN stays unknown; source claims and local calculations separate.
- JSON bounded at 8 MiB, at most 5000 replay rows; evidence at most20 assets/20000 rows.
- Frozen holdout boundaries remain enforced; no private materials or credential export.

## Review Focus
- Stale/missing identities: old reports must not inherit current policy/code.
- Sanitization changes fingerprints: compare explicit numeric products without claiming original provenance equality.
- Asynchronous calendars and delayed availability: no cross-gap return alignment or hindsight exposure.
- Imported mutable/oversized/malformed JSON: bound CPU/storage, no path/network/code execution or scope crossover.
- Late responses and partial reproduction: keep selected run and per-product status accurate.

### Task 1: Frozen identity and explanatory differences
**Files:** Create `src/finahinking/research/research_identity.py`, `research_differences.py`; modify `snapshot_tools.py`, `reports.py`, `local_app.py`; tests `tests/research/test_research_differences.py`.
**Interfaces:** `execution_identity(policy)->dict`; `compare_research(left,right)->dict` consumes verified source snapshots, returns categories/compatibility/metric differences. New analytics carries `reproduction` frozen policy/identity.
- [x] Write failing tests for each changed category, equal/unknown identities, stable source hash and secret-free frozen report projection.
- [x] Run `python3 -m pytest tests/research/test_research_differences.py -q` (expected failing assertions).
- [x] Implement frozen identity at execution and six-category comparison, preserve legacy groups.
- [x] Run `python3 -m pytest tests/research/test_research_differences.py tests/research/test_phase7_comparison.py tests/research/test_phase7_portfolio_workflow.py -q` (expected PASS), commit.

### Task 2: Multi-asset evidence and calibration
**Files:** Create `src/finahinking/research/quant_evidence.py`; modify `snapshot_tools.py`, `reports.py`; test `tests/research/test_quant_evidence.py`.
**Interfaces:** `compute_quant_evidence(snapshot,as_of,publication_before=None)->dict` yields coverage, exposure, variance attribution, stress/sensitivity, uncertainty and limitations, immutable fingerprint.
- [x] Write hand-calculated stock/ETF cases, negative/zero variance, asynchronous gaps, missing units/availability, bounded input and prefix-causality tests.
- [x] Run `python3 -m pytest tests/research/test_quant_evidence.py -q` (expected FAIL).
- [x] Implement gated evidence with exact interval alignment; attach evidence during risk output and freeze in manifest.
- [x] Run `python3 -m pytest tests/research/test_quant_evidence.py tests/research/test_phase7_portfolio_workflow.py tests/research/test_risk_runtime.py -q` (expected PASS), commit.

### Task 3: Portable package and local deterministic restoration
**Files:** Create `src/finahinking/research/reproduction.py`; tests `tests/research/test_reproduction.py`; extend `scripts/check_installed_journey.py`.
**Interfaces:** `ReproductionService(service).export(run_id,include_data=False)`, `.restore(package)`, `.recompute(package_id)`, `.get(package_id)`; scoped, content-addressed local records. Consumes frozen identity, verified reports, snapshots and Task2 evidence.
- [x] Write actual spawn→export→fresh-root→restore→recompute→restart tests plus corruption, missing input/code/dependency, holdout-prefix, consent and foreign scope.
- [x] Run `python3 -m pytest tests/research/test_reproduction.py -q` (expected FAIL).
- [x] Implement allowlisted normalized data/policy/product projection, hash-bound package, bounded restores and per-product receipt; no new job/model call.
- [x] Run `python3 -m pytest tests/research/test_reproduction.py tests/research/test_application_service.py tests/research/test_phase7_run_comparison.py -q` (expected PASS), commit.

### Task 4: Comparison workspace, API and release proof
**Files:** Create `src/finahinking/research/reproduction_page.py`, `frontend/src/research-reproduction.js`, API/browser/JS tests; modify `local_app.py`, `frontend/src/research.js`, package test script, committed bundles, CI; Chinese guide and capability/roadmap/review documents.
**Interfaces:** GET scoped run evidence/differences and restored packages; POST export/restore/recompute protected by existing CSRF. UI renders only server-computed values with per-product reproduction scope.
- [x] Write HTTP/browser failures for six-category comparison, evidence, download/import/recompute/reopen, no automatic POST, error/empty, keyboard/mobile and stale response handling.
- [x] Run new tests and observe expected failures.
- [x] Implement existing-shell workspace with accessible native controls; update build/CI and installed journey.
- [ ] Run frontend tests/build/perf, required Phase19 browsers at 1440x900/1920x1080/375x812, governance/ruff/release/secrets/dependencies and `python3 -m pytest -q -ra --tb=short` (expected PASS), commit.
- [ ] Dispatch one final independent reviewer, address Critical/Important findings with RED→GREEN and full suite.
- [ ] Push PR; verify exact-head CI; merge; verify main SHA and all CI; archive evidence and remove only this plan scratch.

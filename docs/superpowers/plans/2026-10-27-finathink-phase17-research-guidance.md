# Phase 17 研究引导 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成提交前的数据就绪检查、首次研究引导和以证据为中心的研究解释。

**Architecture:** Python 只读投影复用快照、材料校验与任务状态；浏览器只展示并显式提交既有 API。复用原生元素与蓝白外壳，独立模块承载检查与解释。

**Tech Stack:** Python 3.11+、SQLite、原生 JavaScript、Playwright、pytest、Node test。

**Spec:** `docs/superpowers/specs/2026-10-27-finathink-phase17-research-guidance-design.md`

## Global Constraints

- 就绪检查不获取外部数据、不启动任务、不调用模型；受原 CSRF 边界保护。
- 阻断与待核对分开；未知不当零、声明不当验证、SAMPLE 不当真实市场证据。
- 旧报告字节不变；模型授权每次显式确认，不自动重试或改动策略。
- 蓝白外壳、1480px 布局、原生 details、键盘/减少动态效果；无新增运行依赖。

## Review Focus

- 多标的快照中未选标的的未来 PIT：保持与执行引擎整体快照合同一致。
- 输入编辑与迟到检查/任务观察响应：不能展示另一输入或任务的可提交状态和结论。
- 取消/在线恢复：不重复入队、不沿用旧模型授权、不伪称恢复私密材料。
- 损坏发布/零指标/缺失角色：不生成成功金融结论或虚构证据链接。
- 用户来源和模型文本含 HTML、秘密或任意 URL：只显示脱敏文本，链接由服务端白名单构造。

### Task 1: 本地就绪检查和单位声明

**Files:** Create `src/finahinking/research/readiness.py`, `src/finahinking/data/units.py`, `tests/research/test_readiness.py`; Modify `research/application_service.py`, `local_app.py`, `data/user_api.py`, `p8_2/data_sources.py`（均位于 src/finahinking）。

**Interfaces:** Produces `ResearchApplicationService.readiness(values) -> dict` and `inspect_snapshot(snapshot, instrument, as_of) -> list[dict]`; `snapshot_from_batch(..., units=None)` stores validated declaration. API `POST /api/research/readiness` uses existing write guard.

- [ ] Step 1: Add tests: missing/corrupt ref gives BLOCKED without jobs; four rows and current PIT pass; insufficient rows, duplicate UTC dates, future/unknown/global PIT, historical delay block; units/industry/tradability unknown remain REVIEW; material/provider/budget checks; CSRF and inert capture unit validation.
- [ ] Step 2: Run `python3 -m pytest tests/research/test_readiness.py -q`; Expected: FAIL missing readiness behavior.
- [ ] Step 3: Implement bounded redacted projection, fixed repair links, shared existing validators, explicit units before data capture.
- [ ] Step 4: Run `python3 -m pytest tests/research/test_readiness.py tests/research/test_application_service.py tests/data -q`; Expected: PASS.
- [ ] Step 5: Commit `feat: add local research readiness checks`.

### Task 2: 研究解释、阶段与离线摘要

**Files:** Create `src/finahinking/research/explanations.py`, `tests/research/test_explanations.py`; Modify `research/application_service.py`, `research/ui.py`.

**Interfaces:** Consumes existing get_job payload and typed state/verified report. Produces `research_explanation(job) -> dict`, `render_explanation_html(explanation, offline=False) -> str`; get_job exposes `explanation`, `retry_at` and checkpoint presence, with no private path.

- [ ] Step 1: Add tests for queued/running/retryable/cancelled/TIMEOUT/publication invalid/completed, unrecorded vs zero metrics, retained model disagreements and current-run evidence links, escaped output, read-only restart and old-report integrity.
- [ ] Step 2: Run `python3 -m pytest tests/research/test_explanations.py -q`; Expected: FAIL missing explanation contract.
- [ ] Step 3: Implement concise factual summary, evidence/conditions/next-step projection, ordered causal stages, new offline report summary, keep old saved bytes immutable.
- [ ] Step 4: Run `python3 -m pytest tests/research/test_explanations.py tests/research/test_reports.py tests/research/test_application_service.py -q`; Expected: PASS.
- [ ] Step 5: Commit `feat: explain research progress and evidence`.

### Task 3: 首次研究旅程与交互验收

**Files:** Create `frontend/src/research-guidance.js`, `frontend/test/research-guidance.test.mjs`, `tests/p7_5/test_phase17_browser.py`; Modify `frontend/src/run-workspace.js`, `frontend/package.json`, `src/finahinking/local_app.py`, `.github/workflows/ci.yml`, rebuilt browser bundles and docs.

**Interfaces:** Consumes Task 1 readiness and Task 2 explanation; expose check button, four-step guide, linked repair actions, stale-input invalidation, summary and native technical details. Existing submit and observer retain idempotency/authorization boundaries.

- [ ] Step 1: Write Node tests for readiness identity/status validation and late-response rejection. Browser tests: controlled connection→capture→check→spawn→report→reload; blocked repair and no queue; fast edits; restore/cancel/paid authorization reset; responsive and keyboard screenshots.
- [ ] Step 2: Run new Node/browser tests; Expected: FAIL missing controls/projection behavior.
- [ ] Step 3: Implement current-input preflight on submit and explicit check, step states, evidence/timeline display, safe input reuse; add bounded unit fields and field hints.
- [ ] Step 4: Build and run new/existing browser, Node and full Python tests plus Ruff/governance/release/secret/dependency/workflow checks. Expected: PASS, any conditional skip identified. Inspect 1440/1920/375 screenshots.
- [ ] Step 5: Update plan/guide/review/capability snapshot and CI browser list; commit `feat: guide the first research journey`.

## Final gate

Fresh-context whole-branch review using requesting-code-review; verify any important fixes RED→GREEN and full suite, then push/PR/check exact head/merge and check main. Preserve inherited untracked files and managed worktree. User already authorized integration; no intermediate permission round.

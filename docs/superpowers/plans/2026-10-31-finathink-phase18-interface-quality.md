# Phase 18 Interface Diagnostics and Quality Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将受控接口验收、脱敏诊断与固定分析师质量任务接入既有研究工作台。

**Architecture:** 保留通用连接器和兼容适配器；独立模块承担诊断投影/存储、本地 HTTP peer、固定任务评估。真实研究复用当前队列、spawn、预算、材料和调用账本。

**Tech Stack:** Python 3.11+ 标准库/SQLite、原生 JS、pytest、Playwright、Node test；无新运行依赖。

**Spec:** `docs/superpowers/specs/2026-10-31-finathink-phase18-interface-quality-design.md`

## Global Constraints

- 诊断真实请求：一次尝试、一页数据、5秒请求超时；模型合成上下文最多1次、1024输出单位；金额和未返回用量保持未知。
- 生产公网/DNS固定/HTTPS/禁止重定向/字节上限不放宽；本地 peer 只能绑定自己创建的 loopback 端口。
- scope 隔离、记录0600、每scope50份、公共投影128KiB；不保存/导出接口地址、Key/引用名称、原响应或用户数据行。
- 固定评估研究：既有队列/worker/调用账本，最多6次模型调用、一次尝试、既有5–300秒预算。逐次授权与幂等。
- 蓝白 DESIGN、1480px外壳、原生details、键盘/减少动态效果，未知不当零，引用成员有效不当语义真实。

## Review Focus

- 两个并发相同探测键、失去响应及服务重启：先预留、仅一次外发；未知预留不自动重发。
- 合法 JSON 前缀但 HTTP 正文截断，或 SSE 伪装成功：不得 READY；不回显含秘密的异常/响应。
- 用户改模型配置、调换样例/任务或跨 scope 读取：评估只认冻结输入和完整绑定，不落入同名回退。
- 部分角色失败、取消时未知用量、零用量和未发送：分母与未知分开，不美化结构通过率或费用。
- 当前 UI 请求晚到、双击、下载、刷新和键盘：不重复收费、不显示另一输入的结果、不吞控制台错误。

### Task 1: 有界请求和传输失败分类

**Files:** Create `research/transport_failures.py`; Modify `research/provider_transport.py`, `research/provider_adapters.py`, `research/model_call_journal.py`, `data/http_transport.py`, `data/user_api.py` (all under src/finahinking); Create `tests/research/test_interface_failure_contracts.py`.

**Interfaces:** `ProviderTransportError(ValueError).code` uses controlled statuses NON_JSON, STREAM_INTERRUPTED, UNSUPPORTED_STREAM, RESPONSE_TOO_LARGE, TRANSPORT. `JsonApiConnector(..., timeout=15, max_attempts=3, max_pages=20)` adds opt-in bounded limits with unchanged defaults; typed transport codes survive normalization. Shared provider enum/journal permit new terminal outcomes.

- [x] Step 1: Tests for valid-JSON prefix with short Content-Length, IncompleteRead, broken chunk, SSE, invalid JSON, bad size/header, timeout; default security/no redirects; data one attempt and page/timeout validation; adapter/journal matching normalized types and no raw secrets.
- [x] Step 2: Run `python3 -m pytest tests/research/test_interface_failure_contracts.py -q`; Expected FAIL missing contracts.
- [x] Step 3: Implement controlled exceptions and exact body-completion checks; opt-in connector bounds; no retry for deterministic malformed/oversize/interrupted bodies.
- [x] Step 4: Run new tests plus `tests/research/test_provider_transport.py tests/research/test_provider_adapters.py tests/research/test_model_call_journal.py tests/data -q`; Expected PASS.
- [x] Step 5: Commit `feat: classify bounded interface failures`.

### Task 2: 持久诊断、本地兼容验收与显式探测

**Files:** Create `src/finahinking/research/interface_diagnostics.py`, `diagnostic_store.py`, `compatibility_peer.py`; Create `tests/research/test_interface_diagnostics.py`, `test_diagnostic_store.py`.

**Interfaces:** `DiagnosticStore(path, scope_id)` reserve(key, intent), finish(receipt_id, report), get/list public safe records; later quality bindings use same scoped database. `InterfaceDiagnostics(data_connections, provider_configs, store, *, data_transport, provider_transport, credential_store, allow_model_calls)` exposes `run_local()`, `probe(values)`; fixed `run_compatibility_suite()` returns cases and local-only provenance. Projection schema `interface-diagnostic.v1`.

- [x] Step 1: Tests real local HTTP success/auth/rate/bad JSON/missing fields/interrupted/SSE/timeout for data + both adapter kinds. No external DNS/socket/real credentials. Assert local marker and no promotion of user configs.
- [x] Step 2: Tests explicit authorization, offline model rejection, missing credentials, one request, no snapshot/job writes; reserve/repeat/concurrent/crash/no resend; scope/0600/retention/128KiB/corruption; safe exports exclude seeded secret/URL/raw/body/context.
- [x] Step 3: Run new tests; Expected FAIL missing service/store.
- [x] Step 4: Implement owned loopback peer, capped service and allowlisted projection, single-attempt probes with request-local credentials and idempotent reservation.
- [x] Step 5: Run both new modules plus Task1 suite and data tests; Expected PASS. Commit `feat: add controlled interface diagnostics`.

### Task 3: 固定质量任务、研究绑定和有依据的比较

**Files:** Create `src/finahinking/research/quality_cases.py`, `quality_evaluation.py`; Modify `diagnostic_store.py`; Create `tests/research/test_quality_evaluation.py`, `test_quality_research_flow.py`.

**Interfaces:** `quality_cases()` and `quality_case(case_id)` return 3 versioned curated SAMPLE inputs/expected roles. `QualityEvaluation(service, store)` exposes `submit(values)` (existing idempotency + provider consent), `evaluate(job_id)`, `compare(job_ids)` max4. Frozen binding case/input digest/request digest persisted per scope. `research-quality.v1` carries numerator/denominator/unknown, per-role evidence and recorded disagreements, latency/units/cost-null. Only comparisons with identical controlled conditions are comparable.

- [ ] Step 1: Tests catalogue identity/PIT/expected roles; missing consent produces no job; identical submit returns one job; swap task/snapshot/scope/manifest/materials rejects. Existing controlled peer actually runs spawn for full/sparse/disagreement cases and reopens saved results.
- [ ] Step 2: Tests failed/unknown/not-sent calls, zero/missing units, missing reports, foreign references, no denominator ->null; model identity does not grade quality. Same frozen conditions compare; changed budget/policy/case cannot compare. Cite current-run evidence only, no policy mutations.
- [ ] Step 3: Run both new modules; Expected FAIL missing quality service.
- [ ] Step 4: Implement catalogue, immutable binding and read-only scorer over existing request/report/journal, using frozen context validators; live submissions stay in current runtime. Include fixed-task scoring demonstration in local compatibility results without implying live model quality.
- [ ] Step 5: Run new modules and grounded/context/journal/application suites; Expected PASS. Commit `feat: evaluate fixed research tasks with evidence`.

### Task 4: 诊断工作区、交互验收和发布

**Files:** Create `src/finahinking/research/diagnostic_page.py`, `frontend/src/interface-diagnostics.js`, `frontend/test/interface-diagnostics.test.mjs`, `tests/p7_5/test_phase18_browser.py`, `tests/research/test_phase18_api.py`; Modify `local_app.py`, `frontend/src/research.js`, `frontend/package.json`, rebuilt site/package bundles, CI, installed journey and docs.

**Interfaces:** GET `/api/diagnostics/interfaces`, GET `/api/diagnostics/interfaces/<id>`; POST `/api/diagnostics/interfaces/local`, POST `/api/diagnostics/interfaces/probe`; GET `/api/research/quality/cases`, POST `/api/research/quality/jobs`, GET `/api/research/quality/jobs/<id>`, POST `/api/research/quality/compare`. All POST CSRF; record GET/download inert. Same-origin evidence/repair allowlist. New diagnostic page uses existing shell.

- [ ] Step 1: HTTP tests methods/CSRF/scope/malformed budgets/body/no-autocalls/safe JSON export and restart. Node late-response and safe-link tests. Browser local suite→record→download→reload, explicit own-interface probe and failures, controlled spawn quality task→report→assessment/compare; double submit, retained keys, error/recovery, keyboard and 1440/1920/375 screenshots.
- [ ] Step 2: Run new API/Node/browser tests; Expected FAIL missing routes/controls.
- [ ] Step 3: Implement scoped endpoints and Chinese diagnostic workspace, refresh/read-only history, explicit consent/budget, fixed-case model form and evidence details. Link nearby data/model settings. No auto retry or live benchmark on mount.
- [ ] Step 4: Build, targeted + all existing browser, Node/perf, full Python; Ruff/governance/release/secret/pip/actionlint/clean installed wheel+backup. Inspect screenshots in one batched round; run Impeccable technical audit/detector once and fix verified issues in one batch. Expected PASS, conditional skips identified.
- [ ] Step 5: Add guide, review/capability/roadmap status, CI browser list and installed APIs. Commit `feat: expose interface and research quality workbench`.

## Final gate

One fresh-context whole-branch reviewer on the most capable available model using requesting-code-review. Re-grade actual impact, fix important findings RED→GREEN in one pass and verify full suite; record decisions. Push PR, attach it, exact-head CI all success, merge to main and verify main CI; archive own evidence and remove only this plan scratch. User has authorized publishing and no repeated permission round.

# Finathink Dark Intelligence Dashboard Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved Dark Intelligence Design System, global AppShell, and data-bound Overview Dashboard without changing Finathink's research contracts or paper-only boundaries.

**Architecture:** Keep the existing Python `stdlib-http.server` renderer and vanilla JavaScript frontend. Add a small read-only Overview projection sourced from existing local application data, render the shell and Overview with shared CSS tokens, and use the existing ECharts/Lightweight Charts bundle for bounded interactions. No React migration, vendor SDK, broker integration, or new UI framework.

**Tech Stack:** Python 3.13-compatible stdlib HTML renderer, SQLite-backed `LocalApplication`, vanilla ES2020, esbuild, ECharts 6.1, Lightweight Charts 5.2, KaTeX, Node test runner, Playwright browser tests.

**Spec:** `docs/superpowers/specs/2026-10-11-finathink-dark-intelligence-dashboard-design.md`

## Global Constraints

- Preserve existing `ResearchRun`, workbench, knowledge, provider, and data-connection JSON contracts.
- All Overview values come from server-owned payloads; missing values render explicit empty states, never random metrics.
- Keep `PAPER-ONLY`, `READ-ONLY`, provenance, as-of, and limitation language visible.
- Do not write API keys, credential values, prompts, raw provider responses, absolute paths, or arbitrary URLs into public HTML or frontend state.
- Use existing ECharts/Lightweight Charts/KaTeX; do not add React, a router, or a second component library.
- Use the token palette from the spec: `#050B18`, `#081326`, `#071122`, `#0B1730`, `#8B5CF6`, `#A78BFA`, `#38BDF8`, `#34D399`, `#FBBF24`, `#FB7185`.
- Respect keyboard focus, semantic HTML, `prefers-reduced-motion`, and responsive widths 375px, 768px, 1024px, and 1440px.

## Review Focus

- No-data local database: Overview must show actionable empty states rather than fabricated counts.
- Existing sample payloads: the projection must preserve sample/offline/PIT limitations and never label them as live.
- Secret-like or path-like fields in source/provenance: public Overview payload and HTML must reject or redact them.
- Keyboard-only chart interaction: tooltip facts must have a focusable/text-table equivalent, not hover-only behavior.
- Narrow viewport and reduced-motion mode: shell must avoid horizontal overflow and remove non-essential motion.

---

### Task 1: Add the read-only Overview projection

**Files:**
- Modify: `src/finahinking/local_app.py` — add `overview()` and the `GET /api/overview` route; keep all aggregation display-only.
- Test: `tests/p7_5/test_local_app.py` or `tests/research/test_ui.py` — add payload contract and empty-state fixtures.

**Interfaces:**
- Consumes: existing `event()`, `personal()`, `build_research_payload()`, and registered research-run state.
- Produces: `LocalApplication.overview() -> dict[str, Any]` with `schema_version=1`, `mode`, `paper_only`, `as_of`, `metrics`, `activity`, `topics`, `learning_activity`, `research_status`, `limitations`, and `provenance`.

- [x] **Step 1: Write failing tests** for `GET /api/overview`, required top-level fields, real sample provenance, and a fresh `:memory:` app returning empty/zero-safe cards without random values.
- [x] **Step 2: Run `PYTHONPATH=src python3 -m pytest tests/research/test_ui.py tests/p7_5/test_local_app.py -q`** and confirm the new contract assertions fail.
- [x] **Step 3: Implement `LocalApplication.overview()` and the route** using only existing server-owned values. Counts may be zero, ratios may be `None` when denominators are absent, and the response must include a limitation explaining fixture/offline mode.
- [x] **Step 4: Add a public-payload scan assertion** that secret/path/prompt-like values do not appear in the serialized response.
- [x] **Step 5: Re-run the focused tests** and confirm PASS.
- [x] **Step 6: Commit** with `feat(ui): add read-only overview projection`.

### Task 2: Replace the legacy shell styling with the Dark Intelligence token system

**Files:**
- Modify: `src/finahinking/local_app.py` — replace `_APP_CSS` tokens/layout, refine `render_shell()`, `_nav()`, and inspector markup while preserving route semantics.
- Test: `tests/p7_5/test_local_app.py` — extend shell assertions for tokens, `aria-current`, Local Workspace status, and existing navigation labels.

**Interfaces:**
- Consumes: Task 1's payload-independent shell and current `render_shell(page, title, body, ...)` signature.
- Produces: shared classes/tokens `ft-*`, `.app-shell`, `.sidebar`, `.topbar`, `.workspace`, `.panel`, `.status-badge`, `.empty-state`, and stable semantic navigation markup.

- [x] **Step 1: Write failing shell assertions** for deep navy background tokens, purple active navigation, `aria-current="page"`, `Local Workspace`, visible focus styles, and absence of emoji navigation icons.
- [x] **Step 2: Run the focused local-app tests** and confirm the new assertions fail against the light legacy shell.
- [x] **Step 3: Implement the tokenized CSS** with the spec's palette, 8px spacing rhythm, 240px desktop sidebar, responsive single-column layout, typography fallbacks, border/radius/glow restraint, and reduced-motion media query.
- [x] **Step 4: Implement semantic shell markup** with grouped Workspace/Learn/System navigation, topbar context, boundary badge, and truthful local status. Keep unavailable routes visibly marked rather than inventing pages.
- [x] **Step 5: Re-run focused tests and `python3 -m ruff check src tests`**; fix any selector or lint regressions.
- [x] **Step 6: Commit** with `feat(ui): establish dark intelligence app shell`.

### Task 3: Render the Overview Dashboard from the projection

**Files:**
- Modify: `src/finahinking/local_app.py` — replace the `/` body with the Overview structure and bind server-owned values/data attributes.
- Test: `tests/p7_5/test_local_app.py` and `tests/research/test_ui.py` — test metric labels, empty states, source/as-of/boundary text, and no fabricated topics.

**Interfaces:**
- Consumes: `LocalApplication.overview()` from Task 1 and shell components from Task 2.
- Produces: Overview markup containing `.overview-header`, `.metric-grid`, `.metric-card`, `[data-finathink-overview]`, `[data-overview-payload-url]`, `[data-overview-chart]`, `[data-overview-topics]`, `[data-overview-heatmap]`, and accessible fallback tables/lists.

- [x] **Step 1: Write failing HTML assertions** for the four metric cards, chart/list/heatmap regions, truthful `PAPER-ONLY`/`OFFLINE` badges, and explicit empty state on a fresh app.
- [x] **Step 2: Run the local-app focused tests** and confirm the legacy home page fails the new assertions.
- [x] **Step 3: Implement the server-rendered Overview** with real projection values, `aria-label`/`aria-live` text, text-first status badges, and a no-JavaScript fallback that remains useful.
- [x] **Step 4: Ensure the Overview does not expose raw payload objects or secret-like strings** and links only to existing routes (`/events`, `/research`, `/knowledge`, `/workbench`, `/settings/data-connections`).
- [x] **Step 5: Re-run focused Python tests** and confirm existing `/` route behavior remains valid.
- [x] **Step 6: Commit** with `feat(ui): render evidence bound overview dashboard`.

### Task 4: Add Overview chart and interaction behavior

**Files:**
- Create: `frontend/src/overview.js` — normalize the versioned Overview payload, render the activity chart and bounded topic/heatmap interactions, and expose text fallback behavior.
- Modify: `frontend/src/research.js` or `frontend/build.mjs` — include the Overview module in the existing browser bundle without adding a second chart library.
- Modify: `src/finahinking/local_app.py` — serve the existing bundled asset on `/` when needed.
- Test: `frontend/test/overview.test.mjs` — normalization, empty state, unsafe payload rejection, reduced-motion configuration, and keyboard text summary behavior.

**Interfaces:**
- Consumes: `/api/overview` payload and Overview data attributes from Task 3.
- Produces: `normalizeOverviewPayload(payload)`, `overviewStatus(payload, error)`, and `mountOverview(root, { payload, payloadUrl })`.

- [x] **Step 1: Write failing Node tests** for schema validation, metrics preservation, chronological activity points, empty topics, and rejection of `api_key`, `prompt`, `endpoint`, or absolute-path fields.
- [x] **Step 2: Run `cd frontend && node --test test/overview.test.mjs`** and confirm the new module/tests fail.
- [x] **Step 3: Implement normalization and rendering** using the existing bundle, a text summary fallback, explicit status content, and `matchMedia('(prefers-reduced-motion: reduce)')` to disable non-essential animation.
- [x] **Step 4: Wire the module into the existing bundle** and load it on the Overview page without changing the existing research/workbench data bindings.
- [x] **Step 5: Run `cd frontend && npm test && npm run build`** and confirm the bundle and all frontend tests pass.
- [x] **Step 6: Commit** with `feat(ui): add accessible overview interactions`.

### Task 5: Verify AppShell and Overview across routes and viewports

**Files:**
- Modify: `tests/p7_5/test_e2e.py` — extend local browser coverage for Overview, existing Research, Knowledge, and Quant routes.
- Create: `docs/reviews/2026-10-11-finathink-dark-dashboard-ui-review.md` — record viewport, keyboard, reduced-motion, contrast, and known limitations.
- Modify: `docs/RESEARCH_RUNTIME_RELEASE_CHECKLIST.md` — add the UI Phase 1 evidence without changing external verification labels.

**Interfaces:**
- Consumes: Tasks 1–4's route, HTML, bundle, and token contracts.
- Produces: reproducible browser evidence and a Web Design Guidelines review record.

- [x] **Step 1: Run local browser tests** at 1440×900 and 375px for `/`, `/research`, `/knowledge`, and `/quant`; capture console/page errors and screenshot paths.
- [x] **Step 2: Keyboard-test navigation, metric cards, chart fallback, focus rings, and reduced-motion mode**; record any limitation rather than silently skipping it.
- [x] **Step 3: Apply the pinned `web-design-guidelines` checklist** to changed HTML/CSS/JS and fix P0/P1 findings.
- [x] **Step 4: Run `python3 -m pytest -q`, `python3 scripts/validate_governance.py .`, `python3 -m pip check`, `python3 -m ruff check src tests scripts`, and `cd frontend && npm test`**.
- [x] **Step 5: Commit** with `test(ui): verify dark dashboard phase one`.

### Task 6: Final review and handoff

**Files:**
- Modify: `docs/superpowers/plans/2026-10-11-finathink-dark-intelligence-dashboard-phase1.md` — mark completed steps and record evidence.
- Modify: `README.md` only if the Overview route or local run instructions changed.

- [x] **Step 1: Run `git diff --check` and inspect the final diff for scope creep**; ensure no user untracked files were staged.
- [x] **Step 2: Confirm all existing research boundaries remain unchanged** and no external provider or trading claim was added.
- [x] **Step 3: Record test counts, screenshots, unresolved external verification, and next Phase 2 work.**
- [x] **Step 4: Complete the final Native self-review before publishing; no parallel reviewer was dispatched because the approved execution mode is Native single-agent.**

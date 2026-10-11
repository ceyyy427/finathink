# Finathink Dark Intelligence Dashboard Phase 2

## Goal

Extend the Phase 1 Overview with evidence-bound activity, topic heatmap, and a
read-only research comparison workspace. The implementation remains local,
offline-capable, paper-only, and compatible with the Phase 1 Overview schema.

## Data contract

- Keep `schema_version: 1`; add only optional, server-owned fields.
- `activity_series` is an aggregated daily series derived from captured event,
  personal history, learning evidence, and registered research-run timestamps.
- `topic_heatmap` is derived from recorded event/history/node categories and
  contains only topic, count, and last-active date.
- `learning_activity` gains dated counts and outcome counts only when real
  mastery evidence exists.
- `research_comparison` contains run id, as-of, state, stage counts, analyst
  evidence counts, report count, and decision eligibility. It never ranks a
  strategy or invents a quality score.
- No credentials, prompts, raw payloads, personal titles, absolute paths, or
  provider responses cross the public projection.

## Execution tasks

1. Add failing backend and frontend tests for the new projections, empty states,
   safe fields, chart/heatmap rendering, and comparison route.
2. Implement the server projections and `/api/research/compare` plus the
   `/research/compare` read-only page.
3. Render a real activity chart, topic heatmap, dated learning heatmap, and
   comparison table with text fallbacks and keyboard semantics.
4. Update browser acceptance, accessibility checks, release notes, and build
   artifacts.
5. Run full verification, perform a scoped review, commit, push, open a PR,
   verify CI, and merge to `main`.

## Acceptance gates

- Empty local state stays empty; no sample topic/activity is fabricated.
- All chart values are present in the server payload and remain inspectable in
  text/table fallbacks.
- `prefers-reduced-motion` does not block rendering.
- Existing Phase 1 tests and routes remain green.
- Full Python, frontend, browser, governance, dependency, and build checks
  pass before release.

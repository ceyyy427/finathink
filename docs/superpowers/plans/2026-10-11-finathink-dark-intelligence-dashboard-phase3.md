# Finathink Dark Intelligence Dashboard Phase 3

## Goal

Harden the Phase 1/2 dashboard interaction layer for keyboard users, reduced
motion, explicit async states, tooltips, and the requested desktop/mobile
viewport sizes without changing the research data boundary.

## Scope

- Add focus-visible coverage for native controls and all keyboard rows/regions.
- Add live loading/error/empty state containers for Overview and research views.
- Add keyboard-accessible hover/focus tooltips for activity and topic visuals.
- Keep CSS and chart animation disabled or reduced under
  `prefers-reduced-motion: reduce`.
- Verify 1440×900, 1920×1080, 390×844, and 375×812 with no horizontal overflow.
- Capture browser screenshots and retain deterministic artifact manifests.
- Run frontend unit tests, browser acceptance, performance smoke checks, and
  Web Interface Guidelines review.

## Acceptance

- Skip link, nav, chart, table rows, and tooltip targets are reachable by Tab
  and have visible focus rings.
- Loading, error, empty, and ready states are text-first and announced through
  `aria-live` without exposing secrets.
- Hover-only information has an equivalent focus/text summary.
- No `transition: all`, zoom-disabling viewport setting, or unbounded layout
  overflow is introduced.
- Screenshots exist for all requested viewports and CI remains green.

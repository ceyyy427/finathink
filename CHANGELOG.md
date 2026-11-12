# Changelog

All notable changes are recorded here. Entries describe user-visible
research, reproducibility, or governance changes and link to the relevant
phase gate where possible.

The P8.1 entry below is the historical local work-in-progress record. The
reviewed P8.2B branch was published without force and is now merged into the
renamed repository's remote `main` through PR #2.

## Unreleased

- Autonomous factor-research delivery: added an AST-checked factor DSL and
  bounded candidate miner; deterministic IC/ICIR, quantile, turnover/cost,
  decay, admission and append-only registry evidence; and a ResearchCharter
  loop with explicit freeze-before-test/OOS visibility.
- Added secret-free provider readiness at `/api/research/providers` and
  `/settings/providers`. User-owned credentials are referenced by environment
  variable/keychain name only; raw API-key values never enter the UI, logs,
  prompts, checkpoints, or HTML reports.
- Added factor research evidence to the server-owned workbench payload,
  keyboard-readable research page, and self-contained offline HTML report.
  The complete execution scope and release gates are in
  `docs/FINATHINK_AUTONOMOUS_EXECUTION_REPORT.md` and
  `docs/FINATHINK_AUTONOMOUS_VALIDATION.md`.

- P8.2B education and mathematics layer: added the six-unit flagship knowledge
  catalog, Crossref-audited literature metadata, precedence-aware MathML/LaTeX
  rendering, code/math/data traces, context-aware learning routes, typed
  widgets, local KaTeX CSS/fonts, scoped learning evidence, and `finathink`
  distribution/CLI identity while preserving `finahinking` imports.
- P8.2B final local gate: 332 Python tests passed with one environment skip,
  32 focused tests passed, frontend tests/build/performance passed, Crossref
  citation audit passed, and the clean wheel install passed. Browser-level
  assistive-technology verification remains explicitly unverified.
- Normalized experiment result fingerprints to 14 significant digits to avoid
  cross-platform NumPy floating-point noise. Dataset fingerprints and stored
  numeric result values remain unchanged; new regression coverage guards the
  stable-hash contract.
- Published the reviewed P8.2B commit on
  `codex/finathink-p82b-release` and merged it into `main` through PR #2;
  no force push or hosted deployment was used. The branch carries a
  non-destructive history bridge to the renamed repository's `main`.

- P8.2 local conditional checkpoint: added Finathink-owned research/market
  contracts with PIT-aware timezone validation, bounded parameter sweeps,
  deterministic ML fallback, ML/Parameter Lab routes, and a same-origin
  Lightweight Charts + ECharts bundle with a server-rendered table fallback.
- Added isolated capability records and smoke scripts for vectorbt 1.1.1
  (sandbox-only) and Qlib 0.9.7 on Linux/amd64 Python 3.12 (in-memory
  LightGBM/OOS smoke only). QMT remains a disconnected, read-only,
  token-bounded bridge boundary with no order/account methods.
- Added P8.2 reference, architecture, security, performance, dependency, UI,
  QMT, Qlib, vectorbt, and final validation records. Browser-level E2E,
  native Qlib provider/PIT data, live QMT, and optional-license admission are
  explicitly deferred; P8.2B is recorded in the education final validation
  report.
- P8.1 product polish: added a shared responsive shell with Home, Events,
  Explore, Knowledge, Quant, Strategy Lab, Workspace, Inspector context,
  semantic status tokens, actionable empty/error states, progressive knowledge
  depth, staged research language, and reduced-motion-aware launch frames.
- Added the two user-provided Finathink launch assets under `site/assets/` and
  packaged optimized copies for the local application. The animation is a
  finite CSS crossfade/gradient welcome cue, not a financial chart.
- Recorded the actual canonical GitHub audit: authenticated remote is healthy
  with successful CI and a published `v1.3.0`, but local and remote histories
  are unrelated. No force push, silent overwrite, or false public-beta claim
  was made.
- Repaired the optional-quant import-boundary test to run in a fresh
  interpreter, removing order dependence in the `.venv-quant` full suite.
- Prepared the P8 public-beta distribution surface: source-install quickstart,
  public contribution contracts, privacy/security/limitations documentation,
  issue templates, CI and release workflow definitions, and reproducible P8
  gate evidence. A GitHub release is not claimed until a maintainer publishes
  the tagged artifact.

## 0.2.0 - 2026-11-11

- 中文量化研究工作台统一检索 206 个独立对象，提供 12 条已验收的研究路径。
- 自然语言研究支持数据就绪检查、冻结计划、五类分析师、经理综合、显式模型授权和离线证据报告。
- 因子定义库收录十个家族的 80 个独立公式，支持时点面板、预登记验证、稳健性评分和冻结样本外测试。
- 12 个策略模板和 12 条风险规则接入股票与 ETF 统一纸面账本。研究池记录五类决策来源及冻结窗口观察。
- 来源更新按文件比较和审核。历史证据随报告保留，安装验收覆盖备份恢复和索引重建。
- 使用入口见 [量化知识与研究工作台指南](docs/QUANT_KNOWLEDGE_GUIDE.md)。验收基线与后续安排见 [阶段 29–35 规划](docs/FINATHINK_ROADMAP_PHASE_29_35.md)。

## 0.1.0 - 2026-10-03 (source candidate; no GitHub release published)

- Prepared the governed P0–P7 research foundation and local-first P7.5 product
  direction as a `0.1.x` source-beta line. The package does not execute trades
  or provide investment advice.

- Added P7 private continuity: an owner-scoped knowledge graph, explainable
  mastery evidence, learning/research/strategy history, explicit fingerprinted
  projections, and room-scoped evidence-linked posts/comments.
- Added additive migration 003, a parameterized SQLite adapter, P7 vertical
  slice tests, independent A–J review documents, and a P8 readiness report.
- Repaired P6.6 threshold/rank/window/configuration semantics and explicit
  panel-paper behavior. No plugin, broker SDK, or live execution path was added.

- Revalidated and hardened the P0–P3 research foundation (historical): all role contracts
  are governed, the build backend is pinned, ECB provider failures fail closed,
  and feature/factor edge cases are tested. No plugin or research integration
  was installed.

- Added governed P0–P4 research infrastructure, including data provenance,
  feature/factor evaluation, deterministic ResearchRun execution, and local
  JSON persistence.
- Recorded all gates as passed; the project is waiting for human review before
  any P5 scope is considered.
- Added the P4 final architecture review, deferred provenance improvement
  proposal, P5 gate design, and P5 dependency comparison. These documents do
  not implement P5 or install P5 dependencies.
- Added the P4 architecture freeze, philosophy review, P5 readiness review, and
  explicit dependency decision. The repository remains stopped at P4 awaiting
  human approval for P5.
- Completed the P4.5 Research OS validation across architecture, ResearchRun,
  experiment lifecycle, provenance, quantitative validity, user workflow, P5/P6,
  future agent boundaries, and dependencies. P4 remains frozen; P5 remains
  unimplemented pending human approval.
- Began the approved P5 Quant Engine Foundation with component admission,
  dependency policy, and a P5-specific gate review. The runtime remains
  research-only and keeps external quant dependencies out of the core runtime.
- Completed the P5 Quant Engine Foundation with domain contracts, an in-house
  deterministic backtest engine, evaluation/risk metrics, portfolio validation,
  a ResearchRun-linked vertical slice, and lazy optional adapter seams. P6 is
  held for human approval.
- Admitted `statsmodels==0.15.0` only in the isolated `.venv-quant` adapter
  sandbox, with a clean dependency check and normalized OLS smoke result; the
  project manifest remains unchanged.
- Completed P5.5 stabilization: frozen Finahinking-owned quant contracts,
  validity/OOS metadata, explicit realism warnings, and the deterministic
  cross-sectional momentum vertical slice. Gate A passed and P6 was authorized.
- Completed P6 guided quant research and learning: typed task classification,
  explicit hypotheses and assumption review, plan-bound tool gateway, grounded
  explanations, predict → reveal → explain, learning cards, minimal learning
  state, and an isolated regression-learning workflow. The project stops at
  the P6 Gate Review; P7 remains waiting for explicit human approval.

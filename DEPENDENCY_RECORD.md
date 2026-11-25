# Dependency Record

This file is the authoritative record of why each third-party dependency is
present, who owns it, and which project phase first needs it. No dependency is
installed without an entry here. Versions are pinned in `requirements.lock`
when the P1 environment is created.

## P0 baseline

P0 uses only the Python standard library for governance validation. There are
no runtime or development package dependencies in this phase.

## P1–P3 dependencies

| Package | Version | Role | License / maintenance / security | First phase |
| --- | --- | --- | --- | --- |
| `numpy` | 2.5.3 | Numerical arrays for feature calculations | BSD-3-Clause; NumPy project maintenance; update through reviewed lock refresh | P1 |
| `pandas` | 3.0.6 | Indexed datasets and tabular transformations | BSD-3-Clause; pandas project maintenance; update through reviewed lock refresh | P1 |
| `pytest` | 9.1.1 | Test runner | MIT; PyPA/pytest maintenance; update through reviewed lock refresh | P1 |
| `ruff` | 0.16.9 | Static checks and formatting | MIT; Astral maintenance; update through reviewed lock refresh | P1 |
| `jupyterlab` | 4.6.4 | Reproducible research workspace | BSD-3-Clause; Jupyter maintenance; update through reviewed lock refresh | P1 |
| `ipykernel` | 7.4.0 | Notebook kernel | BSD-3-Clause; Jupyter maintenance; update through reviewed lock refresh | P1 |
| `tornado` | 6.6.1 | HTTP runtime for the optional Jupyter environment | Apache-2.0; security minimum for the research extra | Phase 29 |
| `setuptools` | 84.0.0 | PEP 517 build backend | MIT; PyPA maintenance; exact build pin in `pyproject.toml` and lock | P1 |

Adding or upgrading a package requires a purpose, a compatibility note, a
review, and an update to this record and the lock file.

The lock file also pins transitive packages required by the six direct runtime,
development, and research tools above. Transitives are not silently promoted
to direct API dependencies; their versions are reviewed as one lock refresh.

P4 adds no dependency; see DEPENDENCY_PROPOSAL.md.

## 2026-10-03 P3 capability decision (historical)

No plugin, MCP connector, provider SDK, broker integration, or additional
package is required for the requested P0–P3 delivery. The existing pinned
`numpy`, `pandas`, `pytest`, `ruff`, `jupyterlab`, and `ipykernel` environment
closes the demonstrated governance, ECB fixture/provider, feature, and factor
requirements. Installing unrelated integrations would expand the approved
scope without closing a measured capability gap. This record is historical; the
current P7 decision is recorded below.

## P5.5–P6 decision

P5.5 and P6 add no core package, plugin, or MCP server. The existing
`statsmodels==0.15.0` adapter remains admitted only in the isolated
`.venv-quant` environment; the primary `.venv` stays unpolluted. P6 reaches
the regression service through the Finahinking-owned normalized adapter seam,
never through a third-party object. PyPortfolioOpt, bt, vectorbt, Qlib,
Riskfolio, Alphalens, QuantStats/Pyfolio, LangGraph, Ollama, and live-data
providers remain deferred because no demonstrated capability gap requires them.

## P6.5 capability decision

P6.5 adds no package-manager dependency, plugin, MCP connector, provider SDK,
or host database service. The bounded BLS adapter uses the Python standard
library HTTP and hashing primitives; deterministic canonicalization and the
SQLite development adapter reuse the existing runtime, while Docker
`postgres:16-alpine` is used only as a disposable migration-verification
environment. The frozen P6 typed gateway, pandas fixtures, and existing
learning contracts are reused. A capability audit considered broader market
data connectors, browser/document extraction, production PostgreSQL drivers,
and agent frameworks; each was deferred because it would expand scope without
closing a demonstrated P6.5 gap. This decision is recorded in
`docs/p6_5/P6_5_CAPABILITY_MATRIX.md` and the final validation report.

## P6.6 capability decision

P6.6 adds no package-manager dependency, plugin, MCP connector, broker SDK,
market-data adapter, ML framework, or database driver. Python `ast`, pandas,
NumPy, the existing P5/P5.5 ledgers, P6 learning contracts, and P6.5
provenance boundaries close the demonstrated feature/strategy/paper/export
gaps. The decision and residual capability risks are recorded in
`docs/p6_6/P6_6_CAPABILITY_MATRIX.md`.

## P7 capability decision

P7 adds no package-manager dependency, plugin, MCP connector, graph/vector
database, broker SDK, or moderation service. Existing Python, pandas/NumPy,
SQLite/PostgreSQL migrations, and the repository's review/test tooling close
the measured capability gap. Installation is intentionally skipped; see
`docs/p7/P7_CAPABILITY_MATRIX.md`.

## P8.2 capability decision

P8.2 keeps the Python core dependency set unchanged. Lightweight Charts
`5.2.1`, ECharts `6.1.0`, and esbuild `0.28.2` are pinned in the separate
`frontend/` npm workspace and compiled into a same-origin static asset; they
are not Python runtime dependencies. `vectorbt==1.1.1` is installed only in
the ignored `.venv-vectorbt` sandbox for an offline smoke and remains outside
the core/quant locks because its Apache-2.0 + Commons Clause terms require
legal review. `pyqlib==0.9.7` was exercised only in a disposable Linux/amd64
Python 3.12 Docker environment; no Qlib package is installed in `.venv` or
`.venv-quant`, and native/provider/PIT admission is deferred. QMT/`xtquant`
was not installed; the repository contains only a Finathink-owned,
read-only, token-bounded mock boundary. No new Codex plugin, MCP connector,
broker SDK, credential store, or live-trading dependency was installed.

## P8.2B capability decision

P8.2B adds only the audited frontend `katex` package (`0.19.0`) to the
existing separate `frontend/` npm workspace. Its CSS and font files are
vendored into the same-origin site and Python package assets; no CDN, remote
font, browser runtime, or Python runtime dependency is introduced. The
Crossref audit uses Python's standard-library HTTP client only and is opt-in;
deterministic CI uses the local citation cache. CodeMirror was not added
because the reviewed code traces are static and do not require an editor.

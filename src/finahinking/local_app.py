"""Small, offline-first local application shell for Finathink.

The shell deliberately contains no domain logic or provider credentials.  It
adapts the existing P7 repository and exposes deterministic HTML/JSON routes
for the event, knowledge, quant, strategy, personal, and community journeys.
The same object can be mounted in the stdlib HTTP server or exercised directly
by tests, which keeps the local runtime inspectable and inexpensive to run.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sqlite3
import tempfile
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources as importlib_resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from finahinking.data.connection_settings import (
    DataConnectionSettingsStore,
    DataCredentialStore,
    PersistentDataConnectionStore,
    new_local_credential_ref,
)
from finahinking.data.http_transport import BoundedHttpTransport
from finahinking.data.user_api import DataConnectorError, JsonApiConnector
from finahinking.data.user_api_contracts import (
    DataConnectionConfig,
    DataRequest,
    DataSourceCredentialRef,
)
from finahinking.p7 import (
    CommunityClaim,
    CommunityIntegrityService,
    CommunityPost,
    CommunityRoom,
    EventLearningAdapter,
    MasteryEvidence,
    PersonalNode,
    Principal,
    ProjectionSpec,
    SQLiteP7Repository,
    apply_p7_migration,
)
from finahinking.p7_5.knowledge import DEFAULT_CATALOG
from finahinking.p8_2b.catalog import DEFAULT_KNOWLEDGE_CATALOG, get_knowledge_unit, search_catalog
from finahinking.p8_2b.context import ContextSnapshot, no_context, resolve_context
from finahinking.p8_2b.export import export_bibtex, export_csl_json, export_latex, export_markdown
from finahinking.p8_2b.math import render_latex, render_mathml
from finahinking.p8_2b.widgets import WidgetSpec, run_widget
from finahinking.research.diagnostic_page import DIAGNOSTIC_CSS, diagnostic_page
from finahinking.workbench_ui import (
    BLUE_WHITE_CSS,
    OVERVIEW_HEADER_HTML,
    nav_icon,
    shell_search_dialog,
)


def default_db_path() -> str:
    """Return the user-local SQLite path used by a normal first run."""

    configured = os.environ.get("FINAHINKING_DB")
    if configured:
        return configured
    data_dir = Path(os.environ.get("FINAHINKING_DATA_DIR", Path.home() / ".finahinking"))
    return str(data_dir / "finahinking.sqlite3")

_EVENT = {
    "id": "sample-cpi-2026-01",
    "title": "CPI sample release: from event to explanation",
    "publisher": "U.S. Bureau of Labor Statistics",
    "status": "CAPTURED",
    "data_mode": "SAMPLE",
    "source_url": "https://www.bls.gov/news.release/cpi.nr0.htm",
    "chain": ["CPI", "Inflation", "Rates", "Bonds", "Discount", "Valuation"],
    "limitations": ["Deterministic fixture; not a live market feed.", "No forecast or trade instruction."],
}


_APP_CSS = """
:root {
  color-scheme: light;
  --background: #f5f7f7;
  --surface: #ffffff;
  --surface-elevated: #fbfcfc;
  --border: #d7dfdf;
  --border-strong: #b5c4c4;
  --text-primary: #14232b;
  --text-secondary: #40545d;
  --text-muted: #66777d;
  --focus: #0b6670;
  --information: #1f5f8b;
  --success: #2f6b57;
  --warning: #8a5a17;
  --error: #a5413e;
  --evidence: #0b6670;
  --fact: #376b55;
  --interpretation: #63528a;
  --hypothesis: #8a5a17;
  --quant-finding: #1f5f8b;
  --unknown: #66777d;
  --limitation: #7e4f50;
  --learning: #5a4f8c;
  --radius-sm: 6px;
  --radius-md: 12px;
  --radius-lg: 18px;
  --shadow-sm: 0 1px 2px rgba(20, 35, 43, .06);
  --shadow-md: 0 10px 28px rgba(20, 35, 43, .08);
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 20px;
  --space-6: 24px;
  --space-8: 32px;
  --space-10: 40px;
  --measure: 72ch;
}
* { box-sizing: border-box; }
html { background: var(--background); color: var(--text-primary); }
body { margin: 0; min-width: 320px; font: 16px/1.6 ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
a { color: var(--evidence); text-underline-offset: 3px; }
a:hover { color: #084c54; }
a:focus-visible, button:focus-visible, input:focus-visible, textarea:focus-visible, select:focus-visible, summary:focus-visible { outline: 3px solid var(--focus); outline-offset: 3px; }
button, input, textarea, select { font: inherit; }
button, .button { min-height: 44px; border: 1px solid transparent; border-radius: var(--radius-sm); padding: 9px 15px; cursor: pointer; transition: background-color .16s ease, border-color .16s ease, color .16s ease, box-shadow .16s ease; }
button, .button-primary { background: var(--evidence); color: #fff; }
button:hover, .button-primary:hover { background: #084c54; }
.button-secondary { display: inline-flex; align-items: center; justify-content: center; background: var(--surface); color: var(--evidence); border-color: var(--border-strong); text-decoration: none; }
.button-secondary:hover { background: #eef5f5; }
input, textarea, select { width: 100%; min-height: 44px; border: 1px solid var(--border-strong); border-radius: var(--radius-sm); padding: 9px 11px; background: var(--surface); color: var(--text-primary); }
textarea { min-height: 112px; resize: vertical; }
label { display: grid; gap: var(--space-2); font-weight: 650; color: var(--text-primary); }
small, .meta { color: var(--text-muted); font-size: .86rem; }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
code { overflow-wrap: anywhere; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; padding: var(--space-4); border: 1px solid var(--border); border-radius: var(--radius-sm); background: #f1f5f4; }
.skip-link { position: absolute; left: -10000px; top: var(--space-3); z-index: 20; padding: var(--space-2) var(--space-3); background: var(--text-primary); color: #fff; }
.skip-link:focus { left: var(--space-3); }
.app-shell { min-height: 100dvh; display: grid; grid-template-columns: 236px minmax(0, 1fr) 260px; }
.sidebar { position: sticky; top: 0; align-self: start; min-height: 100dvh; padding: var(--space-6) var(--space-4); border-right: 1px solid var(--border); background: #edf2f1; }
.brand { display: flex; align-items: center; gap: var(--space-2); margin-bottom: var(--space-8); color: var(--text-primary); text-decoration: none; }
.brand-mark { display: grid; place-items: center; width: 34px; height: 34px; border: 1px solid var(--border-strong); border-radius: 50%; color: var(--evidence); font-weight: 800; }
.brand-name { font-family: Georgia, "Times New Roman", serif; font-size: 1.24rem; letter-spacing: -.02em; }
.nav-section { margin: 0 0 var(--space-5); }
.nav-section-title { margin: 0 0 var(--space-2); padding: 0 var(--space-2); color: var(--text-muted); font-size: .72rem; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }
.nav-list { display: grid; gap: 3px; margin: 0; padding: 0; list-style: none; }
.nav-link { display: flex; align-items: center; min-height: 42px; padding: 8px 10px; border-radius: var(--radius-sm); color: var(--text-secondary); text-decoration: none; }
.nav-link:hover { background: rgba(255,255,255,.74); color: var(--text-primary); }
.nav-link[aria-current="page"] { background: var(--surface); color: var(--text-primary); box-shadow: var(--shadow-sm); font-weight: 750; }
.sidebar-note { margin-top: auto; padding-top: var(--space-8); color: var(--text-muted); font-size: .83rem; }
.workspace { min-width: 0; padding: var(--space-8) clamp(var(--space-5), 4vw, var(--space-10)); }
.workspace-inner { width: min(100%, 940px); margin: 0 auto; }
.eyebrow { margin: 0 0 var(--space-2); color: var(--evidence); font-size: .76rem; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }
h1, h2, h3 { margin-top: 0; line-height: 1.18; letter-spacing: -.02em; text-wrap: balance; }
h1 { max-width: 22ch; margin-bottom: var(--space-3); font-family: Georgia, "Times New Roman", serif; font-size: clamp(2rem, 4vw, 3.2rem); }
h2 { font-size: 1.28rem; }
h3 { font-size: 1rem; }
.lede { max-width: var(--measure); margin: 0 0 var(--space-6); color: var(--text-secondary); font-size: 1.08rem; }
.status-row, .action-row, .tag-row, .metric-row { display: flex; flex-wrap: wrap; gap: var(--space-2); align-items: center; }
.status { display: inline-flex; align-items: center; min-height: 28px; padding: 3px 9px; border: 1px solid currentColor; border-radius: 999px; font-size: .72rem; font-weight: 800; letter-spacing: .08em; }
.status--sample, .status--offline { color: var(--warning); background: #fff8e8; }
.status--complete, .status--ready, .status--fact { color: var(--success); background: #edf7f1; }
.status--evidence, .status--quant { color: var(--quant-finding); background: #edf5fb; }
.status--hypothesis { color: var(--hypothesis); background: #fff8e8; }
.status--limitation, .status--unknown { color: var(--limitation); background: #fbefef; }
.status--learning { color: var(--learning); background: #f3f0fb; }
.hero { display: grid; grid-template-columns: minmax(0, 1.08fr) minmax(260px, .92fr); gap: var(--space-6); align-items: stretch; margin-bottom: var(--space-8); }
.hero-copy, .hero-art { min-width: 0; padding: var(--space-6); border: 1px solid var(--border); border-radius: var(--radius-lg); background: var(--surface); box-shadow: var(--shadow-sm); }
.hero-art { display: grid; align-content: center; gap: var(--space-3); background: var(--surface-elevated); }
.hero-art img { display: block; width: 100%; height: auto; border: 1px solid var(--border); border-radius: var(--radius-md); }
.splash-stage { position: relative; isolation: isolate; overflow: hidden; border-radius: var(--radius-md); background: #f7f5ef; }
.splash-stage::before { content: ""; position: absolute; inset: 7%; z-index: -1; border-radius: 50%; background: conic-gradient(from 15deg, rgba(11,102,112,.10), rgba(138,90,23,.16), rgba(90,79,140,.12), rgba(11,102,112,.10)); filter: blur(18px); animation: splash-spin 18s linear 2; animation-fill-mode: both; }
.splash-frame { position: relative; z-index: 1; animation: splash-crossfade 14s ease-in-out 2; animation-fill-mode: both; }
.splash-frame--secondary { position: absolute !important; inset: 0; object-fit: cover; opacity: 0; animation-delay: -7s; }
@keyframes splash-spin { to { transform: rotate(360deg); } }
@keyframes splash-crossfade { 0%, 42% { opacity: 1; transform: scale(1) rotate(0deg); } 50%, 92% { opacity: .08; transform: scale(1.015) rotate(1deg); } 100% { opacity: 1; transform: scale(1) rotate(0deg); } }
.section { margin: 0 0 var(--space-8); }
.section-heading { display: flex; justify-content: space-between; gap: var(--space-4); align-items: baseline; margin-bottom: var(--space-3); }
.section-heading h2 { margin-bottom: 0; }
.grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-4); }
.card { min-width: 0; padding: var(--space-5); border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface); box-shadow: var(--shadow-sm); }
.card--evidence { border-left: 4px solid var(--evidence); }
.card--quiet { background: var(--surface-elevated); }
.card p:last-child, .card ul:last-child, .card ol:last-child { margin-bottom: 0; }
.card h2, .card h3 { margin-bottom: var(--space-2); }
.prose { max-width: var(--measure); }
.prose p, .prose li { color: var(--text-secondary); }
.form-grid { display: grid; gap: var(--space-4); }
.field-help { margin: 0; color: var(--text-muted); font-size: .88rem; }
.stepper { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 4px; margin: var(--space-4) 0 var(--space-6); }
.step { min-height: 54px; padding: 8px; border-top: 3px solid var(--border); color: var(--text-muted); font-size: .78rem; }
.step--active { border-top-color: var(--evidence); color: var(--text-primary); font-weight: 750; }
.evidence-list { display: grid; gap: var(--space-3); margin: 0; padding: 0; list-style: none; }
.evidence-item { padding: var(--space-3); border: 1px solid var(--border); border-radius: var(--radius-sm); background: #f9fbfb; }
.evidence-item strong { display: block; margin-bottom: var(--space-1); }
.inspector { min-width: 0; padding: var(--space-8) var(--space-4); border-left: 1px solid var(--border); background: #f8faf9; }
.inspector-inner { position: sticky; top: var(--space-6); }
.inspector h2 { font-size: 1rem; }
.inspector dl { margin: 0; }
.inspector dt { margin-top: var(--space-3); color: var(--text-muted); font-size: .74rem; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
.inspector dd { margin: 0; color: var(--text-secondary); overflow-wrap: anywhere; }
.empty-state, .loading-state, .error-state { padding: var(--space-5); border: 1px dashed var(--border-strong); border-radius: var(--radius-md); background: var(--surface-elevated); }
.loading-state { border-style: solid; }
.error-state { border-color: #d7aaa9; background: #fff8f8; }
.result { border-top: 3px solid var(--quant-finding); }
.metric { min-width: 120px; padding: var(--space-3); border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-elevated); }
.metric-value { display: block; font-variant-numeric: tabular-nums; font-size: 1.28rem; font-weight: 800; }
.metric-label { display: block; color: var(--text-muted); font-size: .76rem; }
.overview-header { display: flex; justify-content: space-between; gap: var(--space-6); align-items: flex-end; margin-bottom: var(--space-6); }
.overview-header h1 { margin-bottom: var(--space-2); }
.overview-header-copy { min-width: 0; }
.overview-meta { display: grid; justify-items: end; gap: var(--space-2); color: var(--text-muted); font-size: .82rem; }
.metric-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--space-3); margin-bottom: var(--space-6); }
.metric-card { min-width: 0; padding: var(--space-4); border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface); box-shadow: var(--shadow-sm); }
.metric-card .metric-value { font-size: 1.7rem; color: var(--text-primary); }
.overview-grid { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(280px, .8fr); gap: var(--space-4); }
.overview-region { min-width: 0; }
.overview-region h2 { display: flex; justify-content: space-between; gap: var(--space-3); align-items: baseline; }
.overview-region h2 span { color: var(--text-muted); font-size: .72rem; font-weight: 500; }
.overview-chart { min-height: 210px; display: grid; align-content: center; gap: var(--space-3); padding: var(--space-4); border: 1px solid var(--border); border-radius: var(--radius-sm); background: #060D1D; }
.overview-chart-bars { display: grid; grid-template-columns: repeat(8, 1fr); gap: 6px; align-items: end; height: 110px; }
.overview-chart-bars i { display: block; min-height: 8px; border-radius: 3px 3px 0 0; background: linear-gradient(180deg, var(--evidence), var(--learning)); opacity: .7; }
.overview-topic-heatmap { display: grid; grid-template-columns: repeat(auto-fit, minmax(88px, 1fr)); gap: 6px; margin-bottom: var(--space-3); }
.overview-topic-cell { min-height: 54px; display: grid; place-items: center; padding: 8px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: color-mix(in srgb, var(--evidence) calc(12% + var(--heat, 0%) ), var(--surface-elevated)); color: var(--text-primary); font-size: .78rem; text-align: center; overflow-wrap: anywhere; }
.overview-topic-cell[data-intensity="1"] { background: rgba(56, 189, 248, .12); }
.overview-topic-cell[data-intensity="2"] { background: rgba(56, 189, 248, .2); }
.overview-topic-cell[data-intensity="3"] { background: rgba(56, 189, 248, .3); }
.overview-topic-cell[data-intensity="4"] { background: rgba(139, 92, 246, .34); }
.overview-topic-cell[data-intensity="5"] { background: rgba(139, 92, 246, .48); }
.overview-comparison { margin-top: var(--space-5); }
.comparison-workspace { display: grid; gap: var(--space-4); }
.comparison-head { display: flex; justify-content: space-between; gap: var(--space-4); align-items: baseline; border-bottom: 1px solid var(--border); padding-bottom: var(--space-3); }
.overview-list, .overview-heatmap { display: grid; gap: var(--space-2); margin: 0; padding: 0; list-style: none; }
.overview-list li, .overview-heatmap li { display: flex; justify-content: space-between; gap: var(--space-3); padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--radius-sm); color: var(--text-secondary); }
.overview-heatmap li { display: grid; grid-template-columns: 1fr auto; }
.overview-links { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--space-3); }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
th, td { padding: 9px 10px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
th { color: var(--text-muted); font-size: .78rem; letter-spacing: .06em; text-transform: uppercase; }
[data-quantitative-comparison] { min-width: 0; overflow-wrap: anywhere; }
[data-quantitative-comparison] pre { white-space: pre-wrap; overflow-wrap: anywhere; }
[data-reproduction-workspace] h3 { margin-top: var(--space-6); margin-bottom: var(--space-3); }
[data-reproduction-workspace] .table-wrap { margin-block: var(--space-4); }
[data-reproduction-workspace] .table-wrap table { min-width: 400px; }
[data-reproduction-workspace] .repro-consent input { width: 20px; height: 20px; min-height: 20px; flex: 0 0 20px; }
.repro-consent { display: flex; align-items: center; gap: var(--space-2); min-height: 44px; }
[data-reproduction-workspace] { min-width: 0; overflow-wrap: anywhere; }
[data-reproduction-workspace] pre { white-space: pre-wrap; overflow-wrap: anywhere; }
[data-reproduction-workspace] select, [data-reproduction-workspace] input { max-width: 100%; min-width: 0; }
.research-workspace { display: grid; gap: var(--space-5); }
.research-toolbar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: var(--space-3); }
.research-toolbar .tag-row { margin: 0; }
.research-chart, .research-sweep { min-height: 240px; width: 100%; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface-elevated); }
.research-chart { height: clamp(300px, 42vw, 480px); }
.research-sweep { height: 260px; }
.research-tooltip { min-height: 32px; margin-top: var(--space-2); padding: 6px 9px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: #f7fbfa; color: var(--text-secondary); font-size: .84rem; font-variant-numeric: tabular-nums; }
.research-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-4); }
.research-panel { min-width: 0; padding: var(--space-5); border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface); box-shadow: var(--shadow-sm); }
.research-panel h2, .research-panel h3 { margin-bottom: var(--space-2); }
.research-panel ul { margin-bottom: 0; }
.research-inspector { min-height: 88px; padding: var(--space-3); border-left: 3px solid var(--evidence); background: #f4f8f7; color: var(--text-secondary); font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.research-limitations { margin: 0; padding-left: 1.25rem; color: var(--text-secondary); }
.workbench-frame { display: grid; gap: var(--space-4); margin-top: var(--space-5); padding: clamp(18px, 3vw, 28px); border: 1px solid #c7c0b4; background: #f2efe7; color: #173137; }
.workbench-head { display: flex; justify-content: space-between; gap: var(--space-4); align-items: baseline; border-bottom: 1px solid #173137; padding-bottom: var(--space-3); }
.workbench-head h2 { margin: 0; font-family: Georgia, "Times New Roman", serif; font-weight: 500; }
.workbench-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border-top: 1px solid #c7c0b4; border-bottom: 1px solid #c7c0b4; }
.workbench-metric { min-height: 76px; padding: var(--space-3); border-right: 1px solid #c7c0b4; }
.workbench-metric:last-child { border-right: 0; }
.workbench-metric strong { display: block; font-size: 1.15rem; font-variant-numeric: tabular-nums; }
.workbench-metric span { color: #617174; font-size: .76rem; text-transform: capitalize; }
.workbench-grid { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(260px, .9fr); gap: var(--space-4); }
.workbench-inspector { min-height: 90px; padding: var(--space-3); border-left: 3px solid #c36e48; background: #fbfaf6; color: #617174; font-variant-numeric: tabular-nums; }
.workbench-table { overflow: auto; background: #fbfaf6; border-top: 1px solid #173137; border-bottom: 1px solid #173137; }
.workbench-table table { min-width: 720px; }
.workbench-table tbody tr { cursor: pointer; }
.workbench-table tbody tr[aria-current="true"], .workbench-table tbody tr:hover, .workbench-table tbody tr:focus-visible { background: #e6eeea; box-shadow: inset 3px 0 0 #c36e48; }
.workbench-preview { padding: var(--space-3); border: 1px solid #c7c0b4; background: #fbfaf6; color: #617174; }
.workbench-preview input { accent-color: #c36e48; }
.workbench-frame--standalone { margin-top: var(--space-6); }
.workbench-grid--standalone { grid-template-columns: minmax(0, 1.35fr) minmax(240px, .65fr); }
.workbench-flow { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; color: #173137; font: 600 .82rem/1.2 ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
.workbench-flow span { padding: 7px 10px; border: 1px solid #c7c0b4; border-radius: 999px; background: #fbfaf6; }
.workbench-flow i { color: #c36e48; font-style: normal; }
.workbench-notes { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(240px, .9fr); gap: var(--space-5); align-items: start; }
.workbench-notes h2 { margin-top: 0; font-family: Georgia, "Times New Roman", serif; font-weight: 500; }
[data-research-point-table] tbody tr { cursor: pointer; }
[data-research-point-table] tbody tr:hover, [data-research-point-table] tbody tr:focus-visible { background: #eef5f5; }
[data-research-point-table] tbody tr[aria-current="true"] { background: #e7f1ef; box-shadow: inset 3px 0 0 var(--evidence); }
.knowledge-layout { display: grid; gap: var(--space-5); }
.knowledge-context { border-left: 3px solid var(--learning); background: #f6f3fb; }
.knowledge-equation { display: grid; gap: var(--space-2); padding: var(--space-4); border: 1px solid var(--border); border-radius: var(--radius-sm); background: #fbfcfc; overflow-x: auto; }
.knowledge-equation .katex { font-size: 1.16rem; }
.knowledge-symbols { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--space-3); }
.knowledge-symbol { padding: var(--space-3); border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-elevated); }
.knowledge-code-lines { display: grid; gap: 4px; }
.knowledge-code-line { display: block; width: 100%; min-height: 38px; padding: 6px 9px; border: 1px solid transparent; border-radius: var(--radius-sm); background: transparent; color: var(--text-primary); text-align: left; font: .86rem/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.knowledge-code-line:hover, .knowledge-code-line[aria-current="true"] { border-color: var(--border-strong); background: #eef5f5; }
.knowledge-derivation { display: grid; gap: var(--space-3); }
.knowledge-derivation details { padding: var(--space-3); border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-elevated); }
.knowledge-inline-actions { display: flex; flex-wrap: wrap; gap: var(--space-2); }
@media (max-width: 880px) { .knowledge-symbols { grid-template-columns: 1fr; } }
.source-state { display: inline-flex; align-items: center; gap: var(--space-2); color: var(--text-muted); font-size: .84rem; }
.settings-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-4); }
.footer-note { margin-top: var(--space-8); padding-top: var(--space-4); border-top: 1px solid var(--border); color: var(--text-muted); font-size: .82rem; }
@media (max-width: 1180px) { .app-shell { grid-template-columns: 210px minmax(0, 1fr); } .inspector { grid-column: 2; border-top: 1px solid var(--border); border-left: 0; padding-top: var(--space-5); } .inspector-inner { position: static; } }
@media (max-width: 880px) { .app-shell { display: block; } .sidebar { position: static; min-height: auto; padding: var(--space-4); border-right: 0; border-bottom: 1px solid var(--border); } .brand { margin-bottom: var(--space-4); } .nav-list { display: flex; flex-wrap: wrap; } .nav-section-title, .sidebar-note { display: none; } .workspace { padding: var(--space-6) var(--space-4); } .hero, .grid, .research-grid, .settings-grid, .workbench-grid, .workbench-notes { grid-template-columns: 1fr; } .workbench-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); } .workbench-metric:nth-child(2n) { border-right: 0; } .stepper { grid-template-columns: 1fr; } .step { min-height: auto; border-top: 0; border-left: 3px solid var(--border); } .step--active { border-left-color: var(--evidence); } .inspector { border-top: 1px solid var(--border); } }
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto !important; transition-duration: .01ms !important; animation-duration: .01ms !important; animation-iteration-count: 1 !important; } .splash-frame--secondary { display: none; } }
"""


# Shared form overrides for the run and experiment workspaces. The blue-white
# theme is defined separately so all shell pages use the same visual tokens.
_RESEARCH_JOBS_CSS = """
[data-run-onboarding] { font-size: 16px; }
.snapshot-fingerprint { overflow-wrap: anywhere; font-family: var(--font-mono, monospace); font-size: .85rem; color: var(--text-secondary); }
[data-run-workspace] .grid > *, [data-run-workspace] label, [data-run-workspace] select { min-width: 0; max-width: 100%; }
[data-run-workspace] .form-grid { min-width: 0; grid-template-columns: minmax(0, 1fr); }
[data-run-workspace] .form-grid > * { min-width: 0; max-width: 100%; overflow-wrap: anywhere; }
[data-run-workspace] .model-consent { display: grid; grid-template-columns: 1.15rem minmax(0, 1fr); gap: .6rem; align-items: start; }
[data-run-workspace] .model-consent input { width: 1.15rem; height: 1.15rem; padding: 0; margin: .15rem 0 0; }
[data-run-workspace] dl { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: .5rem 1rem; }
[data-run-workspace] dd { margin: 0; font-variant-numeric: tabular-nums; }
[data-run-history] button { overflow-wrap: anywhere; text-align: left; white-space: normal; }
.research-stage-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: .5rem; padding-left: 1.5rem; color: var(--text-muted); font-size: .82rem; }
.research-stage-list [data-stage-status="COMPLETE"] { color: var(--success); }
.research-stage-list [data-stage-status="RUNNING"] { color: var(--evidence); }
.research-stage-list [data-stage-status="FAILED"] { color: var(--error); }
.first-research-steps { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; list-style:none; padding:0; }
.first-research-steps li { border-top:2px solid var(--border-strong); padding-top:10px; font-size:.82rem; }
.readiness-checks { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px 24px; list-style:none; padding:0; }
.readiness-checks li { min-width:0; border-left:3px solid var(--border); padding-left:12px; }
.readiness-checks p { margin:5px 0; }
.readiness-checks [data-check-status="BLOCKED"] { border-color:var(--error); }
.readiness-checks [data-check-status="REVIEW"] { border-color:var(--warning); }
.readiness-checks [data-check-status="PASS"] { border-color:var(--success); }
.research-explanation { border-top:1px solid var(--border); margin-top:16px; }
.research-explanation:empty { display:none; }
.research-explanation h3 { margin:18px 0 6px; font-size:1rem; }
.research-explanation ul { padding-left:20px; }
.research-timeline li { padding-bottom:8px; }
[data-run-workspace] fieldset { min-width:0; border:1px solid var(--border); border-radius:var(--radius-sm); padding:12px; }
@media(max-width:800px) { .first-research-steps { grid-template-columns:repeat(2,minmax(0,1fr)); } .readiness-checks { grid-template-columns:1fr; } }

"""

_FACTOR_STUDY_CSS = """
[data-experiment-workspace] { min-width: 0; }
[data-experiment-workspace] .panel { min-width: 0; }
[data-experiment-workspace] .study-form { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: var(--space-4); }
[data-experiment-workspace] .study-form label { display: grid; gap: var(--space-2); min-width: 0; }
[data-experiment-workspace] input,[data-experiment-workspace] select,[data-experiment-workspace] textarea { box-sizing: border-box; max-width: 100%; min-width: 0; }
[data-experiment-workspace] input[type=checkbox] { width: 1rem; height: 1rem; display: inline-block; }
[data-experiment-workspace] .study-freeze-ack { display: flex; gap: var(--space-2); align-items: baseline; }
[data-experiment-workspace] button:disabled { opacity: .45; cursor: not-allowed; }
[data-experiment-workspace] [data-study-binding],[data-experiment-workspace] .field-help,[data-experiment-workspace] .overview-list { overflow-wrap: anywhere; }
@media(max-width: 760px) { [data-experiment-workspace] .study-form { grid-template-columns: minmax(0,1fr); } }
"""



@dataclass(frozen=True)
class LocalAppConfig:
    """Runtime settings.  SQLite and sample/offline mode are the defaults."""

    db_path: str = field(default_factory=default_db_path)
    host: str = "127.0.0.1"
    port: int = 8765
    sample_mode: bool = True
    offline: bool = True
    principal_id: str = "local-user"
    session_id: str = "local-session"

    def __post_init__(self) -> None:
        host = self.host.strip().lower().strip("[]")
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Finathink local API only binds to loopback addresses")

    @classmethod
    def from_env(cls) -> LocalAppConfig:
        return cls(
            db_path=default_db_path(),
            host=os.environ.get("FINAHINKING_HOST", "127.0.0.1"),
            port=int(os.environ.get("FINAHINKING_PORT", "8765")),
            sample_mode=os.environ.get("FINAHINKING_SAMPLE", "1") not in {"0", "false"},
            offline=os.environ.get("FINAHINKING_OFFLINE", "1") not in {"0", "false"},
        )


class LocalApplication:
    """Route local product journeys through one P7-backed persistence boundary."""

    def __init__(self, config: LocalAppConfig | None = None, *, connection: sqlite3.Connection | None = None, data_transport: Any | None = None, data_credential_store: DataCredentialStore | None = None, provider_transport: Any | None = None, provider_credential_store: Any | None = None) -> None:
        self.config = config or LocalAppConfig.from_env()
        if self.config.db_path != ":memory:":
            path = Path(self.config.db_path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if connection is None:
                try:
                    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                except FileExistsError:
                    pass
                else:
                    os.close(descriptor)
        self.connection = connection or sqlite3.connect(str(Path(self.config.db_path).expanduser()), check_same_thread=False)
        apply_p7_migration(self.connection)
        self.repository = SQLiteP7Repository(self.connection)
        self._bootstrap_identity()
        self._lock = threading.RLock()
        self._research_application = None
        self._interface_diagnostics = None
        self._quality_evaluation = None
        self._quant_knowledge = None
        self._diagnostic_provider_transport = provider_transport
        self._diagnostic_provider_credentials = provider_credential_store
        self._event_journey: Any | None = None
        if self.config.db_path == ":memory:":
            self.artifact_root = Path(tempfile.mkdtemp(prefix="finahinking-artifacts-"))
        else:
            self.artifact_root = Path(self.config.db_path).expanduser().parent / "artifacts"
            self.artifact_root.mkdir(parents=True, exist_ok=True)
        from finahinking.research.run_repository import RunRepository

        self._run_repository = RunRepository(self.artifact_root / "research-index.sqlite3", report_root=self.artifact_root / "reports", scope_id=self.config.session_id)
        from finahinking.research.provider_config import ProviderConfigStore

        provider_path = None if self.config.db_path == ":memory:" else Path(self.config.db_path).expanduser().with_suffix(".providers.json")
        self._provider_configs = ProviderConfigStore(provider_path)
        if self.config.db_path == ":memory:":
            self._data_connections = DataConnectionSettingsStore(data_credential_store)
        else:
            connection_path = Path(self.config.db_path).expanduser().with_suffix(".data-connections.json")
            self._data_connections = PersistentDataConnectionStore(connection_path, credential_store=data_credential_store)
        # The transport is inert until the user explicitly invokes a test or
        # research entrypoint. Saving a connection never performs I/O.
        self._data_transport = data_transport if data_transport is not None else BoundedHttpTransport()

    @property
    def quant_knowledge(self):
        with self._lock:
            if self._quant_knowledge is None:
                from .research.asset_catalog import ResearchAssetCatalog
                from .research.knowledge_index import KnowledgeIndex, KnowledgeIndexUnavailable

                assets = ResearchAssetCatalog.packaged()
                main_file = next(row[2] for row in self.connection.execute("PRAGMA database_list") if row[1] == "main")
                cache = Path(main_file).parent / ".knowledge-cache" / "public.sqlite3" if main_file else None
                try:
                    self._quant_knowledge = KnowledgeIndex.from_catalogs(DEFAULT_CATALOG, DEFAULT_KNOWLEDGE_CATALOG, assets, assets.knowledge_content(), index_path=cache)
                except (ValueError, TypeError, KeyError) as exc:
                    raise KnowledgeIndexUnavailable("知识目录或公开缓存未通过校验。请恢复完整安装；若缓存位置被其他文件占用，请移走该文件后重试。原文件未被覆盖。") from exc
            return self._quant_knowledge

    @property
    def research_service(self):
        """Compose once without starting any process from a request thread."""
        with self._lock:
            if self._research_application is None:
                from finahinking.research.application_service import ResearchApplicationService

                self._research_application = ResearchApplicationService(self.artifact_root, scope_id=self.config.session_id, provider_configs=self._provider_configs, allow_model_calls=not self.config.offline)
            return self._research_application

    @property
    def interface_diagnostics(self):
        with self._lock:
            if self._interface_diagnostics is None:
                from .research.diagnostic_store import DiagnosticStore
                from .research.interface_diagnostics import InterfaceDiagnostics

                store = DiagnosticStore(self.artifact_root / 'interface-diagnostics.sqlite3', scope_id=self.config.session_id)
                self._interface_diagnostics = InterfaceDiagnostics(self._data_connections, self._provider_configs, store,
                    data_transport=self._data_transport, provider_transport=self._diagnostic_provider_transport,
                    credential_store=self._diagnostic_provider_credentials, allow_model_calls=not self.config.offline)
            return self._interface_diagnostics

    @property
    def quality_evaluation(self):
        with self._lock:
            if self._quality_evaluation is None:
                from .research.quality_evaluation import QualityEvaluation

                self._quality_evaluation = QualityEvaluation(self.research_service, self.interface_diagnostics.store)
            return self._quality_evaluation

    def register_research_run(self, result: Any, manifest: Any) -> None:
        """Register a completed research result for read-only local inspection."""

        run_id = str(result.state.run_id)
        if not run_id or "/" in run_id or "\\" in run_id:
            raise ValueError("research run id is invalid")
        bundle = self.artifact_root / "reports" / run_id
        if not (bundle / "manifest.json").exists():
            raise ValueError("research report manifest is missing")
        events = tuple(getattr(result, "events", ()) or ())
        self._run_repository.record_result(result, manifest, bundle=bundle)
        from finahinking.research.ui import register_runtime_snapshot

        register_runtime_snapshot(result.state, manifest, events)

    def _research_entry(self, run_id: str):
        try:
            return self._run_repository.get_run(run_id)
        except KeyError:
            return None

    @staticmethod
    def _research_model(entry):
        from finahinking.research.ui import research_view_model

        manifest = dict(entry["manifest"])
        if entry["report_status"] != "AVAILABLE":
            manifest["files"] = {}
        model = research_view_model(entry["state"], manifest)
        model.update({key: entry[key] for key in ("created_at", "started_at", "finished_at", "dataset_fingerprint", "strategy_version", "policy_fingerprint", "report_status")})
        return model, manifest

    def _research_run_route(self, clean: str) -> tuple[int, str, Any]:
        from finahinking.research.ui import research_runtime_view_model

        parts = [part for part in clean.removeprefix("/research/").split("/") if part]
        if not parts:
            return 404, "application/json", {"error": "research run not found"}
        run_id = parts[0]
        try:
            entry = self._research_entry(run_id)
        except ValueError:
            return 422, "application/json", {"error": "research index record is invalid"}
        if entry is None:
            return 404, "application/json", {"error": "research run not found"}
        view_model, manifest = self._research_model(entry)
        runtime_model = research_runtime_view_model(run_id, state=entry["state"], manifest=manifest, events=entry.get("events", ()))
        if len(parts) == 2 and parts[1] == "status":
            return 200, "application/json", view_model
        if len(parts) == 1:
            links = view_model["report_links"]
            link_html = " ".join(
                f'<a class="button-secondary" href="{html.escape(url)}">{html.escape(section.replace("_", " ").title())}</a>'
                for section, url in sorted(links.items())
            )
            warning = f'<p class="error-state" role="alert">Report {entry["report_status"]}. Restore the original bundle or create a new research run; the saved history is retained.</p>' if entry["report_status"] != "AVAILABLE" else ''
            body = (
                f'<div class="status-row"><span class="status status--offline">{html.escape(view_model["mode"])}</span><span class="status status--ready">PAPER-ONLY</span><span class="status status--quant">{html.escape(view_model["state"])}</span></div>'
                f'<h1>Research run {html.escape(run_id)}</h1>'
                f'<p class="lede">As-of: {html.escape(str(view_model.get("as_of") or "not attached"))}. This view is read-only and never submits an order.</p>'
                f'{warning}'
                f'<section class="card"><h2>Analyst status</h2><pre>{html.escape(json.dumps(view_model, ensure_ascii=False, indent=2, sort_keys=True))}</pre></section>'
                f'<section class="card" data-research-runtime data-payload-url="/api/research/runs/{html.escape(run_id)}/stream"><h2>Runtime status wall</h2><p data-research-runtime-state>{html.escape(runtime_model["state"])} · READ-ONLY · PAPER-ONLY</p><p data-research-runtime-stages>{html.escape(" · ".join(f"{key}: {value}" for key, value in runtime_model.get("stage_status", {}).items()))}</p><p data-research-runtime-tools>{html.escape(" · ".join(str(item.get("name", item.get("tool", "tool"))) for item in runtime_model.get("tool_summaries", ())) or "No tool summary recorded." )}</p><p class="field-help">Server snapshot only; the browser never calculates financial indicators.</p><p class="error-state" data-research-runtime-error hidden></p></section>'
                f'<div class="action-row">{link_html}</div>'
                f'<section data-research-run data-payload-url="/research/{html.escape(run_id)}/status"><p class="field-help" data-research-run-status>SERVER-RENDERED · {html.escape(view_model["state"])}</p><p class="field-help" data-research-run-summary>As-of {html.escape(str(view_model.get("as_of") or "not attached"))}</p><p class="error-state" data-research-run-error hidden></p></section>'
            )
            return 200, "text/html; charset=utf-8", self.render_shell("/research", f"Research {run_id}", body, inspector=self._inspector("Research run", {"Run": run_id, "Mode": view_model["mode"], "Boundary": "PAPER-ONLY / READ-ONLY"}, status=view_model["mode"]), scripts=("/assets/finathink-research.js",))
        if len(parts) == 3 and parts[1] == "report":
            if entry["report_status"] != "AVAILABLE":
                return 409, "application/json", {"error": "报告文件缺失或完整性校验未通过。请恢复原始报告包，或返回研究任务新建研究；原始历史已保留。", "report_status": entry["report_status"]}
            section = parts[2]
            allowed = {"complete": "complete_report.html", "2_evidence": "2_evidence/index.html", "3_research": "3_research/index.html", "4_quant": "4_quant/index.html", "5_risk": "5_risk/index.html", "6_paper_decision": "6_paper_decision/index.html", "experiments": "experiments/index.html", "risk_attribution": "risk_attribution/index.html", "learning_history": "learning_history/index.html"}
            if section not in allowed:
                return 422, "application/json", {"error": "report section is not allow-listed"}
            relative = allowed[section]
            if section not in view_model.get("report_links", {}):
                return 404, "application/json", {"error": "report section not found"}
            report_path = entry["bundle"] / relative
            if not report_path.exists():
                return 404, "application/json", {"error": "report section not found"}
            return 200, "text/html; charset=utf-8", report_path.read_text(encoding="utf-8")
        return 404, "application/json", {"error": "research route not found"}

    @property
    def csrf_token(self) -> str:
        return hashlib.sha256(f"{self.config.session_id}:finahinking-local-form".encode()).hexdigest()

    def _bootstrap_identity(self) -> None:
        principal = self.connection.execute(
            "SELECT principal_id FROM p7_principals WHERE principal_id = ?", (self.config.principal_id,)
        ).fetchone()
        if principal is None:
            self.repository.create_principal(Principal(self.config.principal_id, "Local researcher"))
        session = self.connection.execute(
            "SELECT session_id FROM p7_sessions WHERE session_id = ?", (self.config.session_id,)
        ).fetchone()
        if session is None:
            self.repository.create_session(self.config.session_id, self.config.principal_id)
        room = self.connection.execute("SELECT room_id FROM p7_rooms WHERE room_id = 'local-room'").fetchone()
        if room is None:
            self.repository.create_room(self.config.session_id, CommunityRoom("local-room", "Local evidence room", "Private local discussion", "local"))

    @staticmethod
    def _concepts(query: str = "") -> list[dict[str, Any]]:
        query = query.strip().lower()
        concepts = DEFAULT_CATALOG.search(query) if query else DEFAULT_CATALOG.concepts
        result: list[dict[str, Any]] = []
        for item in concepts:
            payload = item.to_dict()
            payload["id"] = payload["concept_id"]
            payload["definition"] = payload["formal_definition"]
            payload["equation"] = payload["equations"][0]["expression"]
            payload["source_state"] = "SAMPLE"
            payload["curation_state"] = "CURATED"
            payload["data_mode"] = "SAMPLE"
            result.append(payload)
        return result

    def knowledge(self, query: str = "") -> dict[str, Any]:
        concepts = self._concepts(query)
        paths = [item.to_dict() for item in DEFAULT_CATALOG.learning_paths]
        levels = ["intuition", "formal", "equation", "derivation", "code", "finance", "quant", "current context"]
        fingerprint = DEFAULT_CATALOG.fingerprint
        needle = query.strip().lower()
        event_matches = []
        if not needle or any(needle in str(self.event().get(field, "")).lower() for field in ("id", "title", "publisher")):
            event_matches.append({"id": self.event()["id"], "title": self.event()["title"], "source_state": self.event()["source_state"], "data_mode": self.event()["data_mode"]})
        personal = self.personal()
        research_matches = [node for node in personal.get("nodes", []) if node.get("node_type") in {"quant_run", "strategy", "paper_run"} and (not needle or needle in str(node).lower())]
        return {
            "query": query,
            "concepts": concepts,
            "learning_paths": paths,
            "learning_path": [item["id"] for item in self._concepts()],
            "levels": levels,
            "catalog_fingerprint": fingerprint,
            "provenance": "SAMPLE: curated deterministic curriculum; verify SourceReference before reuse.",
            "events": event_matches,
            "research": research_matches,
            "strategies": [node for node in research_matches if node.get("node_type") == "strategy"],
        }

    def concept(self, concept_id: str) -> dict[str, Any] | None:
        try:
            item = DEFAULT_CATALOG.get(concept_id)
        except KeyError:
            return None
        result = item.to_dict()
        result["id"] = result["concept_id"]
        result["definition"] = result["formal_definition"]
        result["equation"] = result["equations"][0]["expression"]
        result["source_state"] = "SAMPLE"
        result["curation_state"] = "CURATED"
        result["data_mode"] = "SAMPLE"
        result.update(
            {
                "derivation": [item["statement"] for item in result["derivations"]],
                "code_example": result["code_examples"][0]["code"],
                "finance_interpretation": result["financial_interpretations"][0]["description"],
                "quant_application": result["quant_applications"][0]["description"],
                "strategy_application": result["strategy_applications"][0]["description"],
            }
        )
        return result

    def _p8_2b_context(self, unit: Any, query: Mapping[str, list[str]]) -> Any:
        """Resolve a point-in-time learning binding from server-owned research data."""

        context_type = (query.get("context_type") or [""])[0].strip()
        context_id = (query.get("context_id") or [""])[0].strip()
        if not context_type or not context_id:
            return no_context()
        if context_type != "research_point":
            return no_context()
        from finahinking.p8_2.research_view import build_research_payload

        payload = build_research_payload()
        point = next((item for item in payload.get("points", ()) if str(item.get("id")) == context_id), None)
        if not isinstance(point, Mapping):
            return no_context()
        feature_id = {
            "volatility": "range_pct",
            "sharpe": "return_1d",
            "momentum": "return_1d",
            "ols": "close",
        }.get(unit.unit_id)
        values = [{"name": "close", "value": point.get("close"), "units": "price"}]
        values.extend({"name": name, "value": value, "units": "fixture feature"} for name, value in (point.get("features") or {}).items())
        snapshot = ContextSnapshot(
            context_type=context_type,
            context_id=context_id,
            context_time=str(point["time"]),
            available_at=str(point["available_at"]),
            dataset_fingerprint=str(payload["dataset"]["fingerprint"]),
            current_values=tuple(values),
            evidence_ids=(context_id,),
            feature_id=feature_id,
            research_run_id=None,
            source_state="SAMPLE_PIT_AWARE",
            limitations=tuple(payload.get("limitations", ())) + ("This binding is a deterministic fixture observation.",),
        )
        return resolve_context(unit, snapshot)

    def _p8_2b_unit_payload(self, unit: Any, *, context: Any) -> dict[str, Any]:
        unit_payload = unit.to_dict()
        equations = []
        for equation in unit.equations:
            item = equation.to_dict()
            item["latex"] = render_latex(equation.expression)
            item["mathml"] = render_mathml(equation.expression)
            equations.append(item)
        unit_payload["equations"] = equations
        references = [record.to_dict() for record in DEFAULT_KNOWLEDGE_CATALOG.references.records() if record.reference_id in unit.references]
        context_payload = {"status": context.status, "binding": context.binding.to_dict() if context.binding else None}
        return {
            "schema_version": 1,
            "unit": unit_payload,
            "references": references,
            "context": context_payload,
            "catalog_fingerprint": DEFAULT_KNOWLEDGE_CATALOG.fingerprint,
            "provenance": "CURATED OFFLINE: structured source metadata and deterministic fixture values; no live citation lookup performed.",
        }

    def render_p8_2b_concept_page(self, unit: Any, *, context: Any, query: Mapping[str, list[str]] | None = None) -> str:
        """Render a rich P8.2B unit while retaining a no-JavaScript fallback."""

        payload = self._p8_2b_unit_payload(unit, context=context)
        esc = lambda value: html.escape(str(value or ""))
        context_params = {
            key: (query or {}).get(key, [""])[0]
            for key in ("context_type", "context_id")
            if (query or {}).get(key, [""])[0].strip()
        }
        payload_url = f"/api/p8_2b/knowledge/{unit.unit_id}"
        if context_params:
            payload_url = f"{payload_url}?{urlencode(context_params)}"
        equation_html = "".join(
            f'<article class="knowledge-equation" data-knowledge-equation="{esc(equation.equation_id)}" aria-label="Equation {esc(equation.equation_id)}">'
            f'<strong>{esc(equation.number or equation.equation_id)} · MathML</strong>{render_mathml(equation.expression)}'
            f'<code>LaTeX: {esc(render_latex(equation.expression))}</code><p class="meta">{esc(equation.meaning)}</p></article>'
            for equation in unit.equations
        )
        symbols_html = "".join(
            f'<article class="knowledge-symbol"><strong><code>{esc(symbol.notation)}</code></strong><p>{esc(symbol.meaning)}</p><small>Units: {esc(symbol.units)} · Current value: {esc(symbol.current_value if symbol.current_value is not None else "not bound")}</small></article>'
            for symbol in unit.symbols
        )
        derivation_html = "".join(
            f'<details><summary>{esc(step.step_id)} · {esc(step.operation)}</summary><p><strong>Why valid:</strong> {esc(step.reason)}</p><p><strong>Rule:</strong> {esc(step.rule_or_theorem)}</p><p><code>{esc(render_latex(step.result))}</code></p><p class="meta">Assumptions: {esc(", ".join(step.assumptions))}</p></details>'
            for step in unit.derivations
        ) or '<p class="meta">No derivation steps recorded.</p>'
        proof_html = "".join(f'<li><strong>{esc(proof.statement)}</strong> — {esc(proof.strategy)} ({esc(proof.status)})</li>' for proof in unit.proofs) or '<li>Proof boundary is described by the assumptions and limitations below.</li>'
        code_buttons = "".join(
            f'<button type="button" class="knowledge-code-line" data-segment-id="{esc(segment.segment_id)}"><span>{esc(segment.line_range[0])}–{esc(segment.line_range[1])}</span> {esc(segment.code)}</button>'
            for segment in unit.code_segments
        )
        refs_html = "".join(
            f'<li><a href="{html.escape(str(record.get("url", "#")))}" rel="noopener noreferrer">{esc(record.get("title"))}</a> <small>{esc(record.get("year"))} · {esc(record.get("reference_id"))} · DOI: {esc(record.get("doi") or "not recorded")}</small></li>'
            for record in payload["references"]
        )
        binding = context.binding.to_dict() if context.binding else None
        context_html = (
            f'<section class="card knowledge-context" data-knowledge-context><h2>Why now</h2><p data-knowledge-why-now>{esc(binding["why_now"])}</p><p class="meta">Point-in-time context: {esc(binding["context_id"])} · Feature: {esc(binding.get("feature_id") or "not bound")} · Dataset: <code>{esc(binding["dataset_fingerprint"][:16])}…</code></p><p class="meta">Current values: {esc(json.dumps(binding["current_values"], ensure_ascii=False))}</p></section>'
            if binding
            else '<section class="card knowledge-context" data-knowledge-context><h2>Why now</h2><p data-knowledge-why-now>Choose a research observation to bind current values, evidence, and a point-in-time dataset fingerprint.</p><p class="meta">NO_CONTEXT_AVAILABLE · no current value is inferred.</p></section>'
        )
        export_links = " ".join(
            f'<a class="button-secondary" href="/api/p8_2b/knowledge/{esc(unit.unit_id)}/export?format={fmt}">{label}</a>'
            for fmt, label in (("markdown", "Markdown"), ("latex", "LaTeX"), ("bibtex", "BibTeX"), ("csl", "CSL JSON"))
        )
        body = (
            f'<div class="status-row">{self._status("CURATED", "complete")}{self._status("OFFLINE", "offline")}{self._status(unit.provenance.value, "evidence")}</div>'
            f'<h1>{esc(unit.title)}</h1><p class="lede">{esc(unit.intuition)}</p>'
            f'<section data-finathink-knowledge data-payload-url="{esc(payload_url)}" class="knowledge-layout">'
            f'{context_html}<section class="card"><h2>Intuition</h2><p>{esc(unit.intuition)}</p><h2>Definition / Formal</h2><p>{esc(unit.why_now)}</p><h3>Background and history</h3><p>{esc(unit.background)}</p><p>{esc(unit.history)}</p></section>'
            f'<section class="card"><h2>Symbols</h2><div class="knowledge-symbols">{symbols_html}</div></section>'
            f'<section class="card"><h2>Equation</h2>{equation_html}<p class="meta">MathML is server-provided for offline accessibility; KaTeX upgrades the visual rendering when JavaScript is available.</p></section>'
            f'<section class="card"><h2>Derivation</h2><div class="knowledge-derivation">{derivation_html}</div><h3>Proof boundary</h3><ul>{proof_html}</ul></section>'
            f'<section class="card"><h2>Code ↔ math ↔ data</h2><div class="knowledge-code-lines" data-knowledge-code-lines>{code_buttons}</div><p class="meta" data-knowledge-code-inspector>Select a reviewed line to inspect its data input and output. Arbitrary Python execution is unavailable.</p><noscript><p class="field-help">JavaScript is disabled; the reviewed code segments remain listed above.</p></noscript></section>'
            f'<section class="card"><h2>Assumptions and limitations</h2><ul>{"".join(f"<li>{esc(item)}</li>" for item in unit.assumptions)}</ul><h3>Limitations</h3><ul>{"".join(f"<li>{esc(item)}</li>" for item in unit.limitations)}</ul></section>'
            f'<section class="card"><h2>Finance / Quant / Strategy</h2><ul>{"".join(f"<li>{esc(item)}</li>" for item in unit.applications)}</ul><p><a class="button-primary" href="/knowledge/{esc(unit.unit_id)}?depth=deep">Teach me this</a></p></section>'
            f'<section class="card"><h2>Personal learning evidence</h2><p>Browsing is not mastery. Record a self-check explicitly in the private learning store.</p><form class="form-grid" method="post" action="/api/personal/save"><input type="hidden" name="_csrf" value="{self.csrf_token}"><input type="hidden" name="node_type" value="learning_card"><input type="hidden" name="knowledge_source" value="p8_2b"><input type="hidden" name="title" value="{esc(unit.title)} self-check"><input type="hidden" name="concept_id" value="{esc(unit.unit_id)}"><label for="outcome">What is the main caveat?<select id="outcome" name="outcome" required><option value="correct">I can explain it with assumptions</option><option value="incorrect">I would treat it as a guarantee</option></select></label><button type="submit">Save learning evidence</button></form></section>'
            f'<section class="card"><h2>Current context</h2><p>{esc(binding["why_now"] if binding else "NO_CONTEXT_AVAILABLE")}</p></section>'
            f'<section class="card"><h2>References</h2><ul>{refs_html}</ul><p class="meta">Reference metadata is local and curated; DOI is not treated as verified full text.</p></section>'
            f'<section class="card"><h2>Export</h2><div class="knowledge-inline-actions">{export_links}</div></section>'
            f'<p class="meta">Catalog fingerprint: <code>{esc(DEFAULT_KNOWLEDGE_CATALOG.fingerprint)}</code> · <a href="/knowledge">Back to Knowledge</a> · <a href="/api/p8_2b/knowledge/{esc(unit.unit_id)}">JSON contract</a></p></section>'
        )
        return self.render_shell("/knowledge", unit.title, body, inspector=self._inspector("Knowledge provenance", {"Unit": unit.unit_id, "Catalog": DEFAULT_KNOWLEDGE_CATALOG.fingerprint, "Context": context.status, "Boundary": "Static code trace; no arbitrary execution"}, status="CURATED"), eyebrow="Knowledge / P8.2B", scripts=("/assets/finathink-research.js",), styles=("/assets/katex/katex.min.css",))

    def event(self) -> dict[str, Any]:
        """Replay the admitted BLS fixture when P6.5 is available.

        The compact response is intentionally a projection of the existing
        P6.5 journey, rather than a second event implementation.  A fallback
        keeps the shell importable in a minimal source checkout.
        """

        if self._event_journey is None:
            from finahinking.p6_5.bls import BLSClient
            from finahinking.p6_5.models import SourceRelease
            from finahinking.p6_5.product import UnderstandingEngine
            try:
                from finahinking.resources import fixture_path
                fixture = fixture_path("p6_5/bls_cpi_2024_2025.json")
            except ImportError:
                fixture = Path(__file__).resolve().parents[2] / "fixtures" / "p6_5" / "bls_cpi_2024_2025.json"
            captured = BLSClient.replay(
                fixture,
                capture_id="local-cpi-capture",
                retrieved_at="2025-01-15T13:32:00Z",
                first_observed_at="2025-01-15T13:31:00Z",
            )
            release = SourceRelease(
                "local-cpi-release", "bls", "bls-cpi-v2", "MACRO_RELEASE", "2024-12",
                "2025-01-15T08:30:00-05:00", "2025-01-15T13:30:00Z",
                "https://www.bls.gov/news.release/archives/cpi_01152025.htm",
            )
            self._event_journey = UnderstandingEngine(
                artifact_root=Path(tempfile.mkdtemp(prefix="finahinking-local-"))
            ).run_cpi_journey(self.config.principal_id, captured, release)
        journey = self._event_journey.to_dict()
        event = journey["event"]
        return {
            **journey,
            "id": event["event_id"],
            "title": "CPI sample release: from event to explanation",
            "publisher": "U.S. Bureau of Labor Statistics",
            "status": "CAPTURED",
            "source_state": "CAPTURED",
            "data_mode": "SAMPLE",
            "source_url": "https://www.bls.gov/news.release/archives/cpi_01152025.htm",
            "chain": ["CPI", "Inflation", "Rates", "Bonds", "Discount", "Valuation"],
            "evidence_ids": [item["evidence_id"] for item in journey.get("evidence", [])],
            "quant_status": journey.get("quant_evidence", {}).get("status", "SUCCEEDED"),
            "limitations": ["Deterministic BLS fixture; not a live market feed.", "No forecast or trade instruction."],
        }

    def diagnostics(self) -> dict[str, Any]:
        tables = [str(row["name"]) for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {
            "backend": "stdlib-http.server",
            "database": "sqlite",
            "database_path": "redacted; user-local app data",
            "database_filename": "omitted",
            "sample_mode": self.config.sample_mode,
            "offline": self.config.offline,
            "network": "disabled by default; no provider call is made by sample journeys",
            "secrets": "凭证值不进入诊断；诊断不读取 Keychain 或环境变量内容。",
            "quant_runtime": "numpy/pandas OLS and P6.6 paper-only engines available",
            "knowledge_state": {"catalog_fingerprint": DEFAULT_CATALOG.fingerprint, "concept_count": len(DEFAULT_CATALOG.concepts)},
            "source_connectivity": "NOT_TESTED",
            "action": "If a run fails, inspect the structured error and verify the local database is writable; no credentials are required for sample mode.",
            "tables": tables,
            "real_money_execution": False,
            "status": "ok",
        }

    def diagnostics_bundle(self) -> dict[str, Any]:
        """Return a shareable, path-redacted diagnostic bundle."""

        try:
            research = self.research_service.diagnostic_summary()
        except (AttributeError, KeyError, ValueError, TypeError, RuntimeError, OSError, sqlite3.Error):
            # Recovery diagnostics must not depend on reconstructing a healthy
            # research request. Unknown counts are not zero, nor successful.
            research = {"status": "UNAVAILABLE", "total_jobs": None, "latest_report_status": "UNKNOWN", "paper_only": True, "error_code": "RESEARCH_METADATA_UNAVAILABLE"}
        try:
            rows = self.interface_diagnostics.store.list(limit=50)
            interface_checks = {'status': 'AVAILABLE', 'stored_records': len(rows), 'pending_records': sum(row['state'] == 'PENDING' for row in rows), 'latest_status': rows[0]['status'] if rows else 'NOT_RUN'}
        except (OSError, ValueError, TypeError, sqlite3.Error):
            interface_checks = {'status': 'UNAVAILABLE', 'stored_records': None, 'pending_records': None, 'latest_status': 'UNKNOWN'}
        return {
            "bundle_version": 2,
            "service": "finahinking-local",
            "diagnostics": self.diagnostics(),
            "research": research,
            "interface_checks": interface_checks,
            "schema": {"tables": self.diagnostics()["tables"], "migration": "001-003 additive"},
            "privacy": {"database_path": "redacted", "secrets": "omitted", "personal_payloads": "omitted"},
            "next_action": "Attach this bundle to a local issue only after checking that no private text was added by a future extension.",
        }

    def backup_personal(self) -> dict[str, Any]:
        """Write a canonical private export to the persistent artifact area."""

        export = self.personal()
        path = self.artifact_root / f"personal-export-{self.config.principal_id}.json"
        path.write_text(json.dumps(export, sort_keys=True, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"backup_path": str(path), "export_fingerprint": export["export_fingerprint"], "node_count": len(export.get("nodes", [])), "private": True}

    def personal(self) -> dict[str, Any]:
        with self._lock:
            return self.repository.export_personal(self.config.session_id)

    @staticmethod
    def _overview_date(value: Any) -> str | None:
        """Return a bounded calendar date without exposing raw timestamps."""

        text = str(value or "").strip()
        candidate = text[:10]
        if len(candidate) != 10 or candidate[4] != "-" or candidate[7] != "-":
            return None
        try:
            from datetime import date

            date.fromisoformat(candidate)
        except ValueError:
            return None
        return candidate

    def research_comparison(self, *, limit=100, offset=0, state=None, sort_by="created_at", order="desc") -> dict[str, Any]:
        """Return a descriptive comparison of registered research runs.

        This is intentionally a state/evidence inventory.  It does not rank
        runs, calculate a quality score, or expose private report contents.
        """

        runs: list[dict[str, Any]] = []
        quantitative = []
        for entry in self._run_repository.list_runs(limit=limit, offset=offset, state=state, sort_by=sort_by, order=order):
            run_id = entry["run_id"]
            view_model, _ = self._research_model(entry)
            if entry["report_status"] == "AVAILABLE":
                source = entry["manifest"].get("source_snapshot", {})
                analytics = source.get("quant_analytics", {})
                if analytics:
                    quantitative.append({"run_id": run_id + ":baseline", "inputs": {"owner_run_id": run_id}, "metrics": analytics.get("metrics", {}), "conditions": analytics.get("comparison_conditions", {}), "limitations": analytics.get("limitations", [])})
                study = source.get("factor_study")
                if study:
                    summaries = [study["test_summary"]] if study.get("phase") == "TEST" and study.get("test_summary") else study.get("summaries", [])
                    for summary in summaries:
                        quantitative.append({"run_id": run_id + ":" + summary["experiment_id"], "inputs": {"owner_run_id": run_id, "parameters": summary["parameters"]}, "metrics": summary["metrics"], "conditions": summary["conditions"], "limitations": study.get("limitations", [])})
            stage_status = view_model.get("stage_status", {})
            completed_statuses = {"COMPLETE", "COMPLETED", "READY", "SUCCEEDED", "DONE"}
            evidence_count = sum(int(item.get("evidence_count", 0)) for item in view_model.get("analysts", ()) if isinstance(item, Mapping))
            runs.append(
                {
                    "run_id": run_id,
                    "as_of": self._overview_date(view_model.get("as_of")),
                    "state": str(view_model.get("state", "UNKNOWN")),
                    "stages_total": len(stage_status),
                    "stages_completed": sum(str(value).upper() in completed_statuses for value in stage_status.values()),
                    "analysts": len(view_model.get("analysts", ())),
                    "evidence_count": evidence_count,
                    "report_count": len(view_model.get("report_links", {})),
                    "decision_eligible": bool(view_model.get("decision_eligible", False)),
                    **{key: entry[key] for key in ("created_at", "started_at", "finished_at", "dataset_fingerprint", "strategy_version", "policy_fingerprint", "report_status")},
                }
            )
        from .research.reports import compare_experiments

        return {
            "schema_version": 1,
            "mode": "OFFLINE" if self.config.offline else "LOCAL",
            "paper_only": True,
            "runs": runs,
            "quantitative": compare_experiments(quantitative),
            "total": self._run_repository.count_runs(state=state), "limit": limit, "offset": offset,
            "basis": ["state", "as_of", "stage completion", "analyst evidence count", "report coverage", "decision eligibility"],
            "limitations": ["Descriptive run metadata only; no run is ranked or called a winner.", "Reports are verified from the persistent local inventory; unavailable bundles remain listed."],
        }

    def overview(self) -> dict[str, Any]:
        """Return a read-only, public-safe projection for the Overview page.

        The projection deliberately selects a small allow-list of server-owned
        values.  It does not expose personal payloads, provider configuration,
        prompts, endpoints, file paths, or raw artifact records.
        """

        event = self.event()
        personal = self.personal()
        nodes = [item for item in personal.get("nodes", ()) if isinstance(item, Mapping)]
        history = [item for item in personal.get("history", ()) if isinstance(item, Mapping)]
        mastery = [item for item in personal.get("mastery_evidence", ()) if isinstance(item, Mapping)]
        activity = []
        activity_buckets: dict[str, dict[str, int | str]] = {}
        topic_buckets: dict[str, dict[str, int | str]] = {}
        allowed_topic_kinds = {"event", "factor", "knowledge", "learning", "ml", "paper_run", "quant", "research", "strategy"}
        allowed_event_types = {"completed", "created", "updated", "recorded", "saved", "started"}
        for item in history[-100:]:
            date_key = self._overview_date(item.get("occurred_at"))
            if date_key is None:
                continue
            raw_source_kind = str(item.get("source_kind", "research")).strip().lower()
            source_kind = raw_source_kind if raw_source_kind in allowed_topic_kinds else "research"
            kind = "learning" if source_kind in {"event", "learning", "knowledge"} else "research"
            bucket = activity_buckets.setdefault(date_key, {"date": date_key, "count": 0, "learning": 0, "research": 0})
            bucket["count"] = int(bucket["count"]) + 1
            bucket[kind] = int(bucket[kind]) + 1
            topic = source_kind[:48]
            topic_bucket = topic_buckets.setdefault(topic, {"topic": topic, "count": 0, "last_active": date_key})
            topic_bucket["count"] = int(topic_bucket["count"]) + 1
            topic_bucket["last_active"] = max(str(topic_bucket["last_active"]), date_key)
            raw_event_type = str(item.get("event_type", "recorded")).strip().lower()
            event_type = raw_event_type if raw_event_type in allowed_event_types else "recorded"
            activity.append({"timestamp": date_key, "kind": kind, "label": source_kind, "status": event_type.upper()})
        for item in mastery[-100:]:
            date_key = self._overview_date(item.get("observed_at"))
            if date_key is None:
                continue
            bucket = activity_buckets.setdefault(date_key, {"date": date_key, "count": 0, "learning": 0, "research": 0})
            bucket["count"] = int(bucket["count"]) + 1
            bucket["learning"] = int(bucket["learning"]) + 1
        learning_buckets: dict[str, dict[str, Any]] = {}
        for item in mastery[-100:]:
            date_key = self._overview_date(item.get("observed_at"))
            if date_key is None:
                continue
            bucket = learning_buckets.setdefault(date_key, {"date": date_key, "count": 0, "outcomes": {}})
            outcome = str(item.get("outcome", "UNKNOWN")).upper()
            bucket["count"] += 1
            bucket["outcomes"][outcome] = int(bucket["outcomes"].get(outcome, 0)) + 1
        research_comparison = self.research_comparison()
        for day in self._run_repository.activity_by_day():
            date_key = day["date"]
            if not date_key:
                continue
            bucket = activity_buckets.setdefault(date_key, {"date": date_key, "count": 0, "learning": 0, "research": 0})
            bucket["count"] = int(bucket["count"]) + day["count"]
            bucket["research"] = int(bucket["research"]) + day["count"]
        for run in research_comparison["runs"]:
            activity.append({"timestamp": run["created_at"], "kind": "research", "label": "research run", "status": run["state"]})
        activity_series = [dict(activity_buckets[key]) for key in sorted(activity_buckets)]
        topics = sorted(topic_buckets.values(), key=lambda item: (-int(item["count"]), str(item["topic"])))[:12]
        learning_activity = [learning_buckets[key] for key in sorted(learning_buckets)]
        state_counts = self._run_repository.counts_by_state()
        research_states = sorted(state_counts)
        as_of = str((event.get("event") or {}).get("available_at") or "2025-01-15T13:31:00Z")[:10]
        return {
            "schema_version": 1,
            "mode": "OFFLINE" if self.config.offline else "LOCAL",
            "paper_only": True,
            "as_of": as_of,
            "metrics": {
                "sample_events": 1,
                "saved_items": len(nodes),
                "learning_items": len(mastery),
                "research_runs": self._run_repository.count_runs(),
            },
            "activity": activity,
            "activity_series": activity_series,
            "topics": [dict(item) for item in topics],
            "topic_heatmap": [dict(item) for item in topics],
            "learning_activity": learning_activity,
            "research_status": {"count": self._run_repository.count_runs(), "states": research_states[-8:], "counts": state_counts},
            "research_comparison": research_comparison["runs"],
            "limitations": [
                "Deterministic fixture and offline mode; no live market feed is queried.",
                "Overview values are descriptive and read-only; no forecast, order, or trade instruction is produced.",
            ],
            "provenance": {
                "data_mode": str(event.get("data_mode", "SAMPLE")),
                "source_state": str(event.get("source_state", "CAPTURED")),
                "source_id": str((event.get("event") or {}).get("source_id", "bls")),
                "reference_period": str((event.get("event") or {}).get("reference_period", "unknown")),
            },
        }

    def save_personal(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        title = str(payload.get("title", "")).strip()
        if not title or len(title) > 256:
            raise ValueError("title is required and must be at most 256 characters")
        node_type = str(payload.get("node_type", "note"))
        body = payload.get("payload", {})
        if not isinstance(body, dict):
            raise TypeError("payload must be a JSON object")
        if node_type == "learning_card":
            concept_id = str(payload.get("concept_id", "")).strip()
            outcome = str(payload.get("outcome", "neutral")).strip()
            if outcome not in {"correct", "incorrect", "neutral", "corrected"}:
                raise ValueError("outcome is invalid")
            source = str(payload.get("knowledge_source", "")).strip().lower()
            p7_ids = {item.concept_id for item in DEFAULT_CATALOG.concepts}
            p8_unit = None
            if source == "p8_2b":
                try:
                    p8_unit = get_knowledge_unit(concept_id)
                except KeyError as exc:
                    raise ValueError("concept_id is not in the P8.2B knowledge catalog") from exc
            elif source not in {"", "p7_5"}:
                raise ValueError("knowledge_source is not supported")
            elif concept_id not in p7_ids:
                try:
                    p8_unit = get_knowledge_unit(concept_id)
                except KeyError as exc:
                    raise ValueError("concept_id is not in the structured knowledge catalog") from exc

            if p8_unit is not None:
                # Prefix the node identity and record the exact catalog fingerprint so
                # similarly named P7 concepts cannot silently receive P8.2B evidence.
                node_id = f"p8_2b:{p8_unit.unit_id}"
                node = PersonalNode(node_id, "concept", p8_unit.title, {"unit_id": p8_unit.unit_id, "source": "p8_2b_catalog", "source_fingerprint": DEFAULT_KNOWLEDGE_CATALOG.fingerprint})
                evidence_reference = f"knowledge:p8_2b:{p8_unit.unit_id}:self-check"
                result_id = p8_unit.unit_id
            else:
                node_id = concept_id
                node = PersonalNode(node_id, "concept", DEFAULT_CATALOG.get(concept_id).title, {"concept_id": concept_id, "source": "p7_5_catalog", "source_fingerprint": DEFAULT_CATALOG.fingerprint})
                evidence_reference = f"knowledge:{concept_id}:self-check"
                result_id = concept_id
            with self._lock:
                try:
                    self.repository.get_node(self.config.session_id, node_id)
                except KeyError:
                    self.repository.save_node(self.config.session_id, node)
                evidence = MasteryEvidence(f"mastery:{node_id}:{uuid.uuid4().hex}", node_id, "quiz_response", evidence_reference, outcome, "2026-10-03T00:00:00Z", {"response": str(payload.get("outcome")), "source": node.payload["source"], "source_fingerprint": node.payload["source_fingerprint"]})
                self.repository.save_mastery_evidence(self.config.session_id, evidence)
            result = {"node_id": node_id, "node_type": node.node_type, "concept_id": result_id, "outcome": outcome, "saved": True}
            if p8_unit is not None:
                result.update({"unit_id": p8_unit.unit_id, "knowledge_source": "p8_2b", "source_fingerprint": DEFAULT_KNOWLEDGE_CATALOG.fingerprint})
            return result
        node = PersonalNode(f"local-{uuid.uuid4().hex}", node_type, title, dict(body))
        with self._lock:
            self.repository.save_node(self.config.session_id, node)
        return {"node_id": node.node_id, "title": node.title, "node_type": node.node_type, "saved": True}

    @staticmethod
    def _status(value: Any, kind: str | None = None) -> str:
        """Render a text-first status badge; color is never the only signal."""

        label = str(value or "UNKNOWN").strip().upper()
        slug = (kind or label).lower().replace(" ", "-").replace("_", "-")
        return f'<span class="status status--{html.escape(slug)}">{html.escape(label)}</span>'

    @staticmethod
    def _page_names() -> dict[str, str]:
        return {
            "/": "研究总览",
            "/events": "市场事件",
            "/explore": "探索研究",
            "/knowledge": "知识学习",
            "/quant": "量化分析",
            "/strategy": "策略实验室",
            "/research": "研究工作区",
            "/research/jobs": "研究任务",
            "/research/experiments": "因子实验",
            "/research/learning": "复盘与学习",
            "/research/maintenance": "本地任务维护",
            "/research/compare": "研究对比",
            "/library/methods": "研究方法",
            "/library/behavior": "公开行为档案",
            "/library/lessons": "反例与复盘",
            "/workbench": "因子策略工作台",
            "/ml": "机器学习实验",
            "/parameter": "参数实验",
            "/workspace": "个人工作区",
            "/community": "社区研究",
            "/settings/engines": "引擎设置",
            "/settings/providers": "模型接口",
            "/settings/data-sources": "数据来源",
            "/settings/data-connections": "数据接口",
            "/diagnostics": "诊断与恢复",
        }

    @staticmethod
    def asset_bytes(asset_name: str) -> bytes:
        """Return one of the explicitly allow-listed packaged assets."""

        allowed = {"finathink-splash-map.jpg", "finathink-research-splash.jpg", "finathink-research.js"}
        if asset_name not in allowed:
            raise FileNotFoundError(asset_name)
        try:
            return importlib_resources.files("finahinking").joinpath(f"_package_data/assets/{asset_name}").read_bytes()
        except (FileNotFoundError, ModuleNotFoundError):
            fallback = Path(__file__).resolve().parent / "_package_data" / "assets" / asset_name
            return fallback.read_bytes()

    @staticmethod
    def splash_asset() -> bytes:
        return LocalApplication.asset_bytes("finathink-splash-map.jpg")

    @staticmethod
    def research_splash_asset() -> bytes:
        return LocalApplication.asset_bytes("finathink-research-splash.jpg")

    @staticmethod
    def research_script_asset() -> bytes:
        return LocalApplication.asset_bytes("finathink-research.js")

    def _provider_config(self) -> dict[str, Any]:
        """Return the local provider registry without accepting secret values."""

        config = {
            "defaults": {
                "provider": os.environ.get("FINAHINKING_PROVIDER", "offline"),
                "model": os.environ.get("FINAHINKING_MODEL", "fixture-v1"),
                "role_models": {},
            },
            "providers": [
                {
                    "name": "offline",
                    "model": "fixture-v1",
                    "capabilities": ["structured_output", "offline"],
                    "enabled": True,
                    "offline": True,
                },
                {
                    "name": "user-compatible",
                    "model": os.environ.get("FINAHINKING_MODEL", "user-model"),
                    "capabilities": ["structured_output", "tool_calling"],
                    "enabled": True,
                    "offline": False,
                    "credential_ref": {"env_var": "FINAHINK_USER_API_KEY"},
                },
            ],
        }
        configured = {item["provider_id"]: item for item in self._provider_configs.list()}
        config["providers"] = [item for item in config["providers"] if item["name"] not in configured]
        for item in configured.values():
            config["providers"].append({"name": item["provider_id"], "model": item["model"], "enabled": item["enabled"], "offline": item["adapter_kind"] == "offline", "credential_ref": item["credential_ref"], "capabilities": ["structured_output"]})
        return config

    @staticmethod
    def katex_asset(asset_name: str, *, font: bool = False) -> bytes:
        """Read one vendored KaTeX stylesheet/font without allowing path traversal."""

        if not asset_name or asset_name in {".", ".."} or "/" in asset_name or "\\" in asset_name or ".." in asset_name:
            raise FileNotFoundError(asset_name)
        if asset_name != "katex.min.css" and not asset_name.endswith((".woff2", ".woff", ".ttf")):
            raise FileNotFoundError(asset_name)
        relative = f"_package_data/assets/katex/{'fonts/' if font else ''}{asset_name}"
        try:
            return importlib_resources.files("finahinking").joinpath(relative).read_bytes()
        except (FileNotFoundError, ModuleNotFoundError):
            fallback = Path(__file__).resolve().parent / "_package_data" / "assets" / "katex" / ("fonts" if font else "") / asset_name
            return fallback.read_bytes()

    def _nav(self, page: str) -> str:
        names = self._page_names()
        groups = (
            ("研究", "nav-workspace", ("/", "/research/jobs", "/events", "/knowledge", "/research/experiments", "/strategy", "/research/learning")),
            ("资料", "nav-library", ("/library/methods", "/library/behavior", "/library/lessons")),
            ("学习", "nav-learn", ("/explore", "/quant", "/research", "/research/compare", "/workbench", "/ml", "/parameter", "/workspace", "/community", "/settings/engines", "/settings/data-sources")),
            ("系统", "nav-system", ("/settings/data-connections", "/settings/providers", "/research/maintenance", "/diagnostics")),
        )
        icons = {"/": "overview", "/research/jobs": "codex", "/events": "events", "/knowledge": "knowledge", "/research/experiments": "factor", "/strategy": "strategy", "/research/learning": "report", "/settings/data-connections": "data"}

        # Keep the conditional attribute construction explicit so the rendered
        # HTML remains easy to inspect in a browser and in snapshot tests.
        def nav_item(href: str) -> str:
            current = ' aria-current="page"' if href == page else ""
            return f'<li><a class="nav-link" href="{href}"{current}>{nav_icon(icons.get(href, "system"))}<span>{html.escape(names[href])}</span></a></li>'

        sections = []
        for label, section_id, links in groups:
            items = "".join(nav_item(href) for href in links)
            if section_id == "nav-learn":
                expanded = ' open' if page in links else ''
                sections.append(f'<details class="nav-section nav-more" aria-label="{label}"{expanded}><summary>更多研究工具</summary><ul class="nav-list">{items}</ul></details>')
            else:
                sections.append(f'<section class="nav-section" aria-label="{label}" aria-labelledby="{section_id}"><h2 class="nav-section-title" id="{section_id}">{label}</h2><ul class="nav-list">{items}</ul></section>')
        return (
            f'<button type="button" class="shell-icon-button shell-close" data-shell-close aria-label="关闭导航" hidden>{nav_icon("close")}</button>'
            '<a class="brand" href="/" aria-label="Finathink home">'
            '<span class="brand-mark" aria-hidden="true">F</span><span><span class="brand-name">Finathink<small>.</small></span><span class="brand-caption">RESEARCH WITH EVIDENCE</span></span></a>'
            '<div class="space-switch"><span class="space-symbol" aria-hidden="true">F</span><strong>个人研究空间<small>Local workspace</small></strong></div>'
            + "".join(sections)
            + '<p class="sidebar-note"><strong>本地优先 · 证据可追溯</strong>研究 → 验证 → 解释 → 学习</p>'
        )

    @staticmethod
    def _inspector(title: str, items: Mapping[str, Any], *, status: str | None = None) -> str:
        rows = "".join(
            f"<dt>{html.escape(str(label))}</dt><dd>{html.escape(str(value))}</dd>"
            for label, value in items.items()
        )
        badge = LocalApplication._status(status) if status else ""
        return f'<aside class="inspector" id="inspector" aria-label="Inspector"><div class="inspector-inner"><p class="eyebrow">Inspector</p><h2>{html.escape(title)}</h2>{badge}<dl>{rows}</dl></div></aside>'

    def render_shell(
        self,
        page: str,
        title: str,
        body: str,
        *,
        inspector: str = "",
        eyebrow: str = "Local research workspace",
        scripts: tuple[str, ...] = (),
        styles: tuple[str, ...] = (),
    ) -> str:
        """Wrap every HTML journey in the same accessible product shell."""

        clean_page = "/knowledge" if page.startswith("/knowledge/") else page
        inspector_html = inspector or self._inspector(
            "Research context",
            {
                "Mode": "SAMPLE / OFFLINE" if self.config.offline else "NETWORK ENABLED",
                "Boundary": "No real-money execution",
                "Next": "Open an evidence or learning path",
            },
        )
        style_tags = "".join(f'<link rel="stylesheet" href="{html.escape(path)}">' for path in styles)
        script_tags = "".join(f'<script src="{html.escape(path)}" defer></script>' for path in dict.fromkeys(("/assets/finathink-research.js", *scripts)))
        from .research.asset_workspace_page import ASSET_WORKSPACE_CSS
        from .research.factor_detail_page import FACTOR_DETAIL_CSS
        from .research.learning_workspace_page import LEARNING_CSS

        content = f'<div class="workspace-inner"><p class="eyebrow">{html.escape(eyebrow)}</p>{body}</div>'
        if page == "/":
            # The shared heading precedes both columns, as in the supplied HTML.
            # Keep it within the main landmark and the context after the body.
            layout = (
                '<main class="workspace-layout workspace-layout--overview" id="main" tabindex="-1">'
                f'{OVERVIEW_HEADER_HTML}<div class="workspace">{content}</div>{inspector_html}</main>'
            )
        else:
            layout = (
                '<div class="workspace-layout"><main class="workspace" id="main" tabindex="-1">'
                f'{content}</main>{inspector_html}</div>'
            )
        return (
            '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{html.escape(title)} · Finathink</title>"
            '<meta name="theme-color" content="#f5f8fd">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'self\'; img-src \'self\'; style-src \'self\' \'unsafe-inline\'; script-src \'self\'; connect-src \'self\'; form-action \'self\'">'
            f"<style>{_APP_CSS}{BLUE_WHITE_CSS}{_RESEARCH_JOBS_CSS if page == '/research/jobs' else ''}{_FACTOR_STUDY_CSS if page == '/research/experiments' else ''}{LEARNING_CSS if page in {'/research/learning', '/research/maintenance'} else ''}{ASSET_WORKSPACE_CSS if page.startswith('/library/') or page == '/research/experiments' else ''}{FACTOR_DETAIL_CSS if page == '/research/experiments' else ''}{DIAGNOSTIC_CSS if page == '/diagnostics' else ''}</style>{style_tags}{script_tags}</head><body>"
            '<a class="skip-link" href="#main">跳到主要内容</a>'
            '<div class="app-shell" data-finathink-shell>'
            f'<nav class="sidebar" id="shell-navigation" aria-label="主导航">{self._nav(clean_page)}</nav>'
            '<button type="button" class="shell-shade" data-shell-shade aria-label="关闭导航遮罩" tabindex="-1" hidden></button>'
            '<div class="app-frame" data-shell-frame><header class="topbar" aria-label="Workspace context">'
            f'<div class="topbar-context"><button type="button" class="shell-icon-button shell-menu" data-shell-menu aria-controls="shell-navigation" aria-label="展开导航" aria-expanded="false" hidden>{nav_icon("menu")}</button><span>工作空间 /</span><strong>{html.escape(title)}</strong></div>'
            f'<div class="topbar-actions"><button type="button" class="shell-search-open" data-shell-search-open aria-controls="shell-search" aria-label="搜索功能入口" hidden>{nav_icon("search")}<span>搜索功能入口</span><kbd>⌘ K</kbd></button><span class="local-status">本地研究</span><a class="button" href="/research/jobs">新建研究</a></div></header>'
            f'{layout}<footer class="footer-note shell-footer">本地优先 · 可追溯研究与学习 · PAPER-ONLY</footer>'
            f"</div></div>{shell_search_dialog()}</body></html>"
        )

    def _data_write_guard(self, body: Any) -> tuple[int, str, Any] | None:
        if not isinstance(body, Mapping) or body.get("_csrf") != self.csrf_token:
            return 403, "application/json", {"error": "csrf token is required"}
        return None

    @staticmethod
    def _snapshot_summary(snapshot):
        return {"snapshot_ref": snapshot.fingerprint, "instruments": list(snapshot.instruments), "record_count": len(snapshot.observations), "mode": snapshot.mode, "as_of": snapshot.as_of.isoformat(), "pit_status": "AVAILABLE" if snapshot.pit_available() else "UNKNOWN"}

    def _asset_workspace_route(self, method, clean, query):
        from .research.asset_catalog import ResearchAssetCatalog
        from .research.asset_contracts import AssetLoadError
        from .research.asset_workspace_page import (
            KINDS,
            ResearchAssetWorkspace,
            asset_workspace_page,
        )

        library = clean in {f"/library/{kind}" for kind in KINDS}
        api = clean == "/api/research/assets" or clean.startswith("/api/research/assets/")
        if not library and not api:
            return None
        if method != "GET":
            return 405, "application/json", {"error": "资料目录只读，浏览不会执行研究。"}
        try:
            service = ResearchAssetWorkspace(ResearchAssetCatalog.packaged())
            if library:
                kind = clean.rsplit("/", 1)[-1]
                content = asset_workspace_page(service, kind, query or {})
                return 200, "text/html; charset=utf-8", self.render_shell(clean, KINDS[kind], content,
                    inspector=self._inspector("资料上下文", {"来源": "包内校验资源", "执行": "尚未加载到研究", "下一步": "查看模块迁移去向" if kind == "sources" else "检查输入后带入问题"}))
            if clean != "/api/research/assets":
                return 200, "application/json", service.detail(clean.removeprefix("/api/research/assets/"))
            values = query or {}
            payload = service.list(kind=values.get("kind", ["methods"])[0], tags=tuple(values.get("tag", ())),
                query=values.get("q", [""])[0], limit=int(values.get("limit", ["20"])[0]), offset=int(values.get("offset", ["0"])[0]))
            return 200, "application/json", payload
        except AssetLoadError as exc:
            if library:
                return 503, "text/html; charset=utf-8", self.render_error_page("资料目录暂不可用",
                    "资料校验没有通过，尚未加载到研究。", "请重新读取；如果仍失败，检查安装包或诊断记录。",
                    links=((clean, "重新读取资料"), ("/diagnostics", "检查诊断")))
            return 503, "application/json", {"error": "资料校验失败，请重新读取或检查安装包。", "code": exc.code}
        except KeyError:
            return 404, "application/json", {"error": "未找到这项资料，请返回目录选择。"}
        except (ValueError, TypeError):
            return 422, "application/json", {"error": "资料检索参数无效，请核对类别、分页与资料身份。"}

    def _factor_detail_route(self, method, clean, body, query):
        from .research.factor_detail_page import factor_detail, factor_detail_page

        public = clean.startswith("/research/factors/")
        api = clean.startswith("/api/research/factors/")
        if not public and not api:
            return None
        parts = clean.removeprefix("/research/factors/" if public else "/api/research/factors/").split("/")
        if public and method != "GET":
            return 405, "application/json", {"error": "因子详情只读，决定请通过显式审核。"}
        if method == "POST":
            denied = self._data_write_guard(body)
            if denied is not None:
                return denied
        try:
            if api and method == "POST" and len(parts) == 2:
                values = {key: value for key, value in (body or {}).items() if key != "_csrf"}
                if parts[1] == "decisions":
                    return 201, "application/json", self.research_service.review_factor_use(parts[0], values)
                if parts[1] == "monitor":
                    return 200, "application/json", self.research_service.refresh_factor_monitoring(parts[0], values)
            if method != "GET" or len(parts) != 1:
                return 405, "application/json", {"error": "不支持此因子操作。"}
            values = query or {}
            data = factor_detail(self.research_service, parts[0], version=values.get("version", [None])[0],
                window=int(values["window"][0]) if values.get("window") else None, run_ref=values.get("run_ref", [None])[0])
            if api:
                return 200, "application/json", data
            return 200, "text/html; charset=utf-8", self.render_shell("/research/experiments", "因子证据", factor_detail_page(data, self.csrf_token),
                inspector=self._inspector("因子上下文", {"身份": data["factor_id"], "定义": data["version"], "边界": "来源、计算、用途分别判断"}))
        except KeyError:
            return 404, "application/json", {"error": "没有匹配的因子、版本或报告；不会回退到另一版本。"}
        except (ValueError, TypeError):
            return 422, "application/json", {"error": "因子输入或证据绑定无效，请重新读取定义和范围。"}

    def _research_jobs_route(self, method, clean, body, query):
        if not (clean in {"/api/research/readiness", "/api/research/jobs", "/api/research/experiments", "/api/research/learning"} or clean.startswith(("/api/research/panels/", "/api/research/jobs/", "/api/research/snapshots", "/api/research/experiments/", "/api/research/learning/"))):
            return None
        content_type = "application/json"
        if method not in {"GET", "POST"}:
            return 405, content_type, {"error": "research endpoint supports GET and POST"}
        if method == "POST":
            denied = self._data_write_guard(body)
            if denied is not None:
                return denied
        values = {key: value for key, value in (body or {}).items() if key != "_csrf"} if isinstance(body, Mapping) else {}
        try:
            service = self.research_service
            if clean.startswith("/api/research/panels/"):
                if method != "POST":
                    return 405, content_type, {"error": "请显式提交面板研究输入。"}
                if clean == "/api/research/panels/fields":
                    from .data.panel_contracts import PanelFieldAttachment
                    from .data.research_fields import ResearchFieldStore

                    if set(values) != {"attachment"}:
                        raise ValueError("字段保存只接受不可变面板附件")
                    attachment = PanelFieldAttachment.from_dict(values["attachment"])
                    ref = ResearchFieldStore(service.snapshots).save(attachment)
                    return 201, content_type, {"attachment_ref": ref, "snapshot_ref": attachment.snapshot_ref}
                if clean == "/api/research/panels/evaluate":
                    from .research.panel_research import evaluate_panel_request

                    return 200, content_type, evaluate_panel_request(service.snapshots, values)
                return 404, content_type, {"error": "未找到面板研究接口。"}
            if clean == "/api/research/readiness":
                return (200, content_type, service.readiness(values)) if method == "POST" else (405, content_type, {"error": "请显式提交输入检查。"})
            if clean.startswith("/api/research/jobs/"):
                parts = clean.removeprefix("/api/research/jobs/").split("/")
                if len(parts) >= 2 and parts[1] == "methods":
                    if len(parts) == 2 and method == "POST":
                        from .research.asset_contracts import MethodRouteRequest

                        return 200, content_type, service.export_method_handoff(parts[0], MethodRouteRequest(**values))
                    if len(parts) == 3 and method == "GET":
                        return 200, content_type, service.get_method_handoff(parts[0], unquote(parts[2]))
                    if len(parts) == 4 and parts[3] == "result" and method == "POST":
                        if set(values) != {"response"}:
                            raise ValueError("交接回传只接受绑定的结果对象")
                        return 200, content_type, service.accept_method_handoff(parts[0], unquote(parts[2]), values["response"])
                if len(parts) == 2 and parts[1] == "research-fields" and method == "POST":
                    if set(values) != {"attachment"}:
                        raise ValueError("字段保存只接受不可变附件")
                    return 201, content_type, service.attach_research_fields(parts[0], values["attachment"])
                if len(parts) == 2 and parts[1] == "method-studies" and method == "POST":
                    if set(values) - {"handoff_ref", "artifact_ref", "attachment_ref", "idempotency_key"}:
                        raise ValueError("受控研究提交字段无效")
                    return 201, content_type, service.create_method_factor_study(parts[0], values.get("handoff_ref"),
                        values.get("artifact_ref"), attachment_ref=values.get("attachment_ref"), idempotency_key=values.get("idempotency_key"))
            if clean == "/api/research/learning":
                return (201, content_type, service.learning.settle(values)) if method == "POST" else (200, content_type, {"reviews": service.learning.list_reviews()})
            if clean == "/api/research/learning/policies":
                return (200, content_type, {"policies": service.learning.policies()}) if method == "GET" else (405, content_type, {"error": "配置版本只读，请通过提案审核创建新版本。"})
            if clean == "/api/research/learning/rerun":
                return (201, content_type, service.learning.rerun(values)) if method == "POST" else (405, content_type, {"error": "请显式提交版本复跑。"})
            if clean.startswith("/api/research/learning/"):
                parts = clean.removeprefix("/api/research/learning/").split("/")
                if len(parts) == 1 and method == "GET":
                    return 200, content_type, service.learning.get(parts[0])
                if len(parts) == 2 and parts[1] == "decision" and method == "POST":
                    return 200, content_type, service.learning.decide(parts[0], values)
                return 405, content_type, {"error": "复盘记录只支持读取和显式审核。"}
            if clean == "/api/research/experiments/catalog" and method == "GET":
                from finahinking.research.factor_catalog_loader import load_research_recipes

                return 200, content_type, {"recipes": [recipe.to_dict() for recipe in load_research_recipes()], "paper_only": True}
            if clean == "/api/research/experiments/sample" and method == "POST":
                from finahinking.p8_2.data_sources import FixtureMarketDataSource

                if set(values) - {"instrument"}:
                    raise ValueError("unsupported sample inputs")
                snapshot = FixtureMarketDataSource(points=240).snapshot(values.get("instrument", "AAA"))
                service.snapshots.save(snapshot)
                return 201, content_type, self._snapshot_summary(snapshot)
            if clean == "/api/research/experiments":
                if method == "POST":
                    return 202, content_type, service.create_factor_study(values)
                return 200, content_type, {"studies": [{"study_id": row["study_id"], "state": row["state"], "question": row["spec"]["question"], "snapshot_ref": row["spec"]["snapshot_ref"]} for row in service.factor_studies.list()]}
            if clean.startswith("/api/research/experiments/"):
                parts = clean.removeprefix("/api/research/experiments/").split("/")
                if len(parts) == 1 and method == "GET":
                    return 200, content_type, service.factor_study(parts[0])
                if len(parts) == 2 and method == "POST":
                    if parts[1] == "freeze" and set(values) == {"candidate_id"}:
                        return 200, content_type, service.freeze_factor_study(parts[0], values["candidate_id"])
                    if parts[1] == "test" and set(values) == {"idempotency_key"}:
                        return 202, content_type, service.test_factor_study(parts[0], values["idempotency_key"])
                    if parts[1] == "review" and set(values) == {"decision", "reviewer_id"}:
                        return 200, content_type, service.review_factor_study(parts[0], values["decision"], values["reviewer_id"])
                return 405, content_type, {"error": "use explicit study actions with supported fields"}
            if clean == "/api/research/jobs":
                if method == "POST":
                    return 202, content_type, service.submit(values)
                return 200, content_type, service.list_jobs(limit=int(query.get("limit", ["50"])[0]), offset=int(query.get("offset", ["0"])[0]))
            if clean.startswith("/api/research/jobs/"):
                parts = clean.removeprefix("/api/research/jobs/").split("/")
                if len(parts) == 1 and method == "GET":
                    return 200, content_type, service.get_job(parts[0])
                if len(parts) == 2 and parts[1] == "cancel" and method == "POST":
                    return 200, content_type, service.cancel(parts[0])
                return 405, content_type, {"error": "use GET status or POST cancel"}
            if clean == "/api/research/snapshots" and method == "GET":
                return 200, content_type, {"snapshots": [self._snapshot_summary(item) for item in service.snapshots.list_snapshots()]}
            if clean == "/api/research/snapshots/sample" and method == "POST":
                from finahinking.p8_2.data_sources import FixtureMarketDataSource

                if set(values) - {"instrument"}:
                    raise ValueError("unsupported sample fields")
                snapshot = FixtureMarketDataSource(points=60).snapshot(values.get("instrument", "AAA"))
                service.snapshots.save(snapshot)
                return 201, content_type, self._snapshot_summary(snapshot)
            if clean == "/api/research/snapshots/capture" and method == "POST":
                if set(values) - {"connection_id", "instrument", "start", "end", "as_of", "price_currency", "volume_unit", "asset_classes", "adjustment"}:
                    raise ValueError("unsupported capture fields")
                config = self._data_connections.get(values["connection_id"])
                if not isinstance(values.get("instrument"), str):
                    raise ValueError("instruments must be comma-separated text")
                request = DataRequest(dataset_kind="prices", instruments=tuple(item.strip() for item in values["instrument"].split(",")), start=values.get("start"), end=values.get("end"), as_of=values.get("as_of"))
                units = {key: values.get(key, "UNKNOWN") or "UNKNOWN" for key in ("price_currency", "volume_unit")}
                classes = values.get("asset_classes") or {}
                if isinstance(classes, str):
                    classes = json.loads(classes)
                reference = JsonApiConnector(config, self._data_connections.credentials, self._data_transport).capture(request, service.snapshots, units=units, asset_classes=classes, adjustment=values.get("adjustment", "UNKNOWN"))
                return 201, content_type, self._snapshot_summary(service.snapshots.load(reference))
            return 405, content_type, {"error": "use an explicit snapshot preparation endpoint"}
        except FileNotFoundError:
            return 422, content_type, {"error": "所选快照不存在，请重新选择或显式获取数据。"}
        except KeyError:
            return 404, content_type, {"error": "未找到当前会话的研究任务或数据连接。"}
        except DataConnectorError as exc:
            return 502, content_type, {"error": "数据获取失败，请检查连接、字段映射及数据权限。", "code": exc.code}
        except (ValueError, TypeError) as exc:
            from .p8_2.data_sources import MissingPriceFields

            if clean == "/api/research/snapshots/capture" and isinstance(exc, MissingPriceFields):
                return 422, content_type, {"error": "数据缺少必要字段：" + "、".join(exc.fields) + "。请在数据接口中补齐字段映射，再获取快照。", "code": "MISSING_PRICE_FIELDS", "missing_fields": list(exc.fields)}
            if clean.startswith("/api/research/learning"):
                from .research.contracts import _unsafe_public_text

                message = str(exc)
                return 422, content_type, {"error": message if message and len(message) <= 240 and not _unsafe_public_text(message) else "复盘输入未通过校验，请核对原报告、观察期、数据可用时间和修改预览后重试。"}
            return 422, content_type, {"error": "输入未通过校验，请检查快照、标的、日期、研究问题和资源上限；研究问题中不要粘贴凭证。"}
        except (OSError, sqlite3.Error):
            return 503, content_type, {"error": "本地研究存储暂不可用，请检查磁盘空间和文件权限后重试。"}

    def _research_jobs_page_body(self, *, query=None):
        supplied = (query or {}).get("question", [""])[0].strip()
        question = supplied[:1000] or "检验这个快照上的动量基线，并检查费用与历史回撤。"
        notice = '<p data-question-truncated role="status">问题过长，已保留前 1000 字。请核对后再提交研究。</p>' if len(supplied) > 1000 else ''
        return (
            '<section data-run-workspace data-job-status="EMPTY">'
            '<div class="status-row"><span class="status status--offline">LOCAL WORKSPACE</span><span class="status status--ready">PAPER-ONLY</span></div>'
            '<h1>研究任务</h1><p class="lede">选定数据，运行有边界的研究，保留可以复核的结果。</p>'
            '<section class="panel section" data-run-onboarding aria-labelledby="first-use-title"><h2 id="first-use-title">从数据到第一份研究报告</h2>'
            '<p>先接入自己的数据并检查，再明确开始研究。也可展开演示数据，走完一遍离线流程。</p>'
            '<ol class="first-research-steps" data-first-research-steps><li data-guide-step="data">01 准备数据 · 待准备</li><li data-guide-step="check">02 核对输入 · 待检查</li><li data-guide-step="run">03 运行研究 · 待开始</li><li data-guide-step="report">04 阅读报告 · 待生成</li></ol>'
            '<p><a href="/settings/data-connections">配置数据接口与字段映射</a> → 显式获取快照 → 检查输入 → 提交研究。报告可保存为 HTML 离线阅读。</p>'
            '<div data-run-readiness role="status" aria-live="polite"><p data-run-data-state>正在读取数据准备状态…</p><p data-run-model-state>正在读取模型配置状态…</p></div></section>'
            '<p>服务器运行两观察期动量基线、含费用回测和同信号纸面风控。至少四条低频 OHLCV，每个 UTC 日期最多一条；历史信号所用数据必须在下一根观测执行前可用。周期按观测条数计算，未验证交易日历时不展示年化收益、波动率或夏普。可选择离线流程，或明确授权所选模型分析材料；模型观察不修改数值策略。</p>'
            f'<input data-run-csrf type="hidden" value="{self.csrf_token}">'
            '<p data-run-loading role="status">正在读取快照与任务记录…</p>'
            '<p class="error-state" data-run-error role="alert" hidden></p>'
            '<button class="button-secondary" type="button" data-run-refresh>重新读取记录</button>'
            '<div class="grid section"><section class="card"><h2 id="prepare-data" tabindex="-1">01 / 准备数据</h2>'
            '<p>保存连接不会获取数据。这里的操作会显式读取用户配置的 API，并冻结一份有指纹的 OHLCV 快照。</p>'
            '<form class="form-grid" data-snapshot-capture><label>数据连接<select name="connection_id" required><option value="">请选择已保存的连接</option></select></label>'
            '<label>标的（多个用英文逗号分隔）<input name="instrument" required maxlength="2579" value="AAA"></label><label>开始日期<input type="date" name="start" required></label><label>结束日期<input type="date" name="end" required></label><label>数据截止日<input type="date" name="as_of" required></label>'
            '<fieldset id="data-units" tabindex="-1"><legend>数据单位（用户声明）</legend><label>价格币种<select name="price_currency"><option value="UNKNOWN">尚未确认</option><option>CNY</option><option>USD</option><option>HKD</option><option>EUR</option><option>GBP</option><option>JPY</option></select></label><label>成交量单位<select name="volume_unit"><option value="UNKNOWN">尚未确认</option><option value="shares">股（已换算）</option><option value="units">份（已换算）</option></select></label><p class="field-help">价格与现金使用同一币种；手数或合约乘数需由数据端换算。此处记录声明，不转换数值。</p></fieldset>'
            '<details><summary>多资产证据声明（可选）</summary><label>资产类别 JSON<textarea name="asset_classes" maxlength="4096" placeholder="{&quot;AAA&quot;:&quot;EQUITY&quot;,&quot;ETF&quot;:&quot;ETF&quot;}"></textarea></label><p class="field-help">为返回的每个标的填写 EQUITY 或 ETF。声明与单位随快照冻结，用于研究对比中的多资产证据。</p></details>'
            '<button type="submit">获取并保存快照</button></form><p><a href="/settings/data-connections">配置自己的数据接口</a></p>'
            '<details><summary>使用明确标记的演示数据</summary><form class="form-grid" data-snapshot-sample><label>演示标的<input name="instrument" required maxlength="128" value="AAA"></label><button type="submit">生成 SAMPLE 快照</button></form></details></section>'
            '<section class="card"><h2>02 / 提交研究</h2><form class="form-grid" data-run-submit>'
            '<label>已保存的快照<select name="snapshot_ref" required><option value="">请选择数据快照</option></select></label>'
            '<p class="field-help" data-snapshot-description>尚未选择数据，不会自动使用演示数据。</p>'
            '<label>研究标的<input id="research-instrument" name="instrument" required maxlength="128" value="AAA"></label><label>研究截止日（UTC 当日结束）<input id="research-cutoff" type="date" name="as_of" required></label>'
            '<label>研究模板<select data-run-template><option value="">保留自己填写的问题</option><option value="baseline">基础复核：动量、费用与历史回撤</option><option value="risk">风险复核：成本、容量与损失情景</option><option value="materials">材料研究：基本面、新闻与情绪证据</option></select></label><p class="field-help">模板只填入研究问题，不改变基线算法。材料分析另需模型配置与明确授权。</p>'
            f'<label>研究问题<textarea id="research-question" name="question" required maxlength="2000" rows="3">{html.escape(question)}</textarea></label>{notice}'
            '<label>执行配置<select id="research-model" name="model_config_ref"><option value="offline">本地确定性流程 / 不调用模型</option></select></label>'
            '<p><a href="/settings/providers">配置用户自己的模型接口</a></p>'
            '<details><summary>补充基本面、新闻、情绪或学习材料</summary><p class="field-help">可选 JSON 数组，每条包含 role、instrument、summary、source_label、observed_at、available_at、authorized:true。时间需带时区；最多 16 条，每条摘要最多 2000 字。没有对应材料的角色会显示资料不足。</p><label>已授权材料摘要（JSON）<textarea id="research-materials" name="materials" rows="5" maxlength="40000" placeholder="[]"></textarea></label></details>'
            '<label class="model-consent"><input type="checkbox" name="allow_model_calls" value="true">我授权向所选模型发送研究问题、标的、最近最多 16 条行情及上述材料摘要，并承担调用费用。在线任务不自动重试。</label>'
            '<label>墙钟上限（秒，最多 300）<input id="research-budget" name="max_wall_seconds" type="number" min="5" max="300" value="120" required></label>'
            '<button type="submit">提交研究任务</button></form><button type="button" class="button-secondary" data-run-new>新建另一项研究</button></section></div>'
            '<section class="panel section" data-readiness-panel data-readiness-state="STALE" aria-labelledby="readiness-title"><div class="section-heading"><h2 id="readiness-title">提交前的数据就绪检查</h2><button type="button" class="button-secondary" data-readiness-check>检查当前输入</button></div><p data-readiness-summary tabindex="-1" role="status" aria-live="polite">选择快照后检查；提交时也会核对当前输入。</p><ul class="readiness-checks" data-readiness-results></ul><details data-readiness-passed-details hidden><summary data-readiness-passed-count>查看已核对项</summary><ul class="readiness-checks" data-readiness-passed></ul></details><details><summary>查看所选数据的完整指纹</summary><p class="snapshot-fingerprint" data-snapshot-reference>尚未选择快照</p></details></section>'
            '<section class="panel section" data-run-progress aria-live="polite" aria-atomic="false"><h2 data-run-progress-title tabindex="-1">03 / 运行与结果</h2>'
            '<p data-run-empty>还没有研究任务。先选择快照并提交研究。</p><p data-run-state></p><div data-run-explanation class="research-explanation"></div><p data-run-budget></p><p data-run-identity class="snapshot-fingerprint"></p><details><summary>技术阶段与状态码</summary><ol class="research-stage-list" data-run-stages></ol></details>'
            '<details><summary>技术指标、模型记录与完整限制</summary><p class="field-help" data-run-data-note></p><p data-run-model-summary></p><ul class="overview-list" data-run-call-records></ul><ul class="overview-list" data-run-model-roles></ul><div data-run-synthesis></div><dl data-run-metrics></dl></details><p data-run-failure></p><div class="action-row"><button type="button" data-run-cancel disabled>取消任务</button><button type="button" class="button-secondary" data-run-restore disabled>沿用基础输入</button><a data-run-report hidden>打开完整 HTML 报告</a><a data-run-detail hidden>查看研究档案</a></div><p data-run-restore-note role="status"></p></section>'
            '<section class="panel section"><h2>最近的后台任务</h2><p>刷新页面或重启服务后，可从这里继续查看；相同提交标识不会重复排队。</p><ul class="overview-list" data-run-history></ul><div class="action-row"><button type="button" class="button-secondary" data-run-history-prev disabled>上一页任务</button><span data-run-history-page role="status"></span><button type="button" class="button-secondary" data-run-history-next disabled>下一页任务</button></div><a href="/research/compare">比较已发布的研究</a></section>'
            '<section class="panel section" data-method-handoff><h2>方法交接与受控因子研究</h2><p>为当前任务按需加载方法并导出交接包。外部工具负责推理，回传只接受有绑定的 JSON；这里不会自动调用 Codex 或执行模型代码。</p>'
            '<p role="status" aria-live="polite" data-method-status>先选择一个已保存的研究任务。</p><button type="button" data-method-export disabled>导出因子研究交接包</button>'
            '<details><summary>查看交接包与方法指纹</summary><label>可复制给外部研究工具的 JSON<textarea data-method-package rows="8" readonly></textarea></label></details>'
            '<form data-method-result-form><label>外部结果 JSON<textarea name="response" rows="6" maxlength="131072" required></textarea></label><button type="submit" data-method-accept disabled>校验并保存回传</button></form>'
            '<details><summary>绑定可选研究字段附件</summary><p>使用已声明单位和可用时间的 ResearchFieldAttachment JSON，快照指纹必须与当前任务一致；不接供应商 SDK。</p><form data-method-fields-form><label>字段附件文件<input type="file" name="attachment" accept="application/json,.json" required></label><button type="submit" data-method-fields-save disabled>明确保存字段附件</button></form></details>'
            '<form data-method-study-form><label>已验证并保存的提案<select name="artifact_ref" required><option value="">尚无回传提案</option></select></label><button type="submit" data-method-study-create disabled>固定章程并执行受控验证</button></form><a data-method-study-link hidden>进入此因子实验</a></section>'
            '<noscript><p>任务提交和跟踪需要 JavaScript。已发布的 HTML 报告可离线阅读。</p></noscript></section>'
        )

    def _data_connection_from_payload(self, values: Mapping[str, Any]) -> tuple[DataConnectionConfig, str | None]:
        auth_mode = str(values.get("auth_mode", "no_auth"))
        credential_ref = values.get("credential_ref")
        api_key = values.get("api_key")
        secret_value: str | None = None
        if isinstance(api_key, str) and not api_key:
            api_key = None
        if api_key is not None:
            if not isinstance(api_key, str) or not api_key:
                raise ValueError("api_key must be a non-empty value")
            if auth_mode == "no_auth":
                raise ValueError("no_auth connections cannot accept an api_key")
            credential_ref = new_local_credential_ref(str(values.get("connection_id", "connection")))
            secret_value = api_key
        elif credential_ref is not None:
            if not isinstance(credential_ref, Mapping):
                raise ValueError("credential_ref must be a data credential reference")
            credential_ref = DataSourceCredentialRef(
                env_var=credential_ref.get("env_var"),
                keychain_label=credential_ref.get("keychain_label"),
            )
        field_mapping = values.get("field_mapping", {})
        if isinstance(field_mapping, str):
            try:
                field_mapping = json.loads(field_mapping)
            except json.JSONDecodeError as exc:
                raise ValueError("field_mapping must be valid JSON") from exc
        if not isinstance(field_mapping, Mapping):
            raise TypeError("field_mapping must be an object")
        if bool(values.get("allow_local", False)):
            raise ValueError("local data bridge requires an independently configured target")
        config = DataConnectionConfig(
            connection_id=str(values.get("connection_id", "")),
            display_name=str(values.get("display_name", "")),
            base_url=str(values.get("base_url", "")),
            credential_ref=credential_ref,
            auth_mode=auth_mode,
            field_mapping={str(key): str(value) for key, value in field_mapping.items()},
            records_path=(str(values["records_path"]) if values.get("records_path") is not None else None),
            auth_header=(str(values["auth_header"]) if values.get("auth_header") is not None else None),
            source_declaration=str(values.get("source_declaration", "User-declared data source; Finathink has not independently verified it.")),
        )
        return config, secret_value

    def _data_connections_page(self) -> str:
        rows = self._data_connections.list()
        cards = "".join(
            f'<article class="card"><h2>{html.escape(str(item["display_name"]))}</h2><p><code>{html.escape(str(item["connection_id"]))}</code> · {html.escape(str(item["auth_mode"]))}</p><p class="meta">凭证：{"已配置" if item["credential_configured"] else "未配置"} · 配置保存不代表连接已验证，地址不在此视图中展示。</p></article>'
            for item in rows
        ) or '<div class="empty-state"><p>尚无数据接口。填写下方配置，保存后返回研究任务页显式获取快照；也可先使用演示数据完成第一份报告。</p></div>'
        body = (
            '<h1>数据接口</h1>'
            '<p class="lede">接入你选择并有权使用的数据 API。Finathink 保存连接配置和本地凭证引用，读取数据由你明确发起。</p>'
            '<section class="card"><form class="form-grid" method="post" action="/api/data/connections">'
            f'<input type="hidden" name="_csrf" value="{html.escape(self.csrf_token)}">'
            '<label>连接标识（英文、数字或连字符）<input name="connection_id" required maxlength="64" placeholder="my-prices"></label>'
            '<label>显示名称<input name="display_name" required maxlength="160" placeholder="我的日频行情"></label>'
            '<label>API 地址（HTTPS）<input name="base_url" type="url" required></label>'
            '<label>认证方式<select name="auth_mode"><option value="no_auth">无需认证</option><option value="bearer">Bearer 凭证</option><option value="api_key_header">API Key 请求头</option></select></label>'
            '<label>API Key（保存后不回显）<input name="api_key" type="password" autocomplete="new-password"></label>'
            '<label>API Key 请求头名称（请求头认证时填写）<input name="auth_header" value="X-API-Key" maxlength="64"></label>'
            '<label>记录所在路径<input name="records_path" placeholder="data" maxlength="128"></label>'
            '<label>字段映射（左侧为平台字段，右侧改为你的 API 字段）<textarea name="field_mapping" rows="7">{"instrument":"ticker","timestamp":"time","open":"open","high":"high","low":"low","close":"price","volume":"volume","available_at":"available_at"}</textarea></label>'
            '<button type="submit">保存数据接口</button></form><p class="field-help">保存不会下载数据。日频研究需要标的、观测时间、开高低收、成交量与数据可用时间；日期建议使用带时区的时间戳。映射不补造缺失的数据。</p><p><a href="/research/jobs">返回研究任务，获取数据快照</a></p></section>'
            f'<section class="section"><h2>已保存的接口</h2><div class="grid">{cards}</div></section>'
        )
        return self.render_shell("/settings/data-connections", "数据接口", body, inspector=self._inspector("数据边界", {"模式": "用户自己的 API", "凭证": "仅保存引用", "读取": "用户明确发起"}, status="USER-OWNED"))

    def _interface_quality_route(self, method, clean, body):
        interface = '/api/diagnostics/interfaces'
        quality = '/api/research/quality'
        if not (clean == interface or clean.startswith((interface+'/', quality+'/'))):
            return None
        local, probe = interface+'/local', interface+'/probe'
        cases, jobs, compare = quality+'/cases', quality+'/jobs', quality+'/compare'
        allowed = {'GET', 'POST'} if clean == jobs else {'POST'} if clean in {local, probe, compare} else {'GET'}
        if method not in allowed:
            return 405, 'application/json', {'error': '此入口不支持该请求方式，请使用页面中的对应操作。'}
        if method == 'POST':
            guard = self._data_write_guard(body)
            if guard:
                return guard
            values = {key: value for key, value in body.items() if key != '_csrf'}
        else:
            values = {}
        try:
            if clean == interface:
                return 200, 'application/json', {'schema_version': 'interface-diagnostic-list.v1', 'records': self.interface_diagnostics.store.list()}
            if clean == local:
                if values:
                    raise ValueError('本地兼容检查不接受接口地址或额外参数。')
                return 201, 'application/json', self.interface_diagnostics.run_local()
            if clean == probe:
                return 201, 'application/json', self.interface_diagnostics.probe(values)
            if clean.startswith(interface+'/'):
                return 200, 'application/json', self.interface_diagnostics.store.get(clean.removeprefix(interface+'/'))
            if clean == cases:
                from .research.quality_cases import quality_cases

                return 200, 'application/json', {'schema_version': 'research-quality-cases.v1', 'cases': quality_cases()}
            if clean == jobs:
                return (201, 'application/json', self.quality_evaluation.submit(values)) if method == 'POST' else (200, 'application/json', {'schema_version': 'research-quality-list.v1', 'jobs': self.quality_evaluation.list()})
            if clean == compare:
                if set(values) != {'job_ids'}:
                    raise ValueError('请选择2至4个已保存的质量任务。')
                return 200, 'application/json', self.quality_evaluation.compare(values['job_ids'])
            if clean.startswith(jobs+'/'):
                return 200, 'application/json', self.quality_evaluation.evaluate(clean.removeprefix(jobs+'/'))
            return 404, 'application/json', {'error': '未找到此诊断入口。'}
        except KeyError:
            return 404, 'application/json', {'error': '未找到当前空间中的记录，请重新读取本地历史。'}
        except (ValueError, TypeError):
            return 422, 'application/json', {'error': '输入或记录校验未通过，请核对配置、授权、固定任务与资源上限。原记录已保留。'}
        except (OSError, sqlite3.Error):
            return 503, 'application/json', {'error': '本地记录暂时无法读取，请检查磁盘空间与应用状态后重新读取。'}

    def route(self, method: str, path: str, *, query: Mapping[str, list[str]] | None = None, body: Any = None) -> tuple[int, str, Any]:
        """Return ``(status, content_type, payload)`` without requiring a socket."""

        parsed = urlsplit(path)
        query = parse_qs(parsed.query) if query is None else query
        clean = parsed.path.rstrip("/") or "/"
        from .research.knowledge_workspace import route as knowledge_route

        knowledge_response = knowledge_route(self, method, clean, query)
        if knowledge_response is not None:
            return knowledge_response
        from .research.learning_maintenance_routes import route as learning_maintenance_route
        from .research.reproduction_routes import route as reproduction_route

        response = learning_maintenance_route(self, method, clean, query, body)
        if response is not None:
            return response

        response = reproduction_route(self, method, clean, query, body)
        if response is not None:
            return response
        diagnostic_response = self._interface_quality_route(method, clean, body)
        if diagnostic_response is not None:
            return diagnostic_response
        if clean == "/research/jobs" and method == "GET":
            return 200, "text/html; charset=utf-8", self.render_page(clean, query=query)
        asset_response = self._asset_workspace_route(method, clean, query)
        if asset_response is not None:
            return asset_response
        factor_response = self._factor_detail_route(method, clean, body, query)
        if factor_response is not None:
            return factor_response
        job_response = self._research_jobs_route(method, clean, body, query)
        if job_response is not None:
            return job_response
        if clean == "/api/research/runs":
            if method != "GET":
                return 405, "application/json", {"error": "research history is read-only"}
            try:
                pagination = {
                    "limit": int(query.get("limit", ["50"])[0]), "offset": int(query.get("offset", ["0"])[0]),
                    "state": query.get("state", [None])[0], "sort_by": query.get("sort_by", ["created_at"])[0],
                    "order": query.get("order", ["desc"])[0],
                }
                return 200, "application/json", self.research_comparison(**pagination)
            except (ValueError, TypeError):
                return 422, "application/json", {"error": "research history filter or pagination is invalid"}
        if clean == "/api/research/providers/config":
            if method == "GET":
                return 200, "application/json", {"providers": list(self._provider_configs.list()), "paper_only": True, "provider_called": False, "outbound_enabled": not self.config.offline}
            if method != "POST":
                return 405, "application/json", {"error": "provider configuration supports GET and POST"}
            denied = self._data_write_guard(body)
            if denied is not None:
                return denied
            from finahinking.research.provider_config import ProviderConfig

            try:
                values = {key: value for key, value in body.items() if key != "_csrf"}
                if "credential_env" in values:
                    values["credential_ref"] = {"env_var": values.pop("credential_env")}
                if isinstance(values.get("role_models"), str):
                    values["role_models"] = json.loads(values["role_models"] or "{}")
                config = ProviderConfig.from_mapping(values)
                self._provider_configs.save(config)
            except (TypeError, ValueError) as exc:
                status = 409 if str(exc) == "provider already exists" else 400
                return status, "application/json", {"error": str(exc)}
            return 201, "application/json", config.redacted()
        if clean == "/settings/data-connections" and method == "GET":
            return 200, "text/html; charset=utf-8", self._data_connections_page()
        if clean == "/api/data/connections" and method == "GET":
            return 200, "application/json", {"connections": list(self._data_connections.list())}
        if clean == "/api/data/connections" and method == "POST":
            denied = self._data_write_guard(body)
            if denied is not None:
                return denied
            try:
                config, secret_value = self._data_connection_from_payload(body)
                self._data_connections.save(config, credential_value=secret_value)
            except (TypeError, ValueError) as exc:
                return 400, "application/json", {"error": str(exc)}
            return 201, "application/json", {**config.redacted(), "credential_configured": bool(config.credential_ref and self._data_connections.credentials.has(config.credential_ref))}
        if clean.startswith("/api/data/connections/") and clean.endswith("/test") and method == "POST":
            denied = self._data_write_guard(body)
            if denied is not None:
                return denied
            connection_id = clean.removeprefix("/api/data/connections/").removesuffix("/test").strip("/")
            try:
                config = self._data_connections.get(connection_id)
            except KeyError:
                return 404, "application/json", {"error": "data connection not found"}
            if self._data_transport is None:
                return 200, "application/json", {"status": "NOT_RUN", "connection_id": connection_id, "reason": "an explicit local test transport is required"}
            values = body if isinstance(body, Mapping) else {}
            request = DataRequest(dataset_kind=str(values.get("dataset_kind", "prices")), instruments=tuple(values.get("instruments", ()) or ()), start=values.get("start"), end=values.get("end"), as_of=values.get("as_of"))
            try:
                batch = JsonApiConnector(config, self._data_connections.credentials, self._data_transport).fetch(request)
            except (DataConnectorError, TypeError, ValueError) as exc:
                return 502, "application/json", {"status": "FAILED", "connection_id": connection_id, "reason": getattr(exc, "code", "connection test failed")}
            return 200, "application/json", {"status": "READY", "connection_id": connection_id, "record_count": len(batch.records), "quality_issue_count": len(batch.quality_issues), "pit_available": batch.pit_available}
        if clean.startswith("/api/research/runs/") and clean.endswith("/stream"):
            if method != "GET":
                return 405, "application/json", {"error": "research runtime stream is read-only"}
            run_id = clean.removeprefix("/api/research/runs/").removesuffix("/stream").strip("/")
            if not run_id or "/" in run_id or "\\" in run_id:
                return 400, "application/json", {"error": "research run id is invalid"}
            try:
                entry = self._research_entry(run_id)
            except ValueError:
                return 422, "application/json", {"error": "research index record is invalid"}
            if entry is None:
                return 404, "application/json", {"error": "research run not found"}
            from finahinking.research.ui import research_runtime_view_model

            try:
                _, manifest = self._research_model(entry)
                payload = research_runtime_view_model(run_id, state=entry["state"], manifest=manifest, events=entry.get("events", ()))
                payload["report_status"] = entry["report_status"]
            except (TypeError, ValueError) as exc:
                return 422, "application/json", {"error": str(exc)}
            return 200, "application/json", payload
        if clean == "/api/research/compare":
            if method != "GET":
                return 405, "application/json", {"error": "research comparison is read-only"}
            return 200, "application/json", self.research_comparison()
        if clean == "/assets/finathink-splash-map.jpg" and method == "GET":
            return 200, "image/jpeg", self.splash_asset()
        if clean == "/assets/finathink-research-splash.jpg" and method == "GET":
            return 200, "image/jpeg", self.research_splash_asset()
        if clean == "/assets/finathink-research.js" and method == "GET":
            return 200, "application/javascript; charset=utf-8", self.research_script_asset()
        if clean == "/research/compare":
            if method != "GET":
                return 405, "application/json", {"error": "research comparison is read-only"}
            return 200, "text/html; charset=utf-8", self.render_page(clean, query=query)
        if clean in {"/research/experiments", "/research/learning", "/research/maintenance"} and method == "GET":
            return 200, "text/html; charset=utf-8", self.render_page(clean, query=query)
        if clean.startswith("/research/"):
            if method != "GET":
                return 405, "application/json", {"error": "research routes are read-only"}
            return self._research_run_route(clean)
        if clean == "/assets/katex/katex.min.css" and method == "GET":
            return 200, "text/css; charset=utf-8", self.katex_asset("katex.min.css")
        if clean.startswith("/assets/katex/fonts/") and method == "GET":
            font_name = clean.removeprefix("/assets/katex/fonts/")
            try:
                return 200, "font/woff2" if font_name.endswith(".woff2") else "font/woff" if font_name.endswith(".woff") else "font/ttf", self.katex_asset(font_name, font=True)
            except FileNotFoundError:
                return 404, "application/json", {"error": "asset not found"}
        if clean in {"/health", "/api/health"}:
            return 200, "application/json", {"status": "ok", "service": "finahinking-local", "version": "0.1.0"}
        if clean == "/api/diagnostics":
            return 200, "application/json", self.diagnostics()
        if clean == "/api/overview" and method == "GET":
            return 200, "application/json", self.overview()
        if clean == "/api/diagnostics/bundle":
            return 200, "application/json", self.diagnostics_bundle()
        if clean == "/api/p8_2b/knowledge" and method == "GET":
            query_text = (query.get("q") or [""])[0]
            return 200, "application/json", {
                "schema_version": 1,
                "units": [item.to_dict() for item in search_catalog(query_text)],
                "catalog_fingerprint": DEFAULT_KNOWLEDGE_CATALOG.fingerprint,
                "provenance": "CURATED_OFFLINE",
            }
        if clean.startswith("/api/p8_2b/knowledge/") and method == "GET":
            parts = [part for part in clean.removeprefix("/api/p8_2b/knowledge/").split("/") if part]
            if not parts:
                return 404, "application/json", {"error": "knowledge unit not found"}
            try:
                unit = get_knowledge_unit(parts[0])
            except KeyError:
                return 404, "application/json", {"error": "knowledge unit not found"}
            if len(parts) == 1:
                return 200, "application/json", self._p8_2b_unit_payload(unit, context=self._p8_2b_context(unit, query))
            if parts[1] == "context":
                resolution = self._p8_2b_context(unit, query)
                return 200, "application/json", {"status": resolution.status, "binding": resolution.binding.to_dict() if resolution.binding else None}
            if parts[1] == "export":
                fmt = (query.get("format") or ["markdown"])[0].casefold()
                exporters = {"markdown": ("text/markdown; charset=utf-8", export_markdown), "latex": ("application/x-latex; charset=utf-8", export_latex), "bibtex": ("application/x-bibtex; charset=utf-8", export_bibtex), "csl": ("application/json", export_csl_json)}
                if fmt not in exporters:
                    return 400, "application/json", {"error": "format is invalid", "allowed": sorted(exporters)}
                content_type, exporter = exporters[fmt]
                return 200, content_type, exporter(unit, DEFAULT_KNOWLEDGE_CATALOG)
            return 404, "application/json", {"error": "knowledge route not found"}
        if clean == "/api/p8_2b/widgets" and method == "POST":
            if not isinstance(body, Mapping):
                return 400, "application/json", {"error": "widget payload must be an object"}
            try:
                spec = WidgetSpec(str(body.get("widget_id", "")), str(body.get("kind", "")), dict(body.get("parameters", {})))
                return 200, "application/json", run_widget(spec).to_dict()
            except (TypeError, ValueError, KeyError) as exc:
                return 400, "application/json", {"error": str(exc), "action": "use a typed allow-listed widget and bounded parameters"}
        if clean in {"/api/knowledge", "/knowledge.json"}:
            return 200, "application/json", self.knowledge((query.get("q") or [""])[0])
        if clean.startswith("/api/concepts/"):
            if clean.endswith("/prerequisites"):
                concept_id = clean.removeprefix("/api/concepts/").removesuffix("/prerequisites").strip("/")
                try:
                    closure = DEFAULT_CATALOG.prerequisite_closure(concept_id)
                except KeyError:
                    return 404, "application/json", {"error": "concept not found"}
                return 200, "application/json", {"concept_id": concept_id, "concepts": [item.to_dict() for item in closure]}
            concept = self.concept(clean.removeprefix("/api/concepts/"))
            return (200, "application/json", concept) if concept else (404, "application/json", {"error": "concept not found"})
        if clean.startswith("/api/knowledge/path/"):
            path_id = clean.removeprefix("/api/knowledge/path/")
            try:
                return 200, "application/json", DEFAULT_CATALOG.path(path_id).to_dict()
            except KeyError:
                return 404, "application/json", {"error": "learning path not found"}
        if clean == "/api/events":
            return 200, "application/json", {"events": [self.event()]}
        if clean in {"/api/events/save", "/api/events/learn"} and method == "POST":
            journey = self._event_journey
            if journey is None:
                self.event()
                journey = self._event_journey
            assert journey is not None
            event_id = journey.event.event_id
            with self._lock:
                try:
                    existing = self.repository.get_node(self.config.session_id, f"event:{event_id}")
                    return 201, "application/json", {"saved": True, "event_id": event_id, "node_id": existing.node_id, "node_type": existing.node_type, "title": existing.title}
                except KeyError:
                    result = EventLearningAdapter(self.repository).ingest_journey(self.config.session_id, journey)
            return 201, "application/json", {"saved": True, "event_id": result.event_id, "node_id": result.event_node_id, "node_type": "event", "history_id": result.history_id}
        if clean == "/api/quant":
            if method != "POST":
                return 200, "application/json", {"stage": "experiment", "question": "How does the market relate to asset returns?", "hypothesis": "Market returns have a measurable historical beta to asset returns.", "execution": "POST required to create a private ResearchRun", "real_money": False}
            from finahinking.p7_5.research import LocalResearchService
            try:
                result = LocalResearchService(self.repository, self.config.session_id, self.artifact_root).run_quant(dict(body or {}) if method == "POST" else {})
            except (TypeError, ValueError, PermissionError) as exc:
                return 400, "application/json", {"error": str(exc), "action": "revise the bounded research question/options"}
            numeric = dict(result.get("numeric_results", {}))
            numeric.setdefault("observations", numeric.get("sample_count"))
            numeric.setdefault("slope", numeric.get("beta"))
            return 200, "application/json", {**result, "stage": "result" if method == "POST" else "experiment", "question": (body or {}).get("question", "How does the market relate to asset returns?") if isinstance(body, Mapping) else "How does the market relate to asset returns?", "hypothesis": (body or {}).get("hypothesis", "Market returns have a measurable historical beta to asset returns.") if isinstance(body, Mapping) else "Market returns have a measurable historical beta to asset returns.", "result": numeric}
        if clean == "/api/strategy":
            if method != "POST":
                return 200, "application/json", {"stages": ["idea", "spec", "features", "code", "backtest", "oos", "paper", "compare", "learn"], "execution": "paper-only", "real_money": False}
            from finahinking.p7_5.research import LocalResearchService
            try:
                result = LocalResearchService(self.repository, self.config.session_id, self.artifact_root).run_strategy(dict(body or {}))
            except (TypeError, ValueError, PermissionError) as exc:
                return 400, "application/json", {"error": str(exc), "action": "choose a supported reviewed strategy template"}
            return 200, "application/json", {**result, "strategy_spec": result.get("strategy"), "real_money": False, "execution": "paper-only", "backtest": {**dict(result.get("backtest") or {}), "metrics": result.get("numeric_results", {})}, "oos": {**dict(result.get("oos") or {}), "metrics": result.get("numeric_results", {})}, "paper": {**dict(result.get("paper") or {}), "status": "PAPER_ONLY"}}
        if clean == "/api/research/providers":
            if method != "GET":
                return 405, "application/json", {"error": "provider status is read-only"}
            from finahinking.research.provider_status import provider_status_payload

            return 200, "application/json", provider_status_payload(self._provider_config(), os.environ)
        if clean == "/api/research/series" and method == "GET":
            from finahinking.p8_2.research_view import build_research_payload

            return 200, "application/json", build_research_payload()
        if clean == "/api/research/workbench":
            if method != "GET":
                return 405, "application/json", {"error": "research workbench is read-only"}
            from finahinking.p8_2.research_view import build_research_payload

            return 200, "application/json", build_research_payload()["workbench"]
        if clean == "/api/research/capabilities" and method == "GET":
            from finahinking.p8_2.research_view import capability_payload

            return 200, "application/json", capability_payload()
        if clean == "/api/research/qmt" and method == "GET":
            from finahinking.p8_2.research_view import qmt_payload

            return 200, "application/json", qmt_payload()
        if clean == "/api/research/ml" and method == "GET":
            from finahinking.p8_2.research_view import ml_payload

            return 200, "application/json", ml_payload()
        if clean == "/api/research/parameters" and method == "GET":
            from finahinking.p8_2.research_view import parameter_payload

            return 200, "application/json", parameter_payload()
        if clean.startswith("/api/research/artifacts/"):
            node_id = clean.removeprefix("/api/research/artifacts/").strip("/")
            if not node_id or any(part in node_id for part in ("/", "\\", "..")):
                return 400, "application/json", {"error": "artifact id is invalid"}
            from finahinking.p7_5.research import LocalResearchService
            try:
                artifact = LocalResearchService(self.repository, self.config.session_id, self.artifact_root).get_artifact(node_id)
            except (KeyError, ValueError, PermissionError) as exc:
                return 404, "application/json", {"error": str(exc), "action": "run or save the research artifact first"}
            return 200, "application/json", artifact
        if clean == "/api/personal":
            return 200, "application/json", self.personal()
        if clean in {"/api/personal/backup", "/api/personal/export"} and method in {"GET", "POST"}:
            return 200, "application/json", self.backup_personal()
        if clean == "/api/community":
            if method == "POST":
                values = dict(body or {}) if isinstance(body, Mapping) else {}
                text = str(values.get("claim", "")).strip()
                if not text:
                    return 400, "application/json", {"error": "claim is required"}
                post_id = f"post:{uuid.uuid4().hex}"
                post = CommunityPost(post_id, "local-room", self.config.principal_id, "HYPOTHESIS", "Private research question", text)
                claim = CommunityClaim(f"claim:{post_id}", post_id, "HYPOTHESIS", text)
                CommunityIntegrityService(self.repository).create_post(self.config.session_id, post, claim, available_evidence=())
                return 201, "application/json", {"saved": True, "post_id": post_id, "visibility": "PRIVATE", "projection_required": True}
            rows = self.connection.execute("SELECT post_id,room_id,author_id,claim_type,title,body,status,created_at FROM p7_posts WHERE room_id = ? ORDER BY created_at DESC", ("local-room",)).fetchall()
            posts = [{**dict(row), "evidence_ids": []} for row in rows]
            return 200, "application/json", {"posts": posts, "model": "calm evidence discussion", "leaderboards": False, "private_by_default": True, "projection_required": True}
        if clean == "/api/community/project" and method == "POST":
            values = dict(body or {}) if isinstance(body, Mapping) else {}
            if str(values.get("consent", "")).lower() not in {"1", "true", "yes", "on"}:
                return 403, "application/json", {"error": "explicit projection consent is required", "action": "review the allow-listed fields and consent"}
            if self._event_journey is None:
                self.event()
            event_id = str(values.get("source_id", self._event_journey.event.event_id))
            if not event_id:
                return 400, "application/json", {"error": "source_id is required"}
            try:
                event_payload = self.event()
                source_fingerprint = self._event_journey.event.fingerprint
                self.repository.get_node(self.config.session_id, f"event:{event_id}")
            except (KeyError, AttributeError):
                return 404, "application/json", {"error": "save the research source privately before projecting it"}
            fields = tuple(item.strip() for item in str(values.get("fields", "event_type,reference_period,published_at,revision_status,limitations")).split(",") if item.strip())
            allowed = {"event_type", "reference_period", "published_at", "revision_status", "limitations"}
            if not fields or any(item not in allowed for item in fields):
                return 400, "application/json", {"error": "fields must be an explicit allow-list", "allowed_fields": sorted(allowed)}
            spec = ProjectionSpec(f"projection:{uuid.uuid4().hex}", "p6_5_event", event_id, source_fingerprint, fields, str(values.get("visibility", "PUBLIC")).upper(), 1, ("Descriptive projection only; source evidence and limitations remain attached.",), None)
            try:
                projection_payload = {**dict(event_payload.get("event", {})), "limitations": event_payload.get("limitations", [])}
                projection = self.repository.publish_projection(self.config.session_id, spec, projection_payload, consent=True)
            except (PermissionError, ValueError) as exc:
                return 400, "application/json", {"error": str(exc), "action": "check saved source, visibility, and allow-listed fields"}
            return 201, "application/json", {"projection_id": projection.projection_id, "visibility": projection.visibility, "fields": list(fields), "consented": True, "limitations": list(projection.limitations)}
        if clean == "/api/personal/save" and method == "POST":
            try:
                return 201, "application/json", self.save_personal(body if isinstance(body, dict) else {})
            except (TypeError, ValueError) as exc:
                return 400, "application/json", {"error": str(exc)}
        if clean == "/api/reopen" and method == "POST":
            return 200, "application/json", {"reopened": True, "personal": self.personal()}
        if clean.startswith("/knowledge/"):
            unit_id = clean.removeprefix("/knowledge/").strip("/")
            try:
                unit = get_knowledge_unit(unit_id)
            except KeyError:
                unit = None
            if unit is not None:
                return 200, "text/html; charset=utf-8", self.render_p8_2b_concept_page(unit, context=self._p8_2b_context(unit, query), query=query)
            concept = self.concept(unit_id)
            if concept is None:
                return 404, "text/html; charset=utf-8", self.render_error_page(
                    "Concept not found",
                    "That concept is not in the local curated catalog.",
                    "Search the knowledge index or return to Explore to choose a supported path.",
                    links=(("/knowledge", "Browse Knowledge"), ("/explore", "Open Explore")),
                )
            return 200, "text/html; charset=utf-8", self.render_concept_page(concept)
        if clean in {"/", "/events", "/explore", "/knowledge", "/quant", "/research", "/research/compare", "/workbench", "/ml", "/parameter", "/strategy", "/personal", "/workspace", "/community", "/settings/engines", "/settings/providers", "/settings/data-sources", "/diagnostics"}:
            return 200, "text/html; charset=utf-8", self.render_page(clean, query=query)
        if not clean.startswith("/api/"):
            return 404, "text/html; charset=utf-8", self.render_error_page(
                "Page not found",
                "Finathink could not find that workspace route.",
                "Use the product navigation to continue with a real research path.",
                links=(("/", "Go to Home"), ("/diagnostics", "View Diagnostics")),
            )
        return 404, "application/json", {"error": "route not found"}

    def render_page(self, page: str, *, query: Mapping[str, list[str]] | None = None) -> str:
        names = self._page_names()
        active_page = "/workspace" if page == "/personal" else page
        title = names.get(page, "Finathink")
        body = self._page_body(page, query=query)
        inspector = self._page_inspector(page)
        scripts = ("/assets/finathink-research.js",) if page in {"/", "/research", "/research/jobs", "/research/experiments", "/research/learning", "/research/maintenance", "/research/compare", "/workbench", "/settings/providers", "/diagnostics"} else ()
        return self.render_shell(active_page, title, body, inspector=inspector, scripts=scripts)

    def render_result_page(self, title: str, payload: Any) -> str:
        """Render form outcomes in the product shell, including actionable errors."""

        is_error = isinstance(payload, Mapping) and "error" in payload
        escaped = html.escape(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
        heading = "请求需要处理" if is_error else "本地结果已保存"
        message = (
            "请求尚未被接受。请核对字段、认证方式和本地应用状态，再重新操作。"
            if is_error
            else "配置或研究结果已保存在本地，可返回对应工作区继续。"
        )
        action = "请检查输入后重试；保存配置不会自动获取数据或调用模型。" if is_error else "从已保存的记录继续。"
        if is_error or not isinstance(payload, Mapping):
            body = (
                f'<h1>{html.escape(heading)}</h1><p class="lede">{message}</p>'
                f'<section class="card {"error-state" if is_error else "result"}" role="{"alert" if is_error else "status"}" aria-live="polite">'
                f'<h2>{html.escape(title)}</h2><p>{action}</p><details><summary>Inspect structured payload</summary><pre>{escaped}</pre></details>'
                '</section><div class="action-row"><a class="button-secondary" href="/workspace">Open Workspace</a><a class="button-secondary" href="/diagnostics">View Diagnostics</a></div>'
            )
        elif payload.get("connection_id"):
            body = (
                '<h1>数据接口已保存</h1><p class="lede">本次只保存配置，尚未读取数据。</p>'
                f'<p>接口：{html.escape(str(payload.get("display_name", payload["connection_id"])))}</p>'
                '<p>接下来返回研究任务页，选择这个接口、标的与日期，点击“获取并保存快照”。凭证值不会在页面中回显。</p>'
                '<div class="action-row"><a class="button" href="/research/jobs">获取数据并开始研究</a><a href="/settings/data-connections">查看已保存的接口</a></div>'
            )
        elif payload.get("strategy_spec"):
            spec = payload.get("strategy_spec") or {}
            backtest = payload.get("backtest") or {}
            oos = payload.get("oos") or {}
            paper = payload.get("paper") or {}
            metrics = backtest.get("metrics") or payload.get("numeric_results") or {}
            metric_cards = "".join(
                f'<div class="metric"><span class="metric-value">{html.escape(str(value))}</span><span class="metric-label">{html.escape(str(label))}</span></div>'
                for label, value in list(metrics.items())[:6]
            ) or '<div class="empty-state">No numeric metric was returned; inspect the artifact payload and limitations.</div>'
            body = (
                '<h1>Strategy research result</h1><p class="lede">One StrategySpec carried through feature lineage, backtest, OOS, paper, comparison, and learning. This is research output, not an execution instruction.</p>'
                f'<div class="status-row">{self._status("COMPLETE", "complete")}{self._status("OOS", "quant")}{self._status("PAPER SIMULATION", "limitation")}</div>'
                '<nav class="stepper" aria-label="Completed strategy workflow">'
                + "".join(f'<div class="step step--active"><strong>{index + 1}</strong><br>{label}</div>' for index, label in enumerate(("Idea", "StrategySpec", "Features", "Backtest", "OOS", "Paper", "Compare", "Learn")))
                + '</nav>'
                f'<section class="card"><h2>StrategySpec</h2><p><strong>Idea:</strong> {html.escape(str(spec.get("idea", spec.get("title", "Reviewed strategy"))))}</p><p class="meta">Universe: {html.escape(str(spec.get("universe", "sample universe")))} · Rebalance: {html.escape(str(spec.get("rebalance", "declared in spec")))} · Execution: paper-only</p></section>'
                f'<section class="section card result"><h2>Backtest overview</h2><div class="metric-row">{metric_cards}</div><p class="meta">Performance is only one view; inspect risk, behavior, robustness, and validity before learning from it.</p></section>'
                f'<section class="section grid"><article class="card"><h2>Feature Graph</h2><p><code>Price → Return → Momentum → Rank → Signal → Portfolio</code></p><p><code>Returns → Volatility → Filter</code></p></article><article class="card"><h2>OOS validity</h2><p>{html.escape(str(oos.get("status", oos.get("classification", "OOS metadata retained"))))}</p><p class="meta">Train/test boundaries and limitations remain attached.</p></article><article class="card"><h2>Paper state</h2><p>{html.escape(str(paper.get("status", "PAPER_ONLY")))}</p><p class="meta">Frozen strategy · virtual clock · virtual cash · virtual fills.</p></article><article class="card"><h2>Validity panel</h2><ul><li>Look-ahead: PASS</li><li>Transaction costs: MODELED</li><li>Slippage: MODELED</li><li>Liquidity / capacity: UNKNOWN</li></ul></article></section>'
                f'<section class="card card--quiet"><h2>Inspect the full artifact</h2><details><summary>Show structured provenance payload</summary><pre>{escaped}</pre></details></section><div class="action-row"><a class="button-secondary" href="/strategy">Back to Strategy Lab</a><a class="button-secondary" href="/workspace">Open Workspace</a></div>'
            )
        else:
            result = payload.get("result") or payload.get("numeric_results") or {}
            result = result if isinstance(result, Mapping) else {}
            metric_names = ("beta", "standard_error", "confidence_interval", "r_squared", "observations", "slope")
            metric_cards = "".join(
                f'<div class="metric"><span class="metric-value">{html.escape(str(result.get(name, "—")))}</span><span class="metric-label">{html.escape(name.replace("_", " "))}</span></div>'
                for name in metric_names
            )
            body = (
                '<h1>Quant research result</h1><p class="lede">The estimate is shown with context, uncertainty, sample, method, provenance, and limitations. It does not prove causality or predict a trade.</p>'
                f'<div class="status-row">{self._status("COMPLETE", "complete")}{self._status("SAMPLE", "sample")}{self._status("QUANT FINDING", "quant")}</div>'
                f'<section class="card result"><h2>Result</h2><div class="metric-row">{metric_cards}</div><p><strong>Question:</strong> {html.escape(str(payload.get("question", "Bounded research question")))}</p><p><strong>Hypothesis:</strong> {html.escape(str(payload.get("hypothesis", "Declared hypothesis")))}</p></section>'
                '<section class="section grid"><article class="card"><h2>What does this mean?</h2><p>The coefficient summarizes the relationship in this deterministic sample and period under the declared method.</p></article><article class="card"><h2>What does this not mean?</h2><p>It is not a causal claim, forecast, investment advice, or live execution signal.</p></article><article class="card"><h2>Learn the math</h2><p><a href="/knowledge/beta">Open beta and uncertainty</a> to inspect the equation and assumptions.</p></article><article class="card"><h2>View code</h2><p>Open the structured artifact payload to inspect the reviewed computation and fixture fingerprint.</p></article></section>'
                f'<section class="card card--quiet"><h2>Provenance and limitations</h2><p>Sample, period, method, fixture fingerprint, ResearchRun, and limitations stay attached.</p><details><summary>Inspect structured payload</summary><pre>{escaped}</pre></details></section><div class="action-row"><a class="button-secondary" href="/quant">Back to Quant Lab</a><a class="button-secondary" href="/workspace">Open Workspace</a></div>'
            )
        return self.render_shell("/workspace", heading, body, eyebrow="Saved research")

    def render_error_page(
        self,
        title: str,
        summary: str,
        action: str,
        *,
        links: tuple[tuple[str, str], ...] = (),
    ) -> str:
        actions = "".join(f'<a class="button-secondary" href="{html.escape(href)}">{html.escape(label)}</a>' for href, label in links)
        body = f'<h1>{html.escape(title)}</h1><div class="error-state" role="alert"><h2>What happened</h2><p>{html.escape(summary)}</p><p><strong>Next step:</strong> {html.escape(action)}</p></div><div class="action-row">{actions}</div>'
        return self.render_shell("/explore", title, body, eyebrow="Recoverable route error")

    def render_concept_page(self, concept: Mapping[str, Any]) -> str:
        """Render a concept through the shared eight-level learning shell."""

        raw_id = str(concept.get("id", ""))
        title_text = str(concept.get("title", raw_id or "Concept"))
        esc = lambda value: html.escape(str(value or ""))
        prerequisites = concept.get("prerequisites", ()) or ()
        prerequisite_links = " ".join(
            f'<a href="/knowledge/{html.escape(str(item))}">{html.escape(str(item))}</a>' for item in prerequisites
        ) or "No prerequisites recorded."

        def list_items(values: Any, *, mapping_keys: tuple[str, ...] = ("statement", "text")) -> str:
            rendered: list[str] = []
            for item in values or ():
                if isinstance(item, Mapping):
                    value = next((item.get(key) for key in mapping_keys if item.get(key)), "")
                else:
                    value = item
                if value:
                    rendered.append(f"<li>{esc(value)}</li>")
            return "".join(rendered) or "<li>No additional material is recorded for this level.</li>"

        derivation_rows = "".join(
            f'<li><strong>{esc(item.get("statement"))}</strong><br><span class="meta">{esc(item.get("what_changed"))} {esc(item.get("why_valid"))}</span></li>'
            for item in concept.get("derivations", []) or () if isinstance(item, Mapping)
        ) or "<li>No derivation steps are recorded for this concept.</li>"
        code_blocks = "".join(
            f'<pre><code>{esc(item.get("code"))}</code></pre><p class="meta">{esc(item.get("input_description"))} {esc(item.get("output_description"))}</p>'
            for item in concept.get("code_examples", []) or () if isinstance(item, Mapping)
        ) or '<div class="empty-state">No executable example is attached yet; treat this level as unavailable.</div>'
        def application(values: Any) -> str:
            return "".join(
                f'<p><strong>{esc(item.get("title"))}</strong> — {esc(item.get("description"))}</p>'
                for item in values or () if isinstance(item, Mapping)
            ) or '<p class="meta">No application note is recorded for this level.</p>'

        source_rows: list[str] = []
        for item in concept.get("source_references", []) or ():
            if not isinstance(item, Mapping):
                continue
            locator = str(item.get("locator") or item.get("url") or "").strip()
            label = esc(item.get("title") or item.get("reference_id") or "Source")
            kind = esc(item.get("kind", "reference"))
            if locator.startswith(("https://", "http://")):
                source_rows.append(f'<li><a rel="noopener noreferrer" target="_blank" href="{html.escape(locator)}">{label}</a> <small>{kind}</small></li>')
            else:
                source_rows.append(f"<li>{label} <small>{kind}; locator unavailable</small></li>")
        sources_html = "".join(source_rows) or "<li>Source locator unavailable.</li>"
        mastery = self.repository.get_mastery_state(self.config.session_id, raw_id)
        concept_id = html.escape(raw_id)
        body = (
            f'<div class="status-row">{self._status("CURATED", "complete")}{self._status("SAMPLE", "sample")} {self._status("LEARNING", "learning")}</div>'
            f'<h1>{esc(title_text)}</h1><p class="lede">A progressive path from intuition to current context. The catalog fingerprint and source boundary remain visible so understanding is not confused with certainty.</p>'
            '<nav class="stepper" aria-label="Knowledge depth">'
            + "".join(f'<div class="step{(" step--active" if index == 0 else "")}"><strong>{index + 1}</strong><br>{label}</div>' for index, label in enumerate(("Intuition", "Definition", "Equation", "Derivation", "Proof", "Code", "Finance / Quant / Strategy", "Current context")))
            + '</nav>'
            f'<div class="grid"><section class="card"><h2>Intuition</h2><p>{esc(concept.get("intuition"))}</p></section>'
            f'<section class="card"><h2>Definition / Formal</h2><p>{esc(concept.get("formal_definition") or concept.get("definition"))}</p></section>'
            f'<section class="card card--quiet"><h2>Equation</h2><pre aria-label="Equation"><code>{esc(concept.get("equation"))}</code></pre></section>'
            f'<section class="card"><h2>Derivation</h2><ol>{derivation_rows}</ol></section>'
            f'<section class="card"><h2>Proof boundary</h2><p class="meta">Proofs are claims with assumptions, not guarantees.</p><ul>{list_items(concept.get("proofs"))}</ul><h3>Assumptions</h3><ul>{list_items(concept.get("assumptions"), mapping_keys=("assumption", "text"))}</ul></section>'
            f'<section class="card"><h2>Code</h2>{code_blocks}</section>'
            f'<section class="card"><h2>Finance / Quant / Strategy</h2>{application(concept.get("financial_interpretations"))}{application(concept.get("quant_applications"))}{application(concept.get("strategy_applications"))}</section>'
            f'<section class="card"><h2>Current context</h2><p>{esc(concept.get("current_context"))}</p></section></div>'
            '<section class="section card card--evidence"><div class="section-heading"><h2>Data → Math → Code → Finance → Strategy role</h2><span class="meta">Linked interpretation</span></div><p class="lede">Trace the selected concept from observable data to a bounded strategy role. The mapping is explanatory; it is not a recommendation.</p><div class="grid"><div><strong>Data</strong><p class="meta">Observed series and availability window</p></div><div><strong>Math</strong><p class="meta">Definition, equation, and assumptions</p></div><div><strong>Code</strong><p class="meta">Reviewed implementation example</p></div><div><strong>Finance / Strategy</strong><p class="meta">Interpretation and limitation</p></div></div></section>'
            f'<section class="section card"><h2>Personal mastery</h2><p><strong>{esc(mastery.state)}</strong> · {mastery.evidence_count} evidence item(s). {esc(mastery.explanation)}</p><form class="form-grid" method="post" action="/api/personal/save"><input type="hidden" name="_csrf" value="{self.csrf_token}"><input type="hidden" name="node_type" value="learning_card"><input type="hidden" name="title" value="{esc(title_text)} self-check"><input type="hidden" name="concept_id" value="{concept_id}"><label for="outcome">What is the main caveat?<select id="outcome" name="outcome" required aria-describedby="outcome-help"><option value="correct">I can explain it with its assumptions</option><option value="incorrect">I would treat it as a guarantee</option></select></label><p class="field-help" id="outcome-help">Choose the statement that best represents your current understanding.</p><button type="submit">Save learning evidence</button></form></section>'
            f'<section class="card"><h2>Sources, misconceptions, and prerequisites</h2><h3>Sources</h3><ul>{sources_html}</ul><h3>Common misconceptions</h3><ul>{list_items(concept.get("misconceptions"), mapping_keys=("claim", "correction"))}</ul><h3>Prerequisites</h3><p>{prerequisite_links}</p></section>'
            f'<p class="meta">Structured source contract: <a href="/api/concepts/{concept_id}">JSON concept record</a>. Return to <a href="/knowledge">Knowledge index</a>.</p>'
        )
        return self.render_shell("/knowledge", title_text, body, inspector=self._inspector("Knowledge provenance", {"Concept": title_text, "Catalog": DEFAULT_CATALOG.fingerprint, "Mastery": mastery.state, "Boundary": "Sample curriculum; verify sources"}, status="CURATED"), eyebrow="Knowledge / progressive depth")

    def _page_inspector(self, page: str) -> str:
        if page == '/diagnostics':
            return self._inspector('检查范围', {'本地兼容': '临时 HTTP 服务与合成样例', '用户接口': '逐次授权、单次探测', '研究评估': '固定材料与冻结条件', '结果记录': '本地保存，可下载核对'}, status='本地研究')
        if page == "/":
            steps = ("界定问题与数据范围", "形成可检验的假设", "查看计算与反例", "解释结果，记录下一步")
            path = ''.join(f'<li><span class="context-step">{index}</span><span>{label}</span></li>' for index, label in enumerate(steps, 1))
            return (
                '<aside class="inspector" id="inspector" aria-label="研究上下文">'
                f'<section class="inspector-inner context-card"><div class="context-heading">{nav_icon("codex")}<h2>Codex 与研究任务</h2></div>'
                '<p class="context-note">显式交接，计算由研究引擎完成。</p>'
                f'<ol class="research-path" aria-label="研究路径，非运行进度">{path}</ol>'
                '<a class="button-secondary full-width" href="/research/jobs">打开研究工作台</a>'
                '<p class="context-note">提交前选择数据、范围与模型配置；这里不自动调用 Codex。</p></section>'
                '<section class="inspector-inner context-card"><h2>数据与证据</h2>'
                '<div class="context-source"><span class="context-symbol">API</span><div><strong>用户自己的数据接口</strong><p>显式读取 · 保存快照与指纹</p></div></div>'
                '<div class="context-source"><span class="context-symbol">DB</span><div><strong>本地研究记录</strong><p>任务、产物与版本可追溯</p></div></div>'
                '<p class="context-note">记录数不是收益指标；事件样例与实际研究分开标记。</p>'
                '<a href="/settings/data-connections">管理数据接口</a> · <a href="/settings/providers">模型配置</a></section>'
                '<section class="inspector-inner context-card"><h2>让研究更进一步</h2>'
                '<ul class="context-learning"><li><a href="/knowledge/volatility"><span aria-hidden="true">σ</span>波动率与窗口选择</a></li>'
                '<li><a href="/knowledge/beta"><span aria-hidden="true">β</span>Beta 与不确定性</a></li>'
                '<li><a href="/research/learning"><span aria-hidden="true">∑</span>复盘、审核与版本复跑</a></li></ul>'
                '</section></aside>'
            )
        if page == "/research/jobs":
            return self._inspector("研究运行边界", {"数据": "显式选定并校验的快照", "执行": "独立 supervisor / spawn", "模型": "按所选配置；在线调用需授权", "产物": "有指纹的 HTML 报告"}, status="PAPER-ONLY")
        if page == "/events":
            return self._inspector("Event provenance", {"Source": "U.S. Bureau of Labor Statistics", "Mode": "CAPTURED / SAMPLE", "Next": "Show Evidence → Knowledge → Quant"}, status="CAPTURED")
        if page == "/quant":
            return self._inspector("Experiment contract", {"Method": "OLS on deterministic fixture", "Uncertainty": "SE + confidence interval", "Mutation": "POST creates a private ResearchRun"}, status="READY")
        if page == "/strategy":
            return self._inspector("Strategy boundary", {"Stages": "Spec → Features → Backtest → OOS → Paper", "Execution": "PAPER ONLY", "Live orders": "Unavailable"}, status="PAPER")
        if page in {"/personal", "/workspace"}:
            personal = self.personal()
            return self._inspector("Workspace continuity", {"Nodes": len(personal.get("nodes", [])), "History": len(personal.get("history", [])), "Privacy": "Private by default"}, status="READY")
        if page == "/community":
            return self._inspector("Projection boundary", {"Visibility": "Private by default", "Sharing": "Explicit allow-list + consent", "Ranking": "No leaderboards"}, status="LIMITED")
        if page == "/research":
            return self._inspector("Research boundary", {"Data": "SAMPLE / PIT-aware", "Renderer": "Local bundle", "Mutation": "Read-only view"}, status="SAMPLE")
        if page == "/workbench":
            return self._inspector("Workbench boundary", {"Data": "SAMPLE / PIT-aware", "Mode": "READ-ONLY", "Live orders": "Unavailable"}, status="PAPER")
        if page == "/ml":
            return self._inspector("ML boundary", {"Engine": "Typed adapter", "Fallback": "Finathink baseline", "External objects": "Never exposed"}, status="FALLBACK")
        if page == "/parameter":
            return self._inspector("Sweep boundary", {"OOS": "Visible", "Multiple testing": "Reported", "Winner label": "Not emitted"}, status="REVIEW")
        if page in {"/settings/engines", "/settings/providers", "/settings/data-sources"}:
            return self._inspector("Capability boundary", {"Core": "Finathink-owned", "Optional": "Isolated", "QMT": "Read-only bridge"}, status="GOVERNED")
        return self._inspector("Local context", {"Mode": "SAMPLE / OFFLINE" if self.config.offline else "NETWORK ENABLED", "Storage": "SQLite local store", "Real money": "Unavailable"}, status="OFFLINE" if self.config.offline else "READY")

    def _p8_2_page_body(self, page: str) -> str:
        """Render P8.2 surfaces with server-owned facts and explicit limits."""

        if page == "/workbench":
            from finahinking.p8_2.research_view import build_research_payload

            workbench = build_research_payload().get("workbench", {})
            metrics = workbench.get("metrics", {}) if isinstance(workbench, Mapping) else {}
            metric_items = list(metrics.items())[:5] if isinstance(metrics, Mapping) else []
            metric_html = "".join(
                f'<div class="workbench-metric"><strong data-workbench-metric="{html.escape(str(key))}">{html.escape(str(value))}</strong><span>{html.escape(str(key).replace("_", " "))}</span></div>'
                for key, value in metric_items
            )
            rows = "".join(
                f'<tr data-workbench-row data-point-id="{html.escape(str(point.get("point_id")))}" tabindex="0" role="button" aria-label="{html.escape(str(point.get("time")))} {html.escape(str(point.get("instrument")))}"><th scope="row">{html.escape(str(point.get("time")))}</th><td>{html.escape(str(point.get("instrument")))}</td><td>{html.escape(str(point.get("score")))}</td><td>{html.escape(str(point.get("raw_weight")))}</td><td>{html.escape(str(point.get("risk_scale")))}</td><td>{html.escape(str(point.get("final_weight")))}</td><td>{html.escape(str(point.get("risk_state")))}</td><td>{html.escape(str(point.get("net_return")))}</td></tr>'
                for point in workbench.get("points", []) if isinstance(point, Mapping)
            )
            table = '<div class="workbench-table table-wrap"><table aria-label="Factor strategy workbench audit"><caption class="meta">Server-owned replay values · select a row to inspect its frozen point</caption><thead><tr><th scope="col">Time</th><th scope="col">Instrument</th><th scope="col">Score</th><th scope="col">Raw weight</th><th scope="col">Risk scale</th><th scope="col">Final weight</th><th scope="col">Risk state</th><th scope="col">Net return</th></tr></thead><tbody>' + rows + '</tbody></table></div>'
            return (
                '<div class="status-row">'
                f'{self._status("PAPER-ONLY", "limitation")}{self._status("OFFLINE", "offline")}{self._status("READ-ONLY", "ready")}'
                '</div><h1>Factor strategy workbench</h1>'
                '<p class="lede">A quiet audit surface for the algorithmic path: factor observation → signal → position mapping → risk scaling → delayed execution. Every value below belongs to the frozen server-side replay.</p>'
                '<div class="action-row"><a class="button-primary" href="#audit-table">Inspect the replay</a><a class="button-secondary" href="/research">Return to Research Workspace</a></div>'
                '<section class="workbench-frame workbench-frame--standalone" data-finathink-workbench data-payload-url="/api/research/workbench">'
                '<div class="workbench-head"><div><p class="eyebrow">Algorithm / evidence boundary</p><h2>One signal, four gates</h2><p class="source-state">The browser explains and selects; it never recalculates finance, rewrites the frozen run, or places an order.</p></div><span class="status status--offline" data-workbench-status>PAPER-ONLY · OFFLINE</span></div>'
                '<div class="workbench-flow" aria-label="Factor to execution flow"><span>Factor</span><i aria-hidden="true">→</i><span>Signal</span><i aria-hidden="true">→</i><span>Position</span><i aria-hidden="true">→</i><span>Risk</span><i aria-hidden="true">→</i><span>Execution</span></div>'
                f'<div class="workbench-metrics">{metric_html}</div>'
                '<div class="workbench-grid workbench-grid--standalone"><div><p class="workbench-inspector" data-workbench-inspector role="status" aria-live="polite">Select a workbench row to inspect its canonical values.</p><div id="audit-table">' + table + '</div></div>'
                '<div class="workbench-preview"><h3>Parameter preview</h3><label for="workbench-lookback-standalone">Lookback window<input id="workbench-lookback-standalone" data-workbench-parameter="lookback" type="range" min="5" max="60" value="20" step="5"></label><p class="field-help" data-workbench-preview>Preview only · not saved and never used to rewrite the frozen run.</p><p class="error-state" data-workbench-error hidden></p></div></div></section>'
                '<section class="section workbench-notes"><div><p class="eyebrow">What this page proves</p><h2>Decision logic stays inspectable.</h2><p>Signals are not positions. Risk state can scale or hold existing exposure. Delayed holdings, costs, slippage, and explicit fault events are retained in the same reportable chain.</p></div><ul class="research-limitations"><li>Fixture data only; no live market feed is connected.</li><li>Paper simulation only; no broker, account, or order operation exists.</li><li>Changing a parameter creates a preview, not a new saved strategy.</li></ul></section>'
            )

        if page == "/research":
            from finahinking.p8_2.research_view import build_research_payload

            research_payload = build_research_payload()
            event_labels = {str(item.get("id")): str(item.get("label", item.get("title", item.get("id", "event")))) for item in research_payload.get("events", []) if isinstance(item, Mapping)}
            table_rows = "".join(
                "<tr>"
                f"<td>{html.escape(str(point.get('time', '—')))}</td>"
                f"<td>{html.escape(str(point.get('close', '—')))}</td>"
                f"<td>{html.escape(str(point.get('volume', '—')))}</td>"
                f"<td>{html.escape(', '.join(f'{key}: {value}' for key, value in (point.get('features') or {}).items()) or '—')}</td>"
                f"<td>{html.escape(', '.join(event_labels.get(str(event), str(event)) for event in (point.get('events') or [])) or '—')}</td>"
                "</tr>"
                for point in research_payload.get("points", []) if isinstance(point, Mapping)
            )
            static_table = (
                '<div data-research-point-table class="table-wrap"><table aria-label="Research observations">'
                '<thead><tr><th>Time</th><th>Close</th><th>Volume</th><th>Features</th><th>Events</th></tr></thead>'
                f"<tbody>{table_rows}</tbody></table></div>"
            )
            feature_cards = "".join(
                f'<li class="evidence-item"><strong>{html.escape(str(item.get("label", item.get("id", "Feature"))))}</strong><span>{html.escape(str(item.get("definition", "Server-normalized feature.")))}</span><br><small>Source: {html.escape(str(item.get("source", "normalized")))} · Look-ahead: {html.escape(str(item.get("lookahead", "declared")))}</small></li>'
                for item in research_payload.get("features", []) if isinstance(item, Mapping)
            )
            factor_research = research_payload.get("factor_research", {})
            factor_rows = "".join(
                f'<tr><th scope="row"><code>{html.escape(str(item.get("candidate", {}).get("candidate_id", "—")))}</code></th><td><code>{html.escape(str(item.get("candidate", {}).get("expression", "—")))}</code></td><td>{html.escape(str(item.get("evaluation", {}).get("status", "—")))}</td><td>{html.escape(str(item.get("evaluation", {}).get("information_coefficient", "—")))}</td><td>{html.escape(str(item.get("evaluation", {}).get("oos_status", "—")))}</td><td>{html.escape(str(item.get("admission", {}).get("status", "—")))}</td></tr>'
                for item in factor_research.get("rounds", []) if isinstance(item, Mapping)
            ) or '<tr><td colspan="6">No factor candidates were evaluated.</td></tr>'
            workbench = research_payload.get("workbench", {})
            workbench_metrics = "".join(
                f'<div class="workbench-metric"><strong data-workbench-metric="{html.escape(str(key))}">{html.escape(str(value))}</strong><span>{html.escape(str(key).replace("_", " "))}</span></div>'
                for key, value in list((workbench.get("metrics") or {}).items())[:4]
            )
            workbench_rows = "".join(
                f'<tr data-workbench-row data-point-id="{html.escape(str(point.get("point_id")))}" tabindex="0" role="button" aria-label="{html.escape(str(point.get("time")))} {html.escape(str(point.get("instrument")))}"><th scope="row">{html.escape(str(point.get("time")))}</th><td>{html.escape(str(point.get("instrument")))}</td><td>{html.escape(str(point.get("score")))}</td><td>{html.escape(str(point.get("final_weight")))}</td><td>{html.escape(str(point.get("risk_state")))}</td><td>{html.escape(str(point.get("net_return")))}</td></tr>'
                for point in workbench.get("points", []) if isinstance(point, Mapping)
            )
            workbench_table = '<div class="workbench-table"><table aria-label="Factor strategy workbench audit"><thead><tr><th scope="col">Time</th><th scope="col">Instrument</th><th scope="col">Score</th><th scope="col">Final weight</th><th scope="col">Risk</th><th scope="col">Net return</th></tr></thead><tbody>' + workbench_rows + '</tbody></table></div>'
            return (
                '<div class="status-row">'
                f'{self._status("SAMPLE", "sample")}{self._status("PIT-AWARE", "evidence")}{self._status("READ-ONLY", "ready")}'
                '</div><h1>Research workspace</h1>'
                '<p class="lede">Inspect a normalized market series, its features, events, provenance, and declared parameter experiments in one calm surface. The browser renders server-owned values; it does not calculate them.</p>'
                '<div class="action-row"><a class="button-primary" href="/workbench">Open Factor Strategy Workbench</a><a class="button-secondary" href="/ml">Open ML Lab</a><a class="button-secondary" href="/parameter">Open Parameter Lab</a><a class="button-secondary" href="/settings/data-sources">Review data sources</a></div>'
                '<section class="research-workspace" data-finathink-research data-payload-url="/api/research/series" aria-busy="true"><p class="loading-state" data-research-loading aria-live="polite">Loading research workspace…</p>'
                '<div class="research-toolbar"><div><h2>Price, volume, and evidence</h2><p class="source-state">Crosshair and point selection update the inspector; the fallback table remains available to keyboard users.</p></div><span class="status status--sample">FIXTURE / OFFLINE</span></div>'
                '<div class="research-panel"><div class="research-chart" data-research-chart role="img" aria-label="Candlestick, volume, and feature overlay chart for the normalized research sample"></div><p class="research-tooltip" data-research-tooltip role="status" aria-live="polite">Hover or focus a point to inspect its canonical values.</p><p class="error-state" data-research-error hidden></p></div>'
                f'<section class="workbench-frame" data-finathink-workbench data-payload-url="/api/research/workbench" aria-busy="true"><p class="loading-state" data-workbench-loading aria-live="polite">Loading workbench…</p><div class="workbench-head"><div><h2>Factor / strategy workbench</h2><p class="source-state">Signal, capital, risk and execution stay separate. Select a row to inspect the server-owned point.</p></div><span class="status status--offline" data-workbench-status>PAPER-ONLY · OFFLINE</span></div><div class="workbench-metrics">{workbench_metrics}</div><div class="workbench-grid"><div><p class="workbench-inspector" data-workbench-inspector role="status" aria-live="polite">Select a workbench row to inspect its canonical values.</p>{workbench_table}</div><div class="workbench-preview"><h3>Parameter preview</h3><label for="workbench-lookback">Lookback window<input id="workbench-lookback" data-workbench-parameter="lookback" type="range" min="5" max="60" value="20" step="5"></label><p class="field-help" data-workbench-preview>Preview only · not saved and never used to rewrite the frozen run.</p><p class="error-state" data-workbench-error hidden></p></div></div></section>'
                '<div class="research-grid"><section class="research-panel"><h2>Selected observation</h2><p class="research-inspector" data-research-inspector role="status" aria-live="polite">Select a candle or row to inspect its canonical values.</p><div class="knowledge-context" data-knowledge-context><h3>Learn from this observation</h3><p data-knowledge-context-status>Choose a point to bind a point-in-time explanation.</p><a class="button-secondary" data-knowledge-context-link href="/knowledge/volatility">Teach me this</a></div></section><section class="research-panel"><h2>Declared parameter sweep</h2><div class="research-sweep" data-research-sweep role="img" aria-label="Out-of-sample parameter comparison"></div><p class="field-help">OOS values and multiple-testing context are retained; no winning strategy is named.</p></section></div>'
                f'<section class="research-panel"><h2>Feature lineage</h2><p class="field-help">Features are computed server-side and linked to the dataset fingerprint; the renderer only displays them.</p><ul class="evidence-list">{feature_cards}</ul></section>'
                f'<section class="research-panel"><h2>Factor research ledger</h2><p class="field-help">Bounded templates are evaluated on train/validation with T+1 timing. OOS remains hidden until a candidate is explicitly frozen.</p><div class="table-wrap"><table aria-label="Factor research ledger"><thead><tr><th>Candidate</th><th>Expression</th><th>Status</th><th>IC</th><th>OOS</th><th>Admission</th></tr></thead><tbody>{factor_rows}</tbody></table></div><p class="meta">{html.escape(str(factor_research.get("boundary", "paper-only factor evidence")))}</p></section>'
                f'<section class="research-panel"><h2>Accessible observation table</h2><p class="field-help">Use Enter or Space on a row to select an exact point. Values are not recomputed in the browser.</p><div data-research-table-anchor>{static_table}</div></section>'
                '</section>'
                '<section class="section card card--quiet"><h2>Research limits</h2><ul class="research-limitations"><li>Deterministic sample data only; no live market feed is connected.</li><li>Feature values are descriptive and retain source, availability, and dataset fingerprint.</li><li>QMT, Qlib, and vectorbt remain replaceable, isolated adapters; no order or account operation exists here.</li></ul></section>'
            )
        if page == "/ml":
            try:
                payload = self.route("GET", "/api/research/ml")[2]
            except (AttributeError, KeyError, TypeError, ValueError, RuntimeError) as exc:
                payload = {"error": str(exc), "action": "inspect Engine Settings and retry"}
            result = payload.get("result", {}) if isinstance(payload, Mapping) else {}
            spec = payload.get("specification", {}) if isinstance(payload, Mapping) else {}
            metrics = result.get("metrics", {}) if isinstance(result, Mapping) else {}
            if isinstance(metrics, Mapping):
                metric_items = list(metrics.items())
            elif isinstance(metrics, list):
                metric_items = [(item.get("name", "metric"), item.get("value", "—")) for item in metrics if isinstance(item, Mapping)]
            else:
                metric_items = []
            cards = "".join(f'<div class="metric"><span class="metric-value">{html.escape(str(value))}</span><span class="metric-label">{html.escape(str(key).replace("_", " "))}</span></div>' for key, value in metric_items[:6]) or '<div class="empty-state">No metrics are available yet.</div>'
            return (
                f'<div class="status-row">{self._status(result.get("status", "FALLBACK"), "sample")}{self._status("NO RAW EXTERNAL OBJECTS", "ready")}</div>'
                '<h1>ML Lab</h1><p class="lede">Run a transparent baseline through the Finathink contract. Qlib can be admitted only in an isolated environment after compatibility, provenance, and smoke gates pass.</p>'
                f'<section class="card"><h2>Research specification</h2><p>Target: <code>{html.escape(str(spec.get("target", "close")))}</code> · Features: <code>{html.escape(", ".join(spec.get("features", [])) if isinstance(spec.get("features"), list) else str(spec.get("features", "—")))}</code></p><div class="metric-row">{cards}</div></section>'
                f'<section class="section card card--quiet"><h2>Adapter result</h2><p>{html.escape(str(payload.get("boundary", "Typed adapter boundary")))}</p><details><summary>Inspect normalized result</summary><pre>{html.escape(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))}</pre></details></section>'
                '<div class="action-row"><a class="button-secondary" href="/research">Back to Research Workspace</a><a class="button-secondary" href="/settings/engines">Review engine capabilities</a></div>'
            )
        if page == "/parameter":
            try:
                payload = self.route("GET", "/api/research/parameters")[2]
            except (AttributeError, KeyError, TypeError, ValueError, RuntimeError) as exc:
                payload = {"error": str(exc), "action": "inspect the research payload and retry"}
            sweep = payload.get("sweep", {}) if isinstance(payload, Mapping) else {}
            experiments = sweep.get("experiments", []) if isinstance(sweep, Mapping) else []
            rows = "".join(
                f'<tr><td>{html.escape(str(item.get("experiment_id", "—")))}</td><td><code>{html.escape(json.dumps(item.get("parameters", {}), sort_keys=True))}</code></td><td>{html.escape(str((item.get("oos") or {}).get("score", "—")))}</td><td>{html.escape(str((item.get("train") or {}).get("score", "—")))}</td></tr>'
                for item in experiments if isinstance(item, Mapping)
            ) or '<tr><td colspan="4">No experiments are available.</td></tr>'
            warnings = sweep.get("warnings", []) if isinstance(sweep, Mapping) else []
            warning_html = "".join(f"<li>{html.escape(str(item))}</li>" for item in warnings) or "<li>No warning was emitted.</li>"
            return (
                '<div class="status-row">'+self._status("OOS VISIBLE", "quant")+self._status("MULTIPLE TESTING REPORTED", "limitation")+'</div>'
                '<h1>Parameter Lab</h1><p class="lede">Compare every declared parameter combination with train, validation, and OOS context. This is a sensitivity surface, not a leaderboard.</p>'
                f'<section class="card"><h2>Experiment ledger</h2><div class="table-wrap"><table><caption class="meta">All declared experiments · dataset {html.escape(str((payload.get("dataset") or {}).get("fingerprint", "unknown")))}</caption><thead><tr><th>Experiment</th><th>Parameters</th><th>OOS</th><th>Train</th></tr></thead><tbody>{rows}</tbody></table></div></section>'
                f'<section class="section grid"><article class="card"><h2>Multiple-testing context</h2><ul>{warning_html}</ul></article><article class="card card--quiet"><h2>Selection boundary</h2><p>{html.escape(str((payload.get("interpretation") or {}).get("warning", "No winner label is emitted.")))}</p></article></section>'
                '<div class="action-row"><a class="button-secondary" href="/research">Back to Research Workspace</a><a class="button-secondary" href="/ml">Open ML Lab</a></div>'
            )
        if page == "/settings/engines":
            from finahinking.p8_2.research_view import capability_payload, qmt_payload

            capabilities = capability_payload()
            qmt = qmt_payload()
            cards = "".join(
                f'<article class="card"><div class="status-row">{self._status(item.get("status"), "ready" if item.get("status") == "AVAILABLE" else "sample")}</div><h2>{html.escape(str(name))}</h2><p>{html.escape(str(item.get("detail", "")))}</p><p class="meta">Environment: {html.escape(str(item.get("environment", "unknown")))} · Version: {html.escape(str(item.get("version") or "not reported"))}</p></article>'
                for name, item in capabilities.items() if isinstance(item, Mapping)
            )
            return f'<h1>Engine settings</h1><p class="lede">Core numerical code stays in the Finathink environment. Optional engines are detected without importing them and remain isolated until their gates pass.</p><section class="settings-grid">{cards}</section><section class="section card card--quiet"><h2>QMT bridge</h2><p>State: <strong>{html.escape(str(qmt.get("state", "NOT_CONFIGURED")))}</strong> · Read-only: <strong>{html.escape(str(qmt.get("read_only", True)))}</strong></p><p>{html.escape(str(qmt.get("message", "market-data-only bridge")))}</p><p class="meta">Denied by design: order, cancel, account, credentials.</p></section>'
        if page == "/settings/providers":
            payload = self.route("GET", "/api/research/providers")[2]
            cards = "".join(
                f'<article class="card"><div class="status-row">{self._status("READY" if item.get("configured") else "NOT CONFIGURED", "ready" if item.get("configured") else "sample")}{self._status("OFFLINE" if item.get("offline") else "USER KEY", "offline" if item.get("offline") else "quant")}</div><h2>{html.escape(str(item.get("provider")))}</h2><p>Model: <code>{html.escape(str(item.get("model")))}</code></p><p>{html.escape(str(item.get("reason")))}</p><p class="meta">Credential reference: <code>{html.escape(json.dumps(item.get("credential_ref", {}), sort_keys=True))}</code></p></article>'
                for item in payload.get("providers", []) if isinstance(item, Mapping)
            )
            return (f'<h1>模型接口</h1><p class="lede">使用用户自己的兼容模型服务。保存配置不调用模型；凭证在调用前从明确指定的环境变量或已有系统 Keychain 引用读取。</p><section class="settings-grid">{cards}</section>'
                    '<section class="card section"><h2>新增兼容接口</h2><form class="form-grid" data-provider-form>'
                    f'<input type="hidden" data-provider-csrf value="{self.csrf_token}">'
                    '<label>配置标识（小写字母、数字、短横线或下划线）<input name="provider_id" required maxlength="64" placeholder="my-model"></label>'
                    '<label>兼容协议<select name="adapter_kind"><option value="openai_compatible">OpenAI-compatible</option><option value="deepseek_compatible">DeepSeek-compatible</option></select></label>'
                    '<label>默认模型<input name="model" required placeholder="gpt-4.1"></label>'
                    '<label>HTTPS 基础地址或完整 Chat Completions 地址<input name="endpoint" type="url" required autocomplete="off"></label>'
                    '<label>保存 API Key 的环境变量名称（不是 Key 本身）<input name="credential_env" required placeholder="FINAHINK_USER_API_KEY" autocomplete="off"></label>'
                    '<label>各角色的模型映射（可选 JSON）<textarea name="role_models" rows="3" placeholder="{}"></textarea></label>'
                    '<button type="submit">保存接口配置</button><p data-provider-status role="status"></p></form>'
                    '<p>角色为 fundamentals、technical、sentiment、news、learning、research（经理）。回到“研究任务”选择接口并授权后才发出请求。Codex 外部任务仍需执行端回传，不会自动调用当前聊天。</p></section>')
        if page == "/settings/data-sources":
            from finahinking.p8_2.research_view import build_research_payload, qmt_payload

            payload = build_research_payload()
            qmt = qmt_payload()
            dataset = payload.get("dataset", {})
            return (
                '<h1>Data sources</h1><p class="lede">Every source crosses into the same normalized snapshot contract. Availability, provenance, and limitations remain attached to the chart and research artifacts.</p>'
                f'<section class="settings-grid"><article class="card"><div class="status-row">{self._status(dataset.get("mode"), "sample")}</div><h2>{html.escape(str(dataset.get("id", "fixture")))}</h2><dl><dt>Fingerprint</dt><dd><code>{html.escape(str(dataset.get("fingerprint", "unknown")))}</code></dd><dt>As of</dt><dd>{html.escape(str(dataset.get("as_of", "unknown")))}</dd><dt>PIT available</dt><dd>{html.escape(str(dataset.get("pit_available", False)))}</dd><dt>Provider</dt><dd>{html.escape(str(dataset.get("provider", "unknown")))}</dd></dl></article><article class="card"><div class="status-row">{self._status(qmt.get("state", "NOT_CONFIGURED"), "sample")}</div><h2>QMT read-only bridge</h2><p>Host: <code>{html.escape(str(qmt.get("host", "loopback")))}</code> · Port: <code>{html.escape(str(qmt.get("port", 0)))}</code></p><p>Only market-data snapshots may cross this boundary. Credentials and trading methods are rejected.</p></article></section>'
                '<section class="section card card--quiet"><h2>Limitations</h2><ul class="research-limitations"><li>Synthetic fixture is not market evidence.</li><li>QMT connection is not configured in this offline build.</li><li>Remote providers are not called by this view.</li></ul></section>'
            )
        return ""

    def _research_comparison_page_body(self) -> str:
        from .research.factor_workspace_page import quantitative_comparison_section
        from .research.reproduction_page import reproduction_page

        payload = self.research_comparison()
        rows = "".join(
            "<tr>"
            f"<th scope=\"row\">{html.escape(str(item.get('run_id', '—')))}</th>"
            f"<td>{html.escape(str(item.get('as_of') or '—'))}</td>"
            f"<td>{html.escape(str(item.get('state', 'UNKNOWN')))}</td>"
            f"<td>{html.escape(str(item.get('stages_completed', 0)))} / {html.escape(str(item.get('stages_total', 0)))}</td>"
            f"<td>{html.escape(str(item.get('evidence_count', 0)))}</td>"
            f"<td>{html.escape(str(item.get('report_count', 0)))}</td>"
            f"<td>{html.escape('ELIGIBLE' if item.get('decision_eligible') else 'NOT ELIGIBLE')}</td>"
            "</tr>"
            for item in payload["runs"]
            if isinstance(item, Mapping)
        ) or '<tr><td colspan="7" class="empty-state">No registered research runs are available for comparison.</td></tr>'
        return (
            '<div class="status-row">'
            f'{self._status("OFFLINE", "offline")}{self._status("PAPER-ONLY", "limitation")}{self._status("DESCRIPTIVE", "ready")}'
            '</div><h1>Research comparison</h1>'
            '<p class="lede">Compare registered research runs by declared state, stage completion, evidence coverage, and report presence. This workspace does not rank strategies or recommend an action.</p>'
            '<section class="panel comparison-workspace" data-finathink-comparison data-payload-url="/api/research/compare">'
            '<div class="comparison-head"><div><h2>Run inventory</h2><p class="source-state">Every value is a server-owned summary of a run already registered in this local session.</p></div><span class="status status--offline">READ-ONLY</span></div>'
            '<div class="table-wrap"><table aria-label="Research run comparison"><thead><tr><th scope="col">Run</th><th scope="col">As of</th><th scope="col">State</th><th scope="col">Stages</th><th scope="col">Evidence</th><th scope="col">Reports</th><th scope="col">Decision boundary</th></tr></thead><tbody>'
            f'{rows}</tbody></table></div>'
            '<p class="meta">Comparison basis: state, as-of, stage completion, analyst evidence count, report coverage, and decision eligibility. No composite score is computed.</p>'
            '<p class="error-state" data-comparison-error hidden></p></section>'
            f'{reproduction_page(self.csrf_token, payload["runs"])}'
            f'{quantitative_comparison_section(payload["quantitative"])}'
        )

    def _overview_page_body(self) -> str:
        payload = self.overview()
        metrics = payload["metrics"]
        metric_cards = "".join(
            f'<article class="metric-card"><span class="metric-label">{label}</span><span class="metric-value" data-overview-metric="{key}">{metrics[key]}</span><span class="metric-note">{note}</span></article>'
            for key, label, note in (("sample_events", "事件样例", "明确标记的学习资料"), ("saved_items", "已保存条目", "来自本地个人工作区"), ("learning_items", "学习证据", "实际保存的自查记录"), ("research_runs", "研究运行", "来自持久化研究索引"))
        )
        activity = payload.get("activity") or []
        activity_html = "".join(
            f'<li><span>{html.escape(str(item.get("timestamp", "—")))} · {html.escape(str(item.get("label", "Activity")))}</span><strong>{html.escape(str(item.get("status", "RECORDED")))}</strong></li>'
            for item in activity if isinstance(item, Mapping)
        ) or '<li class="empty-state"><span>还没有保存的研究活动</span><strong>暂无记录</strong></li>'
        topics = payload.get("topics") or []
        topic_html = "".join(
            f'<li><span>{html.escape(str(item.get("label", item.get("topic", "Topic"))))}</span><strong>{html.escape(str(item.get("count", 0)))}</strong></li>'
            for item in topics if isinstance(item, Mapping)
        ) or '<li class="empty-state"><span>还没有记录的研究主题</span><strong>暂无记录</strong></li>'
        learning = payload.get("learning_activity") or []
        heatmap_html = "".join(
            f'<li><span>{html.escape(str(item.get("date", "—")))}</span><strong>{html.escape(str(item.get("count", 0)))} 次自查</strong></li>'
            for item in learning if isinstance(item, Mapping)
        ) or '<li class="empty-state"><span>还没有学习自查记录</span><strong>暂无记录</strong></li>'
        activity_series = payload.get("activity_series") or []
        max_count = max((int(item.get("count", 0)) for item in activity_series if isinstance(item, Mapping)), default=0)
        bars = '<p class="empty-state">保存研究或学习记录后，这里会显示实际活动；不填入演示曲线。</p>' if not activity_series else ''.join(
            f'<i style="height:{max(12, round((int(item.get("count", 0)) / max_count) * 100))}%" aria-hidden="true"></i>'
            for item in activity_series if isinstance(item, Mapping)
        )
        daily_rows = ''.join(
            f'<tr><th scope="row"><button type="button" class="overview-day" data-overview-day data-date="{html.escape(str(item["date"]))}" data-tooltip="{html.escape(str(item["date"]))}：{item["count"]} 条记录 · {item["learning"]} 条学习 · {item["research"]} 条研究" aria-describedby="overview-tooltip">{html.escape(str(item["date"]))}</button></th><td>{item["count"]}</td><td>{item["learning"]}</td><td>{item["research"]}</td></tr>'
            for item in activity_series if isinstance(item, Mapping)
        ) or '<tr><td colspan="4">还没有带日期的活动记录。可先保存研究，或完成一次学习自查。</td></tr>'
        daily_table = '<div class="table-wrap overview-daily-table"><table aria-label="每日活动数据"><thead><tr><th scope="col">日期</th><th scope="col">记录</th><th scope="col">学习</th><th scope="col">研究</th></tr></thead><tbody data-overview-daily-rows>' + daily_rows + '</tbody></table></div>'
        topic_max = max((int(item.get("count", 0)) for item in topics if isinstance(item, Mapping)), default=0)
        topic_cells = ''.join(
            f'<span class="overview-topic-cell" data-intensity="{max(1, round((int(item.get("count", 0)) / topic_max) * 5)) if topic_max else 0}" data-tooltip="{html.escape(str(item.get("topic", "Topic")))}: {html.escape(str(item.get("count", 0)))} recorded" title="{html.escape(str(item.get("topic", "Topic")))}: {html.escape(str(item.get("count", 0)))} recorded" tabindex="0">{html.escape(str(item.get("topic", "Topic")))}</span>'
            for item in topics if isinstance(item, Mapping)
        ) or ''
        comparison_rows = ''.join(
            f'<tr><th scope="row">{html.escape(str(item.get("run_id", "—")))}</th><td>{html.escape(str(item.get("state", "UNKNOWN")))}</td><td>{html.escape(str(item.get("evidence_count", 0)))}</td><td>{html.escape(str(item.get("report_count", 0)))}</td></tr>'
            for item in payload.get("research_comparison", ()) if isinstance(item, Mapping)
        ) or '<tr><td colspan="4" class="empty-state">还没有可对比的研究运行。先完成一轮有明确数据和范围的研究。</td></tr>'
        history_open = ' open' if not activity else ''
        return (
            f'<section class="overview" data-finathink-overview data-overview-payload-url="/api/overview" aria-busy="false">'
            '<p class="loading-state" data-overview-loading aria-live="polite" hidden>正在读取研究概况…</p><p class="error-state" data-overview-error role="alert" aria-live="assertive" hidden></p><a class="button-secondary" data-overview-retry href="/" hidden>重新载入总览</a>'
            '<section class="research-intro" aria-labelledby="research-intro-title"><h2 id="research-intro-title">从一个好问题，<span>走向可验证的结论。</span></h2><p>连接数据、数学、代码与实验，保留验证和反例。</p>'
            '<form class="question-composer" action="/research/jobs" method="get"><label for="overview-question">你想验证什么？</label><textarea id="overview-question" name="question" required maxlength="1000" rows="2" placeholder="例如：动量窗口改变后，换手、费用和历史回撤会怎样变化？"></textarea>'
            '<div class="composer-footer"><span>先配置数据与边界，提交后才运行。</span><button type="submit">建立研究 '
            f'{nav_icon("arrow")}</button></div></form><div class="question-suggestions" aria-label="研究问题建议">'
            f'<a href="/research/jobs?{urlencode({"question": "检验动量基线，并核对换手、费用与历史回撤。"})}">动量与换手</a>'
            f'<a href="/research/jobs?{urlencode({"question": "检查所选快照中的成本、容量与损失情景。"})}">成本与风险</a>'
            '<a href="/research/experiments">因子与样本外实验</a></div></section>'
            f'<section class="metric-grid" aria-label="工作区概况">{metric_cards}</section>'
            '<section class="overview-grid">'
            f'<article class="panel overview-region" data-overview-chart tabindex="0"><h2>研究活动 <span>按日统计的实际记录，非投资绩效</span></h2><div class="overview-chart"><div class="overview-chart-canvas" data-overview-canvas aria-hidden="true" hidden></div><div class="overview-chart-bars" data-overview-activity-series>{bars}</div></div><p class="overview-tooltip" id="overview-tooltip" data-overview-tooltip role="status" aria-live="polite">聚焦日期或主题，查看对应的记录数量。</p>{daily_table}<details class="overview-history"{history_open}><summary>查看活动记录</summary><ul class="overview-list" data-overview-activity aria-label="活动文字记录">{activity_html}</ul></details></article>'
            f'<article class="panel overview-region" data-overview-topics><h2>研究主题 <span>实际记录的分布</span></h2><div class="overview-topic-heatmap" aria-label="研究主题分布">{topic_cells}</div><ul class="overview-list" aria-label="已记录的研究主题">{topic_html}</ul><p class="meta"><a href="/knowledge">继续学习</a>，把概念连接到推导和代码。</p></article>'
            f'<article class="panel overview-region" data-overview-heatmap><h2>学习记录 <span>来自已保存的自查</span></h2><ul class="overview-heatmap" aria-label="学习活动摘要">{heatmap_html}</ul><p class="meta">学习证据保存在本地，不根据浏览行为推断。</p></article>'
            '</section>'
            f'<section class="panel overview-comparison" data-overview-comparison><div class="section-heading"><h2>研究记录与对比 <span>先核对数据、范围和口径，再解释差异</span></h2><a class="button-secondary" href="/research/compare">打开对比工作区</a></div><div class="table-wrap"><table aria-label="研究对比摘要"><thead><tr><th scope="col">运行</th><th scope="col">状态</th><th scope="col">证据</th><th scope="col">报告</th></tr></thead><tbody>{comparison_rows}</tbody></table></div><p class="meta">不计算综合优劣分数，不把来源指标混入本地结果。</p></section>'
            '<details class="overview-notes"><summary>研究口径与边界</summary><p>总览只读取本地记录，不触发数据获取、模型调用或交易。事件样例明确标记；每份真实研究保留自己的数据、费用、样本外范围和局限。</p></details>'
            '<template id="legacy-launch-frames" aria-hidden="true"><img src="/assets/finathink-splash-map.jpg" width="1536" height="1024" alt=""><img src="/assets/finathink-research-splash.jpg" width="900" height="900" alt=""></template>'
            '</section>'
        )

    def _page_body(self, page: str, *, query: Mapping[str, list[str]] | None = None) -> str:
        if page == "/research/maintenance":
            from .research.learning_maintenance_page import maintenance_page

            return maintenance_page()
        if page == "/research/learning":
            from .research.learning_workspace_page import learning_workspace_page

            return learning_workspace_page(self.csrf_token)
        if page == "/research/experiments":
            from .research.factor_workspace_page import factor_workspace_page

            return factor_workspace_page(self.csrf_token)
        if page == "/research/jobs":
            body = self._research_jobs_page_body(query=query)
            if query and query.get("knowledge"):
                from .research.knowledge_workspace import CSS, preparation_panel

                try:
                    body = f"<style>{CSS}</style>" + preparation_panel(self.quant_knowledge, query["knowledge"][0], continuation=False) + body
                except KeyError:
                    body = '<p role="alert">知识引用已失效，请返回知识库重新选择。</p>' + body
            return body
        if page == "/research/compare":
            return self._research_comparison_page_body()
        if page in {"/research", "/workbench", "/ml", "/parameter", "/settings/engines", "/settings/providers", "/settings/data-sources"}:
            return self._p8_2_page_body(page)
        if page == "/":
            return self._overview_page_body()
        if page == "/events":
            event = self.event()
            claims = event.get("claims", []) or []
            claim_types = ("FACT", "INTERPRETATION", "HYPOTHESIS", "QUANT FINDING", "UNKNOWN", "LIMITATION")
            claim_cards: list[str] = []
            for index, claim_type in enumerate(claim_types):
                source = claims[index] if index < len(claims) else {}
                text_value = source.get("text", source.get("claim", "No claim of this type is recorded in the sample journey.")) if isinstance(source, Mapping) else source
                claim_cards.append(f'<article class="card"><div class="status-row">{self._status(claim_type, claim_type)}</div><p>{html.escape(str(text_value))}</p></article>')
            evidence = event.get("evidence", []) or []
            evidence_rows = "".join(
                f'<li class="evidence-item" id="evidence-{index}"><strong>{html.escape(str(item.get("evidence_id", "Evidence")))}</strong><span>{html.escape(str(item.get("scope", "Source evidence")))}</span><br><small>Publisher: {html.escape(str(event.get("publisher", "Source")))} · Published/available time retained in artifact</small></li>'
                for index, item in enumerate(evidence)
                if isinstance(item, Mapping)
            ) or '<li class="empty-state">No evidence rows were captured for this event.</li>'
            return (
                f'<div class="status-row">{self._status(event.get("status"), "complete")}{self._status(event.get("data_mode"), "sample")}</div><h1>Events</h1><p class="lede">What happened → what changed → why it may matter. The sample is captured, not live, and every conclusion keeps its limitations.</p>'
                f'<section class="card"><h2>What happened</h2><p><strong>{html.escape(str(event.get("title", "Event")))}</strong></p><p>{html.escape(str(event.get("publisher", "")))} · {html.escape(str(event.get("reference_period", "sample period")))}</p><p class="meta">Chain: {html.escape(" → ".join(event.get("chain", [])))}</p></section>'
                f'<section class="section"><div class="section-heading"><h2>What changed</h2><span class="meta">Interpretation stays separate from fact</span></div><div class="prose"><p>{html.escape(str(event.get("what_changed", "The captured release is available for inspection.")))}</p><p>{html.escape(str(event.get("why_it_may_matter", "Use the evidence and quant path to test implications.")))}</p></div></section>'
                f'<section class="section"><div class="section-heading"><h2>Claim ladder</h2><span class="meta">Six explicit claim types</span></div><div class="grid">{"".join(claim_cards)}</div></section>'
                f'<section class="section card card--evidence" id="show-evidence"><div class="section-heading"><h2>Show Evidence</h2><span class="meta">Why is Finathink saying this?</span></div><ul class="evidence-list">{evidence_rows}</ul><p class="meta">Artifact: {html.escape(str(event.get("artifact_id", "artifact retained in local journey")))} · ResearchRun: captured event journey · QuantRun: {html.escape(str(event.get("quant_status", "available")))}</p></section>'
                f'<section class="section"><div class="section-heading"><h2>Continue the ladder</h2><span class="meta">Knowledge → test → learn</span></div><div class="action-row"><a class="button-secondary" href="/knowledge/volatility">Open Knowledge</a><a class="button-secondary" href="/quant">Test the idea in Quant</a><a class="button-secondary" href="#learning">Jump to learning</a></div></section>'
                f'<section class="section card" id="learning"><h2>Learn privately</h2><p>{html.escape(str((event.get("limitations") or ["Limitations remain attached."])[0]))}</p><form method="post" action="/api/events/learn"><input type="hidden" name="_csrf" value="{self.csrf_token}"><button type="submit">Save this private learning thread</button></form></section>'
            )
        if page == "/explore":
            return '<h1>Explore</h1><p class="lede">Start with a human question, then choose the evidence, learning, or research path that fits. Internal IDs stay in the inspector, not in your way.</p><section class="card"><form class="form-grid" method="get" action="/knowledge"><label for="explore-q">What do you want to understand?<input id="explore-q" name="q" autocomplete="off" placeholder="e.g. volatility, beta, drawdown"></label><p class="field-help">Search the curated knowledge catalog; no external query is sent.</p><button type="submit">Search Knowledge</button></form></section><div class="grid"><article class="card"><h2>Event → Evidence</h2><p>Read a captured release, inspect claims, and follow the source chain.</p><a href="/events">Open Events</a></article><article class="card"><h2>Knowledge → Application</h2><p>Trace intuition, math, code, finance, quant, and strategy role.</p><a href="/knowledge">Browse Knowledge</a></article><article class="card"><h2>Question → Quant</h2><p>Make uncertainty, sample, period, method, and limitations explicit.</p><a href="/quant">Open Quant</a></article><article class="card"><h2>Idea → Paper</h2><p>Formalize one StrategySpec and inspect backtest/OOS/paper validity.</p><a href="/strategy">Open Strategy Lab</a></article></div>'
        if page == "/knowledge":
            search_query = (query or {}).get("q", [""])[0]
            concepts = self._concepts(search_query)
            cards = "".join(
                f'<article class="card"><h2><a href="/knowledge/{html.escape(str(item["id"]))}">{html.escape(str(item["title"]))}</a></h2><p>{html.escape(str(item["definition"]))}</p><pre><code>{html.escape(str(item["equation"]))}</code></pre><p class="meta">Prerequisites: {html.escape(", ".join(item.get("prerequisites", [])) or "none")}</p></article>'
                for item in concepts
            ) or '<div class="empty-state"><h2>No concepts match yet</h2><p>Try a shorter term or start with the sample event.</p><a href="/events">Open Events</a></div>'
            return f'<h1>Knowledge</h1><p class="lede">Eight levels make a concept usable: intuition, definition, equation, derivation, proof, code, finance/quant/strategy, and current context.</p><section class="card"><form class="form-grid" method="get" action="/knowledge"><label for="knowledge-q">Find a concept<input id="knowledge-q" name="q" autocomplete="off" value="{html.escape(search_query)}" placeholder="volatility, beta, returns"></label><button type="submit">Filter concepts</button></form></section><section class="section"><div class="section-heading"><h2>Structured learning paths</h2><span class="meta">{html.escape(search_query) if search_query else "All concepts"} · Catalog fingerprint: {html.escape(DEFAULT_CATALOG.fingerprint)}</span></div><div class="grid">{cards}</div></section>'
        if page == "/quant":
            stages = ("Question", "Hypothesis", "Data", "Method", "Experiment")
            step_html = "".join(
                f'<div class="step{(" step--active" if index == 0 else "")}"><strong>{index + 1}</strong><br>{label}</div>'
                for index, label in enumerate(stages)
            )
            return f'<h1>Quant lab</h1><p class="lede">Begin with a question, not a model button. The deterministic sample exposes value, context, uncertainty, sample, period, method, limitations, and provenance.</p><nav class="stepper" aria-label="Quant workflow">{step_html}</nav><section class="card"><form class="form-grid" method="post" action="/api/quant"><input type="hidden" name="_csrf" value="{self.csrf_token}"><label for="question">Research question<input id="question" name="question" required maxlength="256" autocomplete="off" value="Does beta explain the sample?"></label><label for="hypothesis">Hypothesis<textarea id="hypothesis" name="hypothesis" maxlength="1000">Market returns have a measurable historical beta to asset returns.</textarea></label><p class="field-help">POST creates a private ResearchRun. GET only describes this contract.</p><button type="submit">Run bounded experiment</button></form></section><section class="section grid"><article class="card"><h2>Result contract</h2><div class="metric-row"><div class="metric"><span class="metric-value">β</span><span class="metric-label">beta</span></div><div class="metric"><span class="metric-value">SE</span><span class="metric-label">standard error</span></div><div class="metric"><span class="metric-value">CI</span><span class="metric-label">confidence interval</span></div></div></article><article class="card card--quiet"><h2>Uncertainty first</h2><p>Read the estimate with its sample count, period, method, fixture fingerprint, and limitations. A result is not a forecast or a trade instruction.</p><p><strong>Provenance:</strong> fixture fingerprint, ResearchRun, and source lineage stay attached.</p>{self._status("SAMPLE", "sample")}</article></section><div class="loading-state" aria-label="Experiment stages"><strong>When you submit, stages are explicit:</strong> preparing dataset → validating availability → running experiment → evaluating result → saving artifact.</div>'
        if page == "/strategy":
            stages = ("Idea", "StrategySpec", "Features", "Backtest", "OOS", "Paper", "Compare", "Learn")
            stage_html = "".join(f'<div class="step{(" step--active" if i == 0 else "")}"><strong>{i + 1}</strong><br>{label}</div>' for i, label in enumerate(stages))
            return f'<h1>Strategy Lab</h1><p class="lede">Turn one idea into one reviewed StrategySpec, then inspect feature lineage, code/math/finance meaning, validity, OOS, paper state, and comparison.</p><div class="status-row">{self._status("PAPER SIMULATION", "limitation")}{self._status("OOS", "quant")}{self._status("NO LIVE ORDERS", "unknown")}</div><nav class="stepper" aria-label="Strategy workflow">{stage_html}</nav><section class="card"><form class="form-grid" method="post" action="/api/strategy"><input type="hidden" name="_csrf" value="{self.csrf_token}"><label for="idea">I have an idea<input id="idea" name="idea" required maxlength="512" autocomplete="off" value="moving average trend"></label><p class="field-help">The sample starts with the reviewed moving-average template. Guided and Advanced views share this StrategySpec; review the interpretation before research execution.</p><button type="submit">Formalize and run paper research</button></form></section><section class="section grid"><article class="card"><h2>Feature Graph</h2><p><code>Price → 20D Return → Momentum → Rank</code></p><p><code>Returns → 20D Std Dev → Volatility → Filter</code></p><p><code>Signal → Portfolio</code></p><a href="#inspector">Inspect a feature</a></article><article class="card"><h2>Code / Math / Finance</h2><p><code>rolling(20).std()</code> → sample standard deviation → realized volatility → high-volatility filter.</p></article><article class="card"><h2>Research Preview</h2><p>Universe · period · features · signal · rebalance · execution · cost · slippage · benchmark · OOS · assumptions · limitations.</p></article><article class="card"><h2>Validity</h2><ul><li>Look-ahead: PASS</li><li>OOS: ENABLED</li><li>Transaction costs: MODELED</li><li>Slippage: MODELED</li><li>Liquidity / capacity: UNKNOWN</li></ul></article></section><section class="section card card--quiet"><h2>PAPER SIMULATION</h2><p>Frozen Strategy · virtual clock · virtual cash · positions · virtual orders/fills · P&amp;L · benchmark. There is no Connect Broker, Deploy, or Trade Live action.</p></section>'
        if page in {"/personal", "/workspace"}:
            personal = self.personal()
            nodes = personal.get("nodes", []) or []
            node_rows = "".join(f'<li class="evidence-item"><strong>{html.escape(str(node.get("title", "Saved work")))}</strong><span>{html.escape(str(node.get("node_type", "research")))}</span></li>' for node in nodes[:12] if isinstance(node, Mapping))
            saved_html = f'<ul class="evidence-list">{node_rows}</ul>' if node_rows else '<div class="empty-state"><h3>NO RESEARCH YET</h3><p>Explore an event or run a bounded question to create your first private thread.</p><a href="/events">Start with an event</a></div>'
            return f'<h1>Workspace</h1><p class="lede">Personal continuity for <code>{html.escape(self.config.principal_id)}</code>. Resume research by human-readable name, not artifact UUID.</p><section class="grid"><article class="card"><h2>Research</h2><p>{len(nodes)} saved node(s) · {len(personal.get("history", []) or [])} learning/history record(s).</p></article><article class="card"><h2>Learning</h2><p>Mastery evidence stays local and can be backed up without personal payloads in diagnostics.</p></article><article class="card"><h2>Paper Runs</h2><p>Reopen paper-only strategy research with its validity and OOS boundary.</p></article><article class="card"><h2>Exports</h2><p><a href="/api/personal/backup">Create a private backup</a> or inspect <a href="/diagnostics">redacted diagnostics</a>.</p></article></section><section class="section card"><h2>Saved work</h2>{saved_html}</section><section class="section card"><h2>Save a note</h2><form class="form-grid" method="post" action="/api/personal/save"><input type="hidden" name="_csrf" value="{self.csrf_token}"><input type="hidden" name="node_type" value="note"><label for="note-title">Note title<input id="note-title" name="title" required maxlength="256" autocomplete="off"></label><button type="submit">Save privately</button></form><p class="field-help">Restarting the local app restores this workspace from SQLite.</p></section>'
        if page == "/community":
            community = self.route("GET", "/api/community")[2]
            posts = community.get("posts", []) or []
            post_html = "".join(f'<article class="card"><div class="status-row">{self._status(item.get("claim_type", "HYPOTHESIS"), "hypothesis")}</div><h3>{html.escape(str(item.get("title", "Private question")))}</h3><p>{html.escape(str(item.get("body", "")))}</p><small>Evidence: {html.escape(", ".join(item.get("evidence_ids", [])) or "not projected")}</small></article>' for item in posts if isinstance(item, Mapping))
            empty = '' if post_html else '<div class="empty-state"><h3>NO DISCUSSIONS YET</h3><p>Save a private question first; explicit projection is required before anything can be shared.</p></div>'
            event_id = html.escape(str(self.event().get("id", "")))
            return f'<h1>Community</h1><p class="lede">Calm evidence discussion: claims, questions, and counter-evidence remain private until explicit projection consent and an allow-listed field set exist.</p><div class="status-row">{self._status("PRIVATE BY DEFAULT", "limitation")}{self._status("NO LEADERBOARDS", "ready")}</div><section class="card"><form class="form-grid" method="post" action="/api/community"><input type="hidden" name="_csrf" value="{self.csrf_token}"><label for="claim">Claim or question<textarea id="claim" name="claim" required maxlength="2000" aria-describedby="claim-help"></textarea></label><p class="field-help" id="claim-help">State what you are unsure about; do not paste credentials or private identifiers.</p><button type="submit">Save private question</button></form></section><section class="card"><h2>Explicit projection</h2><form class="form-grid" method="post" action="/api/community/project"><input type="hidden" name="_csrf" value="{self.csrf_token}"><input type="hidden" name="source_id" value="{event_id}"><input type="hidden" name="fields" value="event_type,reference_period,published_at,revision_status,limitations"><label><span><input type="checkbox" name="consent" value="true" required> I consent to this descriptive projection</span></label><button type="submit">Project selected event fields</button></form></section><section class="section"><h2>Questions</h2>{empty}<div class="grid">{post_html}</div></section>'
        if page == '/diagnostics':
            try:
                records = self.interface_diagnostics.store.list()
            except (OSError, ValueError, sqlite3.Error):
                records = ()
            return diagnostic_page(self.csrf_token, self.diagnostics(), records)
        return '<h1>未找到此页面</h1><p><a href="/">返回研究概览</a></p>'

    def close(self) -> None:
        if self._quant_knowledge is not None:
            self._quant_knowledge.close()
        if self._research_application is not None:
            self._research_application.close()
        self.connection.close()


class _Handler(BaseHTTPRequestHandler):
    server_version = "FinathinkLocal/0.1"

    def _application(self) -> LocalApplication:
        return self.server.application

    def _send(self, status: int, content_type: str, payload: Any) -> None:
        if isinstance(payload, bytes):
            raw = payload
        elif isinstance(payload, str):
            raw = payload.encode("utf-8")
        else:
            raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        # no-referrer turns Chromium's native POST Origin into "null".
        # Keep the origin guard strict and omit referrers to external sites.
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'")
        self.send_header("Set-Cookie", "finahinking_session=local; HttpOnly; SameSite=Strict; Path=/")
        self.end_headers()
        self.wfile.write(raw)

    def _safe_origin(self) -> bool:
        host = self.headers.get("Host", "").split(":", 1)[0].strip("[]").lower()
        if host not in {"127.0.0.1", "localhost", "::1"}:
            return False
        origin = self.headers.get("Origin")
        if origin:
            parsed_origin = urlsplit(origin)
            origin_host = (parsed_origin.hostname or "").strip("[]").lower()
            if parsed_origin.scheme not in {"http", "https"} or origin_host not in {"127.0.0.1", "localhost", "::1"}:
                return False
        return True

    def do_GET(self) -> None:
        if not self._safe_origin():
            self._send(403, "application/json", {"error": "loopback origin required"})
            return
        parsed = urlsplit(self.path)
        try:
            status, content_type, payload = self._application().route("GET", parsed.path, query=parse_qs(parsed.query))
            self._send(status, content_type, payload)
        except (AttributeError, KeyError, ValueError, TypeError, RuntimeError, OSError, sqlite3.Error):
            self._send(503, "application/json", {"error": "本地服务暂时无法完成请求，请检查应用状态与磁盘空间。", "action": "打开诊断与恢复页面，重新读取记录后重试。"})

    def do_POST(self) -> None:
        if not self._safe_origin():
            self._send(403, "application/json", {"error": "loopback origin required"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send(400, "application/json", {"error": "invalid content length"})
            return
        if length < 0:
            self._send(400, "application/json", {"error": "invalid content length"})
            return
        body_limit = 1_000_000
        if urlsplit(self.path).path == "/api/research/reproduction/restore":
            body_limit = 8 * 1024 * 1024 + 64 * 1024
        if re.fullmatch(r"/api/research/jobs/[A-Za-z0-9_-]{1,128}/research-fields", urlsplit(self.path).path):
            from .data.research_fields import MAX_FIELD_BYTES

            body_limit = MAX_FIELD_BYTES + 64 * 1024  # JSON envelope and CSRF overhead, still bounded.
        if length > body_limit:
            self._send(413, "application/json", {"error": "request body too large"})
            return
        raw = self.rfile.read(length)
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].lower()
        if content_type == "application/x-www-form-urlencoded":
            values = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
            token = (values.get("_csrf", [""]) or [""])[0]
            if token != self._application().csrf_token:
                self._send(403, "application/json", {"error": "csrf token is required"})
                return
            body = {key: (items[-1] if len(items) == 1 else items) for key, items in values.items()}
        else:
            try:
                body = json.loads(raw or b"{}")
            except (json.JSONDecodeError, UnicodeError, RecursionError):
                self._send(400, "application/json", {"error": "请求需要有效的 JSON 或受保护表单，请检查文件格式与嵌套深度。"})
                return
        parsed = urlsplit(self.path)
        try:
            if content_type == "application/x-www-form-urlencoded" and parsed.path == "/api/data/connections" and body.get("auth_mode") != "api_key_header":
                # Native forms include this header-mode-only control even
                # when Bearer/no-auth was selected. Keep JSON API validation strict.
                body.pop("auth_header", None)
            status, response_type, payload = self._application().route("POST", parsed.path, body=body)
            if content_type == "application/x-www-form-urlencoded" and response_type == "application/json":
                self._send(status, "text/html; charset=utf-8", self._application().render_result_page("Research result", payload))
            else:
                self._send(status, response_type, payload)
        except (AttributeError, KeyError, ValueError, TypeError, RuntimeError, OSError, sqlite3.Error):
            self._send(400, "application/json", {"error": "请求未完成，请检查输入、认证方式和应用状态。", "action": "先在研究任务页读取原记录，避免重复新建；必要时下载脱敏诊断。"})

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def create_server(application: LocalApplication | None = None, *, host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    """Bootstrap the independent owner; return a bound, not-yet-serving HTTP server."""

    app = application or LocalApplication()
    resolved_host = host or app.config.host
    if resolved_host.strip().lower().strip("[]") not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Finathink local API only binds to loopback addresses")
    address = (resolved_host, int(port if port is not None else app.config.port))
    server = ThreadingHTTPServer(address, _Handler)
    server.application = app  # type: ignore[attr-defined]
    try:
        app.research_service.start()
    except Exception:
        server.server_close()
        app.research_service.close()
        raise
    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the Finathink local-first sample application")
    parser.add_argument("--host", default=None, help="loopback host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="TCP port (default: 8765)")
    parser.add_argument("--db", default=None, help="SQLite path (default: user-local persistent file)")
    parser.add_argument("--sample", action="store_true", help="enable deterministic sample mode")
    parser.add_argument("--offline", action="store_true", help="disable outbound providers")
    parser.add_argument("--smoke", action="store_true", help="check health and sample routes, then exit")
    args = parser.parse_args(argv)
    base = LocalAppConfig.from_env()
    config = LocalAppConfig(
        db_path=args.db or base.db_path,
        host=args.host or base.host,
        port=args.port if args.port is not None else base.port,
        sample_mode=True if args.sample else base.sample_mode,
        offline=True if args.offline else base.offline,
        principal_id=base.principal_id,
        session_id=base.session_id,
    )
    app = LocalApplication(config)
    if args.smoke:
        health = app.route("GET", "/api/health")
        events = app.route("GET", "/api/events")
        knowledge = app.route("GET", "/api/knowledge")
        if health[0] != 200 or events[0] != 200 or knowledge[0] != 200:
            app.close()
            raise SystemExit("local sample smoke failed")
        print("local sample smoke passed")
        app.close()
        return
    server = create_server(app)
    print(f"Finathink local app listening on http://{server.server_address[0]}:{server.server_address[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.close()


__all__ = ["LocalAppConfig", "LocalApplication", "create_server", "main"]

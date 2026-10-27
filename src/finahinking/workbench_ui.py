"""Shared blue/white presentation; no data, model, or execution side effects."""

OVERVIEW_HEADER_HTML = (
    '<header class="overview-header"><div class="overview-header-copy">'
    '<h1>你的研究，从这里继续。</h1><p class="lede">把问题变成假设，让每一步都有证据。</p>'
    '</div><div class="overview-meta"><span>本地研究工作空间</span>'
    '<span>PAPER-ONLY</span></div></header>'
)

BLUE_WHITE_CSS = """
:root {
  color-scheme: light;
  --background: #f5f8fd; --surface: #fff; --surface-elevated: #f8faff; --surface-raised: #edf4ff;
  --border: #e6ecf4; --border-strong: #b6c7df;
  --text-primary: #172b4d; --text-secondary: #62728a; --text-muted: #62728a;
  --focus: #2563eb; --evidence: #2563eb; --information: #2563eb; --quant-finding: #2563eb;
  --learning: #1746b8; --success: #17806a; --warning: #925b10; --error: #b43e49;
  --fact: #17806a; --interpretation: #1746b8; --hypothesis: #925b10; --unknown: #62728a; --limitation: #b43e49;
  --radius-sm: 8px; --radius-md: 13px; --radius-lg: 13px;
  --shadow-sm: none; --shadow-md: 0 10px 28px #172b4d12;
  --sidebar-width: 224px; --context-width: 292px;
}
html { color-scheme: light; }
body { font: 14px/1.65 "PingFang SC", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
::selection { color: #172b4d; background: #dbe8ff; }
* { scrollbar-color: #b6c7df #f5f8fd; scrollbar-width: thin; }
input, textarea { caret-color: var(--evidence); }
input::placeholder, textarea::placeholder { color: var(--text-secondary); opacity: 1; }
button:disabled { opacity: .55; cursor: not-allowed; }
a:hover { color: #1746b8; }
button, .button-primary, .button { background: #2563eb; color: #fff; }
button:hover, .button-primary:hover, .button:hover { background: #1746b8; }
.button, .button-secondary { display: inline-flex; align-items: center; justify-content: center; gap: 8px; text-decoration: none; }
.button-secondary { min-height: 38px; padding: 8px 12px; background: #fff; color: #38577f; border: 1px solid var(--border-strong); border-radius: 8px; font-size: 12px; }
.button-secondary:hover { background: #edf4ff; color: #1746b8; border-color: #7da6ec; }
[tabindex="0"]:focus-visible { outline: 3px solid var(--focus); outline-offset: 3px; }
input[type=checkbox], input[type=radio] { accent-color: var(--evidence); }
pre { background: #f8faff; }
.app-shell { display: grid; grid-template-columns: var(--sidebar-width) minmax(0, 1fr); min-height: 100dvh; }
.sidebar { display: flex; flex-direction: column; position: sticky; top: 0; height: 100dvh; min-height: 0; overflow-y: auto; padding: 28px 16px 18px; background: #fff; border-right: 1px solid var(--border); }
.brand { gap: 10px; margin: 0 8px 26px; }
.brand-mark { width: 34px; height: 36px; border: 0; border-radius: 9px; background: #2563eb; color: #fff; font: 700 28px/1 Georgia, serif; }
.brand-name { font-family: inherit; font-size: 21px; font-weight: 700; line-height: 1.2; letter-spacing: -.03em; }
.brand-name small { color: #2563eb; font: inherit; }
.brand-caption { display: block; margin-top: 3px; font-size: 9px; font-weight: 500; letter-spacing: .08em; color: var(--text-secondary); }
.space-switch { display: flex; align-items: center; gap: 10px; padding: 10px; margin-bottom: 20px; border: 1px solid var(--border); border-radius: 8px; background: #f8faff; }
.space-symbol { display: grid; place-items: center; width: 28px; height: 28px; background: #e9effb; color: #2563eb; border-radius: 6px; font-weight: 700; }
.space-switch strong { font-size: 12px; font-weight: 600; }
.space-switch small { display: block; font-size: 11px; font-weight: 400; }
.nav-section { margin-bottom: 18px; }
.nav-section-title { margin: 0 0 8px; padding: 0 12px; color: var(--text-secondary); font-size: 11px; letter-spacing: .08em; font-weight: 500; }
.nav-list { display: grid; gap: 4px; }
.nav-link { gap: 11px; min-height: 43px; padding: 10px 12px; font-size: 13px; color: #62728a; border-radius: 8px; }
.nav-link:hover { background: #f5f8fd; color: #172b4d; }
.nav-link[aria-current="page"] { background: #edf4ff; color: #2563eb; box-shadow: none; font-weight: 650; }
.nav-icon { display: block; width: 19px; height: 19px; flex-shrink: 0; fill: none; stroke: currentColor; stroke-width: 1.65; stroke-linecap: round; stroke-linejoin: round; }
.nav-more summary { padding: 8px 12px; cursor: pointer; font-size: 12px; color: var(--text-secondary); }
.nav-more .nav-list { margin-top: 7px; }
.sidebar-note { margin: auto 0 0; padding: 15px 12px; border: 1px solid var(--border); border-radius: 10px; background: #fcfdff; font-size: 11px; }
.sidebar-note strong { display: block; font-size: 12px; font-weight: 500; color: #38577f; margin-bottom: 4px; }
.app-frame { min-width: 0; }
.topbar { display: flex; justify-content: space-between; align-items: center; gap: 16px; height: 76px; min-height: 76px; padding: 0 34px; margin: 0; background: #fff; border-bottom: 1px solid var(--border); }
.topbar-context { display: flex; align-items: center; gap: 12px; min-width: 0; font-size: 12px; color: var(--text-secondary); }
.topbar-context strong { color: var(--text-primary); font-weight: 500; overflow-wrap: anywhere; }
.topbar-actions { display: flex; align-items: center; justify-content: flex-end; flex-wrap: nowrap; gap: 12px; }
.topbar-actions .button { min-height: 36px; padding: 7px 12px; border-radius: 7px; font-size: 12px; }
.local-status { font-size: 11px; color: var(--text-secondary); }
.shell-search-open { min-height: 36px; display: flex; align-items: center; gap: 8px; background: transparent; border: 0; padding: 6px; color: var(--text-secondary); font-size: 12px; }
.shell-search-open:hover { background: #edf4ff; color: #2563eb; }
.shell-search-open kbd { margin-left: 12px; padding: 1px 5px; border: 1px solid var(--border); border-radius: 4px; font-family: inherit; font-size: 11px; }
.shell-icon-button { display: inline-flex; align-items: center; justify-content: center; width: 38px; min-height: 38px; padding: 8px; border: 0; background: transparent; color: var(--text-secondary); }
.shell-icon-button:hover { background: #edf4ff; color: #2563eb; }
.shell-menu, .shell-close, .shell-shade { display: none; }
.workspace-layout { display: grid; grid-template-columns: minmax(0, 1fr) var(--context-width); gap: 23px; max-width: 1740px; margin: auto; padding: 31px 34px 24px; align-items: start; }
.workspace { min-width: 0; padding: 0; }
.workspace-inner { width: 100%; margin: 0; }
.workspace-inner > .eyebrow { display: none; }
h1, h2, h3 { font-family: inherit; }
h1 { max-width: none; font-size: 25px; font-weight: 650; letter-spacing: -.025em; margin-bottom: 8px; line-height: 1.45; }
h2 { font-size: 16px; font-weight: 650; line-height: 1.5; }
h3 { font-size: 14px; font-weight: 600; line-height: 1.5; }
.lede { font-size: 14px; margin-bottom: 24px; }
.card, .panel, .research-panel { padding: 21px 22px; background: #fff; border: 1px solid var(--border); border-radius: 13px; box-shadow: none; }
.card--quiet { background: #f8faff; }
.card--evidence, .result { border: 1px solid #cdddf4; }
.status { font-size: 11px; letter-spacing: 0; border: 0; border-radius: 5px; font-weight: 500; }
.status--learning { color: #1746b8; background: #edf4ff; }
.status--offline, .status--sample, .status--hypothesis { background: #fff7e8; }
.status--complete, .status--ready, .status--fact { background: #edf7f3; }
.status--evidence, .status--quant { background: #edf4ff; }
.status--limitation, .status--unknown { background: #fff0f1; }
.inspector { min-width: 0; padding: 0; background: transparent; border: 0; grid-column: auto; }
.inspector-inner { position: static; padding: 19px 18px; border: 1px solid var(--border); border-radius: 13px; background: #fff; }
.inspector .eyebrow { display: none; }
.inspector h2 { margin-bottom: 12px; font-size: 14px; }
.inspector dt { font-size: 11px; font-weight: 400; letter-spacing: 0; }
.inspector dd { font-size: 12px; }
.inspector .context-card + .context-card { margin-top: 20px; }
.overview-header { align-items: start; margin-bottom: 23px; }
.workspace-layout--overview > .overview-header { grid-column: 1 / -1; margin-bottom: 0; }
.overview-header h1 { font-size: 25px; }
.overview-meta { font-size: 11px; }
.research-intro { padding: 24px 25px 19px; margin-bottom: 20px; border: 1px solid #dce8fc; border-radius: 13px; background: #edf4ff; }
.research-intro h2 { margin: 0 0 7px; font-size: 27px; font-weight: 650; line-height: 1.5; letter-spacing: -.025em; }
.research-intro h2 span { color: #2563eb; }
.research-intro > p { margin: 0; color: #4b6792; font-size: 13px; }
.question-composer { margin-top: 21px; padding: 12px 14px; background: #fff; border: 1px solid #c5d7f0; border-radius: 9px; }
.question-composer:focus-within { border-color: #2563eb; }
.question-composer label { font-size: 12px; font-weight: 500; color: #4b6792; }
.question-composer textarea { border: 0; border-radius: 4px; background: #fff; padding: 9px 0; min-height: 70px; max-height: 240px; font-size: 14px; color: #172b4d; }
.question-composer textarea:focus-visible { outline-offset: 2px; }
.composer-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 9px; }
.composer-footer > span { color: #62728a; font-size: 12px; }
.composer-footer button { display: inline-flex; gap: 7px; align-items: center; min-height: 38px; padding: 8px 12px; white-space: nowrap; font-size: 12px; }
.composer-footer .nav-icon { width: 15px; height: 15px; }
.question-suggestions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 11px; }
.question-suggestions a { padding: 5px 9px; border: 1px solid #b9cfee; border-radius: 5px; background: #f6f9ff; color: #385f97; text-decoration: none; font-size: 11px; }
.question-suggestions a:hover { border-color: #2563eb; background: #fff; }
.metric-note { display: block; margin-top: 4px; color: #62728a; font-size: 11px; }
.context-heading { display: flex; align-items: center; gap: 10px; color: #2563eb; }
.context-heading h2 { color: #172b4d; margin: 0; }
.context-note { color: #62728a; font-size: 12px; line-height: 1.85; }
.research-path { display: grid; gap: 17px; list-style: none; margin: 20px 0; padding: 0; }
.research-path li { display: flex; align-items: center; gap: 10px; font-size: 12px; color: #38577f; }
.context-step { display: grid; place-items: center; width: 20px; height: 20px; flex-shrink: 0; border: 1px solid #b9cfee; border-radius: 50%; color: #2563eb; font-size: 11px; }
.full-width { width: 100%; }
.context-source { display: flex; align-items: center; gap: 10px; margin: 18px 0; }
.context-symbol { display: grid; place-items: center; flex-shrink: 0; width: 32px; height: 32px; border-radius: 6px; background: #f5f8fd; color: #38577f; font: 11px ui-monospace, monospace; }
.context-source strong { font-size: 12px; font-weight: 500; }
.context-source p { margin: 3px 0 0; font-size: 11px; color: #62728a; }
.context-card > a { font-size: 12px; }
.context-learning { list-style: none; padding: 0; margin: 0; }
.context-learning li + li { border-top: 1px solid var(--border); }
.context-learning a { display: flex; align-items: center; gap: 12px; min-height: 55px; text-decoration: none; color: #38577f; font-size: 12px; }
.context-learning span { width: 25px; font: 24px/1.3 Georgia, serif; text-align: center; color: #2563eb; }
.overview-notes { margin-top: 20px; color: #62728a; font-size: 12px; }
.overview-notes summary { cursor: pointer; }
.metric-grid { gap: 0; padding: 18px 0; border: 1px solid var(--border); border-radius: 13px; background: #fff; margin-bottom: 20px; }
.metric-card { padding: 0 20px; border: 0; border-right: 1px solid var(--border); border-radius: 0; box-shadow: none; }
.metric-card:last-child { border-right: 0; }
.metric-card .metric-value { color: #172b4d; font-size: 25px; font-weight: 600; }
.metric-label { font-size: 12px; }
.overview-grid { grid-template-columns: minmax(0, 1fr); gap: 20px; margin-bottom: 20px; }
.overview-region h2 { font-size: 14px; flex-wrap: wrap; margin-bottom: 16px; }
.overview-region h2 span { font-size: 11px; }
.overview-chart { min-height: 210px; border: 0; padding: 0; background: #fff; }
.overview-chart-canvas { width: 100%; height: 210px; min-width: 0; }
.overview-chart-bars i { background: #2563eb; }
.overview-topic-cell { min-height: 52px; cursor: help; color: #1746b8; }
.overview-topic-cell[data-intensity="1"] { background: #f5f8ff; }
.overview-topic-cell[data-intensity="2"] { background: #edf4ff; }
.overview-topic-cell[data-intensity="3"] { background: #e3edff; }
.overview-topic-cell[data-intensity="4"] { background: #d6e5ff; }
.overview-topic-cell[data-intensity="5"] { background: #c4daff; }
.overview-tooltip, .research-tooltip { min-height: 32px; margin-top: 8px; padding: 8px; color: #62728a; border-radius: 8px; background: #f8faff; font-size: 12px; }
.overview-daily-table { margin-top: 12px; max-height: 280px; }
.overview-day { min-height: 36px; padding: 4px; background: transparent; color: #2563eb; white-space: nowrap; }
.overview-day:hover { color: #1746b8; background: #edf4ff; }
.overview-history { margin-top: 14px; }
.overview-history summary { cursor: pointer; color: var(--text-secondary); font-size: 12px; }
.overview-list li, .overview-heatmap li { background: #fff; border-width: 0 0 1px; border-radius: 0; font-size: 12px; overflow-wrap: anywhere; }
.overview-list strong, .overview-heatmap strong { flex-shrink: 0; font-size: 11px; font-weight: 500; }
.overview-links { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.overview-comparison { margin-top: 20px; }
.overview-comparison h2 span { display: block; margin-top: 5px; color: var(--text-secondary); font-size: 11px; font-weight: 400; }
.table-wrap { max-width: 100%; min-width: 0; }
th { font-size: 11px; letter-spacing: 0; text-transform: none; }
td { font-size: 12px; }
.empty-state, .loading-state, .error-state { font-size: 13px; line-height: 1.8; }
.workbench-frame { color: var(--text-primary); border-color: var(--border); background: #fff; border-radius: 13px; }
.workbench-head, .workbench-metrics, .workbench-metric, .workbench-table { border-color: var(--border); }
.workbench-head h2, .workbench-notes h2 { font-family: inherit; font-weight: 600; }
.workbench-inspector, .workbench-preview, .workbench-table { color: var(--text-secondary); border-color: var(--border); background: #f8faff; }
.workbench-inspector { border-left: 1px solid var(--border); }
.workbench-preview input { accent-color: #2563eb; }
.workbench-table tbody tr[aria-current="true"], .workbench-table tbody tr:hover, .workbench-table tbody tr:focus-visible { background: #edf4ff; box-shadow: inset 2px 0 0 #2563eb; }
.workbench-flow { color: var(--text-primary); }
.workbench-flow span { border-color: var(--border); background: #f8faff; }
.workbench-flow i { color: #2563eb; }
.research-inspector, .knowledge-context { border-left: 1px solid var(--border); background: #edf4ff; }
.knowledge-equation, .evidence-item { background: #f8faff; }
.knowledge-code-line:hover, .knowledge-code-line[aria-current="true"], [data-research-point-table] tbody tr:hover, [data-research-point-table] tbody tr:focus-visible { background: #edf4ff; }
[data-research-point-table] tbody tr[aria-current="true"] { background: #edf4ff; box-shadow: inset 2px 0 0 #2563eb; }
.footer-note { font-size: 11px; margin-top: 26px; }
.shell-footer { margin: 0 34px 24px; padding-top: 16px; }
.shell-search-dialog { max-width: 620px; width: calc(100% - 32px); max-height: min(700px, 85dvh); padding: 22px; border: 1px solid var(--border); border-radius: 13px; color: var(--text-primary); background: #fff; box-shadow: 0 20px 70px #172b4d26; }
.shell-search-dialog::backdrop { background: #172b4d55; }
.shell-dialog-heading { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 18px; }
.shell-dialog-heading h2 { margin: 0; }
.shell-search-results { display: grid; margin-top: 12px; max-height: 50dvh; overflow-y: auto; }
.shell-search-result { display: flex; justify-content: space-between; gap: 12px; padding: 12px 8px; border-bottom: 1px solid var(--border); color: #38577f; font-size: 13px; }
.shell-search-result:hover { background: #edf4ff; }
.shell-search-result small { font-size: 11px; }
[hidden] { display: none !important; }
@media (min-width: 1600px) { .topbar { padding-inline: 42px; } .workspace-layout { padding: 35px 42px 24px; } .research-intro { padding: 29px; } .research-intro h2 { font-size: 31px; } }
@media (max-width: 1250px) { :root { --sidebar-width: 200px; --context-width: 255px; } .sidebar { padding-inline: 12px; } .topbar { padding-inline: 24px; } .workspace-layout { padding: 25px 24px; gap: 18px; } .shell-search-open kbd { display: none; } }
@media (max-width: 1050px) { .workspace-layout { grid-template-columns: minmax(0, 1fr); } .inspector { grid-column: auto; } .shell-search-open span, .local-status { display: none; } }
@media (max-width: 800px) {
  body { font-size: 16px; }
  input, textarea, select, .question-composer textarea { font-size: 16px; }
  .nav-link { min-height: 44px; }
  .shell-icon-button { width: 44px; min-width: 44px; min-height: 44px; }
  .composer-footer button { min-height: 44px; }
  .button-secondary { min-height: 44px; }
  .question-suggestions a { display: inline-flex; align-items: center; min-height: 44px; }
  .app-shell { display: block; }
  .sidebar { position: static; height: auto; min-height: 0; width: auto; padding: 18px; border-bottom: 1px solid var(--border); }
  .sidebar .nav-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .nav-section-title, .sidebar-note { display: block; }
  .topbar { min-height: 66px; height: 66px; padding: 0 18px; gap: 10px; }
  .topbar-context { gap: 7px; font-size: 12px; }
  .topbar-context > span { display: none; }
  .topbar-actions { gap: 5px; }
  .topbar-actions .button { min-height: 44px; padding: 7px 10px; }
  .workspace-layout { padding: 22px 18px; gap: 20px; }
  .shell-footer { margin-inline: 18px; }
  [data-shell-enhanced="true"] .sidebar { position: fixed; inset: 0 auto 0 0; z-index: 50; width: 224px; height: 100dvh; background: #fff; transform: translateX(-100%); visibility: hidden; transition: transform .2s cubic-bezier(.16,1,.3,1); }
  [data-shell-enhanced="true"] .sidebar .nav-list { grid-template-columns: minmax(0, 1fr); }
  [data-shell-enhanced="true"] .shell-menu, [data-shell-enhanced="true"] .shell-close { display: inline-flex; }
  .shell-close { align-self: flex-end; margin-bottom: 8px; }
  [data-navigation-open="true"] .sidebar { visibility: visible; transform: translateX(0); }
  [data-navigation-open="true"] .shell-shade { display: block; position: fixed; inset: 0; z-index: 45; background: #172b4d55; width: 100%; padding: 0; border: 0; border-radius: 0; }
  .brand { margin-bottom: 18px; }
  .overview-header h1, h1 { font-size: 23px; }
  .overview-meta { display: none; }
  .hero, .grid, .research-grid, .settings-grid, .workbench-grid, .workbench-notes { grid-template-columns: minmax(0, 1fr); }
  .workbench-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 560px) {
  .research-intro { padding: 20px 17px; }
  .research-intro h2 { font-size: 25px; }
  .research-intro h2 span { display: block; }
  .question-composer { padding: 11px; }
  .composer-footer { align-items: flex-end; }
  .composer-footer > span { max-width: 12em; font-size: 11px; }
  .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); row-gap: 18px; }
  .metric-card:nth-child(2) { border-right: 0; }
  .metric-card { padding: 0 16px; }
  .card, .panel, .research-panel { padding: 19px 17px; }
  .overview-links { grid-template-columns: minmax(0, 1fr); }
  .topbar-context strong { max-width: 10em; }
}
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation: none !important; transition: none !important; scroll-behavior: auto !important; } }
"""


def nav_icon(name: str) -> str:
    paths = {
        "overview": '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
        "codex": '<path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-13-2 16"/>',
        "events": '<path d="M4 5h16v15H4zM8 3v4m8-4v4M4 10h16m-12 4h3m-3 3h7"/>',
        "knowledge": '<path d="M12 5v15m0-15C8 2 4 3 3 4v15c3-2 6-1 9 1 3-2 6-3 9-1V4c-3-2-6-1-9 1Z"/>',
        "factor": '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="12" cy="18" r="2.5"/><path d="M8.5 6h7M7 8l4 7.5m6-7.5-4 7.5"/>',
        "strategy": '<path d="M3 18h18M5 15l4-5 4 3 6-9m-4 0h4v4"/>',
        "report": '<path d="M6 3h9l4 4v14H6zM14 3v5h5M9 12h7m-7 4h7"/>',
        "data": '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 4 16 4 16 0V5M4 12v7c0 4 16 4 16 0v-7"/>',
        "search": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
        "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
        "close": '<path d="m6 6 12 12M6 18 18 6"/>',
        "arrow": '<path d="M4 12h15m-5-5 5 5-5 5"/>',
        "system": '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/>',
    }
    return f'<svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true">{paths.get(name, paths["system"])}</svg>'


def blue_white_report_css():
    """Share the actual AppShell tokens without its navigation geometry."""
    tokens = BLUE_WHITE_CSS[:BLUE_WHITE_CSS.index("}") + 1]
    return tokens + """
html{color-scheme:light;scroll-behavior:auto}body{margin:0;background:var(--background);color:var(--text-primary);font:14px/1.75 "PingFang SC","Microsoft YaHei",-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;overflow-wrap:anywhere;max-width:none;padding:0}
main{max-width:1060px;margin:0 auto;padding:40px clamp(18px,4vw,48px) 64px}h1{font-size:28px;line-height:1.5;margin:0 0 12px;letter-spacing:-.025em}h2{font-size:20px;margin:32px 0 14px}h3{font-size:15px;margin:24px 0 10px}p{max-width:78ch}a{color:var(--evidence);text-underline-offset:3px}
.report-header{padding:28px;background:var(--surface-raised);border:1px solid var(--border);border-radius:13px}.report-status{color:var(--learning);font-weight:600}.report-toc{display:flex;gap:14px;flex-wrap:wrap;padding:20px 0;border-bottom:1px solid var(--border)}
.report-section{padding:12px 0 28px;border-bottom:1px solid var(--border)}.report-meta{display:grid;grid-template-columns:150px minmax(0,1fr);gap:10px 16px}.report-meta dt{color:var(--text-secondary)}.report-meta dd{margin:0;font-family:ui-monospace,monospace;font-size:12px;overflow-wrap:anywhere}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;margin:16px 0}th,td{text-align:left;vertical-align:top;border-bottom:1px solid var(--border);padding:10px 12px;overflow-wrap:anywhere}th{color:var(--text-secondary);font-weight:500}caption{text-align:left;color:var(--text-secondary);padding:8px 0}pre,code{font-family:ui-monospace,monospace;font-size:12px}pre{padding:16px;border:1px solid var(--border);background:var(--surface);border-radius:8px;white-space:pre-wrap;overflow-wrap:anywhere;max-height:440px;overflow:auto}
.report-stages{padding:0;list-style:none}.report-stage{padding:20px 0;border-bottom:1px solid var(--border)}.report-stage h3{margin:0 0 8px}.stage-COMPLETE{color:var(--success)}.stage-BLOCKED_DATA{color:var(--warning)}.stage-EXECUTION_ERROR{color:var(--error)}.stage-NOT_RUN,.meta{color:var(--text-secondary)}details{margin:14px 0}summary{cursor:pointer;min-height:32px;color:var(--evidence)}.note{border:0;padding:0;color:var(--warning)}svg{max-width:100%;height:auto}.grid li{border-color:var(--border);border-radius:8px;background:var(--surface)}
:focus-visible{outline:2px solid var(--focus);outline-offset:3px}::selection{background:#dbe8ff;color:var(--text-primary)}*{scrollbar-color:var(--border-strong) var(--background);scrollbar-width:thin}
@media(max-width:600px){main{padding:24px 18px 40px}.report-header{padding:20px}.report-meta{grid-template-columns:minmax(0,1fr);gap:4px}.report-meta dd{margin-bottom:10px}th,td{padding:8px 6px}h1{font-size:23px}}@media print{body{background:white}main{max-width:none;padding:0}details{break-inside:avoid}.report-header{background:white}nav{display:none}}
"""


def shell_search_dialog() -> str:
    return (
        '<dialog class="shell-search-dialog" id="shell-search" aria-labelledby="shell-search-title">'
        '<div class="shell-dialog-heading"><h2 id="shell-search-title">搜索功能入口</h2>'
        f'<button type="button" class="shell-icon-button" data-shell-search-close aria-label="关闭入口搜索">{nav_icon("close")}</button></div>'
        '<label for="shell-search-input">想去哪里？</label>'
        '<input type="search" id="shell-search-input" data-shell-search-input maxlength="80" autocomplete="off" placeholder="研究任务、因子实验、数据接口…">'
        '<p class="field-help">搜索本地功能入口，不读取私人研究内容，也不调用模型。</p>'
        '<div class="shell-search-results" data-shell-search-results></div>'
        '<p data-shell-search-status role="status" aria-live="polite"></p></dialog>'
    )

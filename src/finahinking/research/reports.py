"""Escaped, content-addressed HTML report bundles for research runs."""

from __future__ import annotations

import hashlib
import html
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .contracts import ReportManifest, ResearchRunResult, RunEvent, stable_digest, to_jsonable
from .report_sections import (
    build_method_factor_sections,
    render_factor_report_document,
    theme_report_document,
)


def method_handoff_activity(package, status):
    """Digest/count-only facts; external response acceptance is not a backtest."""
    payload = package.context.to_dict()
    methods = payload["method_context"]["manifest"]
    return {
        "schema_version": "method-activity.v1", "status": status,
        "task_id": package.envelope.task_id, "package_digest": package.package_digest,
        "context_digest": package.context.fingerprint,
        "context_bytes": len(package.context._json.encode("utf-8")),
        "methods": [{"skill_id": row["skill_id"], "version": row["version"], "reason": row["reason"]}
                    for row in methods["methods"]],
        "resource_count": len(methods["loaded"]), "input_gaps": methods["input_gaps"],
        "external_response": "WAITING" if status == "EXTERNAL_HANDOFF_REQUIRED" else "RECEIVED",
        "deterministic_execution": "NOT_RUN", "automatic_codex_driver": "NOT_VERIFIED",
        "paper_only": True,
    }


def method_factor_activity(validation_run, method_binding):
    """Project only facts from the same frozen run and explicit method lineage."""
    from .factor_validation import FactorValidationRun

    run = validation_run if isinstance(validation_run, FactorValidationRun) else FactorValidationRun.from_dict(validation_run)
    payload = run.to_dict()
    return {"schema_version": "method-factor-activity.v1", "validation_fingerprint": run.fingerprint,
        "binding_digest": payload["binding_digest"], "status": payload["status"],
        "method_package_digest": method_binding["package_digest"],
        "method_manifest_digest": method_binding["method_manifest_digest"],
        "snapshot_ref": payload["bindings"]["snapshot_ref"], "attachment_ref": payload["bindings"]["attachment_ref"],
        "completed_stages": sum(row["status"] == "COMPLETE" for row in payload["steps"]),
        "total_stages": len(payload["steps"]), "reproduction_scope": payload["reproduction_scope"],
        "requires_human_admission": True, "paper_only": True}

_SECRET_TEXT = re.compile(r"(?i)(?:api[_-]?key|token|secret|password|endpoint)\s*[=:]\s*[^\s<]+")
_ABSOLUTE_PATH = re.compile(r"(?:/(?:Users|home|tmp|var|private|etc|opt|root|Volumes|Applications|Library)(?:/[^\s<]+)+|[A-Za-z]:[\\/][^\s<]+)")
_HTML_HANDLER = re.compile(r"(?i)\bon[a-z]+\s*=")
_PUBLIC_SENSITIVE_KEY = re.compile(
    r"(?i)(?:api[_-]?key|token|secret|password|credential(?![_-]?ref)|authorization|prompt|raw[_-]?(?:provider[_-]?)?response|endpoint|absolute[_-]?path|file[_-]?path)"
)
_REQUIRED_ANALYSTS = ("fundamentals", "technical", "sentiment", "news", "learning")
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
_REQUIRED_SECTIONS = ("2_evidence", "3_research", "4_quant", "5_risk", "6_paper_decision")
_RUNTIME_SECTIONS = ("experiments", "risk_attribution", "learning_history")
_RUNTIME_STAGE_MARKERS = {
    "experiments": "QUANT_VALIDATION",
    "risk_attribution": "RISK_REVIEW",
    "learning_history": "LEARNING_RECORDED",
}
_BLOCKED_RUNTIME_STATES = {
    "NO_DATA_AVAILABLE",
    "DATA_UNAVAILABLE",
    "FAILED",
    "VALIDATION_FAILED",
    "PROVIDER_NOT_CONFIGURED",
    "CANCELLED",
}


def _scrub(value: Any) -> Any:
    if is_dataclass(value):
        value = to_jsonable(value)
    return redact_public_payload(value)


def render_section_html(
    title: str,
    payload: Mapping[str, Any] | Any,
    evidence_refs: Sequence[str] = (),
    limitations: Sequence[str] = (),
) -> str:
    safe_title = html.escape(_scrub(str(title)), quote=True)
    safe_payload = html.escape(
        json.dumps(_scrub(payload), ensure_ascii=False, sort_keys=True, indent=2, default=str),
        quote=False,
    )
    evidence_html = "".join(f"<li>{html.escape(_scrub(str(ref)), quote=True)}</li>" for ref in evidence_refs)
    limitations_html = "".join(f"<li>{html.escape(_scrub(str(item)), quote=True)}</li>" for item in limitations)
    return theme_report_document(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{safe_title}</title></head><body><main>"
        f"<h1>{safe_title}</h1><h2>Findings</h2><pre>{safe_payload}</pre>"
        f"<h2>Evidence</h2><ul>{evidence_html or '<li>None recorded</li>'}</ul>"
        f"<h2>Limitations</h2><ul>{limitations_html or '<li>None recorded</li>'}</ul>"
        "</main></body></html>"
    )


def render_workbench_report_html(payload: Mapping[str, Any], explanation: Mapping[str, Any] | None = None) -> str:
    """Render a self-contained research publication for the workbench payload."""

    safe_payload = _scrub(payload)
    safe_explanation = _scrub(explanation or payload.get("explanation", {}))
    esc = lambda value: html.escape(str(value if value is not None else "—"), quote=True)
    metrics = safe_payload.get("metrics", {}) if isinstance(safe_payload, Mapping) else {}
    provenance = safe_payload.get("provenance", {}) if isinstance(safe_payload, Mapping) else {}
    points = list(safe_payload.get("points", ())) if isinstance(safe_payload, Mapping) else []
    pc = safe_explanation.get("parameter_change", {}) if isinstance(safe_explanation, Mapping) else {}
    limitation_items = "".join(f"<li>{esc(item)}</li>" for item in safe_payload.get("limitations", ())) or "<li>No additional limitation was recorded.</li>"
    metric_items = "".join(
        f'<div class="metric"><span class="metric-value">{esc(round(value, 6) if isinstance(value, float) else value)}</span><span class="metric-label">{esc(str(key).replace("_", " "))}</span></div>'
        for key, value in list(metrics.items())[:6]
    )
    weights = [float(point.get("final_weight", 0)) for point in points if isinstance(point.get("final_weight", 0), (int, float))]
    max_weight = max(weights or [1.0])
    coords = " ".join(f"{28 + index * (744 / max(1, len(weights) - 1)):.1f},{188 - (value / max_weight) * 138:.1f}" for index, value in enumerate(weights))
    line = f'<polyline points="{coords}" fill="none" stroke="#c36e48" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />' if coords else ""
    timeline_rows = "".join(
        f'<tr><th scope="row">{esc(point.get("time"))}</th><td>{esc(point.get("instrument"))}</td><td>{esc(point.get("score"))}</td><td>{esc(point.get("final_weight"))}</td><td>{esc(point.get("risk_state"))}</td><td>{esc(point.get("trade_weight"))}</td><td>{esc(point.get("net_return"))}</td></tr>'
        for point in points
    ) or '<tr><td colspan="7">No points were returned.</td></tr>'
    factor_research = safe_payload.get("factor_research", {}) if isinstance(safe_payload, Mapping) else {}
    factor_experiments = safe_payload.get("factor_experiments", {}) if isinstance(safe_payload, Mapping) else {}
    factor_rows = "".join(
        f'<tr><th scope="row">{esc(item.get("candidate", {}).get("candidate_id"))}</th><td><code>{esc(item.get("candidate", {}).get("expression"))}</code></td><td>{esc(item.get("evaluation", {}).get("status"))}</td><td>{esc(item.get("evaluation", {}).get("information_coefficient"))}</td><td>{esc(item.get("evaluation", {}).get("oos_status"))}</td><td>{esc(item.get("admission", {}).get("status"))}</td></tr>'
        for item in factor_research.get("rounds", ())
        if isinstance(item, Mapping)
    ) or '<tr><td colspan="6">No factor candidates were evaluated.</td></tr>'
    experiment_rows = "".join(
        f'<tr><th scope="row">{esc(item.get("experiment_id"))}</th><td>{esc(item.get("parameters"))}</td><td>{esc(item.get("ic"))}</td><td>{esc(item.get("ir"))}</td><td>{esc(item.get("turnover"))}</td><td>{esc(item.get("decay"))}</td></tr>'
        for item in factor_experiments.get("experiments", ()) if isinstance(item, Mapping)
    ) or '<tr><td colspan="6">No parameter experiments were evaluated.</td></tr>'
    experiment_section = f'''<section class="section"><div class="section-head"><h2>Factor parameter experiments</h2><p>Finite PIT/OOS diagnostics are descriptive and carry their fingerprints and limitations.</p></div><div class="table-wrap"><table><thead><tr><th>Experiment</th><th>Parameters</th><th>IC</th><th>IR</th><th>Turnover</th><th>Decay</th></tr></thead><tbody>{experiment_rows}</tbody></table></div><p class="note">{esc("; ".join(factor_experiments.get("limitations", ())) or "paper-only; no trade advice")}</p></section>'''
    factor_section = f'''<section class="section"><div class="section-head"><h2>Factor research ledger</h2><p>Bounded candidate templates keep expression, evidence, decay and OOS visibility together.</p></div><div class="table-wrap"><table><thead><tr><th>Candidate</th><th>Expression</th><th>Status</th><th>IC</th><th>OOS</th><th>Admission</th></tr></thead><tbody>{factor_rows}</tbody></table></div><p class="note">{esc(factor_research.get("boundary", "paper-only factor evidence"))}</p></section>{experiment_section}'''
    provider_status = safe_payload.get("provider_status", {}) if isinstance(safe_payload, Mapping) else {}
    provider_rows = "".join(
        f'<tr><th scope="row">{esc(item.get("provider"))}</th><td>{esc(item.get("model"))}</td><td>{esc("READY" if item.get("configured") else "NOT CONFIGURED")}</td><td>{esc(item.get("reason"))}</td></tr>'
        for item in provider_status.get("providers", ())
        if isinstance(item, Mapping)
    ) or '<tr><td colspan="4">No provider status was attached.</td></tr>'
    provider_section = f'''<section class="section"><div class="section-head"><h2>Provider readiness</h2><p>Only configured state and credential references are reportable; secret values stay outside the artifact.</p></div><div class="table-wrap"><table><thead><tr><th>Provider</th><th>Model</th><th>Status</th><th>Reason</th></tr></thead><tbody>{provider_rows}</tbody></table></div></section>'''
    traces = safe_explanation.get("traces", {}) if isinstance(safe_explanation, Mapping) else {}
    trace_sections = "".join(
        f'<article class="trace"><h3>{esc(name)}</h3><dl><dt>Concept</dt><dd>{esc(trace.get("concept"))}</dd><dt>Mathematics</dt><dd class="formula">{esc(trace.get("math"))}</dd><dt>Code mapping</dt><dd><code>{esc(trace.get("code"))}</code></dd><dt>Financial meaning</dt><dd>{esc(trace.get("finance"))}</dd><dt>Result and boundary</dt><dd>{esc(trace.get("result"))}</dd></dl></article>'
        for name, trace in traces.items()
        if isinstance(trace, Mapping)
    )
    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Offline factor and strategy research workbench report.">
  <title>Factor / strategy workbench · {esc(safe_payload.get("run_id", "research"))}</title>
  <style>
    :root {{ color-scheme: light; --ink:#173137; --muted:#617174; --paper:#f0ede5; --surface:#fbfaf6; --rule:#c9c2b5; --teal:#2b666b; --ember:#c36e48; --sage:#87aa9c; --violet:#71648a; }}
    * {{ box-sizing:border-box; }}
    html {{ background:var(--paper); }}
    body {{ margin:0; color:var(--ink); background:var(--paper); font:15px/1.6 "Avenir Next","Helvetica Neue",Arial,sans-serif; }}
    a {{ color:var(--teal); }}
    .page {{ width:min(1220px,100%); margin:auto; padding:42px clamp(20px,5vw,76px) 70px; }}
    .masthead {{ display:flex; justify-content:space-between; gap:24px; align-items:flex-start; border-bottom:1px solid var(--ink); padding-bottom:14px; }}
    .mark {{ font-family:Georgia,serif; font-size:1.15rem; letter-spacing:-.04em; }}
    .mast-meta {{ color:var(--muted); font-size:.78rem; text-align:right; }}
    .hero {{ display:grid; grid-template-columns:minmax(0,1.15fr) minmax(260px,.85fr); gap:42px; padding:60px 0 44px; border-bottom:1px solid var(--rule); }}
    .kicker {{ margin:0 0 14px; color:var(--ember); font-size:.78rem; font-weight:700; letter-spacing:.05em; }}
    h1,h2,h3 {{ line-height:1.12; letter-spacing:-.025em; }}
    h1 {{ max-width:14ch; margin:0 0 18px; font-family:Georgia,"Times New Roman",serif; font-size:clamp(2.8rem,7vw,6.3rem); font-weight:500; }}
    h2 {{ margin:0 0 14px; font-size:1.45rem; }} h3 {{ margin:0 0 10px; font-size:1.05rem; }}
    .dek {{ max-width:54ch; margin:0; color:var(--muted); font-size:1.15rem; }}
    .boundary {{ align-self:end; padding:18px 0 0 24px; border-left:4px solid var(--ember); }}
    .boundary strong {{ display:block; margin-bottom:6px; font-family:Georgia,serif; font-size:1.2rem; font-weight:500; }}
    .boundary p {{ margin:0; color:var(--muted); }}
    .status {{ display:inline-flex; margin-bottom:16px; padding:4px 9px; border:1px solid var(--teal); border-radius:99px; color:var(--teal); font-size:.7rem; font-weight:700; letter-spacing:.08em; }}
    .section {{ padding:34px 0; border-bottom:1px solid var(--rule); }}
    .section-head {{ display:flex; justify-content:space-between; align-items:baseline; gap:20px; margin-bottom:20px; }}
    .section-head p {{ max-width:46ch; margin:0; color:var(--muted); font-size:.92rem; }}
    .metrics {{ display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); border-top:1px solid var(--ink); border-bottom:1px solid var(--ink); }}
    .metric {{ min-height:104px; padding:15px 14px; border-right:1px solid var(--rule); }} .metric:last-child {{ border-right:0; }}
    .metric-value {{ display:block; font-size:1.42rem; font-variant-numeric:tabular-nums; }} .metric-label {{ display:block; margin-top:5px; color:var(--muted); font-size:.73rem; text-transform:capitalize; }}
    .plot {{ border:1px solid var(--rule); background:var(--surface); }} .plot svg {{ display:block; width:100%; height:auto; }} .plot-caption {{ display:flex; justify-content:space-between; gap:12px; padding:10px 14px; color:var(--muted); font-size:.78rem; }}
    .chain {{ display:grid; grid-template-columns:repeat(4,1fr); border-top:1px solid var(--ink); border-bottom:1px solid var(--ink); }} .chain div {{ min-height:110px; padding:16px; border-right:1px solid var(--rule); }} .chain div:last-child {{ border-right:0; }} .chain b {{ display:block; margin-bottom:8px; color:var(--ember); font-family:Georgia,serif; font-size:1.08rem; font-weight:500; }} .chain span {{ color:var(--muted); font-size:.85rem; }}
    .two-col {{ display:grid; grid-template-columns:minmax(0,1.15fr) minmax(260px,.85fr); gap:32px; }} .note {{ padding:18px; border:1px solid var(--rule); background:rgba(251,250,246,.7); }} .note p:last-child {{ margin-bottom:0; }} .note dl {{ margin:0; }} .note dt {{ margin-top:12px; color:var(--muted); font-size:.72rem; }} .note dd {{ margin:1px 0 0; overflow-wrap:anywhere; }}
    .table-wrap {{ overflow:auto; border-top:1px solid var(--ink); border-bottom:1px solid var(--ink); }} table {{ width:100%; min-width:720px; border-collapse:collapse; font-variant-numeric:tabular-nums; }} th,td {{ padding:10px 9px; border-bottom:1px solid var(--rule); text-align:left; vertical-align:top; }} thead th {{ color:var(--muted); font-size:.72rem; font-weight:600; text-transform:capitalize; }} tbody tr:last-child th,tbody tr:last-child td {{ border-bottom:0; }}
    .traces {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:18px; }} .trace {{ padding:18px; border:1px solid var(--rule); background:var(--surface); }} .trace dl {{ margin:0; }} .trace dt {{ margin-top:10px; color:var(--teal); font-size:.73rem; font-weight:700; }} .trace dd {{ margin:1px 0 0; color:var(--muted); }} .trace .formula {{ color:var(--ink); font-family:Georgia,serif; font-size:1.04rem; }} code {{ overflow-wrap:anywhere; color:var(--violet); }}
    .limitations {{ columns:2; margin:0; padding-left:20px; color:var(--muted); }} .footer {{ display:flex; justify-content:space-between; gap:18px; padding-top:20px; color:var(--muted); font-size:.78rem; }}
    @media (max-width:850px) {{ .hero,.two-col {{ grid-template-columns:1fr; gap:24px; }} .metrics {{ grid-template-columns:repeat(2,1fr); }} .metric:nth-child(2n) {{ border-right:0; }} .metric {{ border-bottom:1px solid var(--rule); }} .chain {{ grid-template-columns:repeat(2,1fr); }} .chain div:nth-child(2n) {{ border-right:0; }} .traces {{ grid-template-columns:1fr; }} .limitations {{ columns:1; }} .masthead {{ display:block; }} .mast-meta {{ margin-top:8px; text-align:left; }} }}
    @media (prefers-reduced-motion:reduce) {{ * {{ scroll-behavior:auto !important; }} }}
    @media print {{ body {{ background:#fff; }} .page {{ padding:0; }} .section {{ break-inside:avoid; }} }}
  </style>
</head>
<body>
<main class="page">
  <header class="masthead"><div class="mark">Finathink / research notes</div><div class="mast-meta">Offline artifact · {esc(provenance.get("dataset_fingerprint", "dataset not attached"))}<br>Run {esc(safe_payload.get("run_id", "—"))}</div></header>
  <section class="hero"><div><p class="kicker">Factor / strategy workbench</p><span class="status">PAPER-ONLY · HISTORICAL EVIDENCE</span><h1>What changed when the rule changed?</h1><p class="dek">A readable record of signal, capital, risk and execution. Each number keeps its timestamp, policy version and limitation beside it.</p></div><aside class="boundary"><strong>Research question</strong><p>Test whether a slower signal would reduce noise and turnover. This artifact describes a bounded replay; it does not issue an order or promise a future return.</p></aside></section>
  <section class="section"><div class="section-head"><h2>At a glance</h2><p>Metrics are descriptive outputs from the frozen local replay.</p></div><div class="metrics">{metric_items}</div></section>
  <section class="section"><div class="section-head"><h2>Signal → position → risk → execution</h2><p>The four layers stay distinct so a result can be explained, challenged and replayed.</p></div><div class="chain"><div><b>Signal</b><span>Factor score and point-in-time availability.</span></div><div><b>Position</b><span>Declared mapping, caps, cash buffer and turnover budget.</span></div><div><b>Risk</b><span>State, scale, reasons and allowed actions.</span></div><div><b>Execution</b><span>Delayed holding, trade, fee and slippage.</span></div></div></section>
  <section class="section two-col"><div><div class="section-head"><h2>Weight timeline</h2><p>Final weight is server-computed; the line is a visual aid, not a second calculation.</p></div><div class="plot"><svg viewBox="0 0 800 220" role="img" aria-label="Research timeline"><title>Research timeline of final weights</title><line x1="28" y1="188" x2="772" y2="188" stroke="#c9c2b5"/><line x1="28" y1="50" x2="28" y2="188" stroke="#c9c2b5"/>{line}</svg><div class="plot-caption"><span>earlier observations</span><span>later observations</span></div></div></div><aside class="note"><h3>Frozen identity</h3><dl><dt>Dataset</dt><dd><code>{esc(provenance.get("dataset_fingerprint"))}</code></dd><dt>Strategy</dt><dd><code>{esc(provenance.get("strategy_fingerprint"))}</code></dd><dt>Policy</dt><dd><code>{esc(provenance.get("policy_fingerprint"))}</code></dd><dt>Attribution</dt><dd>{esc(pc.get("attribution", {}).get("status", "NOT_ATTACHED"))}</dd></dl></aside></section>
  <section class="section"><div class="section-head"><h2>Point-by-point audit</h2><p>Keyboard-readable table for the same values shown in the timeline.</p></div><div class="table-wrap"><table><thead><tr><th scope="col">Time</th><th scope="col">Instrument</th><th scope="col">Score</th><th scope="col">Final weight</th><th scope="col">Risk state</th><th scope="col">Trade</th><th scope="col">Net return</th></tr></thead><tbody>{timeline_rows}</tbody></table></div></section>
  {factor_section}
  {provider_section}
  <section class="section"><div class="section-head"><h2>Algorithm notes</h2><p>Each note links a concept to a formula, a controlled code boundary and a financial interpretation.</p></div><div class="traces">{trace_sections}</div></section>
  <section class="section two-col"><div><h2>Parameter change</h2><p>{esc(pc.get("intent", "No parameter change explanation was attached."))}</p><p><strong>Before:</strong> <code>{esc(pc.get("formula_before"))}</code><br><strong>After:</strong> <code>{esc(pc.get("formula_after"))}</code></p><p>{esc(pc.get("next_experiment", safe_explanation.get("next_experiment", "Keep one variable fixed before the next replay.")))}</p></div><aside class="note"><h3>What remains unknown</h3><ul class="limitations">{limitation_items}</ul></aside></section>
  <footer class="footer"><span>Finathink · local-first research</span><span>Paper-only · no broker · no network dependency</span></footer>
</main>
</body>
</html>'''


@dataclass(frozen=True, slots=True)
class BundleVerification:
    ok: bool
    errors: tuple[str, ...] = ()


def _digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_manifest(manifest: ReportManifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(redact_public_payload(manifest), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if len(payload.encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise ValueError("research manifest size limit exceeded")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def append_event(event: RunEvent, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(redact_public_payload(event), ensure_ascii=False, sort_keys=True) + "\n")


class ReportBundleWriter:
    def __init__(self, output_root: str | Path | None = None) -> None:
        self.output_root = Path(output_root) if output_root is not None else None

    def write_runtime_tree(self, run_result: ResearchRunResult, output_root: str | Path | None = None) -> ReportManifest:
        """Write the complete report tree, including read-only runtime stage pages."""

        root = output_root or self.output_root or (Path.cwd() / "reports")
        return self.write(run_result, root)

    def write(
        self,
        result: ResearchRunResult,
        output_root: str | Path,
        *,
        workbench_payload: Mapping[str, Any] | None = None,
        explanation_package: Mapping[str, Any] | None = None,
        quant_analytics: Mapping[str, Any] | Any | None = None,
    ) -> ReportManifest:
        bundle = Path(output_root) / result.state.run_id
        if quant_analytics is not None:
            if hasattr(quant_analytics, "to_dict") and callable(quant_analytics.to_dict):
                quant_analytics = quant_analytics.to_dict()
            elif not isinstance(quant_analytics, Mapping):
                raise TypeError("quant_analytics must be a mapping or to_dict object")
        factor_sections = None
        raw_study = quant_analytics.get("factor_study") if quant_analytics else None
        if isinstance(raw_study, Mapping) and isinstance(raw_study.get("validation_run"), Mapping):
            binding = raw_study.get("charter", {}).get("method_binding", {})
            # Verify the private, immutable methods BEFORE generic public text
            # redaction.  Public reports retain the manifest, not method bodies.
            factor_sections = build_method_factor_sections(raw_study["validation_run"], binding.get("loaded_methods"),
                _scrub(raw_study.get("factor_evidence")), _scrub(raw_study.get("factor_lifecycle")))
            if binding.get("loaded_methods") is not None and (
                binding.get("methods_digest") != factor_sections.metadata["methods_digest"]
                or binding.get("method_manifest_digest") != factor_sections.metadata["method_manifest_digest"]):
                raise ValueError("frozen method summary does not match loaded methods")
        analytics_payload = _scrub(quant_analytics) if quant_analytics is not None else None
        study = analytics_payload.get("factor_study") if analytics_payload else None
        if factor_sections is not None and isinstance(study.get("charter", {}).get("method_binding"), dict):
            public_binding = study["charter"]["method_binding"]
            loaded = public_binding.pop("loaded_methods", None)
            if loaded is not None:
                public_binding["method_manifest_snapshot"] = loaded["manifest"]
        inputs = {"state": stable_digest(result.state), "decision": stable_digest(result.decision),
            "events": stable_digest(result.events), "analytics": stable_digest(analytics_payload),
            "workbench": stable_digest(_scrub(workbench_payload)), "explanation": stable_digest(_scrub(explanation_package))}
        existing_path = bundle / "manifest.json"
        if existing_path.exists():
            verification = verify_report_bundle(existing_path)
            if not verification.ok:
                raise ValueError("published report is corrupt; restore it instead of overwriting")
            previous = json.loads(existing_path.read_text(encoding="utf-8"))
            snapshot = previous["source_snapshot"]
            original_inputs = snapshot.get("report_input_digests")
            if original_inputs is not None:
                required = {"state", "decision", "events"}
                required.update(key for key, value in (("analytics", quant_analytics), ("workbench", workbench_payload),
                    ("explanation", explanation_package)) if value is not None)
                unchanged = all(original_inputs.get(key) == inputs[key] for key in required)
            else:
                unchanged = (snapshot.get("state_digest") == inputs["state"]
                    and snapshot.get("decision_digest") == (inputs["decision"] if result.decision else None)
                    and workbench_payload is None and explanation_package is None)
                if quant_analytics is not None:
                    old_analytics = bundle / "4_quant" / "analytics.json"
                    unchanged = unchanged and old_analytics.exists() and old_analytics.stat().st_size <= MAX_MANIFEST_BYTES
                    if unchanged:
                        unchanged = stable_digest(json.loads(old_analytics.read_text(encoding="utf-8"))) == inputs["analytics"]
            if not unchanged or previous["run_id"] != result.state.run_id:
                raise FileExistsError("published reports are immutable; use a new run ID for changed inputs")
            return ReportManifest(**previous)
        bundle.mkdir(parents=True, exist_ok=True)
        analyst_by_role = {report.role: report for report in result.state.analyst_reports}

        complete_sections: list[str] = []
        for role in _REQUIRED_ANALYSTS:
            report = analyst_by_role.get(role)
            if report is None:
                html_text = render_section_html(
                    role,
                    {"status": "UNAVAILABLE", "role": role},
                    limitations=("optional or unconfigured analyst did not return a report",),
                )
            else:
                html_text = render_section_html(
                    role,
                    {"status": report.status, "claims": report.claims, "model_ref": report.model_ref},
                    report.evidence_refs,
                    report.limitations,
                )
            path = bundle / "1_analysts" / f"{role}.html"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(html_text, encoding="utf-8")
            complete_sections.append(html_text)

        section_payload = {
            "state": result.state.current_state.value,
            "state_history": result.state.state_history,
            "decision_eligible": result.state.decision_eligible,
            "decision": result.decision,
        }
        for section in _REQUIRED_SECTIONS:
            path = bundle / section / "index.html"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                render_section_html(section, section_payload, result.decision.evidence_refs if result.decision else (), result.decision.limitations if result.decision else ()),
                encoding="utf-8",
            )

        if workbench_payload is not None:
            workbench_path = bundle / "workbench" / "index.html"
            workbench_path.parent.mkdir(parents=True, exist_ok=True)
            workbench_path.write_text(theme_report_document(render_workbench_report_html(workbench_payload, explanation_package)), encoding="utf-8")

        # Analytics are computed on the server and persisted as a redacted,
        # content-addressed snapshot.  The HTML only displays these values.
        if analytics_payload is not None:
            analytics_path = bundle / "4_quant" / "analytics.json"
            analytics_path.parent.mkdir(parents=True, exist_ok=True)
            analytics_path.write_text(json.dumps(analytics_payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            quant_index = bundle / "4_quant" / "index.html"
            quant_index.write_text(render_section_html("4_quant", analytics_payload, limitations=analytics_payload.get("limitations", ())), encoding="utf-8")

        runtime_payloads = _runtime_report_payloads(result)
        for section, payload in runtime_payloads.items():
            path = bundle / section / "index.html"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_render_runtime_section_html(result.state.run_id, section, payload), encoding="utf-8")

        # The complete report starts with the same server-owned status facts as
        # the local UI.  It remains useful without JavaScript and never performs
        # client-side metric calculations.
        from .ui import render_status_wall_html, research_view_model

        status_snapshot = _status_snapshot(result)
        status_manifest = {
            "run_id": result.state.run_id,
            "schema_version": "research-report.v1",
            "files": {},
            "source_snapshot": status_snapshot,
        }
        status_model = research_view_model(result.state, status_manifest)
        if analytics_payload:
            status_model["quant_metrics"] = analytics_payload.get("metrics", {})
            status_model["data_mode"] = analytics_payload.get("data_mode", "UNKNOWN")
            status_model["data_limitations"] = analytics_payload.get("limitations", [])
            status_model["source_metadata"] = analytics_payload.get("source_metadata", {})
            status_model["model_research"] = analytics_payload.get("model_research")
            status_model["factor_study"] = analytics_payload.get("factor_study")
            status_model["risk_portfolio"] = analytics_payload.get("risk_portfolio")
            status_model["policy_lineage"] = analytics_payload.get("policy_lineage")
            status_model["publication_scope"] = analytics_payload.get("publication_scope")
        complete_path = bundle / "complete_report.html"
        complete_document = (render_factor_report_document(factor_sections, run_id=result.state.run_id,
            question=study.get("charter", {}).get("question", "按冻结章程核对因子假设")) if factor_sections is not None
            else theme_report_document(render_status_wall_html(status_model, title="Finathink 研究报告")))
        complete_path.write_text(complete_document, encoding="utf-8")
        activity_path = bundle / "activity.jsonl"
        if activity_path.exists():
            activity_path.unlink()
        activity_path.touch()
        for event in result.events:
            append_event(event, activity_path)

        files: dict[str, str] = {}
        for path in sorted(bundle.rglob("*")):
            if path.is_file() and path.name != "manifest.json":
                files[str(path.relative_to(bundle))] = _digest_file(path)
        manifest = ReportManifest(
            run_id=result.state.run_id,
            schema_version="research-report.v1",
            files=files,
            source_snapshot={
                "as_of": result.state.as_of,
                "report_template": "blue-white.v1",
                "report_input_digests": inputs,
                **({"method_factor_snapshot": factor_sections.metadata} if factor_sections is not None else {}),
                "state_digest": stable_digest(result.state),
                "decision_digest": stable_digest(result.decision) if result.decision else None,
                **status_snapshot,
                **({"data_mode": analytics_payload.get("data_mode", "UNKNOWN"), "data_limitations": analytics_payload.get("limitations", []), "source_metadata": analytics_payload.get("source_metadata", {})} if analytics_payload else {}),
                **({"model_research": analytics_payload["model_research"]} if analytics_payload and analytics_payload.get("model_research") else {}),
                **({"factor_study": analytics_payload["factor_study"]} if analytics_payload and analytics_payload.get("factor_study") else {}),
                **({"risk_portfolio": analytics_payload["risk_portfolio"]} if analytics_payload and analytics_payload.get("risk_portfolio") else {}),
                **({"policy_lineage": analytics_payload["policy_lineage"]} if analytics_payload and analytics_payload.get("policy_lineage") else {}),
                **{section: payload for section, payload in runtime_payloads.items()},
                **({"quant_analytics": {"fingerprint": analytics_payload.get("fingerprint"), "policy_fingerprint": analytics_payload.get("policy_fingerprint"), "metrics": analytics_payload.get("metrics", {}), "comparison_conditions": analytics_payload.get("comparison_conditions", {}), "limitations": analytics_payload.get("limitations", ())}} if analytics_payload else {}),
            },
            created_at=datetime.now(UTC),
        )
        write_manifest(manifest, bundle / "manifest.json")
        return manifest


def verify_report_bundle(manifest_path: str | Path) -> BundleVerification:
    manifest_path = Path(manifest_path)
    errors: list[str] = []
    try:
        if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            return BundleVerification(ok=False, errors=("manifest size limit exceeded",))
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            return BundleVerification(ok=False, errors=("manifest must be a JSON object",))
        if payload.get("schema_version") != "research-report.v1":
            errors.append("manifest schema_version is invalid")
        bundle = manifest_path.parent
        files = payload.get("files", {})
        if not isinstance(files, Mapping):
            return BundleVerification(ok=False, errors=("manifest files must be an object",))
        required = {"complete_report.html", "activity.jsonl"}
        required.update(f"1_analysts/{role}.html" for role in _REQUIRED_ANALYSTS)
        required.update(f"{section}/index.html" for section in _REQUIRED_SECTIONS)
        required.update(f"{section}/index.html" for section in _RUNTIME_SECTIONS)
        for relative in sorted(required):
            if relative not in files:
                errors.append(f"manifest missing required file: {relative}")
        for relative, expected in files.items():
            relative_path = Path(relative)
            if relative_path.is_absolute() or ".." in relative_path.parts:
                errors.append(f"unsafe manifest path: {relative}")
                continue
            path = bundle / relative_path
            if not path.exists():
                errors.append(f"missing file: {relative}")
            elif _digest_file(path) != expected:
                errors.append(f"digest mismatch: {relative}")
    except (OSError, json.JSONDecodeError, TypeError, ValueError, AttributeError) as exc:
        errors.append(f"manifest unreadable: {exc}")
    return BundleVerification(ok=not errors, errors=tuple(errors))


def _runtime_report_payloads(result: ResearchRunResult) -> dict[str, dict[str, Any]]:
    """Collect only redacted, server-owned runtime stage summaries."""

    metadata = [event.metadata for event in result.events if isinstance(event.metadata, Mapping)]
    values: dict[str, Any] = {}
    for key in _RUNTIME_SECTIONS:
        for item in reversed(metadata):
            if key in item:
                values[key] = item[key]
                break
    terminal = result.state.current_state.value
    # ResearchRunState keeps the current state in state_history.  Exclude it
    # before deriving historical completion, otherwise an active stage is
    # incorrectly shown as COMPLETE instead of CURRENT.
    history = {item.value for item in result.state.state_history if item.value != terminal}
    failure_kind = result.state.failure_kind.value if result.state.failure_kind else terminal
    output: dict[str, dict[str, Any]] = {}
    for section in _RUNTIME_SECTIONS:
        marker = _RUNTIME_STAGE_MARKERS[section]
        status = "CURRENT" if marker == terminal else "COMPLETE" if marker in history else "BLOCKED" if terminal in _BLOCKED_RUNTIME_STATES else "PENDING"
        value = values.get(section, [])
        if isinstance(value, Mapping):
            payload: dict[str, Any] = dict(value)
        elif isinstance(value, (list, tuple)):
            payload = {"items": list(value)}
        else:
            payload = {"value": value}
        payload = _scrub(payload)
        payload["status"] = status
        if status == "BLOCKED":
            payload["failure_kind"] = failure_kind
            payload["blocked_evidence"] = [f"state:{terminal}", f"failure_kind:{failure_kind}"]
            payload.setdefault("evidence_refs", [])
        payload.setdefault("limitations", ["server snapshot; descriptive and paper-only"])
        output[section] = payload
    return output


def _render_runtime_section_html(run_id: str, section: str, payload: Mapping[str, Any]) -> str:
    safe = _scrub(payload)
    title = html.escape(section.replace("_", " ").title(), quote=True)
    status = html.escape(str(safe.get("status", "PENDING")), quote=True)
    body = html.escape(json.dumps(safe, ensure_ascii=False, sort_keys=True, indent=2), quote=False)
    links = " ".join(
        f'<a href="/research/{html.escape(run_id, quote=True)}/report/{other}">{html.escape(other.replace("_", " ").title())}</a>'
        for other in _RUNTIME_SECTIONS if other != section
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{title} · research runtime</title></head><body><main>"
        f"<nav><a href=\"/research/{html.escape(run_id, quote=True)}/report/complete\">Complete report</a> {links}</nav>"
        f"<h1>{title}</h1><p>Status: <strong>{status}</strong> · READ-ONLY · PAPER-ONLY</p>"
        f"<pre>{body}</pre><p>Values are server-provided; no financial indicators are calculated in the browser.</p>"
        "</main></body></html>"
    )


def _status_snapshot(result: ResearchRunResult) -> dict[str, Any]:
    """Derive report status facts from persisted state/events only."""

    reports = {report.role: report for report in result.state.analyst_reports}
    role_status = {
        role: (reports[role].status if role in reports else "UNAVAILABLE")
        for role in _REQUIRED_ANALYSTS
    }
    missing_evidence = [
        f"{role}:evidence"
        for role, report in sorted(reports.items())
        if not report.evidence_refs
    ]
    stage_states = (
        ("analysts", "ANALYSTS_READY"),
        ("evidence", "EVIDENCE_REVIEW"),
        ("research", "RESEARCH_PLAN_READY"),
        ("quant", "QUANT_VALIDATION"),
        ("risk", "RISK_REVIEW"),
        ("paper_decision", "PAPER_DECISION_READY"),
        ("publication", "REPORT_PUBLISHED"),
        ("learning", "LEARNING_RECORDED"),
    )
    terminal = result.state.current_state.value
    history = {item.value for item in result.state.state_history if item.value != terminal}
    stage_status: dict[str, str] = {}
    for name, state_name in stage_states:
        if terminal == state_name:
            stage_status[name] = "CURRENT"
        elif state_name in history:
            stage_status[name] = "COMPLETE"
        elif terminal in _BLOCKED_RUNTIME_STATES:
            stage_status[name] = "CANCELLED" if terminal == "CANCELLED" else "BLOCKED"
        else:
            stage_status[name] = "PENDING"
    metadata = [event.metadata for event in result.events if isinstance(event.metadata, Mapping)]
    checkpoint_state = next(
        (item.get("checkpoint_status") or item.get("checkpoint_state") for item in reversed(metadata) if item.get("checkpoint_status") or item.get("checkpoint_state")),
        None,
    )
    factor_proposals = next((item.get("factor_proposals") for item in reversed(metadata) if item.get("factor_proposals") is not None), [])
    provider_readiness = next((item.get("provider_readiness") for item in reversed(metadata) if item.get("provider_readiness") is not None), {"status": "UNKNOWN"})
    dataset_fingerprint = next((item["dataset_fingerprint"] for item in metadata if isinstance(item.get("dataset_fingerprint"), str)), None)
    return _scrub({
        "role_status": role_status,
        "stage_status": stage_status,
        "missing_evidence": sorted(missing_evidence),
        "checkpoint_status": checkpoint_state if isinstance(checkpoint_state, Mapping) else {"state": checkpoint_state or "NOT_ATTACHED"},
        "factor_proposals": factor_proposals if isinstance(factor_proposals, Sequence) and not isinstance(factor_proposals, (str, bytes, bytearray)) else [],
        "provider_readiness": provider_readiness if isinstance(provider_readiness, Mapping) else {"status": "UNKNOWN"},
        **({"dataset_fingerprint": dataset_fingerprint} if dataset_fingerprint else {}),
    })


def _manifest_payload(value: ReportManifest | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, ReportManifest):
        return {
            "run_id": value.run_id,
            "schema_version": value.schema_version,
            "files": dict(value.files),
            "source_snapshot": _plain(value.source_snapshot),
            "created_at": _plain(value.created_at),
        }
    if not isinstance(value, Mapping):
        raise TypeError("manifest must be ReportManifest or mapping")
    return dict(value)


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {item.name: _plain(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return str(value)


_PUBLIC_UNSAFE_TEXT = re.compile(
    r"(?i)(?:api[_-]?key\s*[=:]|token\s*[=:]|secret\s*[=:]|password\s*[=:]|prompt\b|raw[\s_-]*(?:provider[\s_-]*)?response\b|endpoint\b)"
)
_PUBLIC_ABSOLUTE_PATH = re.compile(r"(?:/(?:Users|home|tmp|var|private|etc|opt|root|Volumes|Applications|Library)(?:/[^\s<>\"']*)+|[A-Za-z]:[\\/][^\s<>\"']+)")


def redact_public_payload(value: Any) -> Any:
    """Recursively redact public UI/HTML values with comparison safety rules."""

    if is_dataclass(value):
        value = _plain(value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        if _PUBLIC_UNSAFE_TEXT.search(value) or _PUBLIC_ABSOLUTE_PATH.search(value):
            return "[REDACTED]"
        return _HTML_HANDLER.sub("[ATTR_REDACTED]=", value)
    if isinstance(value, Mapping):
        return {
            key: redact_public_payload(item)
            for key, item in value.items()
            if isinstance(key, str) and not _PUBLIC_SENSITIVE_KEY.search(key)
            and not _PUBLIC_UNSAFE_TEXT.search(key) and not _PUBLIC_ABSOLUTE_PATH.search(key)
        }
    if isinstance(value, (list, tuple)):
        return [redact_public_payload(item) for item in value]
    raise TypeError("unsupported public payload value")


def _sanitize_public(value: Any, path: str = "manifest") -> Any:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        if _PUBLIC_UNSAFE_TEXT.search(value) or _PUBLIC_ABSOLUTE_PATH.search(value):
            raise ValueError(f"unsafe text is not allowed in {path}")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if _PUBLIC_SENSITIVE_KEY.search(str(key)):
                raise ValueError(f"sensitive field is not allowed in {path}: {key}")
            result[str(key)] = _sanitize_public(item, f"{path}.{key}")
        return result
    if isinstance(value, (list, tuple)):
        return [_sanitize_public(item, f"{path}[{index}]") for index, item in enumerate(value)]
    raise TypeError(f"unsupported comparison value at {path}: {type(value).__name__}")


def _assert_public_comparison_payload(value: Any, path: str = "manifest") -> None:
    """Backward-compatible validation helper for callers that used it internally."""

    _sanitize_public(value, path)


def _comparison_view(value: ReportManifest | Mapping[str, Any]) -> dict[str, Any]:
    payload = _manifest_payload(value)
    payload = _sanitize_public(payload)
    run_id = payload.get("run_id")
    schema_version = payload.get("schema_version")
    files = payload.get("files", {})
    if not isinstance(run_id, str) or not run_id.strip() or not isinstance(files, Mapping):
        raise ValueError("manifest identity or files are invalid")
    safe_files: dict[str, str] = {}
    for path, digest in files.items():
        relative = Path(str(path))
        if relative.is_absolute() or ".." in relative.parts or not isinstance(digest, str):
            raise ValueError("manifest contains an unsafe file entry")
        safe_files[str(path)] = digest
    snapshot = _sanitize_public(payload.get("source_snapshot", {}), "manifest.source_snapshot")
    if not isinstance(snapshot, Mapping):
        raise TypeError("manifest source snapshot is invalid")
    metrics = _sanitize_public(payload.get("metrics", snapshot.get("metrics", {})), "manifest.metrics")
    limitations = payload.get("limitations", snapshot.get("limitations", []))
    if not isinstance(metrics, Mapping) or not isinstance(limitations, (list, tuple)) or not all(isinstance(item, str) for item in limitations):
        raise TypeError("manifest metrics or limitations are invalid")
    return {
        "run_id": run_id,
        "schema_version": schema_version,
        "manifest_digest": hashlib.sha256(
            json.dumps(
                {"run_id": run_id, "schema_version": schema_version, "files": safe_files, "source_snapshot": snapshot},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "files": dict(sorted(safe_files.items())),
        "metrics": dict(metrics),
        "limitations": sorted(limitations),
        "source_digests": {key: value for key, value in snapshot.items() if key.endswith("_digest") and isinstance(value, str)},
    }


def compare_report_manifests(left: ReportManifest | Mapping[str, Any], right: ReportManifest | Mapping[str, Any]) -> dict[str, Any]:
    """Compare two reports without ranking strategies or recomputing metrics."""

    left_view = _comparison_view(left)
    right_view = _comparison_view(right)
    changed_files = {
        path: {"left": left_view["files"].get(path), "right": right_view["files"].get(path)}
        for path in sorted(set(left_view["files"]) | set(right_view["files"]))
        if left_view["files"].get(path) != right_view["files"].get(path)
    }
    return {
        "left": left_view,
        "right": right_view,
        "differences": {
            "files": changed_files,
            "source_digests": {
                key: {"left": left_view["source_digests"].get(key), "right": right_view["source_digests"].get(key)}
                for key in sorted(set(left_view["source_digests"]) | set(right_view["source_digests"]))
                if left_view["source_digests"].get(key) != right_view["source_digests"].get(key)
            },
            "metrics": {"left": left_view["metrics"], "right": right_view["metrics"]},
            "limitations": {
                "added": sorted(set(right_view["limitations"]) - set(left_view["limitations"])),
                "removed": sorted(set(left_view["limitations"]) - set(right_view["limitations"])),
            },
        },
    }


def compare_experiments(runs: Sequence[Mapping[str, Any] | ReportManifest]) -> dict[str, Any]:
    """Return a descriptive comparison of experiment inputs, metrics and limits.

    This function deliberately does not rank runs, name a preferred strategy,
    or produce any trading language.  The browser receives this server-owned
    snapshot and only renders it.
    """

    output: list[dict[str, Any]] = []
    if isinstance(runs, Mapping):
        if isinstance(runs.get("runs"), (list, tuple)):
            runs = runs["runs"]
        elif isinstance(runs.get("experiments"), (list, tuple)):
            runs = tuple(
                {
                    "run_id": item.get("experiment_id"),
                    "inputs": {"parameters": item.get("parameters", {}), "dataset_fingerprint": item.get("dataset_fingerprint"), "config_digest": item.get("config_digest"), "research_fingerprint": item.get("research_fingerprint")},
                    "metrics": {"ic": item.get("ic"), "ir": item.get("ir"), "turnover": item.get("turnover"), "decay": item.get("decay", {})},
                    "limitations": item.get("limitations", ()),
                }
                for item in runs["experiments"] if isinstance(item, Mapping)
            )
        else:
            runs = ()
    if not isinstance(runs, (list, tuple)):
        raise TypeError("runs must be a list or tuple")
    for item in runs:
        payload = _manifest_payload(item) if isinstance(item, ReportManifest) else dict(item) if isinstance(item, Mapping) else None
        if payload is None:
            raise TypeError("each experiment must be a mapping or ReportManifest")
        # FactorExperimentResult.to_dict() is accepted directly as a compact
        # server payload; each bounded experiment remains an independent run.
        run_id = payload.get("run_id")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("experiment run_id is required")
        safe = _sanitize_public(payload)
        snapshot = safe.get("source_snapshot", {})
        if not isinstance(snapshot, Mapping):
            snapshot = {}
        raw_inputs = safe.get("inputs", snapshot.get("inputs", {}))
        metrics = safe.get("metrics", snapshot.get("metrics", {}))
        limitations = safe.get("limitations", snapshot.get("limitations", ()))
        if not isinstance(raw_inputs, Mapping) or not isinstance(metrics, Mapping):
            raise TypeError("experiment inputs and metrics must be mappings")
        if not isinstance(limitations, (list, tuple)) or not all(isinstance(value, str) for value in limitations):
            raise TypeError("experiment limitations must be text")
        output.append({
            "run_id": run_id,
            "inputs": dict(raw_inputs),
            "metrics": dict(metrics),
            "limitations": sorted(set(limitations)),
            "conditions": safe.get("conditions", snapshot.get("comparison_conditions", {})),
        })
    required = {"dataset_fingerprint", "instrument", "sample_period", "scoring_index", "split", "cost", "version", "metric_basis"}
    groups = {}
    for row in output:
        conditions = row["conditions"]
        if not isinstance(conditions, Mapping) or not required <= conditions.keys() or any(conditions[key] is None or conditions[key] == {} or conditions[key] == [] for key in required):
            row.update(comparability="UNKNOWN", comparison_group=None)
            continue
        reference = stable_digest(conditions)
        row.update(comparability="MATCHED_CONDITIONS", comparison_group=reference)
        groups.setdefault(reference, []).append(row)
    grouped = []
    for reference, members in sorted(groups.items()):
        first = members[0]
        differences = []
        for row in members[1:]:
            metrics = {key: value - first["metrics"][key] for key, value in row["metrics"].items() if type(value) in {int, float} and math.isfinite(value) and type(first["metrics"].get(key)) in {int, float} and math.isfinite(first["metrics"][key])}
            differences.append({"run_id": row["run_id"], "reference_run_id": first["run_id"], "metrics": metrics})
        grouped.append({"group_id": reference, "run_ids": [row["run_id"] for row in members], "conditions": first["conditions"], "differences": differences})
    return {"schema_version": 1, "paper_only": True, "runs": output, "groups": grouped, "limitations": ["Differences are descriptive and computed only under identical explicit conditions; unknown or different conditions are not ranked together."]}

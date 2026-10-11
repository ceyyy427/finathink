from __future__ import annotations

import json
import sqlite3

from finahinking.local_app import LocalAppConfig, LocalApplication


def test_local_shell_defaults_to_offline_sample_mode() -> None:
    app = LocalApplication(connection=sqlite3.connect(":memory:"))
    assert app.route("GET", "/health")[0] == 200
    diagnostics = app.route("GET", "/api/diagnostics")[2]
    assert diagnostics["database"] == "sqlite"
    assert diagnostics["offline"] is True
    assert diagnostics["real_money_execution"] is False
    assert app.route("GET", "/")[1].startswith("text/html")
    assert "Skip to content" in app.route("GET", "/")[2]
    app.close()


def test_normal_config_is_file_backed_for_restart_continuity() -> None:
    config = LocalAppConfig()
    assert config.db_path != ":memory:"
    assert config.db_path.endswith("finahinking.sqlite3")


def test_routes_expose_each_product_world_and_structured_knowledge() -> None:
    app = LocalApplication(connection=sqlite3.connect(":memory:"))
    for path in ("/", "/events", "/explore", "/knowledge", "/quant", "/strategy", "/personal", "/workspace", "/community", "/diagnostics"):
        status, content_type, payload = app.route("GET", path)
        assert status == 200
        assert content_type.startswith("text/html")
        assert "Finathink" in payload
    payload = app.route("GET", "/api/knowledge", query={"q": ["volatility"]})[2]
    assert payload["concepts"][0]["id"] == "volatility"
    concept = app.route("GET", "/api/concepts/volatility")[2]
    assert concept["derivation"]
    assert concept["equation"]
    closure = app.route("GET", "/api/concepts/volatility/prerequisites")[2]
    assert closure["concepts"][-1]["concept_id"] == "volatility"
    path = app.route("GET", "/api/knowledge/path/reference-curriculum")[2]
    assert len(path["concept_ids"]) >= 15
    app.close()


def test_shared_shell_has_product_hierarchy_and_semantic_states() -> None:
    app = LocalApplication(connection=sqlite3.connect(":memory:"))
    page = app.route("GET", "/")[2]
    for label in ("Home", "Events", "Explore", "Knowledge", "Quant", "Strategy Lab", "Workspace"):
        assert label in page
    for token in ("--background", "--surface", "--evidence", "--hypothesis", "--focus"):
        assert token in page
    assert "aria-current=\"page\"" in page
    assert "finathink-splash-map.jpg" in page
    assert "finathink-research-splash.jpg" in page
    assert "splash-spin" in page and "prefers-reduced-motion" in page
    assert "prefers-reduced-motion" in page
    assert "aria-label=\"Primary\"" in page
    app.close()


def test_dark_intelligence_shell_has_grouped_navigation_and_truthful_local_status() -> None:
    app = LocalApplication(connection=sqlite3.connect(":memory:"))
    page = app.route("GET", "/")[2]
    for token in ("#050B18", "#081326", "#8B5CF6", "#38BDF8", "#34D399", ".topbar", ".panel"):
        assert token in page
    for label in ("Workspace", "Learn", "System", "Local Workspace", "PAPER-ONLY"):
        assert label in page
    assert "aria-label=\"Workspace\"" in page
    assert "aria-label=\"Learn\"" in page
    assert "aria-label=\"System\"" in page
    assert "🧠" not in page and "📊" not in page
    assert "outline: 3px solid var(--focus)" in page
    app.close()


def test_overview_dashboard_is_evidence_bound_and_has_no_data_fallbacks() -> None:
    app = LocalApplication(LocalAppConfig(db_path=":memory:", offline=True), connection=sqlite3.connect(":memory:"))
    page = app.route("GET", "/")[2]
    assert 'data-finathink-overview' in page
    assert 'data-overview-payload-url="/api/overview"' in page
    assert 'class="overview-header"' in page
    for label in ("Events", "Saved items", "Learning evidence", "Research runs"):
        assert label in page
    for region in ('data-overview-chart', 'data-overview-topics', 'data-overview-heatmap', 'data-overview-comparison', 'data-overview-activity-series'):
        assert region in page
    assert "PAPER-ONLY" in page and "OFFLINE" in page
    assert "No saved research activity yet" in page
    assert "No topics are recorded yet" in page
    assert "api_key" not in page and "prompt" not in page
    assert "/research/compare" in page
    app.close()


def test_home_shell_exposes_human_questions_and_workspace_alias() -> None:
    app = LocalApplication(connection=sqlite3.connect(":memory:"))
    home = app.route("GET", "/")[2]
    workspace = app.route("GET", "/workspace")[2]
    for phrase in ("What can I understand today?", "Continue learning", "Current research", "Paper simulation"):
        assert phrase in home
    assert "Personal continuity" in workspace
    assert "Research" in workspace and "Learning" in workspace
    app.close()


def test_knowledge_search_and_recoverable_html_routes() -> None:
    app = LocalApplication(connection=sqlite3.connect(":memory:"))
    filtered = app.route("GET", "/knowledge?q=volatility")[2]
    assert "value=\"volatility\"" in filtered
    assert "Volatility" in filtered
    missing = app.route("GET", "/knowledge/not-a-concept")
    assert missing[0] == 404 and "Skip to content" in missing[2] and "Browse Knowledge" in missing[2]
    asset = app.route("GET", "/assets/finathink-splash-map.jpg")
    assert asset[0] == 200 and asset[1] == "image/jpeg" and asset[2][:3] == b"\xff\xd8\xff"
    asset_two = app.route("GET", "/assets/finathink-research-splash.jpg")
    assert asset_two[0] == 200 and asset_two[1] == "image/jpeg" and asset_two[2][:3] == b"\xff\xd8\xff"
    app.close()


def test_personal_save_and_reopen_use_the_same_p7_store(tmp_path) -> None:
    db = tmp_path / "finahinking.sqlite3"
    first = LocalApplication(LocalAppConfig(db_path=str(db)))
    status, _, result = first.route(
        "POST",
        "/api/personal/save",
        body={"title": "CPI review", "node_type": "note", "payload": {"mode": "SAMPLE"}},
    )
    assert status == 201
    assert result["saved"] is True
    first.close()

    reopened = LocalApplication(LocalAppConfig(db_path=str(db)))
    status, _, exported = reopened.route("GET", "/api/personal")
    assert status == 200
    assert any(item["title"] == "CPI review" for item in exported["nodes"])
    status, _, payload = reopened.route("POST", "/api/reopen", body={})
    assert status == 200 and payload["reopened"] is True
    reopened.close()


def test_api_rejects_invalid_payload_without_sql_or_network() -> None:
    app = LocalApplication(connection=sqlite3.connect(":memory:"))
    status, content_type, payload = app.route("POST", "/api/personal/save", body={"title": ""})
    assert (status, content_type) == (400, "application/json")
    assert "required" in payload["error"]
    assert json.dumps(app.diagnostics())[0] == "{"
    app.close()


def test_overview_projection_is_versioned_server_owned_and_sample_provenance() -> None:
    app = LocalApplication(LocalAppConfig(db_path=":memory:", offline=True), connection=sqlite3.connect(":memory:"))
    status, content_type, payload = app.route("GET", "/api/overview")
    assert (status, content_type) == (200, "application/json")
    assert payload["schema_version"] == 1
    assert payload["mode"] == "OFFLINE"
    assert payload["paper_only"] is True
    for field in ("as_of", "metrics", "activity", "topics", "learning_activity", "research_status", "limitations", "provenance"):
        assert field in payload
    assert payload["provenance"]["data_mode"] == "SAMPLE"
    assert payload["provenance"]["source_state"] == "CAPTURED"
    assert payload["metrics"]["saved_items"] == 0
    assert payload["topics"] == []
    assert any("fixture" in str(item).lower() or "offline" in str(item).lower() for item in payload["limitations"])
    app.close()


def test_overview_projection_is_empty_safe_and_rejects_secret_or_path_like_fields() -> None:
    app = LocalApplication(LocalAppConfig(db_path=":memory:", offline=True), connection=sqlite3.connect(":memory:"))
    payload = app.overview()
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    assert payload["metrics"]["research_runs"] == 0
    assert payload["metrics"]["learning_items"] == 0
    assert payload["activity"] == []
    assert payload["learning_activity"] == []
    for forbidden in ("api_key", "prompt", "endpoint", "/users/", "\\\\"):
        assert forbidden not in serialized
    app.close()


def test_overview_projection_aggregates_real_activity_topics_and_learning_dates() -> None:
    from finahinking.p7.models import MasteryEvidence, PersonalNode

    app = LocalApplication(LocalAppConfig(db_path=":memory:", offline=True), connection=sqlite3.connect(":memory:"))
    app.repository.save_node(app.config.session_id, PersonalNode("concept-vol", "concept", "Volatility", {}))
    app.repository.save_mastery_evidence(
        app.config.session_id,
        MasteryEvidence("evidence-vol-1", "concept-vol", "quiz_response", "knowledge:volatility:q1", "correct", "2026-10-03T09:00:00+00:00"),
    )
    app.repository.save_history_entry(
        app.config.session_id,
        history_id="history-1",
        source_kind="quant",
        source_id="quant-1",
        source_fingerprint="a" * 64,
        event_type="completed",
        title="Quant research",
        occurred_at="2026-10-03T10:00:00+00:00",
    )
    payload = app.overview()
    assert payload["activity_series"] == [{"date": "2026-10-03", "count": 2, "learning": 1, "research": 1}]
    assert payload["topics"] == [{"topic": "quant", "count": 1, "last_active": "2026-10-03"}]
    assert payload["learning_activity"] == [{"date": "2026-10-03", "count": 1, "outcomes": {"CORRECT": 1}}]
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    assert "volatility" not in serialized
    app.close()


def test_research_comparison_projection_is_empty_safe_and_route_is_read_only() -> None:
    app = LocalApplication(LocalAppConfig(db_path=":memory:", offline=True), connection=sqlite3.connect(":memory:"))
    status, content_type, payload = app.route("GET", "/api/research/compare")
    assert (status, content_type) == (200, "application/json")
    assert payload["schema_version"] == 1
    assert payload["runs"] == []
    status, content_type, page = app.route("GET", "/research/compare")
    assert status == 200
    assert content_type.startswith("text/html")
    assert "Research comparison" in page
    assert "No registered research runs" in page
    assert app.route("POST", "/research/compare")[0] == 405
    app.close()


def test_provider_config_routes_require_csrf_and_persist_redacted_config(tmp_path) -> None:
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "app.sqlite3")))
    payload = {"provider_id": "personal", "adapter_kind": "openai_compatible", "model": "user-model", "endpoint": "https://provider.example.net/v1", "credential_ref": {"env_var": "FINAHINK_USER_API_KEY"}, "enabled": True, "role_models": {"technical": "user-model"}}
    assert app.route("POST", "/api/research/providers/config", body=payload)[0] == 403
    status, _, saved = app.route("POST", "/api/research/providers/config", body={**payload, "_csrf": app.csrf_token})
    assert status == 201
    assert saved["endpoint_configured"] is True
    assert "https://provider.example.net" not in json.dumps(saved)
    assert app.route("POST", "/api/research/providers/config", body={**payload, "_csrf": app.csrf_token})[0] == 409
    app.close()
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "app.sqlite3")))
    status, _, listed = app.route("GET", "/api/research/providers/config")
    assert status == 200
    assert listed["providers"][0]["provider_id"] == "personal"
    assert listed["providers"][0]["role_models"] == {"technical": "user-model"}
    readiness = app.route("GET", "/api/research/providers")[2]
    assert any(item["provider"] == "personal" for item in readiness["providers"])
    app.close()


def test_provider_config_route_rejects_raw_secrets_and_unlisted_fields() -> None:
    app = LocalApplication(LocalAppConfig(db_path=":memory:"))
    secret = "never-store-this-value"
    payload = {"provider_id": "personal", "adapter_kind": "offline", "model": "fixture-v1", "api_key": secret, "_csrf": app.csrf_token}
    status, _, error = app.route("POST", "/api/research/providers/config", body=payload)
    assert status == 400
    assert secret not in json.dumps(error)
    assert app.route("GET", "/api/research/providers/config")[2]["providers"] == []
    app.close()

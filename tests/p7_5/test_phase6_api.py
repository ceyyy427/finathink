from finahinking.local_app import LocalAppConfig, LocalApplication


def test_model_settings_and_job_page_offer_explicit_grounded_consent(tmp_path):
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "app.sqlite")))
    try:
        page = app.route("GET", "/research/jobs")[2]
        assert 'name="allow_model_calls"' in page and 'name="materials"' in page
        assert 'data-run-model-summary' in page
        settings = app.route("GET", "/settings/providers")[2]
        assert 'data-provider-form' in settings and 'name="credential_env"' in settings
        assert 'name="api_key"' not in settings
    finally:
        app.close()


def test_saving_provider_form_is_inert_and_returns_redacted_metadata(tmp_path):
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "app.sqlite")))
    try:
        code, _, result = app.route("POST", "/api/research/providers/config", body={
            "_csrf": app.csrf_token, "provider_id": "desk", "adapter_kind": "openai_compatible", "model": "gpt-4o",
            "endpoint": "https://models.acme.com/v1", "credential_env": "FINAHINK_TEST_KEY", "role_models": '{"technical":"gpt-4.1"}',
        })
        assert code == 201 and result["endpoint_configured"]
        assert result["role_models"] == {"technical": "gpt-4.1"}
        assert "models.acme.com" not in str(result)
    finally:
        app.close()

from finahinking.local_app import LocalAppConfig, LocalApplication


def test_factor_workspace_and_catalog_are_readable_but_writes_require_csrf(tmp_path):
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "app.sqlite"), offline=True))
    try:
        status, _, page = app.route("GET", "/research/experiments")
        assert status == 200 and '<main' in page and 'data-experiment-workspace' in page
        status, _, catalog = app.route("GET", "/api/research/experiments/catalog")
        assert status == 200 and 20 <= len(catalog["recipes"]) <= 30
        assert all(item["effect_status"] == "NOT_EVALUATED_ON_USER_DATA" for item in catalog["recipes"])
        assert app.route("POST", "/api/research/experiments", body={})[0] == 403
        status, _, history = app.route("GET", "/api/research/experiments")
        assert status == 200 and history["studies"] == []
    finally:
        app.close()


def test_sample_requires_an_explicit_post_and_is_marked_sample(tmp_path):
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "app.sqlite"), offline=True))
    try:
        status, _, payload = app.route("POST", "/api/research/experiments/sample", body={"_csrf": app.csrf_token, "instrument": "AAA"})
        assert status == 201 and payload["mode"] == "SAMPLE" and payload["record_count"] >= 200
        assert app.route("GET", "/api/research/experiments/sample")[0] != 201
        assert app.research_service.list_jobs()["total"] == 0
    finally:
        app.close()

from finahinking.local_app import LocalAppConfig, LocalApplication


def test_learning_review_uses_shared_shell_csrf_scoped_lists_and_method_guards(tmp_path):
    app = LocalApplication(LocalAppConfig(db_path=str(tmp_path / "app.sqlite"), offline=True))
    try:
        status, _, page = app.route("GET", "/research/learning")
        assert status == 200
        assert 'class="app-shell"' in page and 'data-learning-workspace' in page
        assert 'data-learning-form' in page and 'data-learning-review-form' in page
        assert 'data-learning-rerun-form' in page and 'aria-current="page">复盘与学习' in page
        assert '/assets/finathink-research.js' in page
        assert app.route("GET", "/api/research/learning")[2] == {"reviews": []}
        policies = app.route("GET", "/api/research/learning/policies")[2]["policies"]
        assert len(policies) == 1 and policies[0]["version"] == 0
        assert app.route("POST", "/api/research/learning", body={})[0] == 403
        assert app.route("POST", "/api/research/learning", body={"_csrf": app.csrf_token})[0] == 422
        invalid = app.route("POST", "/api/research/learning", body={"_csrf": app.csrf_token, "run_id": "research-missing"})
        assert invalid[0] == 422 and "原研究" in invalid[2]["error"]
        assert app.route("GET", "/api/research/learning/learning-missing")[0] == 404
        assert app.route("DELETE", "/api/research/learning")[0] == 405
        assert app.route("POST", "/api/research/learning/policies", body={"_csrf": app.csrf_token})[0] == 405
    finally:
        app.close()

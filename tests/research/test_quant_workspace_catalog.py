from finahinking.local_app import LocalAppConfig, LocalApplication


def test_catalog_uses_distinct_reviewed_objects_and_reading_never_runs_jobs():
    app = LocalApplication(LocalAppConfig(db_path=":memory:", offline=True))
    try:
        status, _, catalog = app.route("GET", "/api/research/quant-workspace?kind=factor")
        assert status == 200
        assert catalog["inventory"] == {"knowledge": 80, "factor": 80, "strategy": 12, "risk": 12, "case": 22}
        assert catalog["total"] == 80
        assert len(catalog["journeys"]) == 12
        assert all(row["evidence"]["approval"] == "REQUIRES_REVIEW" for row in catalog["items"])
        assert len({row["object_id"] for row in catalog["items"]}) == len(catalog["items"])
        assert app.research_service.list_jobs()["total"] == 0
    finally:
        app.close()

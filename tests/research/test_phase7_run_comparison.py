from finahinking.local_app import LocalAppConfig, LocalApplication
from finahinking.p8_2.data_sources import FixtureMarketDataSource
from tests.research.test_factor_study_application import values, wait_study


def test_real_factor_run_comparison_separates_different_costs_after_restart(tmp_path):
    config = LocalAppConfig(db_path=str(tmp_path / "app.sqlite"), offline=True)
    app = LocalApplication(config)
    service = app.research_service
    reference = service.snapshots.save(FixtureMarketDataSource(points=240).snapshot("AAA"))
    service.start()
    try:
        first = service.create_factor_study(values(reference))
        wait_study(service, first["study_id"], "validation")
        second = service.create_factor_study(values(reference, idempotency_key="different-cost", fee_bps=10))
        wait_study(service, second["study_id"], "validation")
        result = app.research_comparison()["quantitative"]
        factor_groups = [group for group in result["groups"] if group["conditions"]["version"] == "factor-workbench.v1"]
        assert len(factor_groups) == 2
        assert {group["conditions"]["cost"]["fee_bps"] for group in factor_groups} == {5, 10}
        page = app.route("GET", "/research/compare")[2]
        assert "量化可比条件" in page and "验证 IC" in page
    finally:
        app.close()
    restarted = LocalApplication(config)
    try:
        assert len(restarted.research_comparison()["quantitative"]["groups"]) >= 2
    finally:
        restarted.close()

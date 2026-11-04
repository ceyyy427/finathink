"""The derived catalogue can be recreated after restoring private state."""
from finahinking.local_app import LocalAppConfig, LocalApplication
from finahinking.maintenance import backup_state, restore_state
from finahinking.research.source_updates import SourceVersionStore
from tests.research.test_source_intake import source_tree
from tests.research.test_source_updates import _changed_checkout

__all__ = ["source_tree"]


def test_clean_wheel_backup_restore_and_reindex(tmp_path, source_tree):
    root = tmp_path / "state"
    root.mkdir()
    db = root / "app.sqlite3"
    old_root, old, new_root, new = _changed_checkout(tmp_path, source_tree)
    app = LocalApplication(LocalAppConfig(db_path=str(db), offline=True))
    try:
        source_store = SourceVersionStore(app.artifact_root)
        ref = source_store.stage(old, new, old_checkout=old_root, new_checkout=new_root)
        source_store.review(ref, decision="WITHDRAW", reviewer="local-reviewer", rationale="固定撤回说明。")
        original = app.route("GET", "/api/research/quant-workspace")[2]
        source = app.route("GET", f"/api/research/quant-workspace/sources?ref={ref}")[2]
        assert source["decision"] == "WITHDRAW"
    finally:
        app.close()
    target = tmp_path / "restored"
    archive = backup_state(db, tmp_path / "state.zip")
    restore_state(archive, target)
    cache = target / ".knowledge-cache" / "public.sqlite3"
    assert not cache.exists()
    restored = LocalApplication(LocalAppConfig(db_path=str(target / "app.sqlite3"), offline=True))
    try:
        assert restored.route("GET", f"/api/research/quant-workspace/sources?ref={ref}")[2] == source
        assert restored.route("GET", "/api/research/quant-workspace")[2] == original
        assert cache.is_file()
        assert restored.research_service.list_jobs()["total"] == 0
    finally:
        restored.close()

"""Private offline backup: explicit files, immutable source and new destination."""

import json
import sqlite3
import stat
import zipfile

import pytest

from finahinking.local_app import LocalAppConfig, LocalApplication
from finahinking.maintenance import backup_state, restore_state
from finahinking.p8_2.data_sources import FixtureMarketDataSource


def prepare(tmp_path):
    db = tmp_path / "source" / "private.sqlite"
    app = LocalApplication(LocalAppConfig(db_path=str(db), offline=True))
    ref = app.research_service.snapshots.save(FixtureMarketDataSource(points=20).snapshot("AAA"))
    app.close()
    return db, ref


def test_backup_restore_preserves_snapshot_and_old_schema_upgrade_is_idempotent(tmp_path, monkeypatch):
    db, ref = prepare(tmp_path)
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE retained_user_note (text TEXT)")
        connection.execute("INSERT INTO retained_user_note VALUES ('用户原有研究')")
    db.with_suffix(".providers.json").write_text(json.dumps({"schema_version": 1, "providers": []}))
    (db.parent / "unrelated-secret.txt").write_text("not-an-application-file")
    monkeypatch.setenv("FINAHINK_PRIVATE_CREDENTIAL", "not-an-application-file")
    archive = tmp_path / "private-backup.zip"
    backup_state(db, archive)
    with zipfile.ZipFile(archive) as bundle:
        assert not any('unrelated-secret' in name for name in bundle.namelist())
        assert b'not-an-application-file' not in b''.join(bundle.read(name) for name in bundle.namelist())
    restored = restore_state(archive, tmp_path / "recovered")
    assert restored == tmp_path / "recovered" / "app.sqlite3"
    for _ in range(2):
        app = LocalApplication(LocalAppConfig(db_path=str(restored), offline=True))
        try:
            assert app.research_service.snapshots.load(ref).fingerprint == ref
            assert app.connection.execute("SELECT text FROM retained_user_note").fetchone()[0] == "用户原有研究"
            assert app.research_service.list_jobs()["total"] == 0
        finally:
            app.close()
    assert db.is_file()


def test_backup_refuses_active_work_and_existing_outputs(tmp_path):
    db, ref = prepare(tmp_path)
    app = LocalApplication(LocalAppConfig(db_path=str(db), offline=True))
    job = app.research_service.submit({"idempotency_key": "pending", "snapshot_ref": ref, "instrument": "AAA", "as_of": "2026-10-01", "question": "检查离线基线", "model_config_ref": "offline"})
    app.close()
    archive = tmp_path / "private.zip"
    with pytest.raises(ValueError, match="任务"):
        backup_state(db, archive)
    assert not archive.exists()
    app = LocalApplication(LocalAppConfig(db_path=str(db), offline=True))
    app.research_service.cancel(job["job_id"])
    app.close()
    backup_state(db, archive)
    original = archive.read_bytes()
    with pytest.raises(FileExistsError):
        backup_state(db, archive)
    assert archive.read_bytes() == original
    with pytest.raises(FileExistsError):
        restore_state(archive, db.parent)
    assert db.is_file()


@pytest.mark.parametrize("name", ['../escape', '/absolute', 'artifacts/../escape', 'artifacts\\escape', 'unknown.txt'])
def test_restore_rejects_unsafe_or_unlisted_archive_entries_without_a_partial_target(tmp_path, name):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr(name, 'unsafe')
        bundle.writestr('manifest.json', '{}')
    target = tmp_path / "restored"
    with pytest.raises(ValueError):
        restore_state(archive, target)
    assert not target.exists()


def test_restore_checks_hashes_and_symlinks(tmp_path):
    db, _ = prepare(tmp_path)
    good = tmp_path / "good.zip"
    backup_state(db, good)
    for mode in ('hash', 'symlink', 'duplicate'):
        archive = tmp_path / f'{mode}.zip'
        with zipfile.ZipFile(good) as source, zipfile.ZipFile(archive, 'w') as output:
            for item in source.infolist():
                raw = source.read(item)
                if item.filename == 'app.sqlite3':
                    if mode == 'hash':
                        raw += b'changed'
                    elif mode == 'symlink':
                        item.external_attr = (stat.S_IFLNK | 0o777) << 16
                output.writestr(item, raw)
                if mode == 'duplicate' and item.filename == 'app.sqlite3':
                    with pytest.warns(UserWarning):
                        output.writestr(item, raw)
        target = tmp_path / f'restored-{mode}'
        with pytest.raises(ValueError):
            restore_state(archive, target)
        assert not target.exists()


def test_backup_rejects_symlink_and_corrupt_database(tmp_path):
    db, _ = prepare(tmp_path)
    link = db.parent / 'artifacts' / 'unsafe.json'
    link.symlink_to(db)
    with pytest.raises(ValueError, match='链接'):
        backup_state(db, tmp_path / 'one.zip')
    link.unlink()
    db.write_bytes(b'not sqlite')
    with pytest.raises(ValueError, match='数据库'):
        backup_state(db, tmp_path / 'two.zip')


def test_backup_refuses_dangling_output_link_and_reserved_config_names(tmp_path):
    db, _ = prepare(tmp_path)
    target = tmp_path / 'never-create.zip'
    link = tmp_path / 'output-link.zip'
    link.symlink_to(target)
    with pytest.raises(FileExistsError):
        backup_state(db, link)
    assert not target.exists()
    for suffix in ('.providers.json', '.data-connections.json'):
        with pytest.raises(ValueError):
            backup_state(db, db.with_suffix(suffix))
        assert not db.with_suffix(suffix).exists()


def test_legacy_understanding_schema_upgrades_without_losing_data(tmp_path):
    from finahinking.p6_5.repository import MIGRATION_PATH

    db = tmp_path / 'legacy.sqlite'
    with sqlite3.connect(db) as connection:
        connection.executescript(MIGRATION_PATH.read_text())
        connection.execute("INSERT INTO p6_5_artifacts VALUES (?,?,?,?,?)", ('old-artifact', 'a' * 64, 'legacy/material.json', 0, '2026-01-01T00:00:00Z'))
    previous = db.read_bytes()
    archive = tmp_path / 'old-state.zip'
    backup_state(db, archive)
    restored = restore_state(archive, tmp_path / 'upgraded')
    for _ in range(2):
        app = LocalApplication(LocalAppConfig(db_path=str(restored), offline=True))
        try:
            assert app.connection.execute('SELECT artifact_id FROM p6_5_artifacts').fetchone()[0] == 'old-artifact'
            assert app.research_service.list_jobs()['total'] == 0
        finally:
            app.close()
    assert db.read_bytes() == previous

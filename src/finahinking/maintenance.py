"""Offline, private state transfer. Close the application before either command.

Only the explicit app database, adjacent reference configurations and its
artifacts are included. Keychain/environment values are never resolved.
Restoration is integrity checked and never overwrites an existing directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath

MAX_FILES = 20_000
MAX_BYTES = 1_073_741_824
MAX_FILE_BYTES = 67_108_864
MAX_MANIFEST_BYTES = 8_000_000
_CONFIGS = (".providers.json", ".data-connections.json")


def _relative(name):
    value = PurePosixPath(name)
    if not name or value.is_absolute() or '\\' in name or ':' in name or any(part in {'', '.', '..'} for part in name.split('/')):
        raise ValueError('备份包含不安全的文件名。')
    if name not in {'app.sqlite3', 'app.providers.json', 'app.data-connections.json', 'manifest.json'} and not (len(value.parts) > 1 and value.parts[0] == 'artifacts'):
        raise ValueError('备份包含清单范围外的文件。')
    return value


def _regular(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('备份不接受符号链接、硬链接或特殊文件。')
    return info


def _idle(db):
    try:
        with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)) as connection:
            if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('数据库完整性检查失败。')
            exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone()
            if exists and connection.execute("SELECT 1 FROM jobs WHERE status NOT IN ('completed','failed','cancelled') LIMIT 1").fetchone():
                raise ValueError('仍有未结束的任务。请取消或等待完成，关闭应用后再备份。')
    except sqlite3.Error as exc:
        raise ValueError('数据库不可读取或已损坏，请先恢复原始数据库。') from exc


def _files(db):
    values = {'app.sqlite3': db}
    for suffix in _CONFIGS:
        path = db.with_suffix(suffix)
        if path.exists() or path.is_symlink():
            values['app' + suffix] = path
    root = db.parent / 'artifacts'
    if root.is_symlink():
        raise ValueError('备份不接受链接目录。')
    if root.exists():
        for path in sorted(root.rglob('*')):
            if path.is_symlink():
                raise ValueError('备份不接受链接文件或目录。')
            if path.is_dir():
                continue
            if path.name.endswith(('-wal', '-shm', '-journal')):
                continue
            name = 'artifacts/' + path.relative_to(root).as_posix()
            _relative(name)
            values[name] = path
    if len(values) > MAX_FILES:
        raise ValueError('备份文件数量超过上限，请先归档不需要的研究。')
    return values


def _stamp(values):
    result = {}
    for name, path in values.items():
        info = _regular(path)
        result[name] = (info.st_size, info.st_mtime_ns, info.st_ino)
    if sum(item[0] for item in result.values()) > MAX_BYTES or any(item[0] > MAX_FILE_BYTES for item in result.values()):
        raise ValueError('备份超过总计 1 GiB 或单文件 64 MiB 上限，请先归档不需要的研究。')
    return result


def _archive_limits(sizes):
    """The same uncompressed member contract applies in both directions."""
    if len(sizes) > MAX_FILES + 1 or sum(sizes) > MAX_BYTES or any(size > MAX_FILE_BYTES for size in sizes):
        raise ValueError('备份数量或体积超过上限，清单也计入总大小。')


def backup_state(db_path, output):
    """Create a private archive of a quiescent application; refuse overwrites."""
    supplied = Path(db_path).expanduser()
    if supplied.is_symlink():
        raise ValueError('备份不接受链接数据库。')
    db = supplied.resolve(strict=True)
    supplied_output = Path(output).expanduser().absolute()
    if supplied_output.exists() or supplied_output.is_symlink():
        raise FileExistsError('备份文件已经存在，请使用新的文件名。')
    archive = supplied_output.resolve()
    if archive.exists() or archive.is_symlink():
        raise FileExistsError('备份文件已经存在，请使用新的文件名。')
    if archive.is_relative_to(db.parent / 'artifacts') or archive in {db, *(db.with_suffix(suffix) for suffix in _CONFIGS)}:
        raise ValueError('请把备份保存到 artifacts 目录之外，且不要使用应用配置文件名。')
    values = _files(db)
    before = _stamp(values)
    for name, path in values.items():
        if name == 'app.sqlite3' or path.suffix == '.sqlite3':
            _idle(path)
    with tempfile.TemporaryDirectory(prefix='.finathink-backup-', dir=archive.parent) as directory:
        work = Path(directory)
        manifest = {'format_version': 1, 'private': True, 'credentials': 'Keychain/environment values not read', 'files': {}}
        bundle_path = work / 'state.zip'
        with zipfile.ZipFile(bundle_path, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
            for index, (name, path) in enumerate(values.items()):
                target = work / f'entry-{index}'
                if name == 'app.sqlite3' or path.suffix == '.sqlite3':
                    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(target)) as destination:
                        source.backup(destination)
                else:
                    shutil.copyfile(path, target)
                raw = target.read_bytes()
                manifest['files'][name] = {'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
                bundle.write(target, name)
            encoded_manifest = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode('utf-8')
            _archive_limits([item['size'] for item in manifest['files'].values()] + [len(encoded_manifest)])
            if len(encoded_manifest) > MAX_MANIFEST_BYTES:
                raise ValueError('备份清单超过上限，请先归档不需要的研究。')
            bundle.writestr('manifest.json', encoded_manifest)
        if before != _stamp(_files(db)):
            raise ValueError('备份期间应用状态发生变化。请关闭应用后重新备份。')
        for name, path in values.items():
            if name == 'app.sqlite3' or path.suffix == '.sqlite3':
                _idle(path)
        os.chmod(bundle_path, 0o600)
        os.link(bundle_path, archive)  # atomic creation: never replace a user's file
    return archive


def restore_state(archive_path, destination):
    """Verify all members before publishing into a new private directory."""
    target = Path(destination).expanduser().absolute()
    if target.exists() or target.is_symlink():
        raise FileExistsError('恢复目录已经存在，请选择新的目录；原始数据保持不变。')
    with zipfile.ZipFile(archive_path) as bundle:
        entries = bundle.infolist()
        names = [item.filename for item in entries]
        _archive_limits([item.file_size for item in entries])
        if len(set(names)) != len(names):
            raise ValueError('备份数量、体积或重复文件检查失败。')
        for item in entries:
            _relative(item.filename)
            if item.is_dir() or stat.S_IFMT(item.external_attr >> 16) not in {0, stat.S_IFREG}:
                raise ValueError('备份包含链接或特殊文件。')
        if 'manifest.json' not in names or bundle.getinfo('manifest.json').file_size > MAX_MANIFEST_BYTES:
            raise ValueError('备份缺少有效清单。')
        try:
            manifest = json.loads(bundle.read('manifest.json'))
            files = manifest['files']
            valid = manifest['format_version'] == 1 and isinstance(files, dict) and set(files) == set(names) - {'manifest.json'} and 'app.sqlite3' in files
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError('备份清单格式无效。') from exc
        if not valid:
            raise ValueError('备份版本或文件清单无效。')
        with tempfile.TemporaryDirectory(prefix='.finathink-restore-', dir=target.parent) as directory:
            restored = Path(directory) / 'state'
            restored.mkdir(mode=0o700)
            for name, declaration in files.items():
                raw = bundle.read(name)
                if not isinstance(declaration, dict) or declaration.get('size') != len(raw) or declaration.get('sha256') != hashlib.sha256(raw).hexdigest():
                    raise ValueError('备份文件内容与指纹不一致。')
                path = restored.joinpath(*_relative(name).parts)
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with path.open('xb') as handle:
                    os.chmod(path, 0o600)
                    handle.write(raw)
                if name == 'app.sqlite3' or path.suffix == '.sqlite3':
                    _idle(path)
            # Refuse an existing target even if another process created it
            # during verification. Do not replace an empty user directory.
            target.mkdir(mode=0o700)
            try:
                for path in restored.iterdir():
                    path.rename(target / path.name)
            except OSError:
                # Only this call's new directory is involved; never remove
                # an existing user's directory during recovery.
                raise ValueError('恢复写入未完成，请保留原备份并选择新的目录重试。') from None
    return target / 'app.sqlite3'


def main(argv=None):
    parser = argparse.ArgumentParser(description='Finathink 私密状态维护：先关闭应用；恢复不覆盖原文件。')
    commands = parser.add_subparsers(dest='command', required=True)
    backup = commands.add_parser('backup', help='备份指定应用状态（最多 1 GiB）')
    backup.add_argument('--db', required=True, type=Path)
    backup.add_argument('--output', required=True, type=Path)
    restore = commands.add_parser('restore', help='校验后恢复到一个新目录')
    restore.add_argument('--archive', required=True, type=Path)
    restore.add_argument('--to', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = backup_state(args.db, args.output) if args.command == 'backup' else restore_state(args.archive, args.to)
    except (ValueError, OSError, zipfile.BadZipFile):
        parser.exit(1, '维护未完成：请检查路径、磁盘空间与备份完整性，确认任务已结束且应用已关闭；使用新的输出位置重试。原始文件未覆盖。\n')
    print(f'私密备份已保存：{result}' if args.command == 'backup' else f'已恢复，可用 --db 指定此数据库启动：{result}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

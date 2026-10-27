"""Safe, repeatable source-only v0.6.0 -> v0.6.1 upgrade.

No database or browser profile is read, overwritten, or included in backups.
Usage:
  python tools/upgrade.py --target ../Browser-Automation-Gateway-v0.6.0-Python
  python tools/upgrade.py --target ../Browser-Automation-Gateway-v0.6.0-Python --apply
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent
CODE_DIRS = {'gateway': '.py', 'tests': '.py', 'web': '.html', 'tools': '.py', 'docs': '.md'}
TOP_FILES = (
    '.gitignore', 'CHANGELOG.md', 'README.md', 'client.py',
    'config.example.json', 'requirements.txt', 'requirements-dev.txt',
    'START_WINDOWS.bat', 'START_DEBIAN.sh',
    'UPGRADE_WINDOWS.bat', 'UPGRADE_DEBIAN.sh',
)
OLD_VERSION = '0.6.0'
NEW_VERSION = '0.6.1'


class UpgradeError(RuntimeError):
    pass


def read_version(folder: Path) -> str | None:
    path = folder / 'gateway' / '__init__.py'
    if not path.is_file():
        return None
    match = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", path.read_text(encoding='utf-8'))
    return match.group(1) if match else None


def source_files(folder: Path) -> list[Path]:
    paths = [Path(name) for name in TOP_FILES]
    for dirname, ext in CODE_DIRS.items():
        for file in (folder / dirname).rglob('*' + ext):
            paths.append(file.relative_to(folder))
    paths = sorted(set(paths))
    if any(not (folder / p).is_file() or (folder / p).is_symlink() for p in paths):
        raise UpgradeError('source_missing_or_symlink')
    return paths


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(131072), b''):
            h.update(block)
    return h.hexdigest()


def preflight(target: Path, source: Path) -> tuple[list[Path], str]:
    target, source = target.resolve(), source.resolve()
    if target == source or target in source.parents or source in target.parents:
        raise UpgradeError('target_and_source_must_be_separate_folders')
    current = read_version(target)
    if current != OLD_VERSION:
        raise UpgradeError(f'expected_v{OLD_VERSION}_target_got_{current or "unknown"}')
    if read_version(source) != NEW_VERSION:
        raise UpgradeError('upgrade_source_version_mismatch')
    files = source_files(source)
    # Do not patch an actively running gateway. A manually moved service port
    # could evade this check; stop the gateway regardless.
    try:
        cfg = json.loads((target / 'config.json').read_text(encoding='utf-8'))
        port = cfg.get('port', 8788)
        if isinstance(port, int) and 1 <= port <= 65535:
            with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                raise UpgradeError('gateway_port_in_use_stop_server_first')
    except (FileNotFoundError, ConnectionError, TimeoutError, OSError, json.JSONDecodeError):
        pass
    return files, current


def replace_atomically(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.bag-upgrade-', dir=dst.parent)
    try:
        with os.fdopen(fd, 'wb') as out, src.open('rb') as inp:
            shutil.copyfileobj(inp, out)
        shutil.copymode(src, tmp)
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def upgrade(target: Path, source: Path = SOURCE_ROOT, apply: bool = False) -> dict:
    target, source = Path(target).resolve(), Path(source).resolve()
    files, old_version = preflight(target, source)
    report = dict(from_version=old_version, to_version=NEW_VERSION, target=str(target),
                  mode='apply' if apply else 'dry_run', file_count=len(files),
                  preserved=['config.json', 'data/', '.venv/', '*.env'],
                  files=[str(p) for p in files])
    if not apply:
        return report
    backup = source / '_backups' / ('v0.6.1-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8])
    backup.mkdir(parents=True, mode=0o700)
    if os.name != 'nt':
        os.chmod(backup, 0o700)
    created, replaced = [], []
    try:
        # Backup entire previous source selection before touching any destination.
        for rel in files:
            current = target / rel
            ancestor = current.parent
            while ancestor != target and ancestor != ancestor.parent:
                if ancestor.is_symlink():
                    raise UpgradeError('target_contains_directory_symlink')
                ancestor = ancestor.parent
            if current.is_symlink():
                raise UpgradeError('target_contains_code_symlink')
            if current.is_file():
                dst = backup / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(current, dst)
                replaced.append(rel)
            elif not current.exists():
                created.append(rel)
            else:
                raise UpgradeError('code_destination_is_not_a_file')
        (backup / 'backup-manifest.json').write_text(json.dumps({
            'old_version': old_version, 'new_version': NEW_VERSION,
            'replaced': [str(p) for p in replaced],
            'created': [str(p) for p in created],
            'before_sha256': {str(p): sha256(backup/p) for p in replaced},
        }, indent=2), encoding='utf-8')
        for rel in files:
            replace_atomically(source / rel, target / rel)
            if sha256(target / rel) != sha256(source / rel):
                raise UpgradeError('copy_checksum_mismatch')
        if read_version(target) != NEW_VERSION:
            raise UpgradeError('post_upgrade_version_mismatch')
        report['backup_path'] = str(backup)
        report['status'] = 'applied'
        return report
    except Exception:
        # Restore source code only. Config, profiles and SQLite were never touched.
        for rel in reversed(replaced):
            if (backup / rel).is_file():
                replace_atomically(backup / rel, target / rel)
        for rel in created:
            (target / rel).unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description='BAG v0.6.1 source-only upgrade; default is dry run')
    parser.add_argument('--target', required=True, help='Existing v0.6.0 folder')
    parser.add_argument('--apply', action='store_true', help='Apply after reviewing dry run')
    args = parser.parse_args()
    try:
        print(json.dumps(upgrade(Path(args.target), apply=args.apply), ensure_ascii=False, indent=2))
        return 0
    except (UpgradeError, OSError, ValueError) as exc:
        print('UPGRADE FAILED:', str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from .runtime import database_url


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _migration_lock(database_path: Path) -> threading.Lock:
    key = str(Path(database_path).resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def migration_script_location() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "g_team_ops" / "db" / "migrations"
    return Path(__file__).resolve().parent / "migrations"


def alembic_config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option(
        "script_location",
        str(migration_script_location()),
    )
    config.set_main_option(
        "sqlalchemy.url",
        database_url(database_path).render_as_string(hide_password=False),
    )
    return config


def _current_revision(database_path: Path) -> str | None:
    if not database_path.exists() or database_path.stat().st_size == 0:
        return None
    with sqlite3.connect(database_path, timeout=10) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='alembic_version'"
        ).fetchone()
        if not exists:
            return None
        row = connection.execute(
            "SELECT version_num FROM alembic_version LIMIT 1"
        ).fetchone()
    return None if row is None else str(row[0])


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_before_upgrade(database_path: Path) -> tuple[Path, str] | None:
    if not database_path.exists() or database_path.stat().st_size == 0:
        return None
    backup_dir = database_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    target = backup_dir / f"app-before-migration-{stamp}.db"
    temporary = backup_dir / f".{target.name}.tmp"
    try:
        source = sqlite3.connect(database_path, timeout=10)
        destination = sqlite3.connect(temporary, timeout=10)
        try:
            source.backup(destination, pages=256, sleep=0.05)
            destination.commit()
            integrity = destination.execute(
                "PRAGMA integrity_check"
            ).fetchall()
            if [str(row[0]) for row in integrity] != ["ok"]:
                raise RuntimeError("迁移前数据库备份完整性检查失败")
        finally:
            destination.close()
            source.close()
        os.replace(temporary, target)
        return target, _file_sha256(target)
    finally:
        if temporary.exists():
            temporary.unlink()


def _catalog_migration_backup(
    database_path: Path,
    backup: tuple[Path, str] | None,
) -> None:
    if backup is None:
        return
    path, sha256 = backup
    with sqlite3.connect(database_path, timeout=10) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='backup_catalog'"
        ).fetchone()
        if not exists:
            return
        connection.execute(
            """
            INSERT OR IGNORE INTO backup_catalog(
                id, file_name, sha256, size_bytes, reason,
                integrity_result, created_by, created_at
            ) VALUES (?, ?, ?, ?, 'migration', 'ok', 'system', ?)
            """,
            (
                secrets.token_hex(16),
                path.name,
                sha256,
                path.stat().st_size,
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def upgrade_database(database_path: Path) -> None:
    """把现有或全新数据库安全升级到当前版本。"""
    resolved = Path(database_path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with _migration_lock(resolved):
        config = alembic_config(resolved)
        head = ScriptDirectory.from_config(config).get_current_head()
        current = _current_revision(resolved)
        if current == head:
            return
        backup = _backup_before_upgrade(resolved)
        command.upgrade(config, "head")
        _catalog_migration_backup(resolved, backup)

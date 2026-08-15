from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL


SQLITE_TIMEOUT_SECONDS = 10
SQLITE_BUSY_TIMEOUT_MILLISECONDS = SQLITE_TIMEOUT_SECONDS * 1000


def _prepare_parent(path: Path) -> Path:
    resolved = Path(path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def _configure_sqlite_connection(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute(
        f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MILLISECONDS}"
    )


def connect_sqlite(
    path: Path,
    *,
    row_factory: bool = False,
) -> sqlite3.Connection:
    """创建具有统一耐久性、外键和等待策略的SQLite连接。"""
    resolved = _prepare_parent(path)
    connection = sqlite3.connect(
        resolved,
        timeout=SQLITE_TIMEOUT_SECONDS,
    )
    if row_factory:
        connection.row_factory = sqlite3.Row
    _configure_sqlite_connection(connection)
    return connection


def database_url(path: Path) -> URL:
    return URL.create("sqlite+pysqlite", database=str(_prepare_parent(path)))


def create_database_engine(path: Path) -> Engine:
    """创建供新Repository和Alembic复用的SQLAlchemy Engine。"""
    engine = create_engine(
        database_url(path),
        future=True,
        pool_pre_ping=True,
        connect_args={"timeout": SQLITE_TIMEOUT_SECONDS},
    )

    @event.listens_for(engine, "connect")
    def configure_connection(dbapi_connection, _connection_record) -> None:
        _configure_sqlite_connection(dbapi_connection)

    return engine

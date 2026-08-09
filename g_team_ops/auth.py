from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .errors import ConfigurationError


PBKDF2_ITERATIONS = 600_000
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.\-\u4e00-\u9fff]{2,32}$")


@dataclass(frozen=True)
class UserAccount:
    id: str
    username: str
    display_name: str
    role: str
    is_active: bool
    must_change_password: bool
    created_at: str
    last_login_at: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_username(username: str) -> str:
    return username.strip().casefold()


def validate_username(username: str) -> str:
    value = username.strip()
    if not USERNAME_PATTERN.fullmatch(value):
        raise ConfigurationError("用户名需为2–32位中文、字母、数字、点、横线或下划线")
    return value


def validate_password(password: str) -> None:
    if len(password) < 8:
        raise ConfigurationError("登录密码至少需要8位")
    if len(password) > 128:
        raise ConfigurationError("登录密码不能超过128位")
    if password.isspace():
        raise ConfigurationError("登录密码不能只包含空格")


def hash_password(password: str) -> str:
    validate_password(password)
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(candidate, bytes.fromhex(digest_hex))
    except (TypeError, ValueError):
        return False


class UserRepository:
    """应用登录账号。这里只保存不可逆密码哈希，不保存任何业务密钥。"""

    def __init__(self, database_path: Path):
        self.path = database_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migration_backup_path = self._backup_legacy_database()
        self._initialize()

    def _backup_legacy_database(self) -> Path | None:
        """首次引入用户表前做一致性备份，避免迁移时影响既有配置。"""
        if not self.path.exists() or self.path.stat().st_size == 0:
            return None
        with sqlite3.connect(self.path) as source:
            tables = {
                row[0]
                for row in source.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "users" in tables or not tables.intersection(
                {"carrier_credentials", "airscript_settings", "wps_settings"}
            ):
                return None
            backup_dir = self.path.parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = backup_dir / f"app-before-users-{stamp}.db"
            with sqlite3.connect(backup_path) as target:
                source.backup(target)
            return backup_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    username_normalized TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin', 'user')),
                    is_active INTEGER NOT NULL DEFAULT 1,
                    must_change_password INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login_at TEXT
                )
                """
            )

    @staticmethod
    def _account(row: sqlite3.Row) -> UserAccount:
        return UserAccount(
            id=row["id"],
            username=row["username"],
            display_name=row["display_name"],
            role=row["role"],
            is_active=bool(row["is_active"]),
            must_change_password=bool(row["must_change_password"]),
            created_at=row["created_at"],
            last_login_at=row["last_login_at"],
        )

    def has_users(self) -> bool:
        with self._connect() as connection:
            return connection.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None

    def create_user(
        self,
        username: str,
        display_name: str,
        password: str,
        role: str = "user",
        must_change_password: bool = False,
        migrate_default_profile: bool = False,
        only_if_empty: bool = False,
    ) -> UserAccount:
        username = validate_username(username)
        display_name = display_name.strip() or username
        if len(display_name) > 40:
            raise ConfigurationError("显示名称不能超过40位")
        if role not in {"admin", "user"}:
            raise ConfigurationError("账号角色无效")
        user_id = secrets.token_hex(16)
        timestamp = _now()
        try:
            with self._connect() as connection:
                if only_if_empty:
                    connection.execute("BEGIN IMMEDIATE")
                    if connection.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                        raise ConfigurationError("系统已经完成初始化")
                connection.execute(
                    """
                    INSERT INTO users(
                        id, username, username_normalized, display_name, password_hash,
                        role, is_active, must_change_password, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        user_id,
                        username,
                        _normalize_username(username),
                        display_name,
                        hash_password(password),
                        role,
                        int(must_change_password),
                        timestamp,
                        timestamp,
                    ),
                )
                if migrate_default_profile:
                    self._migrate_profile(connection, "default", user_id)
        except sqlite3.IntegrityError as exc:
            raise ConfigurationError("该用户名已经存在") from exc
        return self.get_user(user_id)

    @staticmethod
    def _migrate_profile(
        connection: sqlite3.Connection, source_profile: str, target_profile: str
    ) -> None:
        for table in (
            "carrier_credentials",
            "carrier_sessions",
            "wps_settings",
            "airscript_settings",
            "shops",
        ):
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if exists:
                connection.execute(
                    f"UPDATE OR IGNORE {table} SET profile_id=? WHERE profile_id=?",
                    (target_profile, source_profile),
                )

    def authenticate(self, username: str, password: str) -> UserAccount:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username_normalized=?",
                (_normalize_username(username),),
            ).fetchone()
            if row is None or not verify_password(password, row["password_hash"]):
                raise ConfigurationError("用户名或密码错误")
            if not row["is_active"]:
                raise ConfigurationError("账号已停用，请联系管理员")
            timestamp = _now()
            connection.execute(
                "UPDATE users SET last_login_at=?, updated_at=? WHERE id=?",
                (timestamp, timestamp, row["id"]),
            )
        return self.get_user(row["id"])

    def get_user(self, user_id: str) -> UserAccount:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise ConfigurationError("用户不存在")
        return self._account(row)

    def list_users(self) -> list[UserAccount]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM users ORDER BY role ASC, created_at ASC"
            ).fetchall()
        return [self._account(row) for row in rows]

    def set_active(self, actor_id: str, user_id: str, active: bool) -> None:
        actor = self.get_user(actor_id)
        target = self.get_user(user_id)
        if not actor.is_admin:
            raise ConfigurationError("只有管理员可以管理账号")
        if actor.id == target.id and not active:
            raise ConfigurationError("不能停用当前登录的管理员账号")
        with self._connect() as connection:
            connection.execute(
                "UPDATE users SET is_active=?, updated_at=? WHERE id=?",
                (int(active), _now(), user_id),
            )

    def reset_password(self, actor_id: str, user_id: str, new_password: str) -> None:
        actor = self.get_user(actor_id)
        if not actor.is_admin:
            raise ConfigurationError("只有管理员可以重置密码")
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE users SET password_hash=?, must_change_password=1, updated_at=?
                WHERE id=?
                """,
                (hash_password(new_password), _now(), user_id),
            )
        if updated.rowcount != 1:
            raise ConfigurationError("用户不存在")

    def change_password(self, user_id: str, old_password: str, new_password: str) -> None:
        account = self.get_user(user_id)
        self.authenticate(account.username, old_password)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE users SET password_hash=?, must_change_password=0, updated_at=?
                WHERE id=?
                """,
                (hash_password(new_password), _now(), user_id),
            )

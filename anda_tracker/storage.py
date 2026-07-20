from __future__ import annotations

import base64
import ctypes
import sqlite3
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .airscript import AirScriptConfig
from .errors import ConfigurationError
from .wps import DEFAULT_REDIRECT_URI, WpsCredentials, WpsSheetBinding, WpsTokens


SYSTEM_MAX_QUERY_COUNT = 50


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob_from_bytes(value: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(value)
    blob = _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    return blob, buffer


def protect_secret(plaintext: str) -> str:
    """用当前 Windows 用户的 DPAPI 加密，返回可存入数据库的密文。"""
    if not plaintext:
        raise ConfigurationError("密码不能为空")
    if not hasattr(ctypes, "windll"):
        raise ConfigurationError("当前系统不支持 Windows DPAPI")
    input_blob, _buffer = _blob_from_bytes(plaintext.encode("utf-8"))
    output_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not crypt32.CryptProtectData(
        ctypes.byref(input_blob), None, None, None, None, 0x01, ctypes.byref(output_blob)
    ):
        raise ConfigurationError("无法使用 Windows DPAPI 加密密码")
    try:
        encrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
        return base64.b64encode(encrypted).decode("ascii")
    finally:
        kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))


def unprotect_secret(ciphertext: str) -> str:
    try:
        encrypted = base64.b64decode(ciphertext, validate=True)
    except (ValueError, TypeError) as exc:
        raise ConfigurationError("数据库中的密码密文格式无效") from exc
    input_blob, _buffer = _blob_from_bytes(encrypted)
    output_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not crypt32.CryptUnprotectData(
        ctypes.byref(input_blob), None, None, None, None, 0x01, ctypes.byref(output_blob)
    ):
        raise ConfigurationError("无法解密密码；该数据库可能来自其他 Windows 用户或电脑")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigurationError("解密后的密码数据无效") from exc
    finally:
        kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))


@dataclass(frozen=True)
class StoredCredentials:
    username: str
    password: str


class ProjectDatabase:
    """项目内 SQLite：账号按 profile/carrier 隔离，密码字段只保存 DPAPI 密文。"""

    def __init__(self, path: Path, profile_id: str = "default"):
        self.path = path
        self.profile_id = profile_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS carrier_credentials (
                    profile_id TEXT NOT NULL,
                    carrier TEXT NOT NULL,
                    username TEXT NOT NULL,
                    password_ciphertext TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (profile_id, carrier)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS system_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS carrier_sessions (
                    profile_id TEXT NOT NULL,
                    carrier TEXT NOT NULL,
                    token_ciphertext TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (profile_id, carrier)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS wps_settings (
                    profile_id TEXT PRIMARY KEY,
                    app_id TEXT NOT NULL,
                    app_secret_ciphertext TEXT NOT NULL,
                    share_url TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL,
                    access_token_ciphertext TEXT,
                    refresh_token_ciphertext TEXT,
                    expires_at REAL,
                    file_id TEXT,
                    worksheet_id INTEGER,
                    worksheet_name TEXT,
                    max_row INTEGER,
                    max_col INTEGER,
                    fba_col INTEGER,
                    route_col INTEGER,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS airscript_settings (
                    profile_id TEXT PRIMARY KEY,
                    share_url TEXT NOT NULL,
                    webhook_url TEXT NOT NULL,
                    api_token_ciphertext TEXT NOT NULL,
                    sheet_name TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            existing_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(wps_settings)")
            }
            if "fba_col" not in existing_columns:
                connection.execute("ALTER TABLE wps_settings ADD COLUMN fba_col INTEGER")
            if "route_col" not in existing_columns:
                connection.execute("ALTER TABLE wps_settings ADD COLUMN route_col INTEGER")
            connection.execute(
                "INSERT OR IGNORE INTO system_settings(setting_key, setting_value) VALUES('max_query_count', ?)",
                (str(SYSTEM_MAX_QUERY_COUNT),),
            )

    def save_credentials(self, carrier: str, username: str, password: str) -> None:
        username = username.strip()
        carrier = carrier.strip().lower()
        if not carrier or not username or not password:
            raise ConfigurationError("账号和密码不能为空")
        ciphertext = protect_secret(password)
        updated_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO carrier_credentials
                    (profile_id, carrier, username, password_ciphertext, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(profile_id, carrier) DO UPDATE SET
                    username=excluded.username,
                    password_ciphertext=excluded.password_ciphertext,
                    updated_at=excluded.updated_at
                """,
                (self.profile_id, carrier, username, ciphertext, updated_at),
            )

    def load_credentials(self, carrier: str) -> StoredCredentials | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT username, password_ciphertext
                FROM carrier_credentials
                WHERE profile_id=? AND carrier=?
                """,
                (self.profile_id, carrier.strip().lower()),
            ).fetchone()
        if row is None:
            return None
        return StoredCredentials(username=row[0], password=unprotect_secret(row[1]))

    def delete_credentials(self, carrier: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM carrier_credentials WHERE profile_id=? AND carrier=?",
                (self.profile_id, carrier.strip().lower()),
            )

    def save_session_token(self, carrier: str, token: str) -> None:
        carrier = carrier.strip().lower()
        ciphertext = protect_secret(token)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO carrier_sessions(profile_id, carrier, token_ciphertext, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(profile_id, carrier) DO UPDATE SET
                    token_ciphertext=excluded.token_ciphertext,
                    updated_at=excluded.updated_at
                """,
                (self.profile_id, carrier, ciphertext, datetime.now(timezone.utc).isoformat()),
            )

    def load_session_token(self, carrier: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT token_ciphertext FROM carrier_sessions WHERE profile_id=? AND carrier=?",
                (self.profile_id, carrier.strip().lower()),
            ).fetchone()
        return None if row is None else unprotect_secret(row[0])

    def delete_session_token(self, carrier: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM carrier_sessions WHERE profile_id=? AND carrier=?",
                (self.profile_id, carrier.strip().lower()),
            )

    def save_anda_credentials(self, username: str, password: str) -> None:
        self.save_credentials("anda", username, password)

    def load_anda_credentials(self) -> StoredCredentials | None:
        return self.load_credentials("anda")

    def delete_anda_credentials(self) -> None:
        self.delete_credentials("anda")

    def save_wps_credentials(self, credentials: WpsCredentials) -> None:
        app_id = credentials.app_id.strip()
        share_url = credentials.share_url.strip()
        if not app_id or not credentials.app_secret or not share_url:
            raise ConfigurationError("WPS APPID、APPKEY和共享表链接不能为空")
        secret_ciphertext = protect_secret(credentials.app_secret)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT app_id FROM wps_settings WHERE profile_id=?", (self.profile_id,)
            ).fetchone()
            app_changed = existing is not None and existing[0] != app_id
            connection.execute(
                """
                INSERT INTO wps_settings(
                    profile_id, app_id, app_secret_ciphertext, share_url, redirect_uri,
                    fba_col, route_col, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id) DO UPDATE SET
                    app_id=excluded.app_id,
                    app_secret_ciphertext=excluded.app_secret_ciphertext,
                    share_url=excluded.share_url,
                    redirect_uri=excluded.redirect_uri,
                    fba_col=excluded.fba_col,
                    route_col=excluded.route_col,
                    updated_at=excluded.updated_at
                """,
                (
                    self.profile_id,
                    app_id,
                    secret_ciphertext,
                    share_url,
                    credentials.redirect_uri or DEFAULT_REDIRECT_URI,
                    credentials.fba_col,
                    credentials.route_col,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            if app_changed:
                connection.execute(
                    """
                    UPDATE wps_settings SET
                        access_token_ciphertext=NULL, refresh_token_ciphertext=NULL, expires_at=NULL,
                        file_id=NULL, worksheet_id=NULL, worksheet_name=NULL, max_row=NULL, max_col=NULL
                    WHERE profile_id=?
                    """,
                    (self.profile_id,),
                )

    def load_wps_credentials(self) -> WpsCredentials | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT app_id, app_secret_ciphertext, share_url, redirect_uri, fba_col, route_col
                FROM wps_settings WHERE profile_id=?
                """,
                (self.profile_id,),
            ).fetchone()
        if row is None:
            return None
        return WpsCredentials(
            app_id=row[0],
            app_secret=unprotect_secret(row[1]),
            share_url=row[2],
                redirect_uri=row[3],
                fba_col=int(row[4]) if row[4] is not None else None,
                route_col=int(row[5]) if row[5] is not None else None,
        )

    def save_wps_tokens(self, tokens: WpsTokens) -> None:
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE wps_settings SET
                    access_token_ciphertext=?, refresh_token_ciphertext=?, expires_at=?, updated_at=?
                WHERE profile_id=?
                """,
                (
                    protect_secret(tokens.access_token),
                    protect_secret(tokens.refresh_token),
                    tokens.expires_at,
                    datetime.now(timezone.utc).isoformat(),
                    self.profile_id,
                ),
            )
        if updated.rowcount != 1:
            raise ConfigurationError("请先保存 WPS 应用配置")

    def load_wps_tokens(self) -> WpsTokens | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT access_token_ciphertext, refresh_token_ciphertext, expires_at
                FROM wps_settings WHERE profile_id=?
                """,
                (self.profile_id,),
            ).fetchone()
        if row is None or not row[0] or not row[1] or row[2] is None:
            return None
        return WpsTokens(
            access_token=unprotect_secret(row[0]),
            refresh_token=unprotect_secret(row[1]),
            expires_at=float(row[2]),
        )

    def save_wps_binding(self, binding: WpsSheetBinding) -> None:
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE wps_settings SET
                    file_id=?, worksheet_id=?, worksheet_name=?, max_row=?, max_col=?,
                    fba_col=?, route_col=?, updated_at=?
                WHERE profile_id=?
                """,
                (
                    binding.file_id,
                    binding.worksheet_id,
                    binding.worksheet_name,
                    binding.max_row,
                    binding.max_col,
                    binding.fba_col,
                    binding.route_col,
                    datetime.now(timezone.utc).isoformat(),
                    self.profile_id,
                ),
            )
        if updated.rowcount != 1:
            raise ConfigurationError("请先保存 WPS 应用配置")

    def load_wps_binding(self) -> WpsSheetBinding | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT file_id, worksheet_id, worksheet_name, max_row, max_col, fba_col, route_col
                FROM wps_settings WHERE profile_id=?
                """,
                (self.profile_id,),
            ).fetchone()
        if row is None or row[0] is None or row[1] is None:
            return None
        return WpsSheetBinding(
            file_id=row[0],
            worksheet_id=int(row[1]),
            worksheet_name=row[2],
            max_row=int(row[3] or 0),
                max_col=int(row[4] or 0),
                fba_col=int(row[5]) if row[5] is not None else None,
                route_col=int(row[6]) if row[6] is not None else None,
        )

    def clear_wps_authorization(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE wps_settings SET
                    access_token_ciphertext=NULL, refresh_token_ciphertext=NULL, expires_at=NULL,
                    file_id=NULL, worksheet_id=NULL, worksheet_name=NULL, max_row=NULL, max_col=NULL
                WHERE profile_id=?
                """,
                (self.profile_id,),
            )

    def save_airscript_config(self, config: AirScriptConfig) -> None:
        share_url = config.share_url.strip()
        webhook_url = config.webhook_url.strip()
        sheet_name = config.sheet_name.strip()
        if not share_url or not webhook_url or not config.api_token or not sheet_name:
            raise ConfigurationError("共享表链接、AirScript webhook和脚本令牌不能为空")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO airscript_settings(
                    profile_id, share_url, webhook_url, api_token_ciphertext,
                    sheet_name, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id) DO UPDATE SET
                    share_url=excluded.share_url,
                    webhook_url=excluded.webhook_url,
                    api_token_ciphertext=excluded.api_token_ciphertext,
                    sheet_name=excluded.sheet_name,
                    updated_at=excluded.updated_at
                """,
                (
                    self.profile_id,
                    share_url,
                    webhook_url,
                    protect_secret(config.api_token),
                    sheet_name,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def load_airscript_config(self) -> AirScriptConfig | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT share_url, webhook_url, api_token_ciphertext, sheet_name
                FROM airscript_settings WHERE profile_id=?
                """,
                (self.profile_id,),
            ).fetchone()
        if row is None:
            return None
        return AirScriptConfig(
            share_url=row[0],
            webhook_url=row[1],
            api_token=unprotect_secret(row[2]),
            sheet_name=row[3],
        )

    def delete_airscript_config(self) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM airscript_settings WHERE profile_id=?", (self.profile_id,)
            )

    def max_query_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT setting_value FROM system_settings WHERE setting_key='max_query_count'"
            ).fetchone()
        if row is None:
            return SYSTEM_MAX_QUERY_COUNT
        try:
            return min(SYSTEM_MAX_QUERY_COUNT, max(1, int(row[0])))
        except (TypeError, ValueError):
            return SYSTEM_MAX_QUERY_COUNT

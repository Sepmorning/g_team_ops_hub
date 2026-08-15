from __future__ import annotations

import base64
import ctypes
import json
import re
import sqlite3
import secrets
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .airscript import AirScriptConfig
from .db.migration import upgrade_database
from .db.runtime import connect_sqlite
from .errors import ConfigurationError
from .sites import infer_country_code, normalize_country_code


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


@dataclass(frozen=True)
class StoredShop:
    id: str
    name: str
    listing_prefix: str
    config: AirScriptConfig
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class StoredListingConnection:
    shop_id: str
    webhook_url: str
    api_token: str
    updated_at: str


@dataclass(frozen=True)
class StoredShopCountry:
    id: str
    shop_id: str
    country_name: str
    sheet_name: str
    country_code: str
    fba_sheet_name: str
    detail_sheet_name: str
    created_at: str
    updated_at: str


class ProjectDatabase:
    """项目内 SQLite：账号按 profile/carrier 隔离，密码字段只保存 DPAPI 密文。"""

    def __init__(self, path: Path, profile_id: str = "default"):
        self.path = path
        self.profile_id = profile_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.shop_migration_backup_path = self._backup_before_shop_migration()
        self._initialize()

    def _backup_before_shop_migration(self) -> Path | None:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return None
        with sqlite3.connect(self.path) as source:
            tables = {
                row[0]
                for row in source.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "shops" in tables or "airscript_settings" not in tables:
                return None
            backup_dir = self.path.parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = backup_dir / f"app-before-shops-{stamp}.db"
            with sqlite3.connect(backup_path) as target:
                source.backup(target)
            return backup_path

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.path)

    def _initialize(self) -> None:
        upgrade_database(self.path)

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

    def credential_username(self, carrier: str) -> str | None:
        """只读取非敏感账号名，用于状态页面，避免无必要地解密密码。"""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT username FROM carrier_credentials
                WHERE profile_id=? AND carrier=?
                """,
                (self.profile_id, carrier.strip().lower()),
            ).fetchone()
        return None if row is None else str(row[0])

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

    def load_tracking_cache(
        self, carrier: str, fba: str
    ) -> tuple[int, str, str, dict] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT schema_version, latest_time, latest_event, payload_json
                FROM tracking_detail_cache
                WHERE profile_id=? AND carrier=? AND fba=?
                """,
                (
                    self.profile_id,
                    carrier.strip().lower(),
                    fba.strip().upper(),
                ),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(str(row[3]))
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return int(row[0]), str(row[1]), str(row[2]), payload

    def save_tracking_cache(
        self,
        carrier: str,
        fba: str,
        schema_version: int,
        latest_time: str,
        latest_event: str,
        payload: dict,
    ) -> None:
        updated_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO tracking_detail_cache(
                    profile_id, carrier, fba, schema_version,
                    latest_time, latest_event, payload_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id, carrier, fba) DO UPDATE SET
                    schema_version=excluded.schema_version,
                    latest_time=excluded.latest_time,
                    latest_event=excluded.latest_event,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    self.profile_id,
                    carrier.strip().lower(),
                    fba.strip().upper(),
                    int(schema_version),
                    str(latest_time or ""),
                    str(latest_event or ""),
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    updated_at,
                ),
            )

    def list_shops(self) -> list[StoredShop]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, name, share_url, webhook_url, api_token_ciphertext,
                       sheet_name, listing_prefix, created_at, updated_at
                FROM shops WHERE profile_id=? ORDER BY created_at, name
                """,
                (self.profile_id,),
            ).fetchall()
        return [self._shop_from_row(row) for row in rows]

    def get_shop(self, shop_id: str) -> StoredShop | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, name, share_url, webhook_url, api_token_ciphertext,
                       sheet_name, listing_prefix, created_at, updated_at
                FROM shops WHERE profile_id=? AND id=?
                """,
                (self.profile_id, shop_id),
            ).fetchone()
        return None if row is None else self._shop_from_row(row)

    @staticmethod
    def _shop_from_row(row) -> StoredShop:
        encrypted_token = str(row[4] or "")
        return StoredShop(
            id=str(row[0]),
            name=str(row[1]),
            listing_prefix=str(row[6] or ""),
            config=AirScriptConfig(
                share_url=str(row[2]),
                webhook_url=str(row[3]),
                api_token=(
                    unprotect_secret(encrypted_token) if encrypted_token else ""
                ),
                sheet_name=str(row[5]),
            ),
            created_at=str(row[7]),
            updated_at=str(row[8]),
        )

    def save_shop_listing_prefix(
        self, shop_id: str, listing_prefix: str
    ) -> StoredShop:
        listing_prefix = listing_prefix.strip()
        if not listing_prefix or len(listing_prefix) > 60:
            raise ConfigurationError(
                "Listing子表前缀不能为空且不能超过60位"
            )
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE shops SET listing_prefix=?, updated_at=?
                WHERE id=? AND profile_id=?
                """,
                (listing_prefix, timestamp, shop_id, self.profile_id),
            )
        if updated.rowcount != 1:
            raise ConfigurationError("店铺不存在或不属于当前用户")
        shop = self.get_shop(shop_id)
        assert shop is not None
        return shop

    def save_shop(
        self, name: str, config: AirScriptConfig, shop_id: str | None = None
    ) -> StoredShop:
        name = name.strip()
        if not name or len(name) > 60:
            raise ConfigurationError("店铺名称不能为空且不能超过60位")
        share_url = config.share_url.strip()
        webhook_url = config.webhook_url.strip()
        sheet_name = config.sheet_name.strip()
        if not share_url or not sheet_name:
            raise ConfigurationError("店铺名称和共享表链接不能为空")
        if bool(webhook_url) != bool(config.api_token):
            raise ConfigurationError("物流脚本Webhook和令牌必须同时填写")
        timestamp = datetime.now(timezone.utc).isoformat()
        encrypted_token = protect_secret(config.api_token) if config.api_token else ""
        try:
            with self._connect() as connection:
                if shop_id:
                    updated = connection.execute(
                        """
                        UPDATE shops SET name=?, share_url=?, webhook_url=?,
                            api_token_ciphertext=?, sheet_name=?, updated_at=?
                        WHERE id=? AND profile_id=?
                        """,
                        (
                            name,
                            share_url,
                            webhook_url,
                            encrypted_token,
                            sheet_name,
                            timestamp,
                            shop_id,
                            self.profile_id,
                        ),
                    )
                    if updated.rowcount != 1:
                        raise ConfigurationError("店铺不存在或不属于当前用户")
                else:
                    shop_id = secrets.token_hex(16)
                    connection.execute(
                        """
                        INSERT INTO shops(
                            id, profile_id, name, share_url, webhook_url,
                            api_token_ciphertext, sheet_name, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            shop_id,
                            self.profile_id,
                            name,
                            share_url,
                            webhook_url,
                            encrypted_token,
                            sheet_name,
                            timestamp,
                            timestamp,
                        ),
                    )
        except sqlite3.IntegrityError as exc:
            raise ConfigurationError("当前账号已经存在同名店铺") from exc
        shop = self.get_shop(shop_id)
        assert shop is not None
        return shop

    def delete_shop(self, shop_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM listing_connections WHERE shop_id=? AND profile_id=?",
                (shop_id, self.profile_id),
            )
            connection.execute(
                "DELETE FROM shop_countries WHERE shop_id=? AND profile_id=?",
                (shop_id, self.profile_id),
            )
            deleted = connection.execute(
                "DELETE FROM shops WHERE id=? AND profile_id=?",
                (shop_id, self.profile_id),
            )
        if deleted.rowcount != 1:
            raise ConfigurationError("店铺不存在或不属于当前用户")

    def load_listing_connection(
        self, shop_id: str
    ) -> StoredListingConnection | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT shop_id, webhook_url, api_token_ciphertext, updated_at
                FROM listing_connections
                WHERE profile_id=? AND shop_id=?
                """,
                (self.profile_id, shop_id),
            ).fetchone()
        if row is None:
            return None
        return StoredListingConnection(
            shop_id=str(row[0]),
            webhook_url=str(row[1]),
            api_token=unprotect_secret(str(row[2])),
            updated_at=str(row[3]),
        )

    def save_listing_connection(
        self, shop_id: str, webhook_url: str, api_token: str
    ) -> StoredListingConnection:
        if self.get_shop(shop_id) is None:
            raise ConfigurationError("店铺不存在或不属于当前用户")
        webhook_url = webhook_url.strip()
        if not webhook_url or not api_token:
            raise ConfigurationError("Listing脚本Webhook和令牌不能为空")
        timestamp = datetime.now(timezone.utc).isoformat()
        ciphertext = protect_secret(api_token)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO listing_connections(
                    profile_id, shop_id, webhook_url,
                    api_token_ciphertext, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(profile_id, shop_id) DO UPDATE SET
                    webhook_url=excluded.webhook_url,
                    api_token_ciphertext=excluded.api_token_ciphertext,
                    updated_at=excluded.updated_at
                """,
                (
                    self.profile_id,
                    shop_id,
                    webhook_url,
                    ciphertext,
                    timestamp,
                ),
            )
        stored = self.load_listing_connection(shop_id)
        assert stored is not None
        return stored

    def list_shop_countries(self, shop_id: str) -> list[StoredShopCountry]:
        if self.get_shop(shop_id) is None:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, shop_id, country_name, sheet_name,
                       country_code, fba_sheet_name, detail_sheet_name,
                       created_at, updated_at
                FROM shop_countries
                WHERE profile_id=? AND shop_id=?
                ORDER BY created_at, country_name
                """,
                (self.profile_id, shop_id),
            ).fetchall()
        return [
            StoredShopCountry(
                id=str(row[0]),
                shop_id=str(row[1]),
                country_name=str(row[2]),
                sheet_name=str(row[3]),
                country_code=(
                    normalize_country_code(str(row[4]))
                    or infer_country_code(str(row[2]))
                ),
                fba_sheet_name=(
                    str(row[5])
                    or (
                        "US-FBA"
                        if infer_country_code(str(row[2])) == "US"
                        else ""
                    )
                ),
                detail_sheet_name=(
                    str(row[6])
                    or (
                        "US-轨迹明细"
                        if infer_country_code(str(row[2])) == "US"
                        else ""
                    )
                ),
                created_at=str(row[7]),
                updated_at=str(row[8]),
            )
            for row in rows
        ]

    def get_shop_country(self, country_id: str) -> StoredShopCountry | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, shop_id, country_name, sheet_name,
                       country_code, fba_sheet_name, detail_sheet_name,
                       created_at, updated_at
                FROM shop_countries
                WHERE profile_id=? AND id=?
                """,
                (self.profile_id, country_id),
            ).fetchone()
        if row is None:
            return None
        return StoredShopCountry(
            id=str(row[0]),
            shop_id=str(row[1]),
            country_name=str(row[2]),
            sheet_name=str(row[3]),
            country_code=(
                normalize_country_code(str(row[4]))
                or infer_country_code(str(row[2]))
            ),
            fba_sheet_name=(
                str(row[5])
                or (
                    "US-FBA"
                    if infer_country_code(str(row[2])) == "US"
                    else ""
                )
            ),
            detail_sheet_name=(
                str(row[6])
                or (
                    "US-轨迹明细"
                    if infer_country_code(str(row[2])) == "US"
                    else ""
                )
            ),
            created_at=str(row[7]),
            updated_at=str(row[8]),
        )

    def save_shop_country(
        self,
        shop_id: str,
        country_name: str,
        sheet_name: str,
        country_id: str | None = None,
        *,
        country_code: str = "",
        fba_sheet_name: str = "",
        detail_sheet_name: str = "",
    ) -> StoredShopCountry:
        if self.get_shop(shop_id) is None:
            raise ConfigurationError("店铺不存在或不属于当前用户")
        country_name = country_name.strip()
        sheet_name = sheet_name.strip()
        country_code = normalize_country_code(country_code) or infer_country_code(
            country_name
        )
        fba_sheet_name = fba_sheet_name.strip()
        detail_sheet_name = detail_sheet_name.strip()
        if not country_name or len(country_name) > 40:
            raise ConfigurationError("国家名称不能为空且不能超过40位")
        if not sheet_name or len(sheet_name) > 80:
            raise ConfigurationError("Listing子表名称不能为空且不能超过80位")
        if country_code and not re.fullmatch(r"[A-Z]{2,3}", country_code):
            raise ConfigurationError("国家代码应为2至3位英文字母")
        if len(fba_sheet_name) > 80 or len(detail_sheet_name) > 80:
            raise ConfigurationError("FBA主表和轨迹明细表名称不能超过80位")
        timestamp = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as connection:
                if country_id:
                    updated = connection.execute(
                        """
                        UPDATE shop_countries
                        SET country_name=?, sheet_name=?, country_code=?,
                            fba_sheet_name=?, detail_sheet_name=?, updated_at=?
                        WHERE id=? AND profile_id=? AND shop_id=?
                        """,
                        (
                            country_name,
                            sheet_name,
                            country_code,
                            fba_sheet_name,
                            detail_sheet_name,
                            timestamp,
                            country_id,
                            self.profile_id,
                            shop_id,
                        ),
                    )
                    if updated.rowcount != 1:
                        raise ConfigurationError(
                            "国家配置不存在或不属于当前店铺"
                        )
                else:
                    country_id = secrets.token_hex(16)
                    connection.execute(
                        """
                        INSERT INTO shop_countries(
                            id, profile_id, shop_id, country_name,
                            sheet_name, country_code, fba_sheet_name,
                            detail_sheet_name, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            country_id,
                            self.profile_id,
                            shop_id,
                            country_name,
                            sheet_name,
                            country_code,
                            fba_sheet_name,
                            detail_sheet_name,
                            timestamp,
                            timestamp,
                        ),
                    )
        except sqlite3.IntegrityError as exc:
            raise ConfigurationError(
                "当前店铺已存在同名国家或相同Listing子表"
            ) from exc
        stored = self.get_shop_country(country_id)
        assert stored is not None
        return stored

    def delete_shop_country(self, country_id: str) -> None:
        with self._connect() as connection:
            deleted = connection.execute(
                "DELETE FROM shop_countries WHERE id=? AND profile_id=?",
                (country_id, self.profile_id),
            )
        if deleted.rowcount != 1:
            raise ConfigurationError("国家配置不存在或不属于当前用户")

    def query_batch_size(self) -> int:
        """单个内部查询批次上限；不限制用户一次任务的总数量。"""
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

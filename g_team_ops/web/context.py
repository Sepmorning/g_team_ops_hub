"""Web层共享上下文。

功能路由只通过本对象取得仓储、业务服务和页面依赖，避免再次形成一个
同时导入所有模块的路由文件。这里不承载具体业务规则。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from ..airscript import AirScriptConfig
from ..auth import UserAccount, UserRepository
from ..errors import ConfigurationError
from ..listing import (
    ListingConnectionConfig,
    ListingPreviewRegistry,
)
from ..db.backup import DatabaseBackupService
from ..modules.operations.repository import OperationRepository
from ..storage import ProjectDatabase
from .services import CaptchaRegistry, QueryCoordinator


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def check_csrf(request: Request, supplied: str | None) -> None:
    expected = request.session.get("csrf")
    if (
        not expected
        or not supplied
        or not secrets.compare_digest(expected, supplied)
    ):
        raise HTTPException(
            status_code=403,
            detail="页面安全校验已失效，请刷新后重试",
        )


def json_error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "message": message}, status_code=status)


@dataclass(frozen=True)
class WebContext:
    data_dir: Path
    database_path: Path
    settings_path: Path
    users: UserRepository
    coordinator: QueryCoordinator
    captchas: CaptchaRegistry
    listing_previews: ListingPreviewRegistry
    templates: Jinja2Templates
    logger: Any
    operations: OperationRepository
    backups: DatabaseBackupService

    async def json_payload(self, request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail="请求内容不是有效JSON",
            ) from exc
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=400,
                detail="请求内容必须是JSON对象",
            )
        return payload

    def current_account(self, request: Request) -> UserAccount | None:
        user_id = request.session.get("user_id")
        if not user_id:
            return None
        try:
            account = self.users.get_user(user_id)
        except ConfigurationError:
            request.session.clear()
            return None
        if not account.is_active:
            request.session.clear()
            return None
        return account

    def page_context(
        self,
        request: Request,
        account: UserAccount | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        return {
            "request": request,
            "account": account,
            "csrf_token": csrf_token(request),
            **extra,
        }

    def require_api_user(self, request: Request) -> UserAccount:
        account = self.current_account(request)
        if account is None:
            raise HTTPException(status_code=401, detail="登录状态已失效")
        if account.must_change_password:
            raise HTTPException(
                status_code=403,
                detail="请先修改临时登录密码",
            )
        return account

    def database_for(self, user_id: str) -> ProjectDatabase:
        return ProjectDatabase(self.database_path, profile_id=user_id)

    def listing_config(
        self,
        database: ProjectDatabase,
        shop_id: str,
        country_id: str,
    ) -> tuple[ListingConnectionConfig, Any, Any]:
        shop = database.get_shop(shop_id)
        if shop is None:
            raise ConfigurationError("店铺不存在或不属于当前用户")
        country = database.get_shop_country(country_id)
        if country is None or country.shop_id != shop_id:
            raise ConfigurationError("国家配置不存在或不属于当前店铺")
        connection = database.load_listing_connection(shop_id)
        if connection is None:
            raise ConfigurationError("该店铺尚未配置独立的Listing AirScript")
        return (
            ListingConnectionConfig(
                share_url=shop.config.share_url,
                webhook_url=connection.webhook_url,
                api_token=connection.api_token,
                sheet_name=country.sheet_name,
            ),
            shop,
            country,
        )

    def logistics_config(
        self,
        database: ProjectDatabase,
        shop_id: str,
        country_id: str,
    ) -> tuple[AirScriptConfig, Any, Any]:
        shop = database.get_shop(shop_id)
        if shop is None:
            raise ConfigurationError("店铺不存在或不属于当前用户")
        if not shop.config.webhook_url or not shop.config.api_token:
            raise ConfigurationError("该店铺尚未配置FBA物流AirScript")
        country = database.get_shop_country(country_id)
        if country is None or country.shop_id != shop_id:
            raise ConfigurationError("国家站点不存在或不属于当前店铺")
        if not country.fba_sheet_name or not country.detail_sheet_name:
            raise ConfigurationError(
                "该国家站点尚未配置FBA主表和轨迹明细表"
            )
        return (
            AirScriptConfig(
                share_url=shop.config.share_url,
                webhook_url=shop.config.webhook_url,
                api_token=shop.config.api_token,
                sheet_name=country.fba_sheet_name,
                detail_sheet_name=country.detail_sheet_name,
            ),
            shop,
            country,
        )

    def site_payload(
        self,
        database: ProjectDatabase,
        item: Any,
    ) -> dict[str, Any]:
        listing_connection = database.load_listing_connection(item.shop_id)
        shop = database.get_shop(item.shop_id)
        return {
            "id": item.id,
            "country_code": item.country_code,
            "country_name": item.country_name,
            "sheet_name": item.sheet_name,
            "listing_sheet_name": item.sheet_name,
            "fba_sheet_name": item.fba_sheet_name,
            "detail_sheet_name": item.detail_sheet_name,
            "listing_ready": bool(listing_connection and item.sheet_name),
            "tracking_ready": bool(
                shop
                and shop.config.webhook_url
                and shop.config.api_token
                and item.fba_sheet_name
                and item.detail_sheet_name
            ),
        }

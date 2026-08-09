"""FastAPI应用工厂。

这里只装配共享依赖和业务模块，不放置具体业务路由。
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from ..auth import UserRepository
from ..listing import ListingPreviewRegistry
from ..logging_config import configure_logging
from ..modules.accounts import build_router as build_accounts_router
from ..modules.carrier_connections import (
    build_router as build_carrier_connections_router,
)
from ..modules.inventory import build_router as build_inventory_router
from ..modules.shops import build_router as build_shops_router
from ..modules.tracking import build_router as build_tracking_router
from ..storage import protect_secret, unprotect_secret
from .context import WebContext, json_error
from .services import CaptchaRegistry, QueryCoordinator


def _web_assets() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "g_team_ops" / "web"
    return Path(__file__).resolve().parent


def _session_secret(data_dir: Path) -> str:
    path = data_dir / "web_session.key"
    if path.exists():
        return unprotect_secret(path.read_text(encoding="ascii").strip())
    value = secrets.token_urlsafe(64)
    encrypted = protect_secret(value)
    try:
        with path.open("x", encoding="ascii") as stream:
            stream.write(encrypted)
        return value
    except FileExistsError:
        return unprotect_secret(path.read_text(encoding="ascii").strip())


def create_app(data_dir: Path | None = None) -> FastAPI:
    data_dir = Path(
        data_dir or (Path(__file__).resolve().parents[2] / "data")
    )
    data_dir.mkdir(parents=True, exist_ok=True)
    logger = configure_logging(data_dir)
    database_path = data_dir / "app.db"
    settings_path = data_dir / "settings.json"
    user_repository = UserRepository(database_path)
    coordinator = QueryCoordinator(database_path, settings_path)
    captcha_registry = CaptchaRegistry()
    listing_preview_registry = ListingPreviewRegistry()

    app = FastAPI(
        title="G组运营工作台",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(
        SessionMiddleware,
        secret_key=_session_secret(data_dir),
        session_cookie="g_team_ops_session",
        same_site="lax",
        https_only=False,
        max_age=8 * 60 * 60,
    )
    assets = _web_assets()
    templates = Jinja2Templates(directory=str(assets / "templates"))
    app.mount(
        "/static",
        StaticFiles(directory=str(assets / "static")),
        name="static",
    )

    context = WebContext(
        data_dir=data_dir,
        database_path=database_path,
        settings_path=settings_path,
        users=user_repository,
        coordinator=coordinator,
        captchas=captcha_registry,
        listing_previews=listing_preview_registry,
        templates=templates,
        logger=logger,
    )

    # 保留既有app.state名称，兼容测试、诊断和后续管理工具。
    app.state.data_dir = data_dir
    app.state.database_path = database_path
    app.state.users = user_repository
    app.state.coordinator = coordinator
    app.state.captchas = captcha_registry
    app.state.listing_previews = listing_preview_registry
    app.state.logger = logger
    app.state.web_context = context

    for router in (
        build_accounts_router(context),
        build_carrier_connections_router(context),
        build_shops_router(context),
        build_tracking_router(context),
        build_inventory_router(context),
    ):
        app.include_router(router)

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        return json_error(str(exc.detail), exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected_error(_request: Request, exc: Exception):
        logger.exception("unhandled_web_exception", exc_info=exc)
        return json_error("系统发生未预期错误，请查看本地日志", 500)

    return app

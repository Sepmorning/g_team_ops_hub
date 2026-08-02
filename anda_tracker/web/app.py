from __future__ import annotations

import asyncio
import base64
import secrets
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from ..airscript import AirScriptClient, AirScriptConfig, parse_share_file_id
from ..auth import UserAccount, UserRepository
from ..client import AndaClient
from ..errors import CarrierError, ConfigurationError
from ..logging_config import configure_logging
from ..listing import (
    MAX_LISTING_FILE_SIZE,
    ListingAirScriptClient,
    ListingConnectionConfig,
    ListingPreviewRegistry,
    infer_listing_data_date,
    parse_listing_export,
    validate_listing_data_date,
)
from ..parser import parse_fba_input
from ..storage import ProjectDatabase, protect_secret, unprotect_secret
from ..sites import discover_sites, listing_prefixes, normalize_sheet_name
from .services import (
    CaptchaRegistry,
    CarrierConnectionStatus,
    QueryCoordinator,
    carrier_key_from_sheet,
    result_dict,
    summary_dict,
)


def _web_assets() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "anda_tracker" / "web"
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
        # 两个进程同时首次启动时，只采用成功创建文件的那个密钥。
        return unprotect_secret(path.read_text(encoding="ascii").strip())


def _csrf(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def _check_csrf(request: Request, supplied: str | None) -> None:
    expected = request.session.get("csrf")
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        raise HTTPException(status_code=403, detail="页面安全校验已失效，请刷新后重试")


def _json_error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "message": message}, status_code=status)


def create_app(data_dir: Path | None = None) -> FastAPI:
    data_dir = Path(data_dir or (Path(__file__).resolve().parents[2] / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    logger = configure_logging(data_dir)
    database_path = data_dir / "app.db"
    user_repository = UserRepository(database_path)
    settings_path = data_dir / "settings.json"
    coordinator = QueryCoordinator(database_path, settings_path)
    captcha_registry = CaptchaRegistry()
    listing_preview_registry = ListingPreviewRegistry()

    app = FastAPI(
        title="FBA运营工作台",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(
        SessionMiddleware,
        secret_key=_session_secret(data_dir),
        session_cookie="fba_tracker_session",
        same_site="lax",
        https_only=False,  # 本机HTTP；部署到服务器时必须改为True并启用HTTPS。
        max_age=8 * 60 * 60,
    )
    assets = _web_assets()
    templates = Jinja2Templates(directory=str(assets / "templates"))
    app.mount("/static", StaticFiles(directory=str(assets / "static")), name="static")
    app.state.data_dir = data_dir
    app.state.database_path = database_path
    app.state.users = user_repository
    app.state.coordinator = coordinator
    app.state.captchas = captcha_registry
    app.state.listing_previews = listing_preview_registry
    app.state.logger = logger

    async def json_payload(request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="请求内容不是有效JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="请求内容必须是JSON对象")
        return payload

    def current_account(request: Request) -> UserAccount | None:
        user_id = request.session.get("user_id")
        if not user_id:
            return None
        try:
            account = user_repository.get_user(user_id)
        except ConfigurationError:
            request.session.clear()
            return None
        if not account.is_active:
            request.session.clear()
            return None
        return account

    def page_context(request: Request, account: UserAccount | None = None, **extra):
        return {
            "request": request,
            "account": account,
            "csrf_token": _csrf(request),
            **extra,
        }

    def require_api_user(request: Request) -> UserAccount:
        account = current_account(request)
        if account is None:
            raise HTTPException(status_code=401, detail="登录状态已失效")
        if account.must_change_password:
            raise HTTPException(status_code=403, detail="请先修改临时登录密码")
        return account

    def listing_config(
        database: ProjectDatabase, shop_id: str, country_id: str
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
        database: ProjectDatabase, shop_id: str, country_id: str
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
            raise ConfigurationError("该国家站点尚未配置FBA主表和轨迹明细表")
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

    def site_payload(database: ProjectDatabase, item: Any) -> dict[str, Any]:
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

    @app.get("/health")
    async def health():
        return {"ok": True, "service": "FBA运营工作台"}

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        if not user_repository.has_users():
            return RedirectResponse("/setup", status_code=303)
        return RedirectResponse(
            "/dashboard" if current_account(request) else "/login", status_code=303
        )

    @app.get("/setup", response_class=HTMLResponse)
    async def setup_page(request: Request):
        if user_repository.has_users():
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="setup.html",
            context=page_context(request),
        )

    @app.post("/setup")
    async def setup_submit(request: Request):
        if user_repository.has_users():
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        _check_csrf(request, str(form.get("csrf_token") or ""))
        password = str(form.get("password") or "")
        if password != str(form.get("confirm_password") or ""):
            return templates.TemplateResponse(
                request=request,
                name="setup.html",
                context=page_context(request, error="两次输入的密码不一致"),
                status_code=400,
            )
        try:
            user_repository.create_user(
                str(form.get("username") or ""),
                str(form.get("display_name") or ""),
                password,
                role="admin",
                migrate_default_profile=True,
                only_if_empty=True,
            )
        except ConfigurationError as exc:
            return templates.TemplateResponse(
                request=request,
                name="setup.html",
                context=page_context(request, error=exc.user_message),
                status_code=400,
            )
        return RedirectResponse("/login?created=1", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        if not user_repository.has_users():
            return RedirectResponse("/setup", status_code=303)
        if current_account(request):
            return RedirectResponse("/dashboard", status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context=page_context(request, created=request.query_params.get("created")),
        )

    @app.post("/login")
    async def login_submit(request: Request):
        form = await request.form()
        _check_csrf(request, str(form.get("csrf_token") or ""))
        try:
            account = user_repository.authenticate(
                str(form.get("username") or ""), str(form.get("password") or "")
            )
        except ConfigurationError as exc:
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context=page_context(request, error=exc.user_message),
                status_code=401,
            )
        request.session.clear()
        request.session["user_id"] = account.id
        request.session["csrf"] = secrets.token_urlsafe(32)
        logger.info("login_success user=%s", account.id)
        return RedirectResponse(
            "/change-password" if account.must_change_password else "/dashboard",
            status_code=303,
        )

    @app.post("/logout")
    async def logout(request: Request):
        form = await request.form()
        _check_csrf(request, str(form.get("csrf_token") or ""))
        request.session.clear()
        logger.info("logout")
        return RedirectResponse("/login", status_code=303)

    def render_private(request: Request, name: str, **extra):
        account = current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)
        if account.must_change_password:
            return RedirectResponse("/change-password", status_code=303)
        return templates.TemplateResponse(
            request=request,
            name=name,
            context=page_context(request, account, **extra),
        )

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard(request: Request):
        return render_private(request, "dashboard.html", active="dashboard")

    @app.get("/change-password", response_class=HTMLResponse)
    async def change_password_page(request: Request):
        account = current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="change_password.html",
            context=page_context(request, account),
        )

    @app.post("/change-password")
    async def change_password_submit(request: Request):
        account = current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        _check_csrf(request, str(form.get("csrf_token") or ""))
        new_password = str(form.get("new_password") or "")
        if new_password != str(form.get("confirm_password") or ""):
            return templates.TemplateResponse(
                request=request,
                name="change_password.html",
                context=page_context(request, account, error="两次输入的新密码不一致"),
                status_code=400,
            )
        try:
            user_repository.change_password(
                account.id,
                str(form.get("old_password") or ""),
                new_password,
            )
        except ConfigurationError as exc:
            return templates.TemplateResponse(
                request=request,
                name="change_password.html",
                context=page_context(request, account, error=exc.user_message),
                status_code=400,
            )
        return RedirectResponse("/dashboard?password_changed=1", status_code=303)

    @app.get("/tracking", response_class=HTMLResponse)
    async def tracking(request: Request):
        account = current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)
        if account.must_change_password:
            return RedirectResponse("/change-password", status_code=303)
        database = ProjectDatabase(database_path, profile_id=account.id)
        return templates.TemplateResponse(
            request=request,
            name="tracking.html",
            context=page_context(
                request,
                account,
                active="tracking",
                shops=database.list_shops(),
                carrier_statuses=coordinator.configured_status(account.id),
            ),
        )

    @app.get("/carriers", response_class=HTMLResponse)
    async def carriers_page(request: Request):
        account = current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)
        if account.must_change_password:
            return RedirectResponse("/change-password", status_code=303)
        database = ProjectDatabase(database_path, profile_id=account.id)
        return templates.TemplateResponse(
            request=request,
            name="carriers.html",
            context=page_context(
                request,
                account,
                active="carriers",
                anda_username=database.credential_username("anda") or "",
                yitong_username=database.credential_username("yitong") or "",
            ),
        )

    @app.get("/shops", response_class=HTMLResponse)
    async def shops_page(request: Request):
        account = current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)
        if account.must_change_password:
            return RedirectResponse("/change-password", status_code=303)
        database = ProjectDatabase(database_path, profile_id=account.id)
        return templates.TemplateResponse(
            request=request,
            name="shops.html",
            context=page_context(
                request,
                account,
                active="shops",
                shops=database.list_shops(),
            ),
        )

    @app.get("/inventory", response_class=HTMLResponse)
    async def inventory_page(request: Request):
        account = current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)
        if account.must_change_password:
            return RedirectResponse("/change-password", status_code=303)
        database = ProjectDatabase(database_path, profile_id=account.id)
        return templates.TemplateResponse(
            request=request,
            name="inventory.html",
            context=page_context(
                request,
                account,
                active="inventory",
                shops=database.list_shops(),
            ),
        )

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_page(request: Request):
        account = current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)
        if not account.is_admin:
            return RedirectResponse("/dashboard", status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="admin.html",
            context=page_context(
                request,
                account,
                active="admin",
                users=user_repository.list_users(),
            ),
        )

    @app.post("/api/tracking/query")
    async def query_api(request: Request):
        account = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await json_payload(request)
        raw_input = str(payload.get("input") or "")
        if len(raw_input) > 200_000:
            return _json_error("输入内容过长，请分次提交", 413)
        parsed = parse_fba_input(raw_input)
        if not parsed.valid:
            return _json_error("没有可查询的有效FBA编号")
        sync_value = payload.get("sync_wps", True)
        if not isinstance(sync_value, bool):
            return _json_error("sync_wps必须是布尔值")
        sync_wps = sync_value
        airscript_config = None
        if sync_wps:
            shop_id = str(payload.get("shop_id") or "")
            country_id = str(payload.get("country_id") or "")
            if not shop_id or not country_id:
                return _json_error("勾选更新共享表时必须先选择店铺和国家站点")
            database = ProjectDatabase(database_path, profile_id=account.id)
            try:
                airscript_config, _shop, _site = logistics_config(
                    database, shop_id, country_id
                )
            except ConfigurationError as exc:
                return _json_error(exc.user_message)
        try:
            response = await asyncio.to_thread(
                coordinator.query,
                account.id,
                parsed.valid,
                airscript_config,
            )
        except (CarrierError, ConfigurationError) as exc:
            return _json_error(exc.user_message)
        except Exception:
            logger.exception("manual_tracking_query_unexpected user=%s", account.id)
            return _json_error("查询服务发生未预期错误，请稍后重试", 500)
        return {
            "ok": True,
            "input": {
                "valid": len(parsed.valid),
                "invalid": parsed.invalid,
                "duplicates": parsed.duplicates,
            },
            "results": [result_dict(item) for item in response.results],
            "wps": summary_dict(response.wps_summary),
            "wps_error": response.wps_error,
        }

    @app.get("/api/carriers/status")
    async def carrier_status_api(request: Request):
        account = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        statuses = await asyncio.to_thread(
            coordinator.configured_status,
            account.id,
        )
        return {
            "ok": True,
            "statuses": [
                {
                    "carrier": item.carrier,
                    "connected": item.connected,
                    "message": item.message,
                    "checked": item.checked,
                }
                for item in statuses
            ],
        }

    @app.post("/api/carriers/validation")
    async def validate_carriers_api(request: Request):
        """执行有网络副作用的货代登录与连接验证。"""
        account = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        statuses = await asyncio.to_thread(
            coordinator.validate_all,
            account.id,
        )
        return {
            "ok": True,
            "statuses": [
                {
                    "carrier": item.carrier,
                    "connected": item.connected,
                    "message": item.message,
                    "checked": item.checked,
                }
                for item in statuses
            ],
        }

    @app.get("/api/carriers/credentials")
    async def carrier_credentials_api(request: Request):
        """只向当前登录用户返回自己的已保存凭据，且禁止浏览器缓存。"""
        account = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        database = ProjectDatabase(database_path, profile_id=account.id)
        try:
            anda = database.load_credentials("anda")
            yitong = database.load_credentials("yitong")
        except ConfigurationError as exc:
            return _json_error(exc.user_message)
        return JSONResponse(
            {
                "ok": True,
                "credentials": {
                    "anda": (
                        {"username": anda.username, "password": anda.password}
                        if anda
                        else None
                    ),
                    "yitong": (
                        {"username": yitong.username, "password": yitong.password}
                        if yitong
                        else None
                    ),
                },
            },
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
            },
        )

    @app.post("/api/carriers/anda")
    async def save_anda(request: Request):
        account = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await json_payload(request)
        database = ProjectDatabase(database_path, profile_id=account.id)
        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        try:
            client = AndaClient(retries=coordinator.settings.retries)
            await asyncio.to_thread(client.login, username, password)
            database.save_credentials("anda", username, password)
            coordinator.invalidate_status(account.id, "anda")
            coordinator.remember_status(
                account.id,
                CarrierConnectionStatus("安达", True, "登录成功"),
            )
        except CarrierError as exc:
            return _json_error(exc.user_message)
        except ConfigurationError as exc:
            return _json_error(exc.user_message)
        return {"ok": True, "message": "安达账号已保存并登录成功"}

    @app.delete("/api/carriers/{kind}")
    async def delete_carrier(kind: str, request: Request):
        account = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        database = ProjectDatabase(database_path, profile_id=account.id)
        if kind not in {"anda", "yitong"}:
            return _json_error("未知配置类型", 404)
        database.delete_credentials(kind)
        database.delete_session_token(kind)
        coordinator.invalidate_status(account.id, kind)
        return {"ok": True, "message": "配置已删除"}

    @app.post("/api/carriers/yitong/captcha-challenges")
    async def yitong_captcha(request: Request):
        account = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        challenge_id = secrets.token_urlsafe(24)
        try:
            pending = await asyncio.to_thread(
                captcha_registry.create,
                account.id,
                challenge_id,
                coordinator.settings,
            )
        except CarrierError as exc:
            return _json_error(exc.user_message)
        return {
            "ok": True,
            "challenge_id": challenge_id,
            "image": "data:image/png;base64,"
            + base64.b64encode(pending.challenge.image_bytes).decode("ascii"),
        }

    @app.post("/api/carriers/yitong/session")
    async def yitong_login(request: Request):
        account = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await json_payload(request)
        database = ProjectDatabase(database_path, profile_id=account.id)
        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        try:
            pending = captcha_registry.pop(
                account.id, str(payload.get("challenge_id") or "")
            )
            token = await asyncio.to_thread(
                pending.client.login,
                username,
                password,
                str(payload.get("code") or ""),
                pending.challenge,
            )
            database.save_credentials("yitong", username, password)
            database.save_session_token("yitong", token)
            coordinator.invalidate_status(account.id, "yitong")
            coordinator.remember_status(
                account.id,
                CarrierConnectionStatus("易通", True, "登录有效"),
            )
        except (CarrierError, ConfigurationError) as exc:
            return _json_error(exc.user_message)
        return {"ok": True, "message": "易通账号已保存并登录成功"}

    @app.post("/api/shops")
    async def save_shop_api(request: Request):
        account = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await json_payload(request)
        database = ProjectDatabase(database_path, profile_id=account.id)
        shop_id = str(payload.get("id") or "").strip() or None
        existing = database.get_shop(shop_id) if shop_id else None
        token = str(payload.get("api_token") or "")
        if not token and existing:
            token = existing.config.api_token
        webhook_url = str(payload.get("webhook_url") or "").strip()
        if not webhook_url and existing:
            webhook_url = existing.config.webhook_url
        share_url = str(payload.get("share_url") or "").strip()
        config = AirScriptConfig(
            share_url,
            webhook_url,
            token,
        )
        try:
            parse_share_file_id(share_url)
            shop = database.save_shop(
                str(payload.get("name") or ""), config, shop_id=shop_id
            )
        except (CarrierError, ConfigurationError) as exc:
            return _json_error(exc.user_message)
        return {
            "ok": True,
            "shop_id": shop.id,
            "message": "店铺名称和共享表链接已保存；请在店铺中配置模块脚本并扫描国家站点",
        }

    @app.delete("/api/shops/{shop_id}")
    async def delete_shop_api(shop_id: str, request: Request):
        account = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        database = ProjectDatabase(database_path, profile_id=account.id)
        try:
            database.delete_shop(shop_id)
        except ConfigurationError as exc:
            return _json_error(exc.user_message, 404)
        return {"ok": True, "message": "店铺已删除"}

    @app.post("/api/shops/{shop_id}/validation")
    async def validate_shop_api(shop_id: str, request: Request):
        account = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        database = ProjectDatabase(database_path, profile_id=account.id)
        shop = database.get_shop(shop_id)
        if shop is None:
            return _json_error("店铺不存在或不属于当前用户", 404)
        try:
            binding = await asyncio.to_thread(
                AirScriptClient(
                    shop.config, retries=coordinator.settings.retries
                ).validate
            )
        except (CarrierError, ConfigurationError) as exc:
            return _json_error(exc.user_message)
        return {
            "ok": True,
            "message": (
                f"已连接{binding.sheet_name}和{binding.detail_sheet_name}；"
                f"主表已按名称识别{len(binding.columns)}个标准字段，"
                f"FBA列{binding.fba_column}，货代列{binding.carrier_column}，"
                f"路由列{binding.route_column}"
            ),
        }

    @app.get("/api/shops/{shop_id}/config")
    async def shop_config_api(shop_id: str, request: Request):
        account = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        database = ProjectDatabase(database_path, profile_id=account.id)
        shop = database.get_shop(shop_id)
        if shop is None:
            return _json_error("店铺不存在或不属于当前用户", 404)
        listing_connection = database.load_listing_connection(shop_id)
        sites = database.list_shop_countries(shop_id)
        return {
            "ok": True,
            "shop": {
                "id": shop.id,
                "name": shop.name,
                "share_url": shop.config.share_url,
                "listing_prefix": shop.listing_prefix,
            },
            "connections": {
                "tracking": {
                    "configured": bool(
                        shop.config.webhook_url and shop.config.api_token
                    ),
                    "webhook_url": shop.config.webhook_url,
                },
                "listing": {
                    "configured": bool(listing_connection),
                    "webhook_url": (
                        listing_connection.webhook_url
                        if listing_connection
                        else ""
                    ),
                    "updated_at": (
                        listing_connection.updated_at if listing_connection else ""
                    ),
                },
            },
            "sites": [site_payload(database, item) for item in sites],
        }

    @app.post("/api/shops/{shop_id}/logistics-connection")
    async def save_logistics_connection_api(shop_id: str, request: Request):
        account = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await json_payload(request)
        database = ProjectDatabase(database_path, profile_id=account.id)
        shop = database.get_shop(shop_id)
        if shop is None:
            return _json_error("店铺不存在或不属于当前用户", 404)
        webhook_url = str(payload.get("webhook_url") or "").strip()
        token = str(payload.get("api_token") or "") or shop.config.api_token
        config = AirScriptConfig(
            share_url=shop.config.share_url,
            webhook_url=webhook_url,
            api_token=token,
        )
        try:
            sheets = await asyncio.to_thread(
                AirScriptClient(
                    config, retries=coordinator.settings.retries
                ).discover_sheets
            )
            database.save_shop(shop.name, config, shop_id=shop.id)
        except (CarrierError, ConfigurationError) as exc:
            return _json_error(exc.user_message)
        return {
            "ok": True,
            "message": f"FBA物流脚本已连接，共读取到{len(sheets)}个子表",
            "sheet_count": len(sheets),
        }

    @app.post("/api/shops/{shop_id}/sites")
    async def save_shop_site_api(shop_id: str, request: Request):
        account = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await json_payload(request)
        database = ProjectDatabase(database_path, profile_id=account.id)
        if database.get_shop(shop_id) is None:
            return _json_error("店铺不存在或不属于当前用户", 404)
        try:
            site = database.save_shop_country(
                shop_id,
                str(payload.get("country_name") or ""),
                str(
                    payload.get("listing_sheet_name")
                    or payload.get("sheet_name")
                    or ""
                ),
                country_id=str(payload.get("id") or "").strip() or None,
                country_code=str(payload.get("country_code") or ""),
                fba_sheet_name=str(payload.get("fba_sheet_name") or ""),
                detail_sheet_name=str(payload.get("detail_sheet_name") or ""),
            )
        except ConfigurationError as exc:
            return _json_error(exc.user_message)
        return {
            "ok": True,
            "site": site_payload(database, site),
            "message": "国家站点映射已保存；使用模块前可分别测试Listing和物流连接",
        }

    @app.post("/api/shops/{shop_id}/sites/{site_id}/validation")
    async def validate_shop_site_api(
        shop_id: str, site_id: str, request: Request
    ):
        account = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await json_payload(request)
        module = str(payload.get("module") or "").strip().lower()
        database = ProjectDatabase(database_path, profile_id=account.id)
        try:
            if module == "listing":
                config, _shop, _site = listing_config(
                    database, shop_id, site_id
                )
                binding = await asyncio.to_thread(
                    ListingAirScriptClient(
                        config, retries=coordinator.settings.retries
                    ).validate
                )
                message = (
                    f"Listing已连接“{binding.sheet_name}”，"
                    f"表头位于第{binding.header_row}行"
                )
            elif module == "tracking":
                config, _shop, _site = logistics_config(
                    database, shop_id, site_id
                )
                binding = await asyncio.to_thread(
                    AirScriptClient(
                        config, retries=coordinator.settings.retries
                    ).validate
                )
                message = (
                    f"物流已连接“{binding.sheet_name}”和"
                    f"“{binding.detail_sheet_name}”"
                )
            else:
                return _json_error("module必须是listing或tracking")
        except (CarrierError, ConfigurationError) as exc:
            return _json_error(exc.user_message)
        return {"ok": True, "message": message}

    @app.post("/api/shops/{shop_id}/discover-sites")
    async def discover_shop_sites_api(shop_id: str, request: Request):
        account = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await json_payload(request)
        database = ProjectDatabase(database_path, profile_id=account.id)
        shop = database.get_shop(shop_id)
        if shop is None:
            return _json_error("店铺不存在或不属于当前用户", 404)
        listing_connection = database.load_listing_connection(shop_id)
        discovered_by_module: dict[str, list[dict[str, str]]] = {}
        errors: list[str] = []
        if shop.config.webhook_url and shop.config.api_token:
            try:
                discovered_by_module["物流"] = await asyncio.to_thread(
                    AirScriptClient(
                        shop.config, retries=coordinator.settings.retries
                    ).discover_sheets
                )
            except (CarrierError, ConfigurationError) as exc:
                errors.append(f"物流脚本：{exc.user_message}")
        if listing_connection:
            try:
                discovered_by_module["Listing"] = await asyncio.to_thread(
                    ListingAirScriptClient(
                        ListingConnectionConfig(
                            share_url=shop.config.share_url,
                            webhook_url=listing_connection.webhook_url,
                            api_token=listing_connection.api_token,
                            sheet_name="扫描工作簿",
                        ),
                        retries=coordinator.settings.retries,
                    ).discover_sheets
                )
            except (CarrierError, ConfigurationError) as exc:
                errors.append(f"Listing脚本：{exc.user_message}")
        if not discovered_by_module:
            message = (
                "没有可用于扫描的模块脚本；" + "；".join(errors)
                if errors
                else "请先配置至少一个模块脚本"
            )
            return _json_error(message)
        module_sets = {
            name: {normalize_sheet_name(item["name"]) for item in sheets}
            for name, sheets in discovered_by_module.items()
        }
        if len(module_sets) > 1:
            values = list(module_sets.values())
            if any(value != values[0] for value in values[1:]):
                return _json_error(
                    "物流和Listing脚本返回的子表不一致，可能连接了不同工作簿；"
                    "为避免写错表，已停止自动保存",
                    409,
                )
        sheets = next(iter(discovered_by_module.values()))
        sheet_names = [item["name"] for item in sheets]
        available_prefixes = listing_prefixes(sheet_names)
        available_by_normalized = {
            normalize_sheet_name(prefix): prefix for prefix in available_prefixes
        }
        confirmed_prefix = str(
            payload.get("confirm_listing_prefix") or ""
        ).strip()
        reset_prefix = payload.get("reset_listing_prefix") is True
        active_prefix = "" if reset_prefix else shop.listing_prefix
        if confirmed_prefix:
            matched_prefix = available_by_normalized.get(
                normalize_sheet_name(confirmed_prefix)
            )
            if not matched_prefix:
                return _json_error(
                    "确认的Listing前缀不在本次扫描结果中，请重新扫描",
                    409,
                )
            shop = database.save_shop_listing_prefix(shop_id, matched_prefix)
            active_prefix = shop.listing_prefix
        elif active_prefix:
            matched_prefix = available_by_normalized.get(
                normalize_sheet_name(active_prefix)
            )
            if not matched_prefix:
                return {
                    "ok": True,
                    "confirmation_required": True,
                    "message": (
                        f"已保存的Listing前缀“{active_prefix}”在本次扫描中不存在；"
                        "为避免写错表，未更新站点映射"
                    ),
                    "listing_prefix": active_prefix,
                    "available_listing_prefixes": available_prefixes,
                    "warnings": list(dict.fromkeys(errors)),
                    "candidates": [],
                    "sites": [
                        site_payload(database, item)
                        for item in database.list_shop_countries(shop_id)
                    ],
                }
            active_prefix = matched_prefix
        else:
            exact_prefix = available_by_normalized.get(
                normalize_sheet_name(shop.name)
            )
            if exact_prefix:
                shop = database.save_shop_listing_prefix(
                    shop_id, exact_prefix
                )
                active_prefix = shop.listing_prefix
            elif available_prefixes:
                detected_message = (
                    f"检测到唯一Listing子表前缀“{available_prefixes[0]}”，"
                    f"与店铺显示名称“{shop.name}”不同，请确认是否绑定"
                    if len(available_prefixes) == 1
                    else "检测到多个Listing子表前缀，请选择当前店铺使用的前缀"
                )
                return {
                    "ok": True,
                    "confirmation_required": True,
                    "message": detected_message,
                    "listing_prefix": "",
                    "available_listing_prefixes": available_prefixes,
                    "warnings": list(dict.fromkeys(errors)),
                    "candidates": [],
                    "sites": [
                        site_payload(database, item)
                        for item in database.list_shop_countries(shop_id)
                    ],
                }
            else:
                return {
                    "ok": True,
                    "confirmation_required": False,
                    "message": (
                        f"扫描到{len(sheets)}个子表，但没有识别到“前缀-国家”"
                        "格式的Listing子表"
                    ),
                    "listing_prefix": "",
                    "available_listing_prefixes": [],
                    "warnings": list(dict.fromkeys(errors)),
                    "candidates": [],
                    "sites": [
                        site_payload(database, item)
                        for item in database.list_shop_countries(shop_id)
                    ],
                }

        candidates = discover_sites(
            shop.name,
            sheet_names,
            listing_prefix=active_prefix,
        )
        existing = database.list_shop_countries(shop_id)
        saved_count = 0
        discovery_warnings = list(errors)
        for candidate in candidates:
            discovery_warnings.extend(candidate.warnings)
            if not candidate.listing_sheet_name:
                continue
            current = next(
                (
                    item
                    for item in existing
                    if item.country_code == candidate.country_code
                    or item.country_name == candidate.country_name
                ),
                None,
            )
            try:
                database.save_shop_country(
                    shop_id,
                    candidate.country_name,
                    candidate.listing_sheet_name,
                    country_id=current.id if current else None,
                    country_code=candidate.country_code,
                    fba_sheet_name=(
                        candidate.fba_sheet_name
                        or (current.fba_sheet_name if current else "")
                    ),
                    detail_sheet_name=(
                        candidate.detail_sheet_name
                        or (current.detail_sheet_name if current else "")
                    ),
                )
                saved_count += 1
            except ConfigurationError as exc:
                discovery_warnings.append(
                    f"{candidate.country_name}站点未自动保存：{exc.user_message}"
                )
        sites = database.list_shop_countries(shop_id)
        return {
            "ok": True,
            "confirmation_required": False,
            "message": (
                f"扫描到{len(sheets)}个子表，已保存或更新{saved_count}个明确站点映射"
            ),
            "listing_prefix": active_prefix,
            "available_listing_prefixes": available_prefixes,
            "warnings": list(dict.fromkeys(discovery_warnings)),
            "candidates": [
                {
                    "country_code": item.country_code,
                    "country_name": item.country_name,
                    "listing_sheet_name": item.listing_sheet_name,
                    "fba_sheet_name": item.fba_sheet_name,
                    "detail_sheet_name": item.detail_sheet_name,
                    "warnings": item.warnings,
                }
                for item in candidates
            ],
            "sites": [site_payload(database, item) for item in sites],
        }

    @app.get("/api/inventory/config")
    async def inventory_config_api(request: Request):
        account = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        shop_id = str(request.query_params.get("shop_id") or "").strip()
        database = ProjectDatabase(database_path, profile_id=account.id)
        shop = database.get_shop(shop_id)
        if shop is None:
            return _json_error("店铺不存在或不属于当前用户", 404)
        connection = database.load_listing_connection(shop_id)
        countries = database.list_shop_countries(shop_id)
        return {
            "ok": True,
            "shop": {
                "id": shop.id,
                "name": shop.name,
                "share_url": shop.config.share_url,
            },
            "connection": (
                {
                    "configured": True,
                    "updated_at": connection.updated_at,
                }
                if connection
                else {"configured": False, "updated_at": ""}
            ),
            "countries": [
                site_payload(database, item)
                for item in countries
            ],
        }

    @app.post("/api/shops/{shop_id}/listing-connection")
    async def save_listing_connection_api(shop_id: str, request: Request):
        account = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await json_payload(request)
        database = ProjectDatabase(database_path, profile_id=account.id)
        shop = database.get_shop(shop_id)
        if shop is None:
            return _json_error("店铺不存在或不属于当前用户", 404)
        existing = database.load_listing_connection(shop_id)
        token = str(payload.get("api_token") or "")
        if not token and existing:
            token = existing.api_token
        webhook_url = str(payload.get("webhook_url") or "")
        try:
            sheets = await asyncio.to_thread(
                ListingAirScriptClient(
                ListingConnectionConfig(
                    share_url=shop.config.share_url,
                    webhook_url=webhook_url,
                    api_token=token,
                    sheet_name="扫描工作簿",
                ),
                retries=coordinator.settings.retries,
                ).discover_sheets
            )
            database.save_listing_connection(shop_id, webhook_url, token)
        except (CarrierError, ConfigurationError) as exc:
            return _json_error(exc.user_message)
        return {
            "ok": True,
            "message": f"Listing脚本已连接，共读取到{len(sheets)}个子表",
            "sheet_count": len(sheets),
        }

    @app.post("/api/shops/{shop_id}/countries")
    async def save_shop_country_api(shop_id: str, request: Request):
        account = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await json_payload(request)
        database = ProjectDatabase(database_path, profile_id=account.id)
        shop = database.get_shop(shop_id)
        if shop is None:
            return _json_error("店铺不存在或不属于当前用户", 404)
        connection = database.load_listing_connection(shop_id)
        if connection is None:
            return _json_error("请先保存该店铺的独立Listing AirScript连接")
        sheet_name = str(payload.get("sheet_name") or "").strip()
        config = ListingConnectionConfig(
            share_url=shop.config.share_url,
            webhook_url=connection.webhook_url,
            api_token=connection.api_token,
            sheet_name=sheet_name,
        )
        try:
            binding = await asyncio.to_thread(
                ListingAirScriptClient(
                    config, retries=coordinator.settings.retries
                ).validate
            )
            country = database.save_shop_country(
                shop_id,
                str(payload.get("country_name") or ""),
                sheet_name,
                country_id=str(payload.get("id") or "").strip() or None,
            )
        except (CarrierError, ConfigurationError) as exc:
            return _json_error(exc.user_message)
        except Exception:
            logger.exception(
                "listing_country_validation_unexpected user=%s shop=%s",
                account.id,
                shop_id,
            )
            return _json_error("验证Listing子表时发生未预期错误", 500)
        return {
            "ok": True,
            "country": {
                "id": country.id,
                "country_name": country.country_name,
                "sheet_name": country.sheet_name,
            },
            "message": (
                f"已连接“{binding.sheet_name}”，在第{binding.header_row}行"
                f"按名称识别{len(binding.columns)}个标准表头"
            ),
        }

    @app.delete("/api/inventory/countries/{country_id}")
    async def delete_shop_country_api(country_id: str, request: Request):
        account = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        database = ProjectDatabase(database_path, profile_id=account.id)
        try:
            database.delete_shop_country(country_id)
        except ConfigurationError as exc:
            return _json_error(exc.user_message, 404)
        return {"ok": True, "message": "国家与Listing子表映射已删除"}

    @app.post("/api/inventory/countries/{country_id}/validation")
    async def validate_listing_country_api(country_id: str, request: Request):
        account = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await json_payload(request)
        shop_id = str(payload.get("shop_id") or "").strip()
        database = ProjectDatabase(database_path, profile_id=account.id)
        try:
            config, _shop, _country = listing_config(
                database, shop_id, country_id
            )
            binding = await asyncio.to_thread(
                ListingAirScriptClient(
                    config, retries=coordinator.settings.retries
                ).validate
            )
        except (CarrierError, ConfigurationError) as exc:
            return _json_error(exc.user_message)
        return {
            "ok": True,
            "message": (
                f"已连接“{binding.sheet_name}”，表头位于第{binding.header_row}行，"
                f"已按名称识别{len(binding.columns)}列"
            ),
        }

    @app.post("/api/inventory/imports/preview")
    async def preview_listing_import_api(request: Request):
        account = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        try:
            content_length = int(request.headers.get("Content-Length") or 0)
        except ValueError:
            content_length = 0
        if content_length > MAX_LISTING_FILE_SIZE + 1024 * 1024:
            return _json_error("上传内容超过21MB安全上限", 413)
        form = await request.form()
        shop_id = str(form.get("shop_id") or "").strip()
        country_id = str(form.get("country_id") or "").strip()
        filename_date = infer_listing_data_date(
            str(getattr(form.get("file"), "filename", "") or "")
        )
        try:
            data_date = validate_listing_data_date(
                str(form.get("data_date") or filename_date)
            )
            database = ProjectDatabase(database_path, profile_id=account.id)
            config, shop, country = listing_config(
                database, shop_id, country_id
            )
            uploaded = form.get("file")
            if uploaded is None or not hasattr(uploaded, "read"):
                raise ConfigurationError("请选择领星导出的.xlsx文件")
            filename = str(getattr(uploaded, "filename", "") or "")
            if not filename.lower().endswith(".xlsx"):
                raise ConfigurationError("只支持领星导出的.xlsx文件")
            content = await uploaded.read(MAX_LISTING_FILE_SIZE + 1)
            parsed = await asyncio.to_thread(parse_listing_export, content)
            binding = await asyncio.to_thread(
                ListingAirScriptClient(
                    config, retries=coordinator.settings.retries
                ).validate
            )
            preview_id = listing_preview_registry.create(
                account.id,
                shop_id,
                country_id,
                data_date,
                filename,
                parsed,
            )
        except (CarrierError, ConfigurationError) as exc:
            return _json_error(exc.user_message)
        except Exception:
            logger.exception(
                "listing_preview_unexpected user=%s shop=%s",
                account.id,
                shop_id,
            )
            return _json_error("读取领星文件时发生未预期错误", 500)
        logger.info(
            "listing_preview_ready user=%s shop=%s country=%s rows=%d",
            account.id,
            shop_id,
            country_id,
            len(parsed.rows),
        )
        return {
            "ok": True,
            "preview_id": preview_id,
            "filename": filename,
            "data_date": data_date,
            "shop_name": shop.name,
            "country_name": country.country_name,
            "sheet_name": binding.sheet_name,
            "source_sheet_name": parsed.sheet_name,
            "source_header_row": parsed.header_row,
            "row_count": len(parsed.rows),
            "duplicate_mskus": list(parsed.duplicate_mskus),
            "skipped_rows": list(parsed.skipped_rows),
            "ignored_headers": list(parsed.ignored_headers),
            "sample": [item.preview_dict() for item in parsed.rows[:30]],
        }

    @app.post("/api/inventory/imports/apply")
    async def apply_listing_import_api(request: Request):
        account = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await json_payload(request)
        preview_id = str(payload.get("preview_id") or "").strip()
        try:
            pending = listing_preview_registry.get(account.id, preview_id)
            database = ProjectDatabase(database_path, profile_id=account.id)
            config, shop, country = listing_config(
                database, pending.shop_id, pending.country_id
            )
            summary = await asyncio.to_thread(
                ListingAirScriptClient(
                    config, retries=coordinator.settings.retries
                ).sync,
                pending.parsed.rows,
                pending.data_date,
            )
        except (CarrierError, ConfigurationError) as exc:
            return _json_error(exc.user_message)
        except Exception:
            logger.exception(
                "listing_apply_unexpected user=%s preview=%s",
                account.id,
                preview_id,
            )
            return _json_error("回填Listing共享表时发生未预期错误", 500)
        logger.info(
            "listing_apply_complete user=%s shop=%s country=%s updated=%d same=%d",
            account.id,
            pending.shop_id,
            pending.country_id,
            len(summary.updated),
            len(summary.same_date_updated),
        )
        return {
            "ok": True,
            "message": f"{shop.name} / {country.country_name}：{summary.message}",
            "summary": {
                "updated": summary.updated,
                "same_date_updated": summary.same_date_updated,
                "stale": summary.stale,
                "not_in_sheet": summary.not_in_sheet,
                "duplicate_rows": summary.duplicate_rows,
                "conflicts": summary.conflicts,
                "failures": summary.failures,
            },
        }

    @app.post("/api/shops/{shop_id}/tracking-sync")
    async def query_pending_shop_api(shop_id: str, request: Request):
        account = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await json_payload(request)
        country_id = str(payload.get("country_id") or "").strip()
        database = ProjectDatabase(database_path, profile_id=account.id)
        try:
            config, shop, country = logistics_config(
                database, shop_id, country_id
            )
        except ConfigurationError as exc:
            return _json_error(exc.user_message)

        client = AirScriptClient(config, retries=coordinator.settings.retries)
        try:
            pending_items = await asyncio.to_thread(
                client.list_pending_tracking_items
            )
        except (CarrierError, ConfigurationError) as exc:
            return _json_error(exc.user_message)
        if not pending_items:
            return {
                "ok": True,
                "message": "共享表中没有需要查询的FBA",
                "carrier_statuses": [],
                "pending_count": 0,
                "results": [],
                "wps": None,
                "wps_error": "",
            }

        required_carriers = {
            key
            for item in pending_items
            if (key := carrier_key_from_sheet(item.carrier))
        }
        statuses = await asyncio.to_thread(
            coordinator.validate_required,
            account.id,
            required_carriers,
        )
        status_payload = [
            {
                "carrier": item.carrier,
                "connected": item.connected,
                "message": item.message,
                "checked": item.checked,
            }
            for item in statuses
        ]
        if not all(item.connected for item in statuses):
            return JSONResponse(
                {
                    "ok": False,
                    "message": "共享表本次使用的货代存在未连接项，自动任务已停止",
                    "carrier_statuses": status_payload,
                },
                status_code=409,
            )

        try:
            response = await asyncio.to_thread(
                coordinator.query_routed,
                account.id,
                pending_items,
                config,
            )
        except (CarrierError, ConfigurationError) as exc:
            return _json_error(exc.user_message)
        except Exception:
            logger.exception("automatic_tracking_sync_unexpected user=%s shop=%s", account.id, shop_id)
            return _json_error("自动查询任务发生未预期错误，请稍后重试", 500)
        return {
            "ok": True,
            "message": (
                f"{shop.name} / {country.country_name}："
                f"已按共享表货代列定向查询 {len(pending_items)} 个待更新FBA"
            ),
            "carrier_statuses": status_payload,
            "pending_count": len(pending_items),
            "results": [result_dict(item) for item in response.results],
            "wps": summary_dict(response.wps_summary),
            "wps_error": response.wps_error,
        }

    @app.post("/api/admin/users")
    async def create_user_api(request: Request):
        actor = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        if not actor.is_admin:
            return _json_error("只有管理员可以创建账号", 403)
        payload = await json_payload(request)
        try:
            user_repository.create_user(
                str(payload.get("username") or ""),
                str(payload.get("display_name") or ""),
                str(payload.get("password") or ""),
                role=str(payload.get("role") or "user"),
                must_change_password=True,
            )
        except ConfigurationError as exc:
            return _json_error(exc.user_message)
        return {"ok": True, "message": "账号已创建"}

    @app.post("/api/admin/users/{user_id}/toggle")
    async def toggle_user_api(user_id: str, request: Request):
        actor = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        try:
            target = user_repository.get_user(user_id)
            user_repository.set_active(actor.id, user_id, not target.is_active)
        except ConfigurationError as exc:
            return _json_error(exc.user_message, 403)
        return {"ok": True, "message": "账号状态已更新"}

    @app.post("/api/admin/users/{user_id}/reset-password")
    async def reset_password_api(user_id: str, request: Request):
        actor = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await json_payload(request)
        try:
            user_repository.reset_password(
                actor.id, user_id, str(payload.get("password") or "")
            )
        except ConfigurationError as exc:
            return _json_error(exc.user_message, 403)
        return {"ok": True, "message": "临时密码已重置"}

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        return _json_error(str(exc.detail), exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected_error(_request: Request, exc: Exception):
        logger.exception("unhandled_web_exception", exc_info=exc)
        return _json_error("系统发生未预期错误，请查看本地日志", 500)

    return app

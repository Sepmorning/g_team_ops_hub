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

from ..airscript import AirScriptClient, AirScriptConfig
from ..auth import UserAccount, UserRepository
from ..client import AndaClient
from ..errors import CarrierError, ConfigurationError
from ..logging_config import configure_logging
from ..parser import parse_fba_input
from ..storage import ProjectDatabase, protect_secret, unprotect_secret
from .services import (
    CaptchaRegistry,
    QueryCoordinator,
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
                yitong_logged_in=bool(database.load_session_token("yitong")),
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
        return render_private(request, "inventory.html", active="inventory")

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
            if not shop_id:
                return _json_error("勾选更新共享表时必须先选择店铺")
            database = ProjectDatabase(database_path, profile_id=account.id)
            shop = database.get_shop(shop_id)
            if shop is None:
                return _json_error("所选店铺不存在或不属于当前用户", 404)
            airscript_config = shop.config
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
        live = request.query_params.get("live") == "1"
        statuses = await asyncio.to_thread(
            coordinator.validate_all if live else coordinator.configured_status,
            account.id,
        )
        return {
            "ok": True,
            "statuses": [
                {
                    "carrier": item.carrier,
                    "connected": item.connected,
                    "message": item.message,
                }
                for item in statuses
            ],
        }

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
        config = AirScriptConfig(
            str(payload.get("share_url") or ""),
            str(payload.get("webhook_url") or ""),
            token,
        )
        try:
            binding = await asyncio.to_thread(
                AirScriptClient(config, retries=coordinator.settings.retries).validate
            )
            shop = database.save_shop(
                str(payload.get("name") or ""), config, shop_id=shop_id
            )
        except (CarrierError, ConfigurationError) as exc:
            return _json_error(exc.user_message)
        return {
            "ok": True,
            "shop_id": shop.id,
            "message": (
                f"店铺已保存；{binding.sheet_name}的FBA列{binding.fba_column}，"
                f"是否完成列{binding.completion_column}，路由列{binding.route_column}"
            ),
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
                f"已连接{binding.sheet_name}；FBA列{binding.fba_column}，"
                f"是否完成列{binding.completion_column}，路由列{binding.route_column}"
            ),
        }

    @app.post("/api/shops/{shop_id}/tracking-sync")
    async def query_pending_shop_api(shop_id: str, request: Request):
        account = require_api_user(request)
        _check_csrf(request, request.headers.get("X-CSRF-Token"))
        database = ProjectDatabase(database_path, profile_id=account.id)
        shop = database.get_shop(shop_id)
        if shop is None:
            return _json_error("店铺不存在或不属于当前用户", 404)

        statuses = await asyncio.to_thread(coordinator.validate_all, account.id)
        status_payload = [
            {
                "carrier": item.carrier,
                "connected": item.connected,
                "message": item.message,
            }
            for item in statuses
        ]
        if not all(item.connected for item in statuses):
            return JSONResponse(
                {
                    "ok": False,
                    "message": "存在未连接的货代，自动任务已停止",
                    "carrier_statuses": status_payload,
                },
                status_code=409,
            )

        client = AirScriptClient(shop.config, retries=coordinator.settings.retries)
        try:
            fbas = await asyncio.to_thread(client.list_pending_fbas)
        except (CarrierError, ConfigurationError) as exc:
            return _json_error(exc.user_message)
        if not fbas:
            return {
                "ok": True,
                "message": "共享表中没有需要查询的FBA",
                "carrier_statuses": status_payload,
                "pending_count": 0,
                "results": [],
                "wps": None,
                "wps_error": "",
            }
        try:
            response = await asyncio.to_thread(
                coordinator.query, account.id, fbas, shop.config
            )
        except (CarrierError, ConfigurationError) as exc:
            return _json_error(exc.user_message)
        except Exception:
            logger.exception("automatic_tracking_sync_unexpected user=%s shop=%s", account.id, shop_id)
            return _json_error("自动查询任务发生未预期错误，请稍后重试", 500)
        return {
            "ok": True,
            "message": f"已读取并查询 {len(fbas)} 个未完成FBA",
            "carrier_statuses": status_payload,
            "pending_count": len(fbas),
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

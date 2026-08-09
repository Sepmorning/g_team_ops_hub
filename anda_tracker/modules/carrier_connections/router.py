"""货代连接页面与API路由。"""

from __future__ import annotations

import asyncio
import base64
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ...client import AndaClient
from ...errors import CarrierError, ConfigurationError
from ...web.context import WebContext, check_csrf, json_error
from ...web.services import (
    CARRIER_STATUS_SNAPSHOT_TTL_SECONDS,
    CarrierConnectionStatus,
)


def build_router(ctx: WebContext) -> APIRouter:
    router = APIRouter()

    @router.get("/carriers", response_class=HTMLResponse)
    async def carriers_page(request: Request):
        account = ctx.current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)
        if account.must_change_password:
            return RedirectResponse("/change-password", status_code=303)
        database = ctx.database_for(account.id)
        return ctx.templates.TemplateResponse(
            request=request,
            name="carriers.html",
            context=ctx.page_context(
                request,
                account,
                active="carriers",
                anda_username=database.credential_username("anda") or "",
                yitong_username=database.credential_username("yitong") or "",
            ),
        )

    @router.get("/api/carriers/status")
    async def carrier_status_api(request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        statuses = await asyncio.to_thread(
            ctx.coordinator.configured_status,
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
                    "cached": item.cached,
                }
                for item in statuses
            ],
            "cache_ttl_seconds": CARRIER_STATUS_SNAPSHOT_TTL_SECONDS,
        }

    @router.post("/api/carriers/validation")
    async def validate_carriers_api(request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        force = request.query_params.get("force", "1").lower() not in {
            "0",
            "false",
            "no",
        }
        statuses = await asyncio.to_thread(
            ctx.coordinator.validate_all,
            account.id,
            force=force,
        )
        return {
            "ok": True,
            "statuses": [
                {
                    "carrier": item.carrier,
                    "connected": item.connected,
                    "message": item.message,
                    "checked": item.checked,
                    "cached": item.cached,
                }
                for item in statuses
            ],
            "cache_ttl_seconds": CARRIER_STATUS_SNAPSHOT_TTL_SECONDS,
        }

    @router.get("/api/carriers/credentials")
    async def carrier_credentials_api(request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        database = ctx.database_for(account.id)
        try:
            anda = database.load_credentials("anda")
            yitong = database.load_credentials("yitong")
        except ConfigurationError as exc:
            return json_error(exc.user_message)
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
                        {
                            "username": yitong.username,
                            "password": yitong.password,
                        }
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

    @router.post("/api/carriers/anda")
    async def save_anda(request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await ctx.json_payload(request)
        database = ctx.database_for(account.id)
        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        try:
            client = AndaClient(retries=ctx.coordinator.settings.retries)
            await asyncio.to_thread(client.login, username, password)
            database.save_credentials("anda", username, password)
            ctx.coordinator.invalidate_status(account.id, "anda")
            ctx.coordinator.remember_status(
                account.id,
                CarrierConnectionStatus("安达", True, "登录成功"),
            )
        except (CarrierError, ConfigurationError) as exc:
            return json_error(exc.user_message)
        return {"ok": True, "message": "安达账号已保存并登录成功"}

    @router.delete("/api/carriers/{kind}")
    async def delete_carrier(kind: str, request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        database = ctx.database_for(account.id)
        if kind not in {"anda", "yitong"}:
            return json_error("未知配置类型", 404)
        database.delete_credentials(kind)
        database.delete_session_token(kind)
        ctx.coordinator.invalidate_status(account.id, kind)
        return {"ok": True, "message": "配置已删除"}

    @router.post("/api/carriers/yitong/captcha-challenges")
    async def yitong_captcha(request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        challenge_id = secrets.token_urlsafe(24)
        try:
            pending = await asyncio.to_thread(
                ctx.captchas.create,
                account.id,
                challenge_id,
                ctx.coordinator.settings,
            )
        except CarrierError as exc:
            return json_error(exc.user_message)
        return {
            "ok": True,
            "challenge_id": challenge_id,
            "image": "data:image/png;base64,"
            + base64.b64encode(pending.challenge.image_bytes).decode("ascii"),
        }

    @router.post("/api/carriers/yitong/session")
    async def yitong_login(request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await ctx.json_payload(request)
        database = ctx.database_for(account.id)
        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        try:
            pending = ctx.captchas.pop(
                account.id,
                str(payload.get("challenge_id") or ""),
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
            ctx.coordinator.invalidate_status(account.id, "yitong")
            ctx.coordinator.remember_status(
                account.id,
                CarrierConnectionStatus("易通", True, "登录有效"),
            )
        except (CarrierError, ConfigurationError) as exc:
            return json_error(exc.user_message)
        return {"ok": True, "message": "易通账号已保存并登录成功"}

    return router

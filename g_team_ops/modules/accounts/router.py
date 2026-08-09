"""账号、会话和管理员HTTP路由。"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ...errors import ConfigurationError
from ...web.context import WebContext, check_csrf, json_error


def build_router(ctx: WebContext) -> APIRouter:
    router = APIRouter()

    def render_private(request: Request, name: str, **extra):
        account = ctx.current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)
        if account.must_change_password:
            return RedirectResponse("/change-password", status_code=303)
        return ctx.templates.TemplateResponse(
            request=request,
            name=name,
            context=ctx.page_context(request, account, **extra),
        )

    @router.get("/health")
    async def health():
        return {"ok": True, "service": "G组运营工作台"}

    @router.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        if not ctx.users.has_users():
            return RedirectResponse("/setup", status_code=303)
        return RedirectResponse(
            "/dashboard" if ctx.current_account(request) else "/login",
            status_code=303,
        )

    @router.get("/setup", response_class=HTMLResponse)
    async def setup_page(request: Request):
        if ctx.users.has_users():
            return RedirectResponse("/login", status_code=303)
        return ctx.templates.TemplateResponse(
            request=request,
            name="setup.html",
            context=ctx.page_context(request),
        )

    @router.post("/setup")
    async def setup_submit(request: Request):
        if ctx.users.has_users():
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        check_csrf(request, str(form.get("csrf_token") or ""))
        password = str(form.get("password") or "")
        if password != str(form.get("confirm_password") or ""):
            return ctx.templates.TemplateResponse(
                request=request,
                name="setup.html",
                context=ctx.page_context(
                    request,
                    error="两次输入的密码不一致",
                ),
                status_code=400,
            )
        try:
            ctx.users.create_user(
                str(form.get("username") or ""),
                str(form.get("display_name") or ""),
                password,
                role="admin",
                migrate_default_profile=True,
                only_if_empty=True,
            )
        except ConfigurationError as exc:
            return ctx.templates.TemplateResponse(
                request=request,
                name="setup.html",
                context=ctx.page_context(request, error=exc.user_message),
                status_code=400,
            )
        return RedirectResponse("/login?created=1", status_code=303)

    @router.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        if not ctx.users.has_users():
            return RedirectResponse("/setup", status_code=303)
        if ctx.current_account(request):
            return RedirectResponse("/dashboard", status_code=303)
        return ctx.templates.TemplateResponse(
            request=request,
            name="login.html",
            context=ctx.page_context(
                request,
                created=request.query_params.get("created"),
            ),
        )

    @router.post("/login")
    async def login_submit(request: Request):
        form = await request.form()
        check_csrf(request, str(form.get("csrf_token") or ""))
        try:
            account = ctx.users.authenticate(
                str(form.get("username") or ""),
                str(form.get("password") or ""),
            )
        except ConfigurationError as exc:
            return ctx.templates.TemplateResponse(
                request=request,
                name="login.html",
                context=ctx.page_context(request, error=exc.user_message),
                status_code=401,
            )
        request.session.clear()
        request.session["user_id"] = account.id
        request.session["csrf"] = secrets.token_urlsafe(32)
        ctx.logger.info("login_success user=%s", account.id)
        return RedirectResponse(
            "/change-password" if account.must_change_password else "/dashboard",
            status_code=303,
        )

    @router.post("/logout")
    async def logout(request: Request):
        form = await request.form()
        check_csrf(request, str(form.get("csrf_token") or ""))
        request.session.clear()
        ctx.logger.info("logout")
        return RedirectResponse("/login", status_code=303)

    @router.get("/dashboard", response_class=HTMLResponse)
    async def dashboard(request: Request):
        return render_private(request, "dashboard.html", active="dashboard")

    @router.get("/change-password", response_class=HTMLResponse)
    async def change_password_page(request: Request):
        account = ctx.current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)
        return ctx.templates.TemplateResponse(
            request=request,
            name="change_password.html",
            context=ctx.page_context(request, account),
        )

    @router.post("/change-password")
    async def change_password_submit(request: Request):
        account = ctx.current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        check_csrf(request, str(form.get("csrf_token") or ""))
        new_password = str(form.get("new_password") or "")
        if new_password != str(form.get("confirm_password") or ""):
            return ctx.templates.TemplateResponse(
                request=request,
                name="change_password.html",
                context=ctx.page_context(
                    request,
                    account,
                    error="两次输入的新密码不一致",
                ),
                status_code=400,
            )
        try:
            ctx.users.change_password(
                account.id,
                str(form.get("old_password") or ""),
                new_password,
            )
        except ConfigurationError as exc:
            return ctx.templates.TemplateResponse(
                request=request,
                name="change_password.html",
                context=ctx.page_context(
                    request,
                    account,
                    error=exc.user_message,
                ),
                status_code=400,
            )
        return RedirectResponse(
            "/dashboard?password_changed=1",
            status_code=303,
        )

    @router.get("/admin", response_class=HTMLResponse)
    async def admin_page(request: Request):
        account = ctx.current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)
        if not account.is_admin:
            return RedirectResponse("/dashboard", status_code=303)
        return ctx.templates.TemplateResponse(
            request=request,
            name="admin.html",
            context=ctx.page_context(
                request,
                account,
                active="admin",
                users=ctx.users.list_users(),
            ),
        )

    @router.post("/api/admin/users")
    async def create_user_api(request: Request):
        actor = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        if not actor.is_admin:
            return json_error("只有管理员可以创建账号", 403)
        payload = await ctx.json_payload(request)
        try:
            ctx.users.create_user(
                str(payload.get("username") or ""),
                str(payload.get("display_name") or ""),
                str(payload.get("password") or ""),
                role=str(payload.get("role") or "user"),
                must_change_password=True,
            )
        except ConfigurationError as exc:
            return json_error(exc.user_message)
        return {"ok": True, "message": "账号已创建"}

    @router.post("/api/admin/users/{user_id}/toggle")
    async def toggle_user_api(user_id: str, request: Request):
        actor = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        try:
            target = ctx.users.get_user(user_id)
            ctx.users.set_active(actor.id, user_id, not target.is_active)
        except ConfigurationError as exc:
            return json_error(exc.user_message, 403)
        return {"ok": True, "message": "账号状态已更新"}

    @router.post("/api/admin/users/{user_id}/reset-password")
    async def reset_password_api(user_id: str, request: Request):
        actor = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await ctx.json_payload(request)
        try:
            ctx.users.reset_password(
                actor.id,
                user_id,
                str(payload.get("password") or ""),
            )
        except ConfigurationError as exc:
            return json_error(exc.user_message, 403)
        return {"ok": True, "message": "临时密码已重置"}

    return router

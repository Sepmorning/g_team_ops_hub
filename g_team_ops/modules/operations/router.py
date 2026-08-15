"""操作历史、审计与本机数据库备份HTTP路由。"""

from __future__ import annotations

import asyncio
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ...errors import ConfigurationError
from ...airscript import AirScriptClient
from ...listing import ListingAirScriptClient
from ...web.context import WebContext, check_csrf, json_error
from .service import OperationHistoryService
from .shared_table import SharedTableOperationManager


def build_router(ctx: WebContext) -> APIRouter:
    router = APIRouter()
    service = OperationHistoryService(ctx.operations, ctx.backups)
    shared_tables = SharedTableOperationManager(ctx.operations)

    def restore_client(account_id: str, batch):
        database = ctx.database_for(account_id)
        if batch.module_name == "tracking":
            config, _shop, _country = ctx.logistics_config(
                database,
                batch.shop_id,
                batch.country_id,
            )
            return AirScriptClient(
                config,
                retries=ctx.coordinator.settings.retries,
            )
        if batch.module_name == "inventory":
            config, _shop, _country = ctx.listing_config(
                database,
                batch.shop_id,
                batch.country_id,
            )
            return ListingAirScriptClient(
                config,
                retries=ctx.coordinator.settings.retries,
            )
        raise ConfigurationError("该模块暂不支持共享表恢复")

    def latest_restorable(account_id: str, batch_id: str):
        batch = ctx.operations.get_batch(account_id, batch_id)
        if batch is None:
            raise ConfigurationError("操作批次不存在或不属于当前用户")
        if not batch.reversible or batch.status not in {
            "applied",
            "partial",
            "reconciled",
        }:
            raise ConfigurationError("该操作当前不可恢复")
        latest = ctx.operations.latest_reversible_batch(
            account_id,
            module_name=batch.module_name,
            shop_id=batch.shop_id,
        )
        if latest is None or latest.id != batch.id:
            raise ConfigurationError(
                "必须先恢复该店铺和模块中更晚的可恢复操作"
            )
        return batch

    def uncertain_batch(account_id: str, batch_id: str):
        batch = ctx.operations.get_batch(account_id, batch_id)
        if batch is None:
            raise ConfigurationError("操作批次不存在或不属于当前用户")
        if batch.status != "uncertain":
            raise ConfigurationError("只有状态不确定的批次可以执行核对")
        return batch

    @router.get("/operations", response_class=HTMLResponse)
    async def operations_page(request: Request):
        account = ctx.current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)
        if account.must_change_password:
            return RedirectResponse("/change-password", status_code=303)
        return ctx.templates.TemplateResponse(
            request=request,
            name="operations.html",
            context=ctx.page_context(
                request,
                account,
                active="operations",
                operations=ctx.operations.list_batches(account.id, limit=100),
                backups=(ctx.operations.list_backups() if account.is_admin else []),
                backup_health=(ctx.backups.health_status() if account.is_admin else None),
                database_restores=(
                    ctx.operations.list_database_restores()
                    if account.is_admin
                    else []
                ),
            ),
        )

    @router.get("/api/operations")
    async def list_operations_api(request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        try:
            limit = int(request.query_params.get("limit") or 100)
        except ValueError:
            return json_error("limit必须是整数")
        values = ctx.operations.list_batches(account.id, limit=limit, module_name=str(request.query_params.get("module") or ""), shop_id=str(request.query_params.get("shop_id") or ""))
        return {"ok": True, "operations": [item.to_payload() for item in values]}

    @router.get("/api/operations/{batch_id}")
    async def operation_details_api(batch_id: str, request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        try:
            payload = service.details_payload(account.id, batch_id)
        except ConfigurationError as exc:
            return json_error(exc.user_message, 404)
        return {"ok": True, **payload}

    @router.post("/api/operations/{batch_id}/restore-preview")
    async def restore_preview_api(batch_id: str, request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        try:
            payload = await ctx.json_payload(request)
            selected_change_ids = payload.get("change_ids")
            batch = latest_restorable(account.id, batch_id)
            client = restore_client(account.id, batch)
            preview = await asyncio.to_thread(
                shared_tables.preview_restore,
                account.id,
                batch,
                client,
                selected_change_ids,
            )
        except ConfigurationError as exc:
            return json_error(exc.user_message)
        except Exception as exc:
            if hasattr(exc, "user_message"):
                return json_error(exc.user_message)
            ctx.logger.exception(
                "shared_table_restore_preview_unexpected user=%s batch=%s",
                account.id,
                batch_id,
            )
            return json_error("检查共享表恢复条件时发生未预期错误", 500)
        return {"ok": True, **preview}

    @router.post("/api/operations/{batch_id}/restore")
    async def restore_operation_api(batch_id: str, request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await ctx.json_payload(request)
        selected_change_ids = payload.get("change_ids")
        idempotency_key = str(
            request.headers.get("Idempotency-Key")
            or secrets.token_urlsafe(18)
        )
        try:
            existing = ctx.operations.get_by_idempotency(
                account.id,
                idempotency_key,
            )
            if existing is not None and existing.rollback_of_batch_id != batch_id:
                raise ConfigurationError("该幂等键已经用于其他操作")
            if existing is not None:
                requested_ids = (
                    None
                    if selected_change_ids is None
                    else [str(value).strip() for value in selected_change_ids]
                    if isinstance(selected_change_ids, list)
                    else []
                )
                saved_ids = existing.summary.get("selected_change_ids")
                if requested_ids is not None and requested_ids != saved_ids:
                    raise ConfigurationError("该幂等键已经用于另一组恢复项目")
                summary = existing.summary
                return {
                    "ok": True,
                    "message": (
                        f"已恢复 {summary.get('applied', 0)} 项；"
                        f"冲突 {len(summary.get('conflicts', []))} 项；"
                        f"失败 {len(summary.get('failures', []))} 项"
                    ),
                    "operation": existing.to_payload(),
                    "summary": summary,
                }
            batch = latest_restorable(account.id, batch_id)
            client = restore_client(account.id, batch)
            restored, summary = await asyncio.to_thread(
                shared_tables.restore,
                profile_id=account.id,
                original=batch,
                client=client,
                idempotency_key=idempotency_key,
                selected_change_ids=selected_change_ids,
            )
        except ConfigurationError as exc:
            return json_error(exc.user_message)
        except Exception as exc:
            if hasattr(exc, "user_message"):
                return json_error(exc.user_message)
            ctx.logger.exception(
                "shared_table_restore_unexpected user=%s batch=%s",
                account.id,
                batch_id,
            )
            return json_error("恢复共享表时发生未预期错误", 500)
        return {
            "ok": True,
            "message": (
                f"已恢复 {summary.get('applied', 0)} 项；"
                f"冲突 {len(summary.get('conflicts', []))} 项；"
                f"失败 {len(summary.get('failures', []))} 项"
            ),
            "operation": restored.to_payload(),
            "summary": summary,
        }

    @router.post("/api/operations/{batch_id}/take-over")
    async def take_over_operation_api(batch_id: str, request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await ctx.json_payload(request)
        if str(payload.get("confirm") or "").strip() != "原任务已停止":
            return json_error("请确认原任务已经停止后再执行接管")
        try:
            batch = await asyncio.to_thread(
                ctx.operations.take_over_interrupted,
                account.id,
                batch_id,
            )
        except ConfigurationError as exc:
            return json_error(exc.user_message)
        return {
            "ok": True,
            "message": batch.error_message,
            "operation": batch.to_payload(),
        }

    @router.post("/api/operations/{batch_id}/reconcile-preview")
    async def reconcile_preview_api(batch_id: str, request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        try:
            batch = uncertain_batch(account.id, batch_id)
            client = restore_client(account.id, batch)
            preview = await asyncio.to_thread(
                shared_tables.preview_uncertain,
                account.id,
                batch,
                client,
            )
        except ConfigurationError as exc:
            return json_error(exc.user_message)
        except Exception as exc:
            if hasattr(exc, "user_message"):
                return json_error(exc.user_message)
            ctx.logger.exception(
                "uncertain_reconcile_preview_unexpected user=%s batch=%s",
                account.id,
                batch_id,
            )
            return json_error("核对状态不确定批次时发生未预期错误", 500)
        return {"ok": True, **preview}

    @router.post("/api/operations/{batch_id}/reconcile")
    async def reconcile_operation_api(batch_id: str, request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        try:
            batch = uncertain_batch(account.id, batch_id)
            client = restore_client(account.id, batch)
            reconciled, summary = await asyncio.to_thread(
                shared_tables.confirm_uncertain,
                account.id,
                batch,
                client,
            )
        except ConfigurationError as exc:
            return json_error(exc.user_message)
        except Exception as exc:
            if hasattr(exc, "user_message"):
                return json_error(exc.user_message)
            ctx.logger.exception(
                "uncertain_reconcile_unexpected user=%s batch=%s",
                account.id,
                batch_id,
            )
            return json_error("确认状态不确定批次时发生未预期错误", 500)
        return {
            "ok": True,
            "message": (
                f"核对完成：发现 {summary.get('change_count', 0)} 项实际变化"
            ),
            "operation": reconciled.to_payload(),
            "summary": summary,
        }

    @router.get("/api/backups")
    async def list_backups_api(request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        if not account.is_admin:
            return json_error("只有管理员可以查看整库备份", 403)
        return {"ok": True, "backups": [item.to_payload() for item in ctx.operations.list_backups()]}

    @router.get("/api/backups/health")
    async def backup_health_api(request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        if not account.is_admin:
            return json_error("只有管理员可以查看备份健康状态", 403)
        return {"ok": True, "health": ctx.backups.health_status()}

    @router.post("/api/backups")
    async def create_backup_api(request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        if not account.is_admin:
            return json_error("只有管理员可以创建整库备份", 403)
        payload = await ctx.json_payload(request)
        batch = None
        try:
            batch = ctx.operations.create_batch(account.id, "operations", "database_backup", idempotency_key=str(request.headers.get("Idempotency-Key") or ""), summary={"reason": str(payload.get("reason") or "manual")})
            if batch.status != "prepared":
                return {"ok": True, "operation": batch.to_payload()}
            ctx.operations.update_status(account.id, batch.id, "running")
            backup = await asyncio.to_thread(ctx.backups.create_backup, reason=str(payload.get("reason") or "manual"), created_by=account.id)
            batch = ctx.operations.update_status(account.id, batch.id, "applied", summary={"backup_id": backup.id, "file_name": backup.file_name, "size_bytes": backup.size_bytes, "integrity_result": backup.integrity_result})
        except ConfigurationError as exc:
            if batch is not None:
                ctx.operations.update_status(account.id, batch.id, "failed", error_category="configuration", error_message=exc.user_message)
            return json_error(exc.user_message)
        except Exception:
            if batch is not None:
                ctx.operations.update_status(account.id, batch.id, "failed", error_category="unexpected", error_message="创建数据库备份时发生未预期错误")
            ctx.logger.exception("database_backup_unexpected user=%s", account.id)
            return json_error("创建数据库备份时发生未预期错误", 500)
        return {"ok": True, "message": "数据库一致性备份已创建并通过完整性检查", "backup": backup.to_payload(), "operation": batch.to_payload()}

    @router.post("/api/backups/{backup_id}/verify")
    async def verify_backup_api(backup_id: str, request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        if not account.is_admin:
            return json_error("只有管理员可以验证整库备份", 403)
        try:
            backup = await asyncio.to_thread(ctx.backups.verify_backup, backup_id)
        except ConfigurationError as exc:
            return json_error(exc.user_message)
        return {"ok": True, "message": "备份校验值和数据库完整性检查均通过", "backup": backup.to_payload()}

    return router

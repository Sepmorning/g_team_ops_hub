"""库存销售页面、导入预览和共享表更新路由。"""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ...errors import CarrierError, ConfigurationError
from ...listing import (
    MAX_LISTING_FILE_SIZE,
    ListingAirScriptClient,
    infer_listing_data_date,
    listing_summary_from_payload,
    parse_listing_export,
    validate_listing_data_date,
)
from ...web.context import WebContext, check_csrf, json_error
from ..operations.shared_table import SharedTableOperationManager


def build_router(ctx: WebContext) -> APIRouter:
    router = APIRouter()
    operation_manager = SharedTableOperationManager(ctx.operations)

    @router.get("/inventory", response_class=HTMLResponse)
    async def inventory_page(request: Request):
        account = ctx.current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)
        if account.must_change_password:
            return RedirectResponse("/change-password", status_code=303)
        database = ctx.database_for(account.id)
        return ctx.templates.TemplateResponse(
            request=request,
            name="inventory.html",
            context=ctx.page_context(
                request,
                account,
                active="inventory",
                shops=database.list_shops(),
            ),
        )

    @router.get("/api/inventory/config")
    async def inventory_config_api(request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        shop_id = str(request.query_params.get("shop_id") or "").strip()
        database = ctx.database_for(account.id)
        shop = database.get_shop(shop_id)
        if shop is None:
            return json_error("店铺不存在或不属于当前用户", 404)
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
                ctx.site_payload(database, item)
                for item in countries
            ],
        }

    @router.delete("/api/inventory/countries/{country_id}")
    async def delete_shop_country_api(country_id: str, request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        database = ctx.database_for(account.id)
        try:
            database.delete_shop_country(country_id)
        except ConfigurationError as exc:
            return json_error(exc.user_message, 404)
        return {"ok": True, "message": "国家与Listing子表映射已删除"}

    @router.post("/api/inventory/countries/{country_id}/validation")
    async def validate_listing_country_api(
        country_id: str,
        request: Request,
    ):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await ctx.json_payload(request)
        shop_id = str(payload.get("shop_id") or "").strip()
        database = ctx.database_for(account.id)
        try:
            config, _shop, _country = ctx.listing_config(
                database,
                shop_id,
                country_id,
            )
            binding = await asyncio.to_thread(
                ListingAirScriptClient(
                    config,
                    retries=ctx.coordinator.settings.retries,
                ).validate
            )
        except (CarrierError, ConfigurationError) as exc:
            return json_error(exc.user_message)
        return {
            "ok": True,
            "message": (
                f"已连接“{binding.sheet_name}”，"
                f"表头位于第{binding.header_row}行，"
                f"已按名称识别{len(binding.columns)}列"
            ),
        }

    @router.post("/api/inventory/imports/preview")
    async def preview_listing_import_api(request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        try:
            content_length = int(request.headers.get("Content-Length") or 0)
        except ValueError:
            content_length = 0
        if content_length > MAX_LISTING_FILE_SIZE + 1024 * 1024:
            return json_error("上传内容超过21MB安全上限", 413)
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
            database = ctx.database_for(account.id)
            config, shop, country = ctx.listing_config(
                database,
                shop_id,
                country_id,
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
                    config,
                    retries=ctx.coordinator.settings.retries,
                ).validate
            )
            preview_id = ctx.listing_previews.create(
                account.id,
                shop_id,
                country_id,
                data_date,
                filename,
                parsed,
            )
        except (CarrierError, ConfigurationError) as exc:
            return json_error(exc.user_message)
        except Exception:
            ctx.logger.exception(
                "listing_preview_unexpected user=%s shop=%s",
                account.id,
                shop_id,
            )
            return json_error("读取领星文件时发生未预期错误", 500)
        ctx.logger.info(
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
            "discount_price": {
                "source_present": parsed.has_discount_price,
                "target_present": "discount_price" in binding.columns,
                "will_update": (
                    parsed.has_discount_price
                    and "discount_price" in binding.columns
                ),
            },
            "sample": [item.preview_dict() for item in parsed.rows[:30]],
        }

    @router.post("/api/inventory/imports/apply")
    async def apply_listing_import_api(request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await ctx.json_payload(request)
        preview_id = str(payload.get("preview_id") or "").strip()
        try:
            pending = ctx.listing_previews.get(account.id, preview_id)
            database = ctx.database_for(account.id)
            config, shop, country = ctx.listing_config(
                database,
                pending.shop_id,
                pending.country_id,
            )
            client = ListingAirScriptClient(
                config,
                retries=ctx.coordinator.settings.retries,
            )
            guarded = await asyncio.to_thread(
                operation_manager.execute,
                profile_id=account.id,
                module_name="inventory",
                operation_type="listing_import",
                shop_id=pending.shop_id,
                country_id=pending.country_id,
                idempotency_key=str(
                    request.headers.get("Idempotency-Key")
                    or f"listing-preview:{preview_id}"
                ),
                snapshot_before=lambda: client.snapshot_rows(pending.parsed.rows),
                apply=lambda before: client.sync(
                    pending.parsed.rows,
                    pending.data_date,
                    preconditions=before,
                ),
                snapshot_after=client.snapshot_targets,
                serialize_result=asdict,
                restore_result=listing_summary_from_payload,
                is_partial=lambda result: bool(result.failures),
                initial_summary={
                    "filename": pending.filename,
                    "data_date": pending.data_date,
                    "row_count": len(pending.parsed.rows),
                },
            )
            summary = guarded.business_result
        except (CarrierError, ConfigurationError) as exc:
            return json_error(exc.user_message)
        except Exception:
            ctx.logger.exception(
                "listing_apply_unexpected user=%s preview=%s",
                account.id,
                preview_id,
            )
            return json_error("回填Listing共享表时发生未预期错误", 500)
        ctx.logger.info(
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
            "operation": guarded.batch.to_payload(),
        }

    return router

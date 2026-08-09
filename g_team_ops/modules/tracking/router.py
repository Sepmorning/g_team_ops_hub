"""物流查询页面、手动查询和店铺一键更新路由。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ...airscript import AirScriptClient
from ...errors import CarrierError, ConfigurationError
from ...parser import parse_fba_input
from ...web.context import WebContext, check_csrf, json_error
from ...web.services import (
    carrier_key_from_sheet,
    result_dict,
    summary_dict,
)


def build_router(ctx: WebContext) -> APIRouter:
    router = APIRouter()

    @router.get("/tracking", response_class=HTMLResponse)
    async def tracking_page(request: Request):
        account = ctx.current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)
        if account.must_change_password:
            return RedirectResponse("/change-password", status_code=303)
        database = ctx.database_for(account.id)
        return ctx.templates.TemplateResponse(
            request=request,
            name="tracking.html",
            context=ctx.page_context(
                request,
                account,
                active="tracking",
                shops=database.list_shops(),
                carrier_statuses=ctx.coordinator.configured_status(account.id),
            ),
        )

    @router.post("/api/tracking/query")
    async def query_api(request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await ctx.json_payload(request)
        raw_input = str(payload.get("input") or "")
        if len(raw_input) > 200_000:
            return json_error("输入内容过长，请分次提交", 413)
        parsed = parse_fba_input(raw_input)
        if not parsed.valid:
            return json_error("没有可查询的有效FBA编号")
        sync_value = payload.get("sync_wps", True)
        if not isinstance(sync_value, bool):
            return json_error("sync_wps必须是布尔值")
        airscript_config = None
        if sync_value:
            shop_id = str(payload.get("shop_id") or "")
            country_id = str(payload.get("country_id") or "")
            if not shop_id or not country_id:
                return json_error(
                    "勾选更新共享表时必须先选择店铺和国家站点"
                )
            database = ctx.database_for(account.id)
            try:
                airscript_config, _shop, _site = ctx.logistics_config(
                    database,
                    shop_id,
                    country_id,
                )
            except ConfigurationError as exc:
                return json_error(exc.user_message)
        try:
            response = await asyncio.to_thread(
                ctx.coordinator.query,
                account.id,
                parsed.valid,
                airscript_config,
            )
        except (CarrierError, ConfigurationError) as exc:
            return json_error(exc.user_message)
        except Exception:
            ctx.logger.exception(
                "manual_tracking_query_unexpected user=%s",
                account.id,
            )
            return json_error("查询服务发生未预期错误，请稍后重试", 500)
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

    @router.post("/api/shops/{shop_id}/tracking-sync")
    async def query_pending_shop_api(shop_id: str, request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await ctx.json_payload(request)
        country_id = str(payload.get("country_id") or "").strip()
        database = ctx.database_for(account.id)
        try:
            config, shop, country = ctx.logistics_config(
                database,
                shop_id,
                country_id,
            )
        except ConfigurationError as exc:
            return json_error(exc.user_message)

        client = AirScriptClient(
            config,
            retries=ctx.coordinator.settings.retries,
        )
        try:
            pending_items = await asyncio.to_thread(
                client.list_pending_tracking_items
            )
        except (CarrierError, ConfigurationError) as exc:
            return json_error(exc.user_message)
        if not pending_items:
            try:
                cleanup_summary = await asyncio.to_thread(
                    client.sync_tracking_results,
                    [],
                )
            except (CarrierError, ConfigurationError) as exc:
                return {
                    "ok": True,
                    "message": (
                        "共享表中没有需要查询的FBA，但轨迹明细清理失败"
                    ),
                    "carrier_statuses": [],
                    "pending_count": 0,
                    "results": [],
                    "wps": None,
                    "wps_error": exc.user_message,
                }
            return {
                "ok": True,
                "message": "共享表中没有需要查询的FBA，已检查轨迹明细",
                "carrier_statuses": [],
                "pending_count": 0,
                "results": [],
                "wps": summary_dict(cleanup_summary),
                "wps_error": "",
            }

        required_carriers = {
            key
            for item in pending_items
            if (key := carrier_key_from_sheet(item.carrier))
        }
        statuses = await asyncio.to_thread(
            ctx.coordinator.validate_required,
            account.id,
            required_carriers,
        )
        status_payload = [
            {
                "carrier": item.carrier,
                "connected": item.connected,
                "message": item.message,
                "checked": item.checked,
                "cached": item.cached,
            }
            for item in statuses
        ]
        if not all(item.connected for item in statuses):
            return JSONResponse(
                {
                    "ok": False,
                    "message": (
                        "共享表本次使用的货代存在未连接项，自动任务已停止"
                    ),
                    "carrier_statuses": status_payload,
                },
                status_code=409,
            )

        try:
            response = await asyncio.to_thread(
                ctx.coordinator.query_routed,
                account.id,
                pending_items,
                config,
            )
        except (CarrierError, ConfigurationError) as exc:
            return json_error(exc.user_message)
        except Exception:
            ctx.logger.exception(
                "automatic_tracking_sync_unexpected user=%s shop=%s",
                account.id,
                shop_id,
            )
            return json_error("自动查询任务发生未预期错误，请稍后重试", 500)
        return {
            "ok": True,
            "message": (
                f"{shop.name} / {country.country_name}："
                f"已按共享表货代列定向查询 "
                f"{len(pending_items)} 个待更新FBA"
            ),
            "carrier_statuses": status_payload,
            "pending_count": len(pending_items),
            "results": [result_dict(item) for item in response.results],
            "wps": summary_dict(response.wps_summary),
            "wps_error": response.wps_error,
        }

    return router

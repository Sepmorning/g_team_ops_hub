"""店铺配置、模块连接和工作簿站点发现路由。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ...airscript import (
    AirScriptClient,
    AirScriptConfig,
    parse_share_file_id,
)
from ...errors import CarrierError, ConfigurationError
from ...listing import ListingAirScriptClient, ListingConnectionConfig
from ...sites import discover_sites, listing_prefixes, normalize_sheet_name
from ...web.context import WebContext, check_csrf, json_error


def build_router(ctx: WebContext) -> APIRouter:
    router = APIRouter()

    @router.get("/shops", response_class=HTMLResponse)
    async def shops_page(request: Request):
        account = ctx.current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)
        if account.must_change_password:
            return RedirectResponse("/change-password", status_code=303)
        database = ctx.database_for(account.id)
        return ctx.templates.TemplateResponse(
            request=request,
            name="shops.html",
            context=ctx.page_context(
                request,
                account,
                active="shops",
                shops=database.list_shops(),
            ),
        )

    @router.post("/api/shops")
    async def save_shop_api(request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await ctx.json_payload(request)
        database = ctx.database_for(account.id)
        shop_id = str(payload.get("id") or "").strip() or None
        existing = database.get_shop(shop_id) if shop_id else None
        token = str(payload.get("api_token") or "")
        if not token and existing:
            token = existing.config.api_token
        webhook_url = str(payload.get("webhook_url") or "").strip()
        if not webhook_url and existing:
            webhook_url = existing.config.webhook_url
        share_url = str(payload.get("share_url") or "").strip()
        config = AirScriptConfig(share_url, webhook_url, token)
        try:
            parse_share_file_id(share_url)
            shop = database.save_shop(
                str(payload.get("name") or ""),
                config,
                shop_id=shop_id,
            )
        except (CarrierError, ConfigurationError) as exc:
            return json_error(exc.user_message)
        return {
            "ok": True,
            "shop_id": shop.id,
            "message": (
                "店铺名称和共享表链接已保存；"
                "请在店铺中配置模块脚本并扫描国家站点"
            ),
        }

    @router.delete("/api/shops/{shop_id}")
    async def delete_shop_api(shop_id: str, request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        database = ctx.database_for(account.id)
        try:
            database.delete_shop(shop_id)
        except ConfigurationError as exc:
            return json_error(exc.user_message, 404)
        return {"ok": True, "message": "店铺已删除"}

    @router.post("/api/shops/{shop_id}/validation")
    async def validate_shop_api(shop_id: str, request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        database = ctx.database_for(account.id)
        shop = database.get_shop(shop_id)
        if shop is None:
            return json_error("店铺不存在或不属于当前用户", 404)
        try:
            binding = await asyncio.to_thread(
                AirScriptClient(
                    shop.config,
                    retries=ctx.coordinator.settings.retries,
                ).validate
            )
        except (CarrierError, ConfigurationError) as exc:
            return json_error(exc.user_message)
        return {
            "ok": True,
            "message": (
                f"已连接{binding.sheet_name}和{binding.detail_sheet_name}；"
                f"主表已按名称识别{len(binding.columns)}个标准字段，"
                f"FBA列{binding.fba_column}，"
                f"货代列{binding.carrier_column}，"
                f"路由列{binding.route_column}"
            ),
        }

    @router.get("/api/shops/{shop_id}/config")
    async def shop_config_api(shop_id: str, request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        database = ctx.database_for(account.id)
        shop = database.get_shop(shop_id)
        if shop is None:
            return json_error("店铺不存在或不属于当前用户", 404)
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
                        listing_connection.updated_at
                        if listing_connection
                        else ""
                    ),
                },
            },
            "sites": [
                ctx.site_payload(database, item)
                for item in sites
            ],
        }

    @router.post("/api/shops/{shop_id}/logistics-connection")
    async def save_logistics_connection_api(
        shop_id: str,
        request: Request,
    ):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await ctx.json_payload(request)
        database = ctx.database_for(account.id)
        shop = database.get_shop(shop_id)
        if shop is None:
            return json_error("店铺不存在或不属于当前用户", 404)
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
                    config,
                    retries=ctx.coordinator.settings.retries,
                ).discover_sheets
            )
            database.save_shop(shop.name, config, shop_id=shop.id)
        except (CarrierError, ConfigurationError) as exc:
            return json_error(exc.user_message)
        return {
            "ok": True,
            "message": f"FBA物流脚本已连接，共读取到{len(sheets)}个子表",
            "sheet_count": len(sheets),
        }

    @router.post("/api/shops/{shop_id}/listing-connection")
    async def save_listing_connection_api(
        shop_id: str,
        request: Request,
    ):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await ctx.json_payload(request)
        database = ctx.database_for(account.id)
        shop = database.get_shop(shop_id)
        if shop is None:
            return json_error("店铺不存在或不属于当前用户", 404)
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
                    retries=ctx.coordinator.settings.retries,
                ).discover_sheets
            )
            database.save_listing_connection(shop_id, webhook_url, token)
        except (CarrierError, ConfigurationError) as exc:
            return json_error(exc.user_message)
        return {
            "ok": True,
            "message": f"Listing脚本已连接，共读取到{len(sheets)}个子表",
            "sheet_count": len(sheets),
        }

    @router.post("/api/shops/{shop_id}/sites")
    async def save_shop_site_api(shop_id: str, request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await ctx.json_payload(request)
        database = ctx.database_for(account.id)
        if database.get_shop(shop_id) is None:
            return json_error("店铺不存在或不属于当前用户", 404)
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
            return json_error(exc.user_message)
        return {
            "ok": True,
            "site": ctx.site_payload(database, site),
            "message": (
                "国家站点映射已保存；"
                "使用模块前可分别测试Listing和物流连接"
            ),
        }

    @router.post("/api/shops/{shop_id}/countries")
    async def save_shop_country_api(shop_id: str, request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await ctx.json_payload(request)
        database = ctx.database_for(account.id)
        shop = database.get_shop(shop_id)
        if shop is None:
            return json_error("店铺不存在或不属于当前用户", 404)
        connection = database.load_listing_connection(shop_id)
        if connection is None:
            return json_error("请先保存该店铺的独立Listing AirScript连接")
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
                    config,
                    retries=ctx.coordinator.settings.retries,
                ).validate
            )
            country = database.save_shop_country(
                shop_id,
                str(payload.get("country_name") or ""),
                sheet_name,
                country_id=str(payload.get("id") or "").strip() or None,
            )
        except (CarrierError, ConfigurationError) as exc:
            return json_error(exc.user_message)
        except Exception:
            ctx.logger.exception(
                "listing_country_validation_unexpected user=%s shop=%s",
                account.id,
                shop_id,
            )
            return json_error("验证Listing子表时发生未预期错误", 500)
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

    @router.post("/api/shops/{shop_id}/sites/{site_id}/validation")
    async def validate_shop_site_api(
        shop_id: str,
        site_id: str,
        request: Request,
    ):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await ctx.json_payload(request)
        module = str(payload.get("module") or "").strip().lower()
        database = ctx.database_for(account.id)
        try:
            if module == "listing":
                config, _shop, _site = ctx.listing_config(
                    database,
                    shop_id,
                    site_id,
                )
                binding = await asyncio.to_thread(
                    ListingAirScriptClient(
                        config,
                        retries=ctx.coordinator.settings.retries,
                    ).validate
                )
                message = (
                    f"Listing已连接“{binding.sheet_name}”，"
                    f"表头位于第{binding.header_row}行"
                )
            elif module == "tracking":
                config, _shop, _site = ctx.logistics_config(
                    database,
                    shop_id,
                    site_id,
                )
                binding = await asyncio.to_thread(
                    AirScriptClient(
                        config,
                        retries=ctx.coordinator.settings.retries,
                    ).validate
                )
                message = (
                    f"物流已连接“{binding.sheet_name}”和"
                    f"“{binding.detail_sheet_name}”"
                )
            else:
                return json_error("module必须是listing或tracking")
        except (CarrierError, ConfigurationError) as exc:
            return json_error(exc.user_message)
        return {"ok": True, "message": message}

    @router.post("/api/shops/{shop_id}/discover-sites")
    async def discover_shop_sites_api(shop_id: str, request: Request):
        account = ctx.require_api_user(request)
        check_csrf(request, request.headers.get("X-CSRF-Token"))
        payload = await ctx.json_payload(request)
        database = ctx.database_for(account.id)
        shop = database.get_shop(shop_id)
        if shop is None:
            return json_error("店铺不存在或不属于当前用户", 404)
        listing_connection = database.load_listing_connection(shop_id)
        discovered_by_module: dict[str, list[dict[str, str]]] = {}
        errors: list[str] = []
        if shop.config.webhook_url and shop.config.api_token:
            try:
                discovered_by_module["物流"] = await asyncio.to_thread(
                    AirScriptClient(
                        shop.config,
                        retries=ctx.coordinator.settings.retries,
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
                        retries=ctx.coordinator.settings.retries,
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
            return json_error(message)
        module_sets = {
            name: {
                normalize_sheet_name(item["name"])
                for item in sheets
            }
            for name, sheets in discovered_by_module.items()
        }
        if len(module_sets) > 1:
            values = list(module_sets.values())
            if any(value != values[0] for value in values[1:]):
                return json_error(
                    "物流和Listing脚本返回的子表不一致，"
                    "可能连接了不同工作簿；为避免写错表，已停止自动保存",
                    409,
                )
        sheets = next(iter(discovered_by_module.values()))
        sheet_names = [item["name"] for item in sheets]
        available_prefixes = listing_prefixes(sheet_names)
        available_by_normalized = {
            normalize_sheet_name(prefix): prefix
            for prefix in available_prefixes
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
                return json_error(
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
                        f"已保存的Listing前缀“{active_prefix}”"
                        "在本次扫描中不存在；为避免写错表，未更新站点映射"
                    ),
                    "listing_prefix": active_prefix,
                    "available_listing_prefixes": available_prefixes,
                    "warnings": list(dict.fromkeys(errors)),
                    "candidates": [],
                    "sites": [
                        ctx.site_payload(database, item)
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
                    shop_id,
                    exact_prefix,
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
                        ctx.site_payload(database, item)
                        for item in database.list_shop_countries(shop_id)
                    ],
                }
            else:
                return {
                    "ok": True,
                    "confirmation_required": False,
                    "message": (
                        f"扫描到{len(sheets)}个子表，但没有识别到"
                        "“前缀-国家”格式的Listing子表"
                    ),
                    "listing_prefix": "",
                    "available_listing_prefixes": [],
                    "warnings": list(dict.fromkeys(errors)),
                    "candidates": [],
                    "sites": [
                        ctx.site_payload(database, item)
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
                    f"{candidate.country_name}站点未自动保存："
                    f"{exc.user_message}"
                )
        sites = database.list_shop_countries(shop_id)
        return {
            "ok": True,
            "confirmation_required": False,
            "message": (
                f"扫描到{len(sheets)}个子表，"
                f"已保存或更新{saved_count}个明确站点映射"
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
            "sites": [
                ctx.site_payload(database, item)
                for item in sites
            ],
        }

    return router

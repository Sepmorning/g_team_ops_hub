from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import requests

from .errors import (
    AuthenticationError,
    ConfigurationError,
    NetworkError,
    RateLimitError,
    ResponseError,
    ServerError,
)
from .models import QueryStatus, TrackingResult


TARGET_SHEET_NAME = "US-FBA"
AIRSCRIPT_WRITE_BATCH_SIZE = 50


def parse_share_file_id(share_url: str) -> str:
    """验证金山文档分享链接，并返回不含敏感信息的文件标识。"""
    value = share_url.strip()
    match = re.fullmatch(
        r"https://www\.kdocs\.cn/l/([A-Za-z0-9_-]+)(?:[/?#].*)?", value
    )
    if not match:
        raise ConfigurationError("共享表链接格式不正确，应为 https://www.kdocs.cn/l/…")
    return match.group(1)


@dataclass(frozen=True)
class AirScriptConfig:
    share_url: str
    webhook_url: str
    api_token: str
    sheet_name: str = TARGET_SHEET_NAME


@dataclass(frozen=True)
class AirScriptBinding:
    sheet_name: str
    fba_column: str
    route_column: str
    completion_column: str = ""


@dataclass
class AirScriptSyncSummary:
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    not_in_sheet: list[str] = field(default_factory=list)
    duplicate_rows: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def message(self) -> str:
        parts = [
            f"更新 {len(self.updated)}",
            f"无变化 {len(self.unchanged)}",
            f"表中未找到 {len(self.not_in_sheet)}",
            f"重复行 {len(self.duplicate_rows)}",
            f"跳过 {len(self.skipped)}",
        ]
        if self.failures:
            parts.append(f"失败 {len(self.failures)}")
        return "，".join(parts)


def validate_webhook_url(webhook_url: str) -> str:
    value = webhook_url.strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname != "www.kdocs.cn":
        raise ConfigurationError("AirScript webhook 必须是 https://www.kdocs.cn 地址")
    if parsed.query or parsed.fragment or not re.fullmatch(
        r"/api/v3/ide/file/[^/]+/script/[^/]+/sync_task", parsed.path
    ):
        raise ConfigurationError("AirScript webhook 格式不正确，请从文档共享脚本菜单重新复制")
    return value


def _format_route(result: TrackingResult) -> str:
    return " ".join(
        part.strip() for part in (result.latest_time, result.latest_event) if part.strip()
    )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


class AirScriptClient:
    def __init__(
        self,
        config: AirScriptConfig,
        *,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (8.0, 90.0),
        retries: int = 2,
    ):
        parse_share_file_id(config.share_url)
        validate_webhook_url(config.webhook_url)
        if not config.api_token:
            raise ConfigurationError("AirScript 脚本令牌不能为空")
        if config.sheet_name.strip() != TARGET_SHEET_NAME:
            raise ConfigurationError("当前阶段只允许更新 US-FBA 子表")
        self.config = config
        self.session = session or requests.Session()
        self.timeout = timeout
        self.retries = max(0, retries)

    def _execute(
        self,
        action: str,
        items: list[dict[str, str]],
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        argv = {
            "action": action,
            "sheet_name": self.config.sheet_name,
            "items": items,
        }
        if arguments:
            argv.update(arguments)
        payload = {
            "Context": {
                "argv": argv
            }
        }
        headers = {
            "Content-Type": "application/json",
            "AirScript-Token": self.config.api_token,
        }
        response: requests.Response | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self.session.post(
                    self.config.webhook_url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                if attempt < self.retries:
                    time.sleep(0.8 * (attempt + 1))
                    continue
                raise NetworkError("连接 AirScript 服务失败，请检查网络后重试") from exc

            if response.status_code in (401, 403):
                raise AuthenticationError(
                    "AirScript 脚本令牌无效、已过期，或当前账号没有该表格的编辑权限"
                )
            if response.status_code == 429:
                if attempt < self.retries:
                    time.sleep(1.2 * (attempt + 1))
                    continue
                raise RateLimitError("AirScript 请求过于频繁，请稍后重试")
            if response.status_code >= 500:
                if attempt < self.retries:
                    time.sleep(1.2 * (attempt + 1))
                    continue
                raise ServerError(f"AirScript 服务暂时不可用（HTTP {response.status_code}）")
            if response.status_code >= 400:
                raise ResponseError(f"AirScript 请求失败（HTTP {response.status_code}）")
            break

        assert response is not None
        try:
            body = response.json()
        except ValueError as exc:
            raise ResponseError("AirScript 返回的内容不是有效 JSON") from exc
        if not isinstance(body, dict):
            raise ResponseError("AirScript 返回的数据结构无效")
        if body.get("error"):
            details = body.get("error_details")
            detail_message = details.get("msg") if isinstance(details, dict) else ""
            raise ResponseError(
                "AirScript 执行失败：" + str(detail_message or body.get("error"))
            )
        if body.get("status") not in (None, "finished"):
            raise ResponseError("AirScript 未正常执行完成：" + str(body.get("status")))
        data = body.get("data")
        if not isinstance(data, dict) or "result" not in data:
            raise ResponseError("AirScript 响应中缺少脚本执行结果")
        result: Any = data["result"]
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError as exc:
                raise ResponseError("AirScript 脚本返回值不是有效 JSON 对象") from exc
        if not isinstance(result, dict):
            raise ResponseError("AirScript 脚本返回值结构无效")
        if result.get("success") is not True:
            raise ResponseError(str(result.get("message") or "AirScript 脚本报告执行失败"))
        return result

    def validate(self) -> AirScriptBinding:
        result = self._execute("validate", [])
        columns = result.get("columns")
        if not isinstance(columns, dict):
            raise ResponseError("AirScript 验证结果缺少自动识别的列信息")
        fba_column = str(columns.get("fba") or "").strip()
        route_column = str(columns.get("route") or "").strip()
        completion_column = str(columns.get("completion") or "").strip()
        sheet_name = str(result.get("sheetName") or "").strip()
        if not sheet_name or not fba_column or not route_column or not completion_column:
            raise ResponseError(
                "AirScript 未能识别US-FBA的FBA列、是否完成列或路由列"
            )
        return AirScriptBinding(
            sheet_name, fba_column, route_column, completion_column
        )

    def list_pending_fbas(self, page_size: int = 500) -> list[str]:
        """分页读取“是否完成”不是“是”的FBA，总量不设业务上限。"""
        page_size = max(1, min(500, page_size))
        offset = 0
        values: list[str] = []
        seen: set[str] = set()
        for _page in range(100):
            result = self._execute(
                "list_pending",
                [],
                {"offset": offset, "limit": page_size},
            )
            page = result.get("fbas")
            if not isinstance(page, list):
                raise ResponseError("AirScript待读取结果缺少FBA列表")
            for item in page:
                fba = str(item).strip().upper()
                if re.fullmatch(r"FBA[A-Z0-9-]{5,}", fba) and fba not in seen:
                    seen.add(fba)
                    values.append(fba)
            if result.get("hasMore") is not True:
                return values
            next_offset = result.get("nextOffset")
            try:
                next_offset = int(next_offset)
            except (TypeError, ValueError) as exc:
                raise ResponseError("AirScript分页结果缺少有效偏移量") from exc
            if next_offset <= offset:
                raise ResponseError("AirScript分页偏移量没有前进，已停止读取")
            offset = next_offset
        raise ResponseError("AirScript待读取分页超过安全上限，请检查表格使用区域")

    def sync_tracking_results(
        self, results: list[TrackingResult]
    ) -> AirScriptSyncSummary:
        summary = AirScriptSyncSummary()
        items: list[dict[str, str]] = []
        for result in results:
            route = _format_route(result)
            if result.status != QueryStatus.SUCCESS or not route:
                summary.skipped.append(result.fba)
                continue
            items.append({"fba": result.fba, "route": route})
        if not items:
            return summary
        # 用户总量不限。每次 webhook 调用维持50条安全上限，自动分批并汇总。
        for offset in range(0, len(items), AIRSCRIPT_WRITE_BATCH_SIZE):
            batch = items[offset : offset + AIRSCRIPT_WRITE_BATCH_SIZE]
            try:
                # 继续使用既有sync动作，已安装的正式脚本无需因客户端分批升级而替换。
                result = self._execute("sync", batch)
            except (NetworkError, AuthenticationError, RateLimitError, ServerError, ResponseError) as exc:
                batch_number = offset // AIRSCRIPT_WRITE_BATCH_SIZE + 1
                total_batches = (
                    len(items) + AIRSCRIPT_WRITE_BATCH_SIZE - 1
                ) // AIRSCRIPT_WRITE_BATCH_SIZE
                raise type(exc)(
                    f"AirScript第 {batch_number}/{total_batches} 批回填失败；"
                    f"此前已处理 {offset} 条。可重新查询安全补写：{exc.user_message}"
                ) from exc
            summary.updated.extend(_string_list(result.get("updated")))
            summary.unchanged.extend(_string_list(result.get("unchanged")))
            summary.not_in_sheet.extend(_string_list(result.get("notInSheet")))
            summary.duplicate_rows.extend(_string_list(result.get("duplicateRows")))
            summary.failures.extend(_string_list(result.get("failures")))
        return summary

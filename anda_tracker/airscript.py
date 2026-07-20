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
from .wps import parse_share_file_id


TARGET_SHEET_NAME = "US-FBA"
MAX_AIRSCRIPT_ITEMS = 50


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

    def _execute(self, action: str, items: list[dict[str, str]]) -> dict[str, Any]:
        payload = {
            "Context": {
                "argv": {
                    "action": action,
                    "sheet_name": self.config.sheet_name,
                    "items": items,
                }
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
        except (ValueError, json.JSONDecodeError) as exc:
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
        sheet_name = str(result.get("sheetName") or "").strip()
        if not sheet_name or not fba_column or not route_column:
            raise ResponseError("AirScript 未能识别 US-FBA 的 FBA列或路由列")
        return AirScriptBinding(sheet_name, fba_column, route_column)

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
        if len(items) > MAX_AIRSCRIPT_ITEMS:
            raise ConfigurationError(
                f"单次 AirScript 回填不能超过 {MAX_AIRSCRIPT_ITEMS} 个 FBA"
            )
        result = self._execute("sync", items)
        summary.updated = _string_list(result.get("updated"))
        summary.unchanged = _string_list(result.get("unchanged"))
        summary.not_in_sheet = _string_list(result.get("notInSheet"))
        summary.duplicate_rows = _string_list(result.get("duplicateRows"))
        summary.failures = _string_list(result.get("failures"))
        return summary

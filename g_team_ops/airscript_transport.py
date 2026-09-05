from __future__ import annotations

import json
import time
from typing import Any

import requests

from .errors import (
    AuthenticationError,
    NetworkError,
    RateLimitError,
    ResponseError,
    ServerError,
)


def execute_airscript_request(
    *,
    session: requests.Session,
    webhook_url: str,
    api_token: str,
    argv: dict[str, Any],
    timeout: tuple[float, float],
    retries: int,
    service_name: str,
    required_schema_version: int,
    upgrade_message: str,
) -> dict[str, Any]:
    """执行一次AirScript调用，并统一网络重试、错误分类和响应校验。"""
    payload = {"Context": {"argv": argv}}
    headers = {
        "Content-Type": "application/json",
        "AirScript-Token": api_token,
    }
    retry_count = max(0, retries)
    response: requests.Response | None = None
    for attempt in range(retry_count + 1):
        try:
            response = session.post(
                webhook_url,
                headers=headers,
                json=payload,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            if attempt < retry_count:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise NetworkError(
                f"连接{service_name}服务失败，请检查网络后重试"
            ) from exc

        if response.status_code in (401, 403):
            raise AuthenticationError(
                f"{service_name}脚本令牌无效、已过期，或当前账号没有表格编辑权限"
            )
        if response.status_code == 429:
            if attempt < retry_count:
                time.sleep(1.2 * (attempt + 1))
                continue
            raise RateLimitError(f"{service_name}请求过于频繁，请稍后重试")
        if response.status_code >= 500:
            if attempt < retry_count:
                time.sleep(1.2 * (attempt + 1))
                continue
            raise ServerError(
                f"{service_name}服务暂时不可用（HTTP {response.status_code}）"
            )
        if response.status_code >= 400:
            raise ResponseError(
                f"{service_name}请求失败（HTTP {response.status_code}）"
            )
        break

    if response is None:  # pragma: no cover - 循环至少执行一次，仅作类型与防御保护。
        raise NetworkError(f"连接{service_name}服务失败，请检查网络后重试")
    try:
        body = response.json()
    except ValueError as exc:
        raise ResponseError(f"{service_name}返回的内容不是有效JSON") from exc
    if not isinstance(body, dict):
        raise ResponseError(f"{service_name}返回的数据结构无效")
    if body.get("error"):
        details = body.get("error_details")
        detail_message = details.get("msg") if isinstance(details, dict) else ""
        raise ResponseError(
            f"{service_name}执行失败：{detail_message or body.get('error')}"
        )
    if body.get("status") not in (None, "finished"):
        raise ResponseError(
            f"{service_name}未正常执行完成：{body.get('status')}"
        )
    data = body.get("data")
    if not isinstance(data, dict) or "result" not in data:
        raise ResponseError(f"{service_name}响应中缺少脚本执行结果")
    result: Any = data["result"]
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError as exc:
            raise ResponseError(
                f"{service_name}脚本返回值不是有效JSON对象"
            ) from exc
    if not isinstance(result, dict):
        raise ResponseError(f"{service_name}脚本返回值结构无效")
    if result.get("success") is not True:
        raise ResponseError(
            str(result.get("message") or f"{service_name}报告执行失败")
        )
    try:
        version = int(result.get("schemaVersion") or 0)
    except (TypeError, ValueError):
        version = 0
    if version < required_schema_version:
        raise ResponseError(upgrade_message)
    return result

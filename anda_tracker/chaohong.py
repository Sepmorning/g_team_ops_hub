from __future__ import annotations

import json
import time
from typing import Any, Callable
from urllib.parse import quote

import requests

from .errors import CarrierError, NetworkError, RateLimitError, ResponseError, ServerError
from .models import QueryStatus, TrackingResult


CH_BATCH_URL = "http://8.210.173.142:3000/api/traces/batch/"


class ChaoHongClient:
    """调用超鸿官网页面自身使用的批量查询接口。"""

    def __init__(
        self,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (5.0, 20.0),
        retries: int = 2,
        backoff_seconds: float = 0.5,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.session = session or requests.Session()
        self.timeout = timeout
        self.retries = max(0, retries)
        self.backoff_seconds = max(0.0, backoff_seconds)
        self.sleeper = sleeper

    def query_batch(self, fbas: list[str]) -> list[dict[str, Any]]:
        encoded = quote(json.dumps(fbas, ensure_ascii=False, separators=(",", ":")), safe="")
        url = CH_BATCH_URL + encoded
        last_error: CarrierError | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self.session.get(
                    url,
                    headers={
                        "Accept": "application/json, text/javascript, */*; q=0.01",
                        "Referer": "http://8.210.173.142:8080/track.html",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                    },
                    timeout=self.timeout,
                )
                if response.status_code == 429:
                    raise RateLimitError("超鸿接口请求过于频繁，请稍后再试")
                if response.status_code >= 500:
                    raise ServerError(f"超鸿服务暂时不可用（HTTP {response.status_code}）")
                if response.status_code >= 400:
                    raise ResponseError(f"超鸿接口返回异常（HTTP {response.status_code}）")
                try:
                    data = response.json()
                except ValueError as exc:
                    raise ResponseError("超鸿接口返回了无法解析的数据") from exc
                if not isinstance(data, dict):
                    raise ResponseError("超鸿接口响应格式不符合预期")
                if data.get("code") == 101:
                    return []
                if data.get("code") != 0:
                    message = str(data.get("message") or "查询请求被超鸿服务拒绝")
                    raise ResponseError(f"超鸿查询失败：{message[:160]}")
                records = data.get("data")
                if not isinstance(records, list):
                    raise ResponseError("超鸿查询响应中缺少物流列表")
                return records
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = NetworkError("连接超鸿服务失败或请求超时，请检查网络")
                last_error.__cause__ = exc
            except CarrierError as exc:
                last_error = exc
            if last_error and last_error.retryable and attempt < self.retries:
                self.sleeper(self.backoff_seconds * (2**attempt))
                continue
            assert last_error is not None
            raise last_error
        raise NetworkError("超鸿请求未完成")


class ChaoHongQueryService:
    def __init__(self, client: ChaoHongClient):
        self.client = client

    @staticmethod
    def _record_keys(record: dict[str, Any]) -> set[str]:
        keys: set[str] = set()
        for field in ("trace_no", "tag_no", "no", "fbaCode", "reference_no"):
            value = str(record.get(field) or "").strip().upper()
            if value:
                keys.add(value)
        return keys

    def query_many(self, fbas: list[str]) -> list[TrackingResult]:
        try:
            records = self.client.query_batch(fbas)
        except CarrierError as exc:
            return [
                TrackingResult(
                    fba=fba,
                    status=QueryStatus.FAILED,
                    carrier="超鸿",
                    error_category=exc.category,
                    error_message=exc.user_message,
                )
                for fba in fbas
            ]

        found: dict[str, dict[str, Any]] = {}
        requested = set(fbas)
        for record in records:
            if not isinstance(record, dict):
                continue
            for key in self._record_keys(record) & requested:
                found[key] = record

        results: list[TrackingResult] = []
        for fba in fbas:
            record = found.get(fba)
            if record is None:
                results.append(TrackingResult(fba=fba, status=QueryStatus.NOT_FOUND, carrier="超鸿"))
                continue
            place = str(record.get("place") or "").strip()
            detail = str(record.get("detail") or record.get("status_name") or "").strip()
            latest_event = " - ".join(part for part in (place, detail) if part)
            results.append(
                TrackingResult(
                    fba=fba,
                    status=QueryStatus.SUCCESS,
                    carrier="超鸿",
                    latest_time=str(record.get("happened_at") or record.get("trace_at") or ""),
                    latest_event=latest_event,
                )
            )
        return results


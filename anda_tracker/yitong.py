from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable

import requests

from .errors import (
    AuthenticationError,
    CarrierError,
    NetworkError,
    RateLimitError,
    ResponseError,
    ServerError,
)
from .models import QueryStatus, TrackingDetails, TrackingResult
from .tracking_details import normalize_tracking_details


API_BASE = "http://client.api.etton-log.com"
SITE_ORIGIN = "http://c.etton-log.com"
COMPANY_URL = f"{API_BASE}/api/orm/apiClientUserService/clientWebSiteCompany"
CAPTCHA_URL = f"{API_BASE}/api/file/verifyCode"
LOGIN_URL = f"{API_BASE}/api/orm/apiClientUserService/login"
USER_INFO_URL = f"{API_BASE}/api/orm/apiClientUserService/getUserInfo"
ORDER_LIST_URL = f"{API_BASE}/api/orm/apiClientOrderService/getClientOrderList"
ROUTER_ACTIVITIES_URL = (
    f"{API_BASE}/api/orm/apiWorkorderRouterService/getWorkorderRouterActivities"
)


@dataclass(frozen=True)
class CaptchaChallenge:
    identity: str
    verification_enabled: Any
    image_bytes: bytes
    company_name: str = ""
    logo: str = ""


class YiTongClient:
    """调用易通官网自身使用的 API；验证码只交由用户人工填写。"""

    def __init__(
        self,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (5.0, 20.0),
        retries: int = 2,
        backoff_seconds: float = 0.6,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.session = session or requests.Session()
        self.timeout = timeout
        self.retries = max(0, retries)
        self.backoff_seconds = max(0.0, backoff_seconds)
        self.sleeper = sleeper
        self.token: str | None = None

    @staticmethod
    def _headers(token: str | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": SITE_ORIGIN,
            "Referer": f"{SITE_ORIGIN}/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _json_request(
        self,
        method: str,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        token: str | None = None,
        retries: bool = True,
    ) -> dict[str, Any]:
        last_error: CarrierError | None = None
        attempts = self.retries + 1 if retries else 1
        for attempt in range(attempts):
            try:
                response = self.session.request(
                    method,
                    url,
                    json=payload,
                    headers=self._headers(token),
                    timeout=self.timeout,
                )
                if response.status_code in (401, 403):
                    raise AuthenticationError("易通登录状态无效或已过期")
                if response.status_code == 429:
                    raise RateLimitError("易通接口请求过于频繁，请稍后再试")
                if response.status_code >= 500:
                    raise ServerError(f"易通服务暂时不可用（HTTP {response.status_code}）")
                if response.status_code >= 400:
                    raise ResponseError(f"易通接口返回异常（HTTP {response.status_code}）")
                try:
                    data = response.json()
                except ValueError as exc:
                    raise ResponseError("易通接口返回了无法解析的数据") from exc
                if not isinstance(data, dict):
                    raise ResponseError("易通接口响应格式不符合预期")
                return data
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = NetworkError("连接易通服务失败或请求超时，请检查网络")
                last_error.__cause__ = exc
            except CarrierError as exc:
                last_error = exc
            if last_error.retryable and attempt + 1 < attempts:
                self.sleeper(self.backoff_seconds * (2**attempt))
                continue
            raise last_error
        raise NetworkError("易通请求未完成")

    @staticmethod
    def _message(data: dict[str, Any], fallback: str) -> str:
        return str(data.get("message") or data.get("msg") or fallback)[:160]

    @staticmethod
    def _successful(data: dict[str, Any]) -> bool:
        return data.get("code") in (2000, "2000", 0, "0")

    def fetch_captcha(self) -> CaptchaChallenge:
        company = self._json_request(
            "POST", COMPANY_URL, payload={"webSite": SITE_ORIGIN}
        )
        if not self._successful(company) or not isinstance(company.get("model"), dict):
            raise ResponseError(f"无法获取易通登录配置：{self._message(company, '响应异常')}")
        model = company["model"]
        identity = str(model.get("identity") or "").strip()
        if not identity:
            raise ResponseError("易通登录配置中缺少验证码标识")
        try:
            response = self.session.get(
                CAPTCHA_URL,
                params={"timestamp": int(time.time() * 1000), "identity": identity},
                headers=self._headers(),
                timeout=self.timeout,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise NetworkError("获取易通验证码失败，请检查网络") from exc
        if response.status_code >= 500:
            raise ServerError(f"易通验证码服务暂时不可用（HTTP {response.status_code}）")
        if response.status_code >= 400:
            raise ResponseError(f"获取易通验证码失败（HTTP {response.status_code}）")
        if not response.content or "image" not in response.headers.get("Content-Type", "").lower():
            raise ResponseError("易通验证码响应不是有效图片")
        return CaptchaChallenge(
            identity=identity,
            verification_enabled=model.get("verifiCode"),
            image_bytes=response.content,
            company_name=str(model.get("companyName") or ""),
            logo=str(model.get("logo") or ""),
        )

    def login(
        self,
        username: str,
        password: str,
        verification_code: str,
        challenge: CaptchaChallenge,
    ) -> str:
        payload = {
            "username": username.strip(),
            "passwd": password,
            "verificationCode": verification_code.strip(),
            "identity": challenge.identity,
            "verifiCode": challenge.verification_enabled,
            "companyName": challenge.company_name,
            "logo": challenge.logo,
            "checked": True,
        }
        data = self._json_request("POST", LOGIN_URL, payload=payload, retries=False)
        if not self._successful(data):
            raise AuthenticationError(f"易通登录失败：{self._message(data, '账号、密码或验证码错误')}")
        token = data.get("token")
        if not isinstance(token, str) or not token.strip():
            raise ResponseError("易通登录成功响应中缺少会话令牌")
        self.token = token.strip()
        return self.token

    def validate_token(self) -> None:
        if not self.token:
            raise AuthenticationError("尚未登录易通")
        data = self._json_request("GET", USER_INFO_URL, token=self.token)
        if not self._successful(data):
            self.token = None
            raise AuthenticationError(f"易通登录状态已失效：{self._message(data, '请重新登录')}")

    def query_batch(self, fbas: list[str]) -> list[dict[str, Any]]:
        if not self.token:
            raise AuthenticationError("尚未登录易通，请先完成验证码登录")
        payload = {
            # 易通官网订单页要求起始日期和订单状态字段；缺少时接口只返回“系统错误”。
            "waybillStatus": 0,
            "orderStatus": 0,
            "waybillStatusList": "",
            "queryNoType": "fbaNo",
            "queryNos": "\n".join(fbas),
            "queryOrderTimeType": "waybillDate",
            "queryTime1": (date.today() - timedelta(days=365)).isoformat(),
        }
        data = self._json_request("POST", ORDER_LIST_URL, payload=payload, token=self.token)
        if not self._successful(data):
            message = self._message(data, "查询请求被易通服务拒绝")
            if any(word in message.lower() for word in ("token", "登录", "认证", "过期", "unauthorized")):
                self.token = None
                raise AuthenticationError("易通登录状态已失效，请重新登录")
            raise ResponseError(f"易通查询失败：{message}")
        records = data.get("list")
        if records is None and isinstance(data.get("model"), dict):
            records = data["model"].get("list")
        if not isinstance(records, list):
            raise ResponseError("易通查询响应中缺少订单列表")
        return records

    def get_router_activities(self, order_ids: list[str]) -> dict[str, Any]:
        if not self.token:
            raise AuthenticationError("尚未登录易通，请先完成验证码登录")
        data = self._json_request(
            "POST",
            ROUTER_ACTIVITIES_URL,
            payload={"orderIds": order_ids},
            token=self.token,
        )
        if not self._successful(data):
            message = self._message(data, "完整路由查询被易通服务拒绝")
            if any(
                word in message.lower()
                for word in ("token", "登录", "认证", "过期", "unauthorized")
            ):
                self.token = None
                raise AuthenticationError("易通登录状态已失效，请重新登录")
            raise ResponseError(f"易通完整路由查询失败：{message}")
        values = data.get("list")
        if not isinstance(values, list):
            raise ResponseError("易通完整路由响应中缺少时间轴")
        return data


class YiTongQueryService:
    carrier = "易通"

    def __init__(
        self,
        client: YiTongClient,
        batch_size: int = 20,
        request_interval: float = 1.5,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.client = client
        self.batch_size = max(1, batch_size)
        self.request_interval = max(0.0, request_interval)
        self.sleeper = sleeper
        self.last_records: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _fba_keys(record: dict[str, Any]) -> set[str]:
        values: list[Any] = []
        for field in ("fbaNo", "fbaCode", "fbaNoList"):
            value = record.get(field)
            if isinstance(value, list):
                values.extend(value)
            elif isinstance(value, str) and value.strip().startswith("["):
                try:
                    parsed = json.loads(value)
                    values.extend(parsed if isinstance(parsed, list) else [value])
                except ValueError:
                    values.append(value)
            elif value is not None:
                values.append(value)
        keys: set[str] = set()
        for value in values:
            for part in re.split(r"[\s,，、;；]+", str(value)):
                normalized = part.strip().upper()
                if normalized.startswith("FBA"):
                    keys.add(normalized)
        return keys

    def query_many(self, fbas: list[str]) -> list[TrackingResult]:
        results: dict[str, TrackingResult] = {}
        for offset in range(0, len(fbas), self.batch_size):
            batch = fbas[offset : offset + self.batch_size]
            try:
                records = self.client.query_batch(batch)
                found: dict[str, dict[str, Any]] = {}
                requested = set(batch)
                for record in records:
                    if isinstance(record, dict):
                        for key in self._fba_keys(record) & requested:
                            found[key] = record
                for fba in batch:
                    record = found.get(fba)
                    if record is None:
                        results[fba] = TrackingResult(fba, QueryStatus.NOT_FOUND, carrier="易通")
                    else:
                        self.last_records[fba] = record
                        results[fba] = TrackingResult(
                            fba=fba,
                            status=QueryStatus.SUCCESS,
                            carrier="易通",
                            latest_time=str(record.get("routerTime") or record.get("latestRouteTime") or ""),
                            latest_event=str(
                                record.get("routerInformation")
                                or record.get("latestRoute")
                                or record.get("waybillStatusName")
                                or ""
                            ),
                        )
            except CarrierError as exc:
                for fba in batch:
                    results[fba] = TrackingResult(
                        fba=fba,
                        status=QueryStatus.FAILED,
                        carrier="易通",
                        error_category=exc.category,
                        error_message=exc.user_message,
                    )
            if offset + self.batch_size < len(fbas) and self.request_interval:
                self.sleeper(self.request_interval)
        return [results[fba] for fba in fbas]

    def fetch_tracking_details(self, fba: str) -> TrackingDetails:
        record = self.last_records.get(fba)
        if record is None:
            records = self.client.query_batch([fba])
            record = next(
                (
                    item
                    for item in records
                    if isinstance(item, dict) and fba in self._fba_keys(item)
                ),
                None,
            )
            if record is None:
                raise ValueError("易通订单详情不存在")
            self.last_records[fba] = record
        order_id = str(record.get("orderId") or "").strip()
        if not order_id:
            raise ValueError("易通订单缺少路由详情编号")
        detail = self.client.get_router_activities([order_id])
        raw_events = [
            item for item in detail.get("list", []) if isinstance(item, dict)
        ]
        return normalize_tracking_details(
            fba=fba,
            carrier="易通",
            raw_events=raw_events,
            carrier_order_no=str(record.get("waybillNo") or order_id),
            structured={
                "pickup_time": record.get("inboundTime"),
            },
        )

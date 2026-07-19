from __future__ import annotations

import base64
import secrets
import string
import time
from typing import Any, Callable

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from .errors import (
    CarrierError,
    AuthenticationError,
    NetworkError,
    RateLimitError,
    ResponseError,
    ServerError,
)


LOGIN_URL = "https://fms.yunwuyun.com/api/csm/unicsmuserinfo/login"
QUERY_URL = "https://fms.yunwuyun.com/api/oms/fbxOrder/queryFbxOrderList"
ORIGIN = "https://oms.yunwuyun.com"


def encrypt_login_password(plaintext: str) -> dict[str, str]:
    """按现有网页接口协议生成一次性 AES-CBC 登录字段。"""
    alphabet = string.ascii_letters + string.digits
    key = "".join(secrets.choice(alphabet) for _ in range(16)).encode()
    iv = "".join(secrets.choice(alphabet) for _ in range(16)).encode()
    encrypted = AES.new(key, AES.MODE_CBC, iv).encrypt(pad(plaintext.encode(), AES.block_size))
    return {
        "password": base64.b64encode(encrypted).decode(),
        "key": key.decode(),
        "iv": iv.decode(),
    }


class AndaClient:
    def __init__(
        self,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (8.0, 25.0),
        retries: int = 2,
        backoff_seconds: float = 1.0,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.session = session or requests.Session()
        self.timeout = timeout
        self.retries = max(0, retries)
        self.backoff_seconds = max(0.0, backoff_seconds)
        self.sleeper = sleeper
        self.token: str | None = None

    def _post(self, url: str, *, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
        last_error: CarrierError | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self.session.post(url, json=payload, headers=headers, timeout=self.timeout)
                if response.status_code in (401, 403):
                    raise AuthenticationError("登录凭据无效或会话已过期")
                if response.status_code == 429:
                    raise RateLimitError("安达接口请求过于频繁，请稍后再试")
                if 500 <= response.status_code:
                    raise ServerError(f"安达服务暂时不可用（HTTP {response.status_code}）")
                if response.status_code >= 400:
                    raise ResponseError(f"安达接口返回异常（HTTP {response.status_code}）")
                try:
                    data = response.json()
                except ValueError as exc:
                    raise ResponseError("安达接口返回了无法解析的数据") from exc
                if not isinstance(data, dict):
                    raise ResponseError("安达接口响应格式不符合预期")
                return data
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = NetworkError("连接安达服务失败或请求超时，请检查网络")
                last_error.__cause__ = exc
            except CarrierError as exc:
                last_error = exc

            if last_error and last_error.retryable and attempt < self.retries:
                self.sleeper(self.backoff_seconds * (2**attempt))
                continue
            assert last_error is not None
            raise last_error
        raise NetworkError("请求未完成")

    def login(self, username: str, password: str) -> None:
        encrypted = encrypt_login_password(password)
        payload = {
            "username": username,
            "password": encrypted["password"],
            "tenantCode": "DNAD",
            "userGroup": "10",
            "captcha": None,
            "checkKey": "",
            "clicendId": None,
            "fingerprint": "b3c64b2701a5068d973d1d058ab3ef8e",
            "iv": encrypted["iv"],
            "key": encrypted["key"],
            "loginWay": None,
        }
        data = self._post(
            LOGIN_URL,
            headers={
                "Content-Type": "application/json;charset=UTF-8",
                "Origin": ORIGIN,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            },
            payload=payload,
        )
        result = data.get("result")
        if not data.get("success") or not isinstance(result, dict) or result.get("code") != "SUCCESS":
            detail = result.get("message") if isinstance(result, dict) else None
            message = str(data.get("message") or detail or "账号或密码验证失败")
            raise AuthenticationError(f"安达认证失败：{message[:160]}")
        token = result.get("token")
        if not isinstance(token, str) or not token:
            raise ResponseError("登录成功响应中缺少会话令牌")
        self.token = token

    def query_batch(self, fbas: list[str]) -> list[dict[str, Any]]:
        if not self.token:
            raise AuthenticationError("尚未登录安达")
        payload = {
            "currentPage": 1,
            "pageSize": max(20, len(fbas)),
            "total": None,
            "conditionDtos": [
                {"field": "oms_plat_combination", "operator": "multi_eq_sort", "value": "\n".join(fbas)},
                {"field": "clientReturnStatus", "operator": "not_equal", "value": 20},
            ],
        }
        data = self._post(
            QUERY_URL,
            headers={
                "Token": self.token,
                "Content-Type": "application/json;charset=UTF-8",
                "Origin": ORIGIN,
                "Accept": "application/json, text/plain, */*",
            },
            payload=payload,
        )
        if not data.get("success"):
            message = str(data.get("message") or "查询请求被安达服务拒绝")
            if any(word in message.lower() for word in ("token", "登录", "认证", "过期")):
                self.token = None
                raise AuthenticationError("安达登录状态已失效，请重新登录")
            raise ResponseError(f"安达查询失败：{message[:160]}")
        result = data.get("result")
        records = result.get("records") if isinstance(result, dict) else None
        if not isinstance(records, list):
            raise ResponseError("安达查询响应中缺少订单列表")
        return records

from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from .errors import AuthenticationError, ConfigurationError, NetworkError, ResponseError
from .models import QueryStatus, TrackingResult


OAUTH_AUTHORIZE_URL = "https://openapi.wps.cn/oauth2/auth"
OAUTH_TOKEN_URL = "https://openapi.wps.cn/oauth2/token"
API_BASE = "https://openapi.wps.cn/v7"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8765/wps/callback"
WPS_SCOPES = ("kso.sheets.readwrite", "kso.file.read")
TARGET_SHEET_NAME = "US-FBA"
ROUTE_HEADER = "货代最新路由信息"
WPS_WRITE_BATCH_SIZE = 20
REQUIRED_US_HEADERS = (
    "是否完成",
    "发货",
    "开船（机）时间",
    "到港",
    "提取派送",
    "实际接收日期",
    "预计接收日期",
    ROUTE_HEADER,
)
HEADER_SEARCH_TERMS = (
    *REQUIRED_US_HEADERS,
    "完成",
    "发货",
    "开船",
    "到港",
    "提取",
    "实际接收",
    "预计接收",
    "货代最新路由",
)
FBA_HEADER_ALIASES = {
    "FBA",
    "FBA号",
    "FBA编号",
    "FBA单号",
    "FBANO",
    "FBANUMBER",
}


@dataclass(frozen=True)
class WpsCredentials:
    app_id: str
    app_secret: str
    share_url: str
    redirect_uri: str = DEFAULT_REDIRECT_URI
    fba_col: int | None = None
    route_col: int | None = None


@dataclass(frozen=True)
class WpsTokens:
    access_token: str
    refresh_token: str
    expires_at: float


@dataclass(frozen=True)
class WpsSheetBinding:
    file_id: str
    worksheet_id: int
    worksheet_name: str
    max_row: int
    max_col: int
    fba_col: int | None = None
    route_col: int | None = None


@dataclass
class WpsSyncSummary:
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    not_in_sheet: list[str] = field(default_factory=list)
    duplicate_rows: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def message(self) -> str:
        parts = [f"更新 {len(self.updated)}"]
        parts.append(f"无变化 {len(self.unchanged)}")
        parts.append(f"表中未找到 {len(self.not_in_sheet)}")
        parts.append(f"重复行 {len(self.duplicate_rows)}")
        parts.append(f"跳过 {len(self.skipped)}")
        if self.failures:
            parts.append(f"失败 {len(self.failures)}")
        return "，".join(parts)


def parse_share_file_id(share_url: str) -> str:
    value = share_url.strip()
    match = re.fullmatch(r"https://www\.kdocs\.cn/l/([A-Za-z0-9_-]+)(?:[/?#].*)?", value)
    if not match:
        raise ConfigurationError("共享表链接格式不正确，应为 https://www.kdocs.cn/l/…")
    return match.group(1)


def normalize_header(value: str) -> str:
    return re.sub(r"[\s._-]+", "", value.strip()).upper()


def excel_column_to_index(value: str) -> int:
    column = value.strip().upper()
    if not re.fullmatch(r"[A-Z]{1,3}", column):
        raise ConfigurationError("WPS 列必须填写 Excel 列字母，例如 E 或 Y")
    index = 0
    for char in column:
        index = index * 26 + (ord(char) - ord("A") + 1)
    index -= 1
    if index > 16383:
        raise ConfigurationError("WPS 列字母超出 Excel 最大列 XFD")
    return index


def index_to_excel_column(index: int) -> str:
    if index < 0 or index > 16383:
        raise ConfigurationError("WPS 列序号超出有效范围")
    value = index + 1
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def locate_headers(cells: list[dict[str, Any]]) -> tuple[dict[str, int], int]:
    headers: dict[str, int] = {}
    normalized_to_original: dict[str, str] = {}
    for cell in cells:
        if int(cell.get("row_from", -1)) != 0:
            continue
        text = str(cell.get("cell_text") or cell.get("original_cell_value") or "").strip()
        if not text:
            continue
        col = int(cell.get("col_from", -1))
        if col < 0:
            continue
        headers[text] = col
        normalized_to_original[normalize_header(text)] = text

    missing = [name for name in REQUIRED_US_HEADERS if normalize_header(name) not in normalized_to_original]
    if missing:
        raise ResponseError("US-FBA 表缺少固定表头：" + "、".join(missing))

    fba_col: int | None = None
    aliases = {normalize_header(item) for item in FBA_HEADER_ALIASES}
    for text, col in headers.items():
        if normalize_header(text) in aliases:
            fba_col = col
            break
    if fba_col is None:
        candidates = [(text, col) for text, col in headers.items() if "FBA" in normalize_header(text)]
        if len(candidates) == 1:
            fba_col = candidates[0][1]
    if fba_col is None:
        raise ResponseError("US-FBA 表第一行未找到明确的 FBA 号列")

    canonical = {
        required: headers[normalized_to_original[normalize_header(required)]]
        for required in REQUIRED_US_HEADERS
    }
    return canonical, fba_col


def format_route(result: TrackingResult) -> str:
    return " ".join(part.strip() for part in (result.latest_time, result.latest_event) if part.strip())


class LocalOAuthCallback:
    def __init__(self, redirect_uri: str, timeout_seconds: int = 180):
        parsed = urlparse(redirect_uri)
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost"):
            raise ConfigurationError("桌面版回调地址必须是本机 http://127.0.0.1 地址")
        self.host = parsed.hostname
        self.port = parsed.port or 80
        self.path = parsed.path or "/"
        self.timeout_seconds = timeout_seconds
        self.result: dict[str, str] = {}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed_request = urlparse(self.path)
                if parsed_request.path != owner.path:
                    self.send_response(404)
                    self.end_headers()
                    return
                query = parse_qs(parsed_request.query)
                owner.result = {key: values[0] for key, values in query.items() if values}
                body = "<meta charset='utf-8'><h2>WPS 授权已返回，可以关闭此页面并回到物流程序。</h2>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body.encode("utf-8"))))
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        try:
            self.server = HTTPServer((self.host, self.port), Handler)
        except OSError as exc:
            raise ConfigurationError(f"本机回调端口 {self.port} 被占用，请关闭占用程序后重试") from exc
        self.server.timeout = 1

    def wait(self, expected_state: str) -> str:
        deadline = time.monotonic() + self.timeout_seconds
        try:
            while time.monotonic() < deadline and not self.result:
                self.server.handle_request()
        finally:
            self.server.server_close()
        if not self.result:
            raise AuthenticationError("等待 WPS 授权超时，请重新连接")
        if self.result.get("state") != expected_state:
            raise AuthenticationError("WPS 授权 state 校验失败，请重新连接")
        if self.result.get("error"):
            raise AuthenticationError("WPS 用户拒绝授权或授权失败")
        code = self.result.get("code", "")
        if not code:
            raise AuthenticationError("WPS 授权回调中缺少一次性授权码")
        return code


class WpsClient:
    def __init__(
        self,
        credentials: WpsCredentials,
        tokens: WpsTokens | None = None,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (8.0, 30.0),
        token_callback: Callable[[WpsTokens], None] | None = None,
    ):
        parse_share_file_id(credentials.share_url)
        if not credentials.app_id.strip() or not credentials.app_secret:
            raise ConfigurationError("WPS APPID 和 APPKEY 不能为空")
        self.credentials = credentials
        self.tokens = tokens
        self.session = session or requests.Session()
        self.timeout = timeout
        self.token_callback = token_callback

    def authorization_url(self, state: str) -> str:
        return OAUTH_AUTHORIZE_URL + "?" + urlencode(
            {
                "response_type": "code",
                "client_id": self.credentials.app_id,
                "redirect_uri": self.credentials.redirect_uri,
                "scope": ",".join(WPS_SCOPES),
                "state": state,
            }
        )

    def _parse_token_response(self, response: requests.Response) -> WpsTokens:
        if response.status_code >= 400:
            raise AuthenticationError(f"WPS 获取授权令牌失败（HTTP {response.status_code}）")
        try:
            data = response.json()
        except ValueError as exc:
            raise ResponseError("WPS 令牌接口返回了无法解析的数据") from exc
        access = str(data.get("access_token") or "")
        refresh = str(data.get("refresh_token") or "")
        if not access or not refresh:
            message = str(data.get("msg") or "响应中缺少访问令牌")
            raise AuthenticationError(f"WPS 授权失败：{message[:160]}")
        tokens = WpsTokens(
            access_token=access,
            refresh_token=refresh,
            expires_at=time.time() + max(60, int(data.get("expires_in") or 7200)),
        )
        self.tokens = tokens
        if self.token_callback:
            self.token_callback(tokens)
        return tokens

    def exchange_code(self, code: str) -> WpsTokens:
        try:
            response = self.session.post(
                OAUTH_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "client_id": self.credentials.app_id,
                    "client_secret": self.credentials.app_secret,
                    "code": code,
                    "redirect_uri": self.credentials.redirect_uri,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self.timeout,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise NetworkError("连接 WPS 授权服务失败，请检查网络") from exc
        return self._parse_token_response(response)

    def refresh_tokens(self) -> WpsTokens:
        if not self.tokens or not self.tokens.refresh_token:
            raise AuthenticationError("WPS 尚未授权，请重新连接")
        try:
            response = self.session.post(
                OAUTH_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": self.credentials.app_id,
                    "client_secret": self.credentials.app_secret,
                    "refresh_token": self.tokens.refresh_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self.timeout,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise NetworkError("刷新 WPS 授权失败，请检查网络") from exc
        return self._parse_token_response(response)

    def _ensure_access_token(self) -> str:
        if not self.tokens:
            raise AuthenticationError("WPS 尚未授权，请先连接")
        if self.tokens.expires_at <= time.time() + 60:
            self.refresh_tokens()
        assert self.tokens is not None
        return self.tokens.access_token

    def _api_request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        retry_auth: bool = True,
    ) -> dict[str, Any]:
        token = self._ensure_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Kso-Date": format_datetime(datetime.now(timezone.utc), usegmt=True),
        }
        try:
            response = self.session.request(
                method,
                API_BASE + path,
                json=payload,
                params=params,
                headers=headers,
                timeout=self.timeout,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise NetworkError("连接 WPS 表格服务失败或请求超时") from exc
        if response.status_code == 401 and retry_auth:
            self.refresh_tokens()
            return self._api_request(method, path, payload=payload, params=params, retry_auth=False)
        try:
            data = response.json()
        except ValueError as exc:
            if response.status_code in (401, 403):
                raise AuthenticationError(
                    f"WPS 无权访问该资源（HTTP {response.status_code}），请检查用户授权和文件编辑权限"
                ) from exc
            if response.status_code >= 500:
                raise ResponseError(f"WPS 服务暂时不可用（HTTP {response.status_code}）") from exc
            if response.status_code >= 400:
                raise ResponseError(f"WPS 接口返回异常（HTTP {response.status_code}）") from exc
            raise ResponseError("WPS 接口返回了无法解析的数据") from exc
        api_code = data.get("code") if isinstance(data, dict) else None
        api_message = (
            str(data.get("msg") or data.get("message") or "接口响应异常")[:180]
            if isinstance(data, dict)
            else "接口响应异常"
        )
        api_detail = f"WPS代码 {api_code}：{api_message}" if api_code is not None else api_message
        if response.status_code in (401, 403):
            raise AuthenticationError(
                f"WPS 无权访问该资源（HTTP {response.status_code}，{api_detail}）"
            )
        if response.status_code >= 500:
            raise ResponseError(
                f"WPS 服务暂时不可用（HTTP {response.status_code}，{api_detail}）"
            )
        if response.status_code >= 400:
            raise ResponseError(f"WPS 接口返回异常（HTTP {response.status_code}，{api_detail}）")
        if not isinstance(data, dict) or data.get("code") not in (0, "0", None):
            raise ResponseError(f"WPS 操作失败：{api_detail}")
        return data

    def validate_and_bind(self) -> WpsSheetBinding:
        file_id = parse_share_file_id(self.credentials.share_url)
        # 不先调用 /files/{file_id}/meta。部分 WPS 基础企业账号即使已经
        # 获得 sheets 用户权限，也会在文件元数据接口被 interface_company_doc
        # 套餐权限拦截；工作表接口本身会按当前用户和文件权限完成鉴权。
        data = self._api_request("GET", f"/sheets/{file_id}/worksheets")
        sheet_data = data.get("data") if isinstance(data.get("data"), dict) else {}
        sheets = sheet_data.get("sheets")
        if not isinstance(sheets, list):
            raise ResponseError("WPS 响应中缺少工作表列表")
        matches = [item for item in sheets if str(item.get("name") or "").strip().upper() == TARGET_SHEET_NAME]
        if len(matches) != 1:
            raise ResponseError(
                "未找到唯一的 US-FBA 子表" if not matches else "存在多个同名 US-FBA 子表"
            )
        sheet = matches[0]
        active_area = (
            sheet.get("active_area") if isinstance(sheet.get("active_area"), dict) else {}
        )
        # 工作表列表接口的 active_area 方向与 range_data 单元格坐标相反：
        # active_area.col_to 对应实际行号，active_area.row_to 对应实际列号。
        actual_max_row = max(
            1, int(active_area.get("col_to") or sheet.get("max_row") or 1)
        )
        actual_max_col = max(
            0, min(255, int(active_area.get("row_to") or sheet.get("max_col") or 0))
        )
        if self.credentials.fba_col is None or self.credentials.route_col is None:
            raise ConfigurationError("请填写 US-FBA 的 FBA号列和货代最新路由信息列")
        if self.credentials.fba_col == self.credentials.route_col:
            raise ConfigurationError("FBA号列和货代最新路由信息列不能相同")
        if self.credentials.fba_col > actual_max_col or self.credentials.route_col > actual_max_col:
            raise ConfigurationError(
                f"填写的列超出 US-FBA 当前使用范围（最后一列为 {index_to_excel_column(actual_max_col)}）"
            )
        binding = WpsSheetBinding(
            file_id=file_id,
            worksheet_id=int(sheet.get("sheet_id")),
            worksheet_name=str(sheet.get("name")),
            max_row=actual_max_row,
            max_col=actual_max_col,
            fba_col=self.credentials.fba_col,
            route_col=self.credentials.route_col,
        )
        return binding

    def _find_cells(
        self,
        binding: WpsSheetBinding,
        *,
        row_from: int,
        row_to: int,
        col_from: int,
        col_to: int,
        search: list[dict[str, Any]] | None = None,
        option_cols: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        if not search:
            raise ConfigurationError("WPS 查找选区必须提供至少一个搜索条件")
        page_number = 1
        page_size = 50
        output: list[dict[str, Any]] = []
        while True:
            payload = {
                "filter": {"search": search},
                "ignore_hidden_cell": False,
                "option_cols": option_cols or [],
                "page": {"page": page_number, "page_size": page_size},
                "range": {
                    "row_from": row_from,
                    "row_to": row_to,
                    "col_from": col_from,
                    "col_to": col_to,
                },
                "show_total": True,
            }
            data = self._api_request(
                "POST",
                f"/sheets/{binding.file_id}/worksheets/{binding.worksheet_id}/range_data/find",
                payload=payload,
            )
            result = data.get("data") if isinstance(data.get("data"), dict) else {}
            cells = result.get("range_data")
            merged = result.get("merge_range_data")
            page_output = list(cells) if isinstance(cells, list) else []
            if isinstance(merged, list):
                page_output.extend(merged)
            output.extend(cell for cell in page_output if isinstance(cell, dict))

            total_value = result.get("total")
            try:
                total = int(total_value)
            except (TypeError, ValueError):
                break
            if not page_output or page_number * page_size >= total:
                break
            page_number += 1
        return output

    def read_header_map(self, binding: WpsSheetBinding) -> tuple[dict[str, int], int]:
        # find 接口不接受空筛选。逐列用固定表头搜索；首次命中时接口会返回
        # 整个表头行，因此仍然可以动态定位所有列，且不依赖列顺序。
        for candidate_col in range(binding.max_col + 1):
            cells = self._find_cells(
                binding,
                row_from=0,
                row_to=0,
                col_from=0,
                col_to=binding.max_col,
                search=[{"col": candidate_col, "value": list(HEADER_SEARCH_TERMS)}],
                option_cols=list(range(binding.max_col + 1)),
            )
            header_cells = [cell for cell in cells if int(cell.get("row_from", -1)) == 0]
            if header_cells:
                return locate_headers(header_cells)
        raise ResponseError("US-FBA 表第一行未找到固定表头，请检查表头名称是否完整")

    def sync_tracking_results(
        self, binding: WpsSheetBinding, results: list[TrackingResult]
    ) -> WpsSyncSummary:
        summary = WpsSyncSummary()
        eligible = [
            item
            for item in results
            if item.status == QueryStatus.SUCCESS and bool(format_route(item))
        ]
        summary.skipped.extend(item.fba for item in results if item not in eligible)
        if not eligible:
            return summary

        if binding.fba_col is None or binding.route_col is None:
            raise ConfigurationError("WPS 列映射未配置，请重新填写并验证共享表")
        fba_col = binding.fba_col
        route_col = binding.route_col
        fbas = [item.fba for item in eligible]
        # 只在 FBA 列中查找。WPS 的 option_cols 是附加统计列，不是“返回
        # 同行其他单元格”；对部分传统 .xlsx，命中时同时传 option_cols 会让
        # WPS 后端返回 500410002 CoreExecutionFailed。
        fba_cells = self._find_cells(
            binding,
            # WPS 传统表格 find 会把选区首行按表头处理。必须把真实表头行
            # 一并放进选区，否则第一条数据行会被跳过并显示“表中未找到”。
            row_from=0,
            row_to=max(1, binding.max_row),
            col_from=fba_col,
            col_to=fba_col,
            search=[{"col": fba_col, "value": fbas}],
            option_cols=[],
        )
        row_by_fba: dict[str, list[int]] = {}
        for cell in fba_cells:
            row = int(cell.get("row_from", -1))
            col = int(cell.get("col_from", -1))
            if row < 1 or col != fba_col:
                continue
            fba = str(
                cell.get("cell_text") or cell.get("original_cell_value") or ""
            ).strip().upper()
            if fba:
                row_by_fba.setdefault(fba, []).append(row)

        candidates: list[tuple[TrackingResult, int, str]] = []
        for item in eligible:
            matches = row_by_fba.get(item.fba, [])
            if not matches:
                summary.not_in_sheet.append(item.fba)
                continue
            if len(matches) > 1:
                summary.duplicate_rows.append(item.fba)
                continue
            candidates.append((item, matches[0], format_route(item)))

        # 查找 Q 列中已经等于“新路由”的单元格，以判断是否真的发生变化。
        # find 接口不支持无筛选读取单格，因此按新值精确查找；这样仍然只需
        # 两类批量请求，并且不会读取或覆盖其他业务列。
        unchanged_rows: set[tuple[int, str]] = set()
        if candidates:
            route_values = list(dict.fromkeys(new_value for _, _, new_value in candidates))
            route_cells = self._find_cells(
                binding,
                # 同样从真实表头开始，确保第一条数据行也参与“是否无变化”判断。
                row_from=0,
                row_to=max(row for _, row, _ in candidates),
                col_from=route_col,
                col_to=route_col,
                search=[{"col": route_col, "value": route_values}],
                option_cols=[],
            )
            for cell in route_cells:
                row = int(cell.get("row_from", -1))
                col = int(cell.get("col_from", -1))
                if row < 1 or col != route_col:
                    continue
                value = str(
                    cell.get("cell_text") or cell.get("original_cell_value") or ""
                ).strip()
                unchanged_rows.add((row, value))

        updates: list[dict[str, Any]] = []
        updated_fbas: list[str] = []
        for item, row, new_value in candidates:
            if (row, new_value) in unchanged_rows:
                summary.unchanged.append(item.fba)
                continue
            updates.append(
                {
                    "op_type": "cell_operation_type_formula",
                    "row_from": row,
                    "row_to": row,
                    "col_from": route_col,
                    "col_to": route_col,
                    "formula": new_value,
                }
            )
            updated_fbas.append(item.fba)

        for start in range(0, len(updates), WPS_WRITE_BATCH_SIZE):
            chunk = updates[start : start + WPS_WRITE_BATCH_SIZE]
            self._api_request(
                "POST",
                f"/sheets/{binding.file_id}/worksheets/{binding.worksheet_id}/range_data/batch_update",
                payload={"range_data": chunk},
            )
            summary.updated.extend(updated_fbas[start : start + WPS_WRITE_BATCH_SIZE])
        return summary


def perform_local_authorization(
    client: WpsClient, on_authorization_url: Callable[[str], None]
) -> WpsTokens:
    state = secrets.token_urlsafe(24)
    listener = LocalOAuthCallback(client.credentials.redirect_uri)
    on_authorization_url(client.authorization_url(state))
    code = listener.wait(state)
    return client.exchange_code(code)

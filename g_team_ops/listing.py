from __future__ import annotations

import io
import json
import posixpath
import re
import secrets
import threading
import time
import zipfile
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from xml.etree import ElementTree

import requests

from .airscript import (
    AIRSCRIPT_CHANGE_BATCH_SIZE,
    parse_share_file_id,
    validate_webhook_url,
)
from .errors import (
    AuthenticationError,
    ConfigurationError,
    NetworkError,
    RateLimitError,
    ResponseError,
    ServerError,
)


MAX_LISTING_FILE_SIZE = 20 * 1024 * 1024
MAX_XLSX_EXPANDED_SIZE = 100 * 1024 * 1024
MAX_XLSX_ENTRIES = 2_000
MAX_LISTING_ROWS = 20_000
LISTING_WRITE_BATCH_SIZE = 50
LISTING_PREVIEW_TTL_SECONDS = 20 * 60
REQUIRED_LISTING_SCRIPT_VERSION = 4

SOURCE_HEADERS = (
    "MSKU",
    "ASIN",
    "币种",
    "价格",
    "FBA可售",
    "FBA待调仓",
    "FBA预留",
    "FBA计划入库",
    "FBA标发在途",
    "FBA入库中",
    "FBA不可售",
    "7日销量",
    "14日销量",
    "30日销量",
    "昨日广告费",
    "Rating总数",
    "评分",
)

OPTIONAL_SOURCE_HEADERS = ("优惠价",)

TARGET_HEADERS = (
    "MSKU",
    "品名",
    "ASIN",
    "Listing状态",
    "价格",
    "本次数据日期",
    "上次数据日期",
    "评分",
    "评论数",
    "上次评分",
    "上次评论数",
    "昨日广告费",
    "上次昨日广告费",
    "FBA可售",
    "预留",
    "在途",
    "上次FBA可售",
    "上次预留",
    "上次在途",
    "7日销量",
    "14日销量",
    "30日销量",
    "上次7日销量",
    "上次14日销量",
    "上次30日销量",
    "7日均销",
    "14日均销",
    "30日均销",
    "7日折算月销",
    "月销差异",
    "月销差异率",
    "销量趋势",
    "系统建议月销",
    "最终补货月销",
    "在库覆盖月数",
    "含在途覆盖月数",
    "建议补货量",
    "链接情况",
    "库存情况",
    "广告情况",
    "运营备注",
    "本次更新时间",
)

OPTIONAL_TARGET_HEADERS = ("优惠价",)

_SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_RELATIONSHIP_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _normalize_header(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any, label: str, row_number: int) -> int | float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ConfigurationError(f"第{row_number}行“{label}”不是有效数字")
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"第{row_number}行“{label}”不是有效数字") from exc
    if not number >= 0 or number == float("inf"):
        raise ConfigurationError(f"第{row_number}行“{label}”不能为负数或无穷大")
    return int(number) if number.is_integer() else number


def _sum_optional(
    left: int | float | None, right: int | float | None
) -> int | float | None:
    if left is None and right is None:
        return None
    return (left or 0) + (right or 0)


def calculate_system_monthly_sales(
    sales_7d: int | float | None, sales_30d: int | float | None
) -> int | None:
    """与共享表公式一致：无数据留空，有数据时始终给出可复核的建议值。"""
    if sales_7d is None or sales_30d is None:
        return None
    converted = sales_7d * 4
    if converted == 0 and sales_30d == 0:
        return 0
    if sales_30d == 0:
        return int(Decimal(str(converted)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if converted == 0:
        return int(Decimal(str(sales_30d)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    average = (Decimal(str(converted)) + Decimal(str(sales_30d))) / 2
    return int(average.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class ListingRow:
    source_row: int
    msku: str
    asin: str
    rating: int | float | None
    review_count: int | float | None
    yesterday_ad_spend: int | float | None
    fba_available: int | float | None
    reserved: int | float | None
    inbound: int | float | None
    sales_7d: int | float | None
    sales_14d: int | float | None
    sales_30d: int | float | None
    system_monthly_sales: int | None
    discount_price: int | float | None = None
    discount_price_present: bool = False

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "msku": self.msku,
            "asin": self.asin,
        }
        for key in (
            "rating",
            "review_count",
            "yesterday_ad_spend",
            "fba_available",
            "reserved",
            "inbound",
            "sales_7d",
            "sales_14d",
            "sales_30d",
            "system_monthly_sales",
        ):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        if self.discount_price_present:
            # 有“优惠价”表头但单元格为空，表示当前没有优惠，必须清除共享表旧值。
            payload["discount_price"] = (
                self.discount_price if self.discount_price is not None else ""
            )
        return payload

    def preview_dict(self) -> dict[str, Any]:
        return {
            "source_row": self.source_row,
            "msku": self.msku,
            "asin": self.asin,
            "rating": self.rating,
            "review_count": self.review_count,
            "yesterday_ad_spend": self.yesterday_ad_spend,
            "fba_available": self.fba_available,
            "reserved": self.reserved,
            "inbound": self.inbound,
            "sales_7d": self.sales_7d,
            "sales_14d": self.sales_14d,
            "sales_30d": self.sales_30d,
            "system_monthly_sales": self.system_monthly_sales,
            "discount_price": (
                self.discount_price if self.discount_price_present else None
            ),
        }


@dataclass(frozen=True)
class ParsedListingExport:
    sheet_name: str
    header_row: int
    headers: tuple[str, ...]
    rows: tuple[ListingRow, ...]
    duplicate_mskus: tuple[str, ...] = ()
    skipped_rows: tuple[str, ...] = ()

    @property
    def has_discount_price(self) -> bool:
        optional = {_normalize_header(item) for item in OPTIONAL_SOURCE_HEADERS}
        return any(_normalize_header(item) in optional for item in self.headers)

    @property
    def ignored_headers(self) -> tuple[str, ...]:
        known = {
            _normalize_header(item)
            for item in (*SOURCE_HEADERS, *OPTIONAL_SOURCE_HEADERS)
        }
        return tuple(
            item for item in self.headers if _normalize_header(item) not in known
        )


def infer_listing_data_date(filename: str) -> str:
    match = re.search(r"(?<!\d)(20\d{6})(?!\d)", filename or "")
    if not match:
        return ""
    try:
        return date(
            int(match.group(1)[0:4]),
            int(match.group(1)[4:6]),
            int(match.group(1)[6:8]),
        ).isoformat()
    except ValueError:
        return ""


def validate_listing_data_date(value: str) -> str:
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except (AttributeError, ValueError) as exc:
        raise ConfigurationError("数据日期无效，请使用正确的年-月-日") from exc


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    name = "xl/sharedStrings.xml"
    if name not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read(name))
    text_tag = f"{{{_SPREADSHEET_NS}}}t"
    return [
        "".join(node.text or "" for node in item.iter(text_tag))
        for item in root
    ]


def _first_sheet_path(archive: zipfile.ZipFile) -> tuple[str, str]:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    sheet = workbook.find(f".//{{{_SPREADSHEET_NS}}}sheet")
    if sheet is None:
        raise ConfigurationError("领星文件中没有可读取的工作表")
    sheet_name = str(sheet.attrib.get("name") or "Sheet1")
    relationship_id = sheet.attrib.get(f"{{{_RELATIONSHIP_NS}}}id")
    relationships = ElementTree.fromstring(
        archive.read("xl/_rels/workbook.xml.rels")
    )
    target = ""
    for relationship in relationships.findall(f"{{{_PACKAGE_REL_NS}}}Relationship"):
        if relationship.attrib.get("Id") == relationship_id:
            target = str(relationship.attrib.get("Target") or "")
            break
    if not target:
        raise ConfigurationError("领星文件的工作表关系无效")
    if target.startswith("/"):
        path = target.lstrip("/")
    else:
        path = posixpath.normpath(posixpath.join("xl", target))
    if path not in archive.namelist():
        raise ConfigurationError("领星文件的首个工作表内容缺失")
    return sheet_name, path


def _column_index(cell_reference: str) -> int:
    match = re.match(r"([A-Z]+)", cell_reference.upper())
    if not match:
        return 0
    value = 0
    for character in match.group(1):
        value = value * 26 + ord(character) - 64
    return value - 1


def _cell_value(cell: ElementTree.Element, shared: list[str]) -> Any:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        text_tag = f"{{{_SPREADSHEET_NS}}}t"
        return "".join(node.text or "" for node in cell.iter(text_tag))
    value_node = cell.find(f"{{{_SPREADSHEET_NS}}}v")
    if value_node is None:
        return None
    raw = value_node.text or ""
    if cell_type == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError) as exc:
            raise ConfigurationError("领星文件的文本索引无效") from exc
    if cell_type in {"str", "e"}:
        return raw
    if cell_type == "b":
        return raw == "1"
    try:
        number = float(raw)
    except ValueError:
        return raw
    return int(number) if number.is_integer() else number


def _worksheet_rows(
    archive: zipfile.ZipFile, worksheet_path: str, shared: list[str]
) -> list[tuple[int, list[Any]]]:
    root = ElementTree.fromstring(archive.read(worksheet_path))
    rows: list[tuple[int, list[Any]]] = []
    row_tag = f"{{{_SPREADSHEET_NS}}}row"
    cell_tag = f"{{{_SPREADSHEET_NS}}}c"
    for fallback_row, row in enumerate(root.iter(row_tag), 1):
        try:
            row_number = int(row.attrib.get("r") or fallback_row)
        except ValueError:
            row_number = fallback_row
        values: list[Any] = []
        for cell in row.findall(cell_tag):
            column = _column_index(str(cell.attrib.get("r") or ""))
            while len(values) <= column:
                values.append(None)
            values[column] = _cell_value(cell, shared)
        rows.append((row_number, values))
        if len(rows) > MAX_LISTING_ROWS + 30:
            raise ConfigurationError(
                f"领星文件超过{MAX_LISTING_ROWS}行安全上限，请分文件处理"
            )
    return rows


def _read_xlsx(data: bytes) -> tuple[str, list[tuple[int, list[Any]]]]:
    if not data:
        raise ConfigurationError("上传的领星文件为空")
    if len(data) > MAX_LISTING_FILE_SIZE:
        raise ConfigurationError("领星文件超过20MB，请缩小后重新上传")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ConfigurationError("文件不是有效的.xlsx工作簿") from exc
    with archive:
        entries = archive.infolist()
        if len(entries) > MAX_XLSX_ENTRIES:
            raise ConfigurationError("工作簿内部文件数量异常，已停止读取")
        if any(item.flag_bits & 0x1 for item in entries):
            raise ConfigurationError("暂不支持加密或带打开密码的工作簿")
        if sum(item.file_size for item in entries) > MAX_XLSX_EXPANDED_SIZE:
            raise ConfigurationError("工作簿解压后过大，已停止读取")
        required_parts = {
            "xl/workbook.xml",
            "xl/_rels/workbook.xml.rels",
        }
        if not required_parts.issubset(archive.namelist()):
            raise ConfigurationError("文件缺少.xlsx工作簿的必要结构")
        shared = _shared_strings(archive)
        sheet_name, sheet_path = _first_sheet_path(archive)
        return sheet_name, _worksheet_rows(archive, sheet_path, shared)


def parse_listing_export(data: bytes) -> ParsedListingExport:
    sheet_name, worksheet_rows = _read_xlsx(data)
    required = {_normalize_header(item): item for item in SOURCE_HEADERS}
    optional = {
        _normalize_header(item): item for item in OPTIONAL_SOURCE_HEADERS
    }
    header_position = -1
    header_row_number = 0
    header_values: list[Any] = []
    header_map: dict[str, int] = {}
    best_present: set[str] = set()

    for position, (row_number, values) in enumerate(worksheet_rows[:20]):
        normalized_values = [_normalize_header(item) for item in values]
        present = {item for item in normalized_values if item in required}
        if len(present) > len(best_present):
            best_present = present
        if set(required).issubset(present):
            duplicates = {
                required[item]
                for item in required
                if normalized_values.count(item) > 1
            }
            duplicates.update(
                optional[item]
                for item in optional
                if normalized_values.count(item) > 1
            )
            if duplicates:
                raise ConfigurationError(
                    "领星文件存在重复表头：" + "、".join(sorted(duplicates))
                )
            header_position = position
            header_row_number = row_number
            header_values = values
            header_map = {
                normalized: index
                for index, normalized in enumerate(normalized_values)
                if normalized
            }
            break

    if header_position < 0:
        missing = [required[item] for item in required if item not in best_present]
        raise ConfigurationError(
            "前20行未找到完整的领星表头，缺少：" + "、".join(missing)
        )

    parsed_rows: list[ListingRow] = []
    skipped_rows: list[str] = []
    msku_counts: dict[str, int] = {}

    def source_value(values: list[Any], header: str) -> Any:
        index = header_map[_normalize_header(header)]
        return values[index] if index < len(values) else None

    def optional_source_value(values: list[Any], header: str) -> Any:
        index = header_map.get(_normalize_header(header))
        if index is None:
            return None
        return values[index] if index < len(values) else None

    discount_price_present = _normalize_header("优惠价") in header_map

    for row_number, values in worksheet_rows[header_position + 1 :]:
        msku = _clean_text(source_value(values, "MSKU"))
        if not msku:
            continue
        if len(msku) > 120:
            skipped_rows.append(f"第{row_number}行MSKU过长")
            continue
        try:
            fba_waiting = _number(
                source_value(values, "FBA待调仓"), "FBA待调仓", row_number
            )
            fba_reserved = _number(
                source_value(values, "FBA预留"), "FBA预留", row_number
            )
            planned = _number(
                source_value(values, "FBA计划入库"), "FBA计划入库", row_number
            )
            shipped = _number(
                source_value(values, "FBA标发在途"), "FBA标发在途", row_number
            )
            sales_7d = _number(
                source_value(values, "7日销量"), "7日销量", row_number
            )
            sales_30d = _number(
                source_value(values, "30日销量"), "30日销量", row_number
            )
            row = ListingRow(
                source_row=row_number,
                msku=msku,
                asin=_clean_text(source_value(values, "ASIN")).upper(),
                rating=_number(source_value(values, "评分"), "评分", row_number),
                review_count=_number(
                    source_value(values, "Rating总数"), "Rating总数", row_number
                ),
                yesterday_ad_spend=_number(
                    source_value(values, "昨日广告费"), "昨日广告费", row_number
                ),
                fba_available=_number(
                    source_value(values, "FBA可售"), "FBA可售", row_number
                ),
                reserved=_sum_optional(fba_waiting, fba_reserved),
                inbound=_sum_optional(planned, shipped),
                sales_7d=sales_7d,
                sales_14d=_number(
                    source_value(values, "14日销量"), "14日销量", row_number
                ),
                sales_30d=sales_30d,
                system_monthly_sales=calculate_system_monthly_sales(
                    sales_7d, sales_30d
                ),
                discount_price=(
                    _number(
                        optional_source_value(values, "优惠价"),
                        "优惠价",
                        row_number,
                    )
                    if discount_price_present
                    else None
                ),
                discount_price_present=discount_price_present,
            )
        except ConfigurationError as exc:
            skipped_rows.append(exc.user_message)
            continue
        parsed_rows.append(row)
        normalized_msku = msku.strip().upper()
        msku_counts[normalized_msku] = msku_counts.get(normalized_msku, 0) + 1

    duplicate_keys = {key for key, count in msku_counts.items() if count > 1}
    duplicate_mskus = sorted(
        {row.msku for row in parsed_rows if row.msku.strip().upper() in duplicate_keys}
    )
    unique_rows = tuple(
        row
        for row in parsed_rows
        if row.msku.strip().upper() not in duplicate_keys
    )
    if not unique_rows:
        raise ConfigurationError("领星文件中没有可安全处理的唯一MSKU数据")
    return ParsedListingExport(
        sheet_name=sheet_name,
        header_row=header_row_number,
        headers=tuple(_clean_text(item) for item in header_values if _clean_text(item)),
        rows=unique_rows,
        duplicate_mskus=tuple(duplicate_mskus),
        skipped_rows=tuple(skipped_rows),
    )


@dataclass(frozen=True)
class ListingConnectionConfig:
    share_url: str
    webhook_url: str
    api_token: str
    sheet_name: str


@dataclass(frozen=True)
class ListingAirScriptBinding:
    sheet_name: str
    header_row: int
    columns: dict[str, str]


@dataclass
class ListingSyncSummary:
    updated: list[str] = field(default_factory=list)
    same_date_updated: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    not_in_sheet: list[str] = field(default_factory=list)
    duplicate_rows: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def message(self) -> str:
        parts = [
            f"新日期更新 {len(self.updated)}",
            f"同日修正 {len(self.same_date_updated)}",
            f"表中未找到 {len(self.not_in_sheet)}",
            f"旧日期跳过 {len(self.stale)}",
            f"重复行 {len(self.duplicate_rows)}",
            f"ASIN冲突 {len(self.conflicts)}",
        ]
        if self.failures:
            parts.append(f"失败 {len(self.failures)}")
        return "，".join(parts)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _listing_snapshot_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ResponseError("Listing AirScript响应中缺少共享表快照")
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ResponseError("Listing AirScript快照项目结构无效")
        result.append(dict(item))
    return result


def listing_summary_from_payload(value: dict[str, Any]) -> ListingSyncSummary:
    return ListingSyncSummary(
        updated=_string_list(value.get("updated")),
        same_date_updated=_string_list(value.get("same_date_updated")),
        stale=_string_list(value.get("stale")),
        not_in_sheet=_string_list(value.get("not_in_sheet")),
        duplicate_rows=_string_list(value.get("duplicate_rows")),
        conflicts=_string_list(value.get("conflicts")),
        failures=_string_list(value.get("failures")),
    )


class ListingAirScriptClient:
    def __init__(
        self,
        config: ListingConnectionConfig,
        *,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (8.0, 90.0),
        retries: int = 2,
    ):
        parse_share_file_id(config.share_url)
        validate_webhook_url(config.webhook_url)
        if not config.api_token:
            raise ConfigurationError("Listing AirScript脚本令牌不能为空")
        if not config.sheet_name.strip() or len(config.sheet_name.strip()) > 80:
            raise ConfigurationError("Listing子表名称不能为空且不能超过80位")
        self.config = config
        self.session = session or requests.Session()
        self.timeout = timeout
        self.retries = max(0, retries)

    def _execute(
        self,
        action: str,
        items: list[dict[str, Any]],
        data_date: str = "",
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        argv = {
            "action": action,
            "sheet_name": self.config.sheet_name,
            "data_date": data_date,
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
                raise NetworkError(
                    "连接Listing AirScript失败，请检查网络后重试"
                ) from exc
            if response.status_code in (401, 403):
                raise AuthenticationError(
                    "Listing脚本令牌无效、已过期，或当前账号没有表格编辑权限"
                )
            if response.status_code == 429:
                if attempt < self.retries:
                    time.sleep(1.2 * (attempt + 1))
                    continue
                raise RateLimitError("Listing AirScript请求过于频繁，请稍后重试")
            if response.status_code >= 500:
                if attempt < self.retries:
                    time.sleep(1.2 * (attempt + 1))
                    continue
                raise ServerError(
                    f"Listing AirScript服务暂时不可用（HTTP {response.status_code}）"
                )
            if response.status_code >= 400:
                raise ResponseError(
                    f"Listing AirScript请求失败（HTTP {response.status_code}）"
                )
            break
        assert response is not None
        try:
            body = response.json()
        except ValueError as exc:
            raise ResponseError("Listing AirScript返回的内容不是有效JSON") from exc
        if not isinstance(body, dict):
            raise ResponseError("Listing AirScript返回的数据结构无效")
        if body.get("error"):
            details = body.get("error_details")
            detail_message = details.get("msg") if isinstance(details, dict) else ""
            raise ResponseError(
                "Listing AirScript执行失败："
                + str(detail_message or body.get("error"))
            )
        if body.get("status") not in (None, "finished"):
            raise ResponseError("Listing AirScript未正常执行完成")
        data = body.get("data")
        if not isinstance(data, dict) or "result" not in data:
            raise ResponseError("Listing AirScript响应中缺少脚本执行结果")
        result: Any = data["result"]
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError as exc:
                raise ResponseError(
                    "Listing AirScript脚本返回值不是有效JSON对象"
                ) from exc
        if not isinstance(result, dict):
            raise ResponseError("Listing AirScript脚本返回值结构无效")
        if result.get("success") is not True:
            raise ResponseError(
                str(result.get("message") or "Listing AirScript报告执行失败")
            )
        try:
            version = int(result.get("schemaVersion") or 0)
        except (TypeError, ValueError):
            version = 0
        if version < REQUIRED_LISTING_SCRIPT_VERSION:
            raise ResponseError(
                "WPS中的Listing AirScript版本过旧，请替换为项目内最新脚本"
            )
        return result

    def validate(self) -> ListingAirScriptBinding:
        result = self._execute("validate", [])
        columns = result.get("columns")
        if not isinstance(columns, dict):
            raise ResponseError("Listing AirScript验证结果缺少列信息")
        try:
            header_row = int(result.get("headerRow") or 0)
        except (TypeError, ValueError):
            header_row = 0
        sheet_name = str(result.get("sheetName") or "").strip()
        if not sheet_name or header_row < 1 or len(columns) < len(TARGET_HEADERS):
            raise ResponseError(
                "Listing AirScript未识别完整共享表表头，请更新脚本和表头"
            )
        return ListingAirScriptBinding(
            sheet_name=sheet_name,
            header_row=header_row,
            columns={str(key): str(value) for key, value in columns.items()},
        )

    def discover_sheets(self) -> list[dict[str, str]]:
        result = self._execute("discover", [])
        try:
            version = int(result.get("schemaVersion") or 0)
        except (TypeError, ValueError):
            version = 0
        if version < 2:
            raise ResponseError(
                "WPS中的Listing AirScript不支持自动识别国家；请替换为项目内最新脚本"
            )
        sheets = result.get("sheets")
        if not isinstance(sheets, list):
            raise ResponseError("Listing AirScript扫描结果缺少子表列表")
        parsed: list[dict[str, str]] = []
        for item in sheets:
            if isinstance(item, str):
                name = item.strip()
                sheet_id = ""
            elif isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                sheet_id = str(item.get("id") or "").strip()
            else:
                continue
            if name:
                parsed.append({"id": sheet_id, "name": name})
        if not parsed:
            raise ResponseError("Listing AirScript没有返回任何可识别子表")
        return parsed

    def snapshot_rows(
        self, rows: tuple[ListingRow, ...]
    ) -> list[dict[str, Any]]:
        payload_rows = [row.to_payload() for row in rows]
        snapshots: list[dict[str, Any]] = []
        for offset in range(0, len(payload_rows), LISTING_WRITE_BATCH_SIZE):
            result = self._execute(
                "snapshot",
                payload_rows[offset : offset + LISTING_WRITE_BATCH_SIZE],
            )
            snapshots.extend(_listing_snapshot_list(result.get("snapshots")))
        return snapshots

    def snapshot_targets(
        self, targets: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        snapshots: list[dict[str, Any]] = []
        for offset in range(0, len(targets), AIRSCRIPT_CHANGE_BATCH_SIZE):
            result = self._execute(
                "snapshot_targets",
                [],
                arguments={"targets": targets[offset : offset + AIRSCRIPT_CHANGE_BATCH_SIZE]},
            )
            snapshots.extend(_listing_snapshot_list(result.get("snapshots")))
        return snapshots

    def inspect_changes(
        self,
        changes: list[dict[str, Any]],
        *,
        direction: str,
    ) -> dict[str, list[dict[str, Any]]]:
        aggregate = {"ready": [], "alreadyApplied": [], "conflicts": [], "failures": []}
        for offset in range(0, len(changes), AIRSCRIPT_CHANGE_BATCH_SIZE):
            result = self._execute(
                "inspect_changes",
                [],
                arguments={"changes": changes[offset : offset + AIRSCRIPT_CHANGE_BATCH_SIZE], "direction": direction, "index_offset": offset},
            )
            for key in aggregate:
                aggregate[key].extend(
                    [item for item in result.get(key, []) if isinstance(item, dict)]
                )
        return aggregate

    def apply_changes(
        self,
        changes: list[dict[str, Any]],
        *,
        direction: str,
    ) -> dict[str, list[dict[str, Any]]]:
        aggregate = {"applied": [], "alreadyApplied": [], "conflicts": [], "failures": []}
        for offset in range(0, len(changes), AIRSCRIPT_CHANGE_BATCH_SIZE):
            try:
                result = self._execute(
                    "apply_changes",
                    [],
                    arguments={"changes": changes[offset : offset + AIRSCRIPT_CHANGE_BATCH_SIZE], "direction": direction, "index_offset": offset},
                )
            except CarrierError as exc:
                exc.partial_change_result = aggregate
                raise
            for key in aggregate:
                aggregate[key].extend(
                    [item for item in result.get(key, []) if isinstance(item, dict)]
                )
        return aggregate

    def sync(
        self,
        rows: tuple[ListingRow, ...],
        data_date: str,
        preconditions: list[dict[str, Any]] | None = None,
    ) -> ListingSyncSummary:
        data_date = validate_listing_data_date(data_date)
        summary = ListingSyncSummary()
        payload_rows = [row.to_payload() for row in rows]
        for offset in range(0, len(payload_rows), LISTING_WRITE_BATCH_SIZE):
            batch = payload_rows[offset : offset + LISTING_WRITE_BATCH_SIZE]
            batch_keys = {str(item.get("msku") or "").strip().upper() for item in batch}
            batch_preconditions = [
                item
                for item in (preconditions or [])
                if str(item.get("itemKey") or "").strip().upper() in batch_keys
            ]
            try:
                result = self._execute(
                    "sync",
                    batch,
                    data_date,
                    {"preconditions": batch_preconditions},
                )
            except (
                NetworkError,
                AuthenticationError,
                RateLimitError,
                ServerError,
                ResponseError,
            ) as exc:
                batch_number = offset // LISTING_WRITE_BATCH_SIZE + 1
                total_batches = (
                    len(payload_rows) + LISTING_WRITE_BATCH_SIZE - 1
                ) // LISTING_WRITE_BATCH_SIZE
                raise type(exc)(
                    f"Listing第 {batch_number}/{total_batches} 批回填失败；"
                    f"此前已处理 {offset} 条。同日重试不会再次滚动上次值："
                    f"{exc.user_message}"
                ) from exc
            summary.updated.extend(_string_list(result.get("updated")))
            summary.same_date_updated.extend(
                _string_list(result.get("sameDateUpdated"))
            )
            summary.stale.extend(_string_list(result.get("stale")))
            summary.not_in_sheet.extend(_string_list(result.get("notInSheet")))
            summary.duplicate_rows.extend(
                _string_list(result.get("duplicateRows"))
            )
            summary.conflicts.extend(_string_list(result.get("conflicts")))
            summary.failures.extend(_string_list(result.get("failures")))
        return summary


@dataclass(frozen=True)
class PendingListingImport:
    user_id: str
    shop_id: str
    country_id: str
    data_date: str
    filename: str
    parsed: ParsedListingExport
    created_at: float


class ListingPreviewRegistry:
    def __init__(self):
        self._items: dict[str, PendingListingImport] = {}
        self._lock = threading.Lock()

    def create(
        self,
        user_id: str,
        shop_id: str,
        country_id: str,
        data_date: str,
        filename: str,
        parsed: ParsedListingExport,
    ) -> str:
        token = secrets.token_urlsafe(24)
        now = time.monotonic()
        pending = PendingListingImport(
            user_id,
            shop_id,
            country_id,
            data_date,
            filename,
            parsed,
            now,
        )
        with self._lock:
            self._items = {
                key: value
                for key, value in self._items.items()
                if now - value.created_at <= LISTING_PREVIEW_TTL_SECONDS
                and value.user_id != user_id
            }
            self._items[token] = pending
        return token

    def get(self, user_id: str, token: str) -> PendingListingImport:
        with self._lock:
            pending = self._items.get(token)
        if (
            pending is None
            or pending.user_id != user_id
            or time.monotonic() - pending.created_at > LISTING_PREVIEW_TTL_SECONDS
        ):
            raise ConfigurationError("上传预览已失效，请重新选择文件")
        return pending

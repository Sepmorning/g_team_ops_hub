from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class QueryStatus(str, Enum):
    SUCCESS = "查询成功"
    NOT_FOUND = "未找到"
    FAILED = "查询失败"
    PARTIAL = "部分查询失败"
    CONFLICT = "货代冲突"


@dataclass(frozen=True)
class ParseResult:
    valid: list[str]
    invalid: list[str]
    duplicates: list[str]


@dataclass(frozen=True)
class TrackingResult:
    fba: str
    status: QueryStatus
    carrier: str = ""
    latest_time: str = ""
    latest_event: str = ""
    error_category: str = ""
    error_message: str = ""

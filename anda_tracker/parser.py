from __future__ import annotations

import re

from .models import ParseResult


_SEPARATOR_RE = re.compile(r"[\s、，,；;]+")
# FBA 后至少 3 个字符，总长度限制在 50 以内；允许常见字母、数字、短横线和下划线。
_FBA_RE = re.compile(r"^FBA[A-Z0-9_-]{3,47}$", re.IGNORECASE)


def parse_fba_input(raw: str) -> ParseResult:
    """解析、规范化并稳定去重用户输入。"""
    tokens = [token.strip() for token in _SEPARATOR_RE.split(raw or "") if token.strip()]
    valid: list[str] = []
    invalid: list[str] = []
    duplicates: list[str] = []
    seen: set[str] = set()

    for token in tokens:
        normalized = token.upper()
        if not _FBA_RE.fullmatch(normalized):
            invalid.append(token)
            continue
        if normalized in seen:
            duplicates.append(normalized)
            continue
        seen.add(normalized)
        valid.append(normalized)

    return ParseResult(valid=valid, invalid=invalid, duplicates=duplicates)

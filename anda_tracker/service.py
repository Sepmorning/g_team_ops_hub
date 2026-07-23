from __future__ import annotations

import time
from collections.abc import Callable

from .client import AndaClient
from .errors import AuthenticationError, CarrierError
from .models import QueryStatus, TrackingResult


class AndaQueryService:
    carrier = "安达"

    def __init__(
        self,
        client: AndaClient,
        batch_size: int = 20,
        request_interval: float = 1.5,
        sleeper: Callable[[float], None] = time.sleep,
        reauthenticate: Callable[[], None] | None = None,
    ):
        if batch_size < 1:
            raise ValueError("batch_size 必须大于 0")
        self.client = client
        self.batch_size = batch_size
        self.request_interval = max(0.0, request_interval)
        self.sleeper = sleeper
        self.reauthenticate = reauthenticate

    def query_many(self, fbas: list[str]) -> list[TrackingResult]:
        results: dict[str, TrackingResult] = {}
        for offset in range(0, len(fbas), self.batch_size):
            batch = fbas[offset : offset + self.batch_size]
            try:
                try:
                    records = self.client.query_batch(batch)
                except AuthenticationError:
                    if self.reauthenticate is None:
                        raise
                    # 浏览器登录可能挤掉项目会话。自动重新登录一次，再重试本批。
                    self.reauthenticate()
                    records = self.client.query_batch(batch)
                found: dict[str, dict] = {}
                for record in records:
                    if not isinstance(record, dict):
                        continue
                    fba = str(record.get("fbaCode") or "").strip().upper()
                    if fba in batch:
                        found[fba] = record
                for fba in batch:
                    record = found.get(fba)
                    if record is None:
                        results[fba] = TrackingResult(fba=fba, status=QueryStatus.NOT_FOUND, carrier="安达")
                    else:
                        results[fba] = TrackingResult(
                            fba=fba,
                            status=QueryStatus.SUCCESS,
                            carrier="安达",
                            latest_time=str(record.get("latestTraceTime") or ""),
                            latest_event=str(record.get("latestTraceName") or record.get("stateName") or ""),
                        )
            except CarrierError as exc:
                for fba in batch:
                    results[fba] = TrackingResult(
                        fba=fba,
                        status=QueryStatus.FAILED,
                        carrier="安达",
                        error_category=exc.category,
                        error_message=exc.user_message,
                    )
            if offset + self.batch_size < len(fbas) and self.request_interval:
                self.sleeper(self.request_interval)
        return [results[fba] for fba in fbas]

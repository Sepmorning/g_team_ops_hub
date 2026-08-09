from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .client import AndaClient
from .errors import AuthenticationError, CarrierError
from .models import QueryStatus, TrackingDetails, TrackingResult
from .tracking_details import normalize_tracking_details


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
        self.last_records: dict[str, dict[str, Any]] = {}

    def _query_batch_with_reauthentication(
        self, fbas: list[str]
    ) -> list[dict[str, Any]]:
        try:
            return self.client.query_batch(fbas)
        except AuthenticationError:
            if self.reauthenticate is None:
                raise
            self.reauthenticate()
            return self.client.query_batch(fbas)

    def _trace_list_with_reauthentication(
        self, trace_no: str
    ) -> list[dict[str, Any]]:
        try:
            return self.client.get_trace_list(trace_no)
        except AuthenticationError:
            if self.reauthenticate is None:
                raise
            self.reauthenticate()
            return self.client.get_trace_list(trace_no)

    def query_many(self, fbas: list[str]) -> list[TrackingResult]:
        results: dict[str, TrackingResult] = {}
        for offset in range(0, len(fbas), self.batch_size):
            batch = fbas[offset : offset + self.batch_size]
            try:
                # 浏览器登录可能挤掉项目会话。自动重新登录一次，再重试本批。
                records = self._query_batch_with_reauthentication(batch)
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
                        self.last_records[fba] = record
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

    @staticmethod
    def _status_events(record: dict[str, Any]) -> list[dict[str, Any]]:
        value = record.get("orderStatusMap")
        if isinstance(value, dict):
            values = list(value.values())
        elif isinstance(value, list):
            values = value
        else:
            values = []
        return [
            {
                "event_time": item.get("bizTime"),
                "content": item.get("stateName"),
                "source_status": item.get("stateCode"),
            }
            for item in values
            if isinstance(item, dict)
            and (item.get("bizTime") or item.get("stateName"))
        ]

    @staticmethod
    def _status_time(record: dict[str, Any], *state_codes: str) -> str:
        wanted = {value.upper() for value in state_codes}
        value = record.get("orderStatusMap")
        if isinstance(value, dict):
            values = list(value.values())
        elif isinstance(value, list):
            values = value
        else:
            values = []
        for item in values:
            if (
                isinstance(item, dict)
                and str(item.get("stateCode") or "").upper() in wanted
            ):
                return str(item.get("bizTime") or "")
        return ""

    def fetch_tracking_details(self, fba: str) -> TrackingDetails:
        record = self.last_records.get(fba)
        if record is None:
            records = self._query_batch_with_reauthentication([fba])
            record = next(
                (
                    item
                    for item in records
                    if isinstance(item, dict)
                    and str(item.get("fbaCode") or "").strip().upper() == fba
                ),
                None,
            )
            if record is None:
                raise ValueError("安达订单详情不存在")
            self.last_records[fba] = record

        trace_no = str(
            record.get("traceNo")
            or record.get("waybillNo")
            or record.get("fbaCode")
            or ""
        )
        # 批量订单查询后、读取完整轨迹前也可能被其他登录挤下线。
        trace_groups = self._trace_list_with_reauthentication(trace_no)
        raw_events: list[dict[str, Any]] = []
        for group in trace_groups:
            values = group.get("list")
            if not isinstance(values, list):
                values = group.get("historys")
            if isinstance(values, list):
                raw_events.extend(item for item in values if isinstance(item, dict))
        if not raw_events:
            raw_events = self._status_events(record)

        vessel = str(record.get("vesselName") or record.get("shipName") or "").strip()
        voyage = str(record.get("voyageNo") or record.get("voyage") or "").strip()
        return normalize_tracking_details(
            fba=fba,
            carrier="安达",
            raw_events=raw_events,
            carrier_order_no=str(
                record.get("orderNo") or record.get("waybillNo") or trace_no
            ),
            transport_ref=" / ".join(part for part in (vessel, voyage) if part),
            structured={
                "pickup_time": record.get("warehouseInTime")
                or record.get("warehouseEntryTime"),
                "estimated_departure": record.get("etd"),
                "actual_departure": record.get("atd"),
                "estimated_arrival": record.get("eta"),
                "actual_arrival": record.get("ata"),
                "estimated_delivery": record.get("estimatedDeliveryTime"),
                "last_mile_time": self._status_time(
                    record, "ENTRYTRANSFERWAREHOUSE"
                ),
                "signed_time": record.get("signReceiveTime")
                or self._status_time(record, "CLIENTSIGN"),
            },
        )

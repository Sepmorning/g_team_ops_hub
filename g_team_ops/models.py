from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


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
class TrackingEvent:
    event_id: str
    fba: str
    carrier: str
    carrier_order_no: str = ""
    event_time: str = ""
    phase: str = ""
    node: str = ""
    event_type: str = ""
    content: str = ""
    related_plan: str = ""
    validity: str = "当前有效"
    exception_status: str = "无异常"
    transport_info: str = ""
    attachment: str = ""
    source_status: str = ""
    first_seen: str = ""
    last_confirmed: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "event_id": self.event_id,
            "fba": self.fba,
            "carrier": self.carrier,
            "carrier_order_no": self.carrier_order_no,
            "event_time": self.event_time,
            "phase": self.phase,
            "node": self.node,
            "event_type": self.event_type,
            "content": self.content,
            "related_plan": self.related_plan,
            "validity": self.validity,
            "exception_status": self.exception_status,
            "transport_info": self.transport_info,
            "attachment": self.attachment,
            "source_status": self.source_status,
            "first_seen": self.first_seen,
            "last_confirmed": self.last_confirmed,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TrackingEvent":
        return cls(
            **{
                field: str(value.get(field) or "")
                for field in cls.__dataclass_fields__
                if field not in {"validity", "exception_status"}
            },
            validity=str(value.get("validity") or "当前有效"),
            exception_status=str(value.get("exception_status") or "无异常"),
        )


@dataclass(frozen=True)
class TrackingSnapshot:
    transport_ref: str = ""
    current_phase: str = ""
    current_node: str = ""
    latest_time: str = ""
    latest_event: str = ""
    current_exception: str = ""
    pickup_time: str = ""
    estimated_departure: str = ""
    actual_departure: str = ""
    estimated_arrival: str = ""
    actual_arrival: str = ""
    estimated_delivery: str = ""
    last_mile_time: str = ""
    signed_time: str = ""
    pod_status: str = "未提供"
    data_status: str = "正常"
    updated_time: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            field: str(getattr(self, field) or "")
            for field in self.__dataclass_fields__
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TrackingSnapshot":
        return cls(
            **{
                field: str(value.get(field) or "")
                for field in cls.__dataclass_fields__
                if field not in {"pod_status", "data_status"}
            },
            pod_status=str(value.get("pod_status") or "未提供"),
            data_status=str(value.get("data_status") or "正常"),
        )


@dataclass(frozen=True)
class TrackingDetails:
    snapshot: TrackingSnapshot
    events: tuple[TrackingEvent, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot": self.snapshot.to_dict(),
            "events": [event.to_dict() for event in self.events],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TrackingDetails":
        snapshot = value.get("snapshot")
        events = value.get("events")
        return cls(
            snapshot=TrackingSnapshot.from_dict(
                snapshot if isinstance(snapshot, dict) else {}
            ),
            events=tuple(
                TrackingEvent.from_dict(event)
                for event in (events if isinstance(events, list) else [])
                if isinstance(event, dict)
            ),
        )


@dataclass(frozen=True)
class TrackingResult:
    fba: str
    status: QueryStatus
    carrier: str = ""
    latest_time: str = ""
    latest_event: str = ""
    error_category: str = ""
    error_message: str = ""
    snapshot: TrackingSnapshot | None = None
    events: tuple[TrackingEvent, ...] = ()

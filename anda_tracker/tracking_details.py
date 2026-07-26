from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from .models import TrackingDetails, TrackingEvent, TrackingSnapshot


TRACKING_SCHEMA_VERSION = 4

_DATE_TOKEN = re.compile(
    r"(?:(?P<year>20\d{2})(?:[./-]|年))?"
    r"(?P<month>\d{1,2})(?:[./-]|月)(?P<day>\d{1,2})日?"
)
_TRANSPORT_PATTERNS = (
    re.compile(r"V\.?V\.?\s*[:：]\s*([^，,。；;]+)", re.IGNORECASE),
    re.compile(r"船名航次\s*[:：]?\s*([^，,。；;]+)"),
    re.compile(r"航班(?:号)?\s*[:：]?\s*([^，,。；;]+)"),
)
_EXCEPTION_WORDS = (
    "查验",
    "甩柜",
    "退场",
    "DR监控",
    "扣关",
    "暂未放行",
    "未放行",
    "取消",
    "延误",
    "异常",
    "丢失",
    "退件",
    "预约失败",
    "未拿约",
)
_RECOVERY_WORDS = (
    "查验完成",
    "查验完毕",
    "已放行",
    "报关放行",
    "清关放行",
    "查验已放行",
    "完成退场",
    "重新安排",
    "恢复",
    "已开船",
    "已起飞",
    "已发车",
    "已到港",
    "已提柜",
    "已签收",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def current_time_text() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def date_only(value: Any) -> str:
    match = _DATE_TOKEN.search(_text(value))
    if not match or not match.group("year"):
        return ""
    try:
        return date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        ).isoformat()
    except ValueError:
        return ""


def _event_date(value: str) -> date | None:
    normalized = date_only(value)
    if not normalized:
        return None
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None


def _normalized_date_token(token: str, event_time: str) -> str:
    match = _DATE_TOKEN.fullmatch(token.strip())
    if not match:
        return ""
    event_day = _event_date(event_time) or datetime.now().date()
    year = int(match.group("year") or event_day.year)
    try:
        candidate = date(year, int(match.group("month")), int(match.group("day")))
    except ValueError:
        return ""
    if not match.group("year") and candidate < event_day - timedelta(days=90):
        try:
            candidate = candidate.replace(year=candidate.year + 1)
        except ValueError:
            return ""
    return candidate.isoformat()


def _date_after_label(content: str, labels: str, event_time: str) -> str:
    match = re.search(
        rf"(?:{labels})[^\d]{{0,24}}"
        r"((?:20\d{2}(?:[./-]|年))?\d{1,2}(?:[./-]|月)\d{1,2}日?)",
        content,
        re.IGNORECASE,
    )
    return _normalized_date_token(match.group(1), event_time) if match else ""


def _date_before_label(
    content: str,
    prefixes: str,
    suffixes: str,
    event_time: str,
) -> str:
    match = re.search(
        rf"(?:{prefixes})[^\d]{{0,24}}"
        r"((?:20\d{2}(?:[./-]|年))?\d{1,2}(?:[./-]|月)\d{1,2}日?)"
        rf".{{0,16}}?(?:{suffixes})",
        content,
        re.IGNORECASE,
    )
    return _normalized_date_token(match.group(1), event_time) if match else ""


def _extract_plans(content: str, event_time: str) -> dict[str, str]:
    plans: dict[str, str] = {}
    etd = _date_after_label(
        content,
        r"ETD|预计(?:开船|起飞|发车|离港)|计划(?:开船|起飞|发车)",
        event_time,
    )
    eta = _date_after_label(
        content,
        r"ETA|预计(?:到港|抵达|到达|落地|到站)|到港时间",
        event_time,
    )
    delivery = _date_after_label(
        content,
        r"预约(?:送仓|派送)|预计(?:送仓|派送|送达|签收)|计划(?:送仓|派送)",
        event_time,
    )
    if not etd:
        etd = _date_before_label(
            content,
            r"预计|计划",
            r"开船|起飞|发车|离港",
            event_time,
        )
    if not eta:
        eta = _date_before_label(
            content,
            r"预计|计划",
            r"到港|抵达|到达|落地|到站",
            event_time,
        )
    if not delivery:
        delivery = _date_before_label(
            content,
            r"预计|计划|预约",
            r"送仓|派送|送达|签收",
            event_time,
        )
    if not delivery and any(word in content for word in ("送仓", "送达")):
        match = _DATE_TOKEN.search(content)
        if match:
            delivery = _normalized_date_token(match.group(0), event_time)
    if etd:
        plans["预计出发"] = etd
    if eta:
        plans["预计到达"] = eta
    if delivery:
        plans["预计送达"] = delivery
    return plans


def _transport_info(content: str) -> str:
    for pattern in _TRANSPORT_PATTERNS:
        match = pattern.search(content)
        if match:
            return match.group(1).strip()
    return ""


def _is_exception(content: str) -> bool:
    lowered = content.lower()
    if _is_recovery(content):
        return False
    return any(word.lower() in lowered for word in _EXCEPTION_WORDS)


def _is_recovery(content: str) -> bool:
    lowered = content.lower()
    if "放行" in lowered and any(
        word in lowered for word in ("等待", "待放行", "未放行", "暂未")
    ):
        return False
    return any(word.lower() in lowered for word in _RECOVERY_WORDS)


def _is_estimate(content: str) -> bool:
    return any(
        word.lower() in content.lower()
        for word in ("预计", "预约", "计划", "ETD", "ETA")
    )


def _classify(content: str) -> tuple[str, str, bool]:
    value = content.replace(" ", "")
    upper = value.upper()

    if any(word in value for word in ("已签收", "签收完成", "已送达")) or (
        value in {"签收", "送达"}
    ):
        return "完成", "签收", True
    if "POD" in upper:
        return "完成", "POD", True
    if any(word in value for word in ("货物已提取", "货物提取")):
        return "末端配送", "货物提取", True
    if any(
        word in value
        for word in ("递交快递服务商", "递交快递", "递交末端承运商")
    ):
        return "末端配送", "递交末端承运商", True
    if "派送中" in value and "预约" not in value:
        return "末端配送", "派送中", True
    if any(word in value for word in ("预约派送", "预约送仓", "计划送仓")):
        return "末端配送", "预约派送", False
    if "已提柜" in value or (
        "提柜" in value and "预约" not in value and "预计" not in value
    ):
        return "目的地处理", "提柜", True
    if "预约提柜" in value or "预计提柜" in value:
        return "目的地处理", "预约提柜", False
    if "拆柜" in value or "拆箱" in value:
        return "目的地处理", "拆柜", not _is_estimate(value)
    if any(word in value for word in ("海外仓", "港后入仓")):
        return "目的地处理", "目的仓入库", "已" in value or "抵达" in value
    if any(
        word in value
        for word in ("已到港", "已到目的港", "到达目的港", "已抵港")
    ) or (
        "到港" in value and not _is_estimate(value)
    ):
        return "干线运输", "实际到达", True
    if "清关" in value:
        if (
            any(word in value for word in ("已放行", "清关已放行", "已清关放行"))
            or (
                value == "清关放行"
            )
        ):
            return "目的地处理", "进口放行", True
        return "目的地处理", "进口清关", False
    if "卸船" in value or "卸柜" in value:
        return "目的地处理", "卸船/卸柜", not _is_estimate(value)
    if any(
        word in value for word in ("已开船", "已离港", "已起飞", "已发车")
    ) or (
        any(word in value for word in ("开船", "起飞", "发车"))
        and not _is_estimate(value)
    ):
        return "干线运输", "实际出发", True
    if any(word in value for word in ("航行中", "运输中", "中转", "二程")):
        return "干线运输", "运输中/中转", "已" in value
    if "甩柜" in value:
        return "起运准备", "甩柜/换班次", False
    if any(word in value for word in ("装柜", "装箱")):
        return "起运准备", "装柜/装箱", not _is_estimate(value)
    if "出口放行" in value:
        return "起运准备", "出口放行", True
    if "报关" in value:
        if "已放行" in value or "报关放行" in value:
            return "起运准备", "出口放行", True
        return "起运准备", "出口报关", False
    if "查验" in value:
        return "起运准备", "出口查验", "完成" in value or "放行" in value
    if any(word in value for word in ("船期", "航班", "等待开船")):
        return "起运准备", "等待班次", False
    if any(
        word in value
        for word in (
            "进仓",
            "入仓",
            "入库",
            "揽收",
            "入中国仓",
            "入国内仓",
        )
    ):
        return "接收", "入仓/接收", not _is_estimate(value)
    if "已提货" in value and "柜" not in value:
        return "接收", "提货", True
    if "已受理" in value:
        return "接收", "已受理", True
    if "待受理" in value or "上传箱单" in value:
        return "接收", "待受理", False
    return "其他", "其他", False


def _event_type(
    content: str,
    *,
    actual: bool,
    attachment: str,
) -> str:
    values: list[str] = []
    if actual:
        values.append("实际")
    if _is_estimate(content):
        values.append("预计")
    if any(word in content for word in ("更新", "调整", "换船", "提前", "重新安排")):
        values.append("变更")
    if _is_exception(content):
        values.append("异常")
    elif _is_recovery(content):
        values.append("恢复")
    if attachment or "POD" in content.upper():
        values.append("附件")
    return "/".join(dict.fromkeys(values)) or "信息"


def _event_id(
    carrier: str,
    fba: str,
    event_time: str,
    content: str,
    attachment: str,
) -> str:
    source = "\x1f".join((carrier, fba, event_time, content, attachment))
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:32]


def _as_raw_events(values: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        event_time = _text(
            value.get("event_time")
            or value.get("traceTime")
            or value.get("timestamp")
            or value.get("happened_at")
            or value.get("bizTime")
        )
        content = _text(
            value.get("content")
            or value.get("traceName")
            or value.get("detail")
            or value.get("stateName")
        )
        if not event_time and not content:
            continue
        result.append(
            {
                "event_time": event_time,
                "content": content,
                "attachment": _text(
                    value.get("attachment")
                    or value.get("routerFilePath")
                    or value.get("fileUrl")
                ),
                "source_status": _text(
                    value.get("source_status")
                    or value.get("routerStatus")
                    or value.get("stateCode")
                ),
                "remark": _text(value.get("remark")),
            }
        )
    return result


def _format_related_plan(plans: dict[str, str]) -> str:
    return "；".join(f"{key}={value}" for key, value in plans.items())


def _first_actual(
    events: list[dict[str, Any]], phases: set[str], nodes: set[str]
) -> str:
    for event in events:
        if (
            event["actual"]
            and event["phase"] in phases
            and event["node"] in nodes
        ):
            value = date_only(event["event_time"])
            if value:
                return value
    return ""


def _last_actual(
    events: list[dict[str, Any]], phases: set[str], nodes: set[str]
) -> str:
    for event in reversed(events):
        if (
            event["actual"]
            and event["phase"] in phases
            and event["node"] in nodes
        ):
            value = date_only(event["event_time"])
            if value:
                return value
    return ""


def normalize_tracking_details(
    *,
    fba: str,
    carrier: str,
    raw_events: Iterable[dict[str, Any]],
    carrier_order_no: str = "",
    transport_ref: str = "",
    structured: dict[str, Any] | None = None,
) -> TrackingDetails:
    structured = structured or {}
    now = current_time_text()
    raw = _as_raw_events(raw_events)
    raw.sort(key=lambda item: item["event_time"])
    parsed: list[dict[str, Any]] = []
    latest_plan_event: dict[str, int] = {}

    for item in raw:
        content = " ".join(
            part for part in (item["content"], item["remark"]) if part
        ).strip()
        phase, node, actual = _classify(content)
        plans = _extract_plans(content, item["event_time"])
        event = {
            **item,
            "content": content,
            "phase": phase,
            "node": node,
            "actual": actual,
            "plans": plans,
            "validity": "已取消" if "取消" in content else "当前有效",
            "invalidated_plans": set(),
            "exception_status": (
                "异常中"
                if _is_exception(content)
                else "已恢复"
                if _is_recovery(content)
                else "无异常"
            ),
            "transport_info": _transport_info(content),
        }
        parsed.append(event)
        if event["validity"] != "已取消":
            for target in plans:
                previous = latest_plan_event.get(target)
                if previous is not None:
                    parsed[previous]["invalidated_plans"].add(target)
                latest_plan_event[target] = len(parsed) - 1

    for item in parsed:
        if item["validity"] == "已取消" or not item["plans"]:
            continue
        invalidated_count = len(item["invalidated_plans"])
        if invalidated_count == len(item["plans"]):
            item["validity"] = "已被更新"
        elif invalidated_count:
            item["validity"] = "部分已更新"

    events: list[TrackingEvent] = []
    for item in parsed:
        events.append(
            TrackingEvent(
                event_id=_event_id(
                    carrier,
                    fba,
                    item["event_time"],
                    item["content"],
                    item["attachment"],
                ),
                fba=fba,
                carrier=carrier,
                carrier_order_no=carrier_order_no,
                event_time=item["event_time"],
                phase=item["phase"],
                node=item["node"],
                event_type=_event_type(
                    item["content"],
                    actual=item["actual"],
                    attachment=item["attachment"],
                ),
                content=item["content"],
                related_plan=_format_related_plan(item["plans"]),
                validity=item["validity"],
                exception_status=item["exception_status"],
                transport_info=item["transport_info"],
                attachment=item["attachment"],
                source_status=item["source_status"],
                first_seen=now,
                last_confirmed=now,
                updated_at=now,
            )
        )

    latest = parsed[-1] if parsed else {}
    active_exception = ""
    for item in parsed:
        if _is_exception(item["content"]):
            active_exception = item["content"]
        elif active_exception and (
            _is_recovery(item["content"])
            or item["node"] in {"实际出发", "实际到达", "提柜", "签收", "POD"}
        ):
            active_exception = ""

    latest_plans = {
        target: parsed[index]["plans"][target]
        for target, index in latest_plan_event.items()
    }

    event_transport_ref = ""
    for item in reversed(parsed):
        if item["transport_info"]:
            event_transport_ref = item["transport_info"]
            break

    pickup_event = _first_actual(
        parsed, {"接收"}, {"入仓/接收", "提货"}
    )
    departure_event = _first_actual(
        parsed, {"干线运输"}, {"实际出发"}
    )
    arrival_event = _first_actual(
        parsed, {"干线运输"}, {"实际到达"}
    )
    # “提取派送”用于业务侧判断货物最近一次真正开始派送的日期。
    # 预约可能取消、提前或推后，因此只要存在实际“派送中”，就采用
    # 时间线上最后一次“派送中”；没有该节点时才沿用原有末端节点兜底。
    last_mile_event = _last_actual(
        parsed,
        {"末端配送"},
        {"派送中"},
    ) or _first_actual(
        parsed,
        {"目的地处理", "末端配送"},
        {"提柜", "货物提取", "递交末端承运商"},
    )
    if not last_mile_event:
        # 超鸿等货代不提供提柜/递交节点时，“已入海外仓”已经能够证明
        # 货物完成港后提取并进入末端链路，作为保守的提取派送时间。
        last_mile_event = _first_actual(
            parsed,
            {"目的地处理"},
            {"目的仓入库"},
        )
    signed_event = _first_actual(parsed, {"完成"}, {"签收"})
    pod_present = any(
        event.attachment
        or event.node == "POD"
        or "POD" in event.content.upper()
        for event in events
    )

    snapshot = TrackingSnapshot(
        transport_ref=event_transport_ref or transport_ref,
        current_phase=_text(latest.get("phase")),
        current_node=_text(latest.get("node")),
        latest_time=_text(latest.get("event_time")),
        latest_event=_text(latest.get("content")),
        current_exception=active_exception,
        pickup_time=pickup_event or date_only(structured.get("pickup_time")),
        estimated_departure=(
            latest_plans.get("预计出发")
            or date_only(structured.get("estimated_departure"))
        ),
        actual_departure=(
            date_only(structured.get("actual_departure")) or departure_event
        ),
        estimated_arrival=(
            latest_plans.get("预计到达")
            or date_only(structured.get("estimated_arrival"))
        ),
        actual_arrival=(
            date_only(structured.get("actual_arrival")) or arrival_event
        ),
        estimated_delivery=(
            latest_plans.get("预计送达")
            or date_only(structured.get("estimated_delivery"))
        ),
        last_mile_time=last_mile_event
        or date_only(structured.get("last_mile_time")),
        signed_time=date_only(structured.get("signed_time")) or signed_event,
        pod_status="已提供" if pod_present else "未提供",
        data_status="正常" if parsed else "信息不足",
        updated_time=now,
    )
    return TrackingDetails(snapshot=snapshot, events=tuple(events))


def minimal_tracking_details(
    fba: str,
    carrier: str,
    latest_time: str,
    latest_event: str,
) -> TrackingDetails:
    return normalize_tracking_details(
        fba=fba,
        carrier=carrier,
        raw_events=[
            {
                "event_time": latest_time,
                "content": latest_event,
                "source_status": "latest",
            }
        ],
    )

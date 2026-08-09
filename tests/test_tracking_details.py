from g_team_ops.tracking_details import (
    date_only,
    normalize_tracking_details,
)


def test_anda_timeline_separates_estimates_from_actual_milestones():
    details = normalize_tracking_details(
        fba="FBA_TEST_ANDA",
        carrier="安达",
        raw_events=[
            {
                "traceTime": "2026-07-21 16:02:14",
                "traceName": "已签收",
            },
            {
                "traceTime": "2026-06-17 12:28:57",
                "traceName": "已入仓(港前)",
            },
            {
                "traceTime": "2026-06-22 11:28:00",
                "traceName": (
                    "已装柜，船名航次KAIMANA HILA 074E，"
                    "预计开船时间2026-06-24"
                ),
            },
            {
                "traceTime": "2026-07-01 09:22:05",
                "traceName": (
                    "起运港查验已放行。新的船名航次MATSON MAUI 038E，"
                    "预计开船时间7月2日，到港时间7月13日"
                ),
            },
            {
                "traceTime": "2026-07-02 00:00:00",
                "traceName": "已开船，预计到港时间2026-07-13",
            },
            {
                "traceTime": "2026-07-14 00:00:00",
                "traceName": "已到港，等待卸船中",
            },
            {
                "traceTime": "2026-07-16 15:21:30",
                "traceName": "已提柜，待拆柜",
            },
            {
                "traceTime": "2026-07-17 16:42:06",
                "traceName": "派送中，预计签收时间2026-07-21",
            },
            {
                "traceTime": "2026-07-22 16:08:38",
                "traceName": "已上传POD",
                "fileUrl": "https://example.test/pod.pdf",
            },
        ],
    )

    snapshot = details.snapshot
    assert snapshot.transport_ref == "MATSON MAUI 038E"
    assert snapshot.pickup_time == "2026-06-17"
    assert snapshot.estimated_departure == "2026-07-02"
    assert snapshot.actual_departure == "2026-07-02"
    assert snapshot.estimated_arrival == "2026-07-13"
    assert snapshot.actual_arrival == "2026-07-14"
    assert snapshot.estimated_delivery == "2026-07-21"
    assert snapshot.last_mile_time == "2026-07-17"
    assert snapshot.signed_time == "2026-07-21"
    assert snapshot.pod_status == "已提供"
    assert snapshot.current_phase == "完成"
    assert snapshot.current_node == "POD"
    assert snapshot.current_exception == ""

    old_plan = next(
        event for event in details.events if "KAIMANA HILA" in event.content
    )
    newest_plan = next(
        event for event in details.events if "MATSON MAUI" in event.content
    )
    assert old_plan.validity == "已被更新"
    assert newest_plan.validity == "部分已更新"
    assert "预计出发=2026-07-02" in newest_plan.related_plan


def test_yitong_nodes_map_to_the_same_universal_milestones():
    details = normalize_tracking_details(
        fba="FBA_TEST_YITONG",
        carrier="易通",
        raw_events=[
            {"timestamp": "2026-06-01 08:00:00", "content": "进仓"},
            {"timestamp": "2026-06-03 09:00:00", "content": "装柜"},
            {
                "timestamp": "2026-06-04 10:00:00",
                "content": "出口放行，ETD：2026-06-06，ETA：2026-06-20",
            },
            {
                "timestamp": "2026-06-06 12:00:00",
                "content": "已开船，ETA：2026-06-20，V.V: TEST 001E",
            },
            {"timestamp": "2026-06-20 08:00:00", "content": "到港"},
            {"timestamp": "2026-06-21 08:00:00", "content": "已卸船，清关中"},
            {"timestamp": "2026-06-23 08:00:00", "content": "预约提柜"},
            {"timestamp": "2026-06-24 08:00:00", "content": "已提柜"},
            {"timestamp": "2026-06-25 08:00:00", "content": "拆柜"},
            {
                "timestamp": "2026-06-26 08:00:00",
                "content": "预约派送，预计送达2026-06-28",
            },
            {"timestamp": "2026-06-27 08:00:00", "content": "递交快递"},
            {"timestamp": "2026-06-28 15:00:00", "content": "已签收"},
        ],
    )

    snapshot = details.snapshot
    assert snapshot.transport_ref == "TEST 001E"
    assert snapshot.pickup_time == "2026-06-01"
    assert snapshot.actual_departure == "2026-06-06"
    assert snapshot.actual_arrival == "2026-06-20"
    assert snapshot.last_mile_time == "2026-06-24"
    assert snapshot.signed_time == "2026-06-28"
    assert snapshot.estimated_delivery == "2026-06-28"


def test_chaohong_reverse_date_wording_and_pod_signature_are_parsed():
    details = normalize_tracking_details(
        fba="FBA_TEST_CHAOHONG",
        carrier="超鸿",
        raw_events=[
            {
                "happened_at": "2026-04-10T01:30:00.000Z",
                "detail": (
                    "已装柜，预计2026/4/13开船，"
                    "船名航次：OOCL LAVENDER 002E"
                ),
            },
            {
                "happened_at": "2026-04-13T01:00:00.000Z",
                "detail": (
                    "已开船，预计美国时间2026/5/12到港"
                    "（具体时间以官网为准）"
                ),
            },
            {
                "happened_at": "2026-05-12T08:25:00.000Z",
                "detail": (
                    "晚到港，预计美国时间2026/5/17到港"
                    "（具体时间以官网为准）"
                ),
            },
            {
                "happened_at": "2026-05-18T06:50:00.000Z",
                "detail": "已到目的港",
            },
            {
                "happened_at": "2026-05-21T02:30:00.000Z",
                "detail": "已入海外仓",
            },
            {
                "happened_at": "2026-05-23T07:57:00.000Z",
                "detail": "已签收 POD已回传",
            },
        ],
    )

    snapshot = details.snapshot
    assert snapshot.estimated_departure == "2026-04-13"
    assert snapshot.actual_departure == "2026-04-13"
    assert snapshot.estimated_arrival == "2026-05-17"
    assert snapshot.actual_arrival == "2026-05-18"
    assert snapshot.last_mile_time == "2026-05-21"
    assert snapshot.signed_time == "2026-05-23"
    assert snapshot.pod_status == "已提供"
    assert snapshot.current_node == "签收"

    first_eta = next(
        event for event in details.events if "2026/5/12" in event.content
    )
    latest_eta = next(
        event for event in details.events if "2026/5/17" in event.content
    )
    assert first_eta.validity == "已被更新"
    assert "预计到达=2026-05-12" in first_eta.related_plan
    assert "预计到达=2026-05-17" in latest_eta.related_plan


def test_last_mile_uses_latest_actual_dispatch_after_rescheduling():
    details = normalize_tracking_details(
        fba="FBA_TEST_REDISPATCH",
        carrier="安达",
        raw_events=[
            {
                "event_time": "2026-07-20 09:00:00",
                "content": "预约派送，预计送达2026-07-22",
            },
            {
                "event_time": "2026-07-21 08:00:00",
                "content": "派送中",
            },
            {
                "event_time": "2026-07-21 16:00:00",
                "content": "预约取消，重新安排派送",
            },
            {
                "event_time": "2026-07-24 10:00:00",
                "content": "派送中，预计签收时间2026-07-25",
            },
        ],
    )

    assert details.snapshot.last_mile_time == "2026-07-24"
    assert details.snapshot.estimated_delivery == "2026-07-25"


def test_recovery_event_clears_an_active_exception():
    details = normalize_tracking_details(
        fba="FBA_TEST_RECOVERY",
        carrier="安达",
        raw_events=[
            {
                "event_time": "2026-07-01 09:00:00",
                "content": "起运港海关查验，暂未放行",
            },
            {
                "event_time": "2026-07-02 09:00:00",
                "content": "查验完毕，已报关放行",
            },
        ],
    )

    assert details.snapshot.current_exception == ""
    assert details.events[0].exception_status == "异常中"
    assert details.events[1].exception_status == "已恢复"
    assert "恢复" in details.events[1].event_type


def test_date_only_supports_numeric_and_chinese_dates():
    assert date_only("2026-07-20 10:00:00") == "2026-07-20"
    assert date_only("计划2026年7月21日") == "2026-07-21"
    assert date_only("只有7月21日") == ""

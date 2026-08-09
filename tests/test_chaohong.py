import requests

from g_team_ops.chaohong import ChaoHongClient, ChaoHongQueryService
from g_team_ops.models import QueryStatus


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_uses_single_official_batch_request_for_multiple_fbas():
    session = FakeSession(
        [
            FakeResponse(
                payload={
                    "code": 0,
                    "data": [
                        {"tag_no": "FBA111", "happened_at": "2026-07-19T01:00:00Z", "detail": "已到港"},
                        {"trace_no": "FBA222", "happened_at": "2026-07-19T02:00:00Z", "detail": "派送中"},
                    ],
                }
            )
        ]
    )
    service = ChaoHongQueryService(ChaoHongClient(session=session, retries=0))
    results = service.query_many(["FBA111", "FBA222", "FBA333"])
    assert len(session.calls) == 1
    assert [item.status for item in results] == [QueryStatus.SUCCESS, QueryStatus.SUCCESS, QueryStatus.NOT_FOUND]
    assert results[0].carrier == "超鸿"


def test_network_error_retries_finitely_and_marks_every_item_failed():
    session = FakeSession([requests.Timeout(), requests.Timeout()])
    service = ChaoHongQueryService(
        ChaoHongClient(session=session, retries=1, backoff_seconds=0, sleeper=lambda _: None)
    )
    results = service.query_many(["FBA111", "FBA222"])
    assert len(session.calls) == 2
    assert all(item.status == QueryStatus.FAILED for item in results)
    assert all(item.error_category == "network" for item in results)


def test_large_chaohong_input_is_split_to_keep_request_urls_safe():
    session = FakeSession(
        [FakeResponse(payload={"code": 101, "data": []}) for _ in range(3)]
    )
    service = ChaoHongQueryService(
        ChaoHongClient(session=session, retries=0),
        batch_size=50,
        request_interval=0,
    )
    results = service.query_many([f"FBA{index:05d}" for index in range(101)])
    assert len(session.calls) == 3
    assert len(results) == 101
    assert all(item.status == QueryStatus.NOT_FOUND for item in results)


def test_detail_timeline_and_pod_are_normalized():
    session = FakeSession(
        [
            FakeResponse(
                payload={
                    "code": 0,
                    "data": {
                        "tag_no": "FBA11111",
                        "no": "CH-1",
                        "trace_second_no": "UPS-1",
                        "receiver_info": {
                            "city": "ONTARIO",
                            "country_cn": "美国",
                        },
                        "traces": [
                            {
                                "happened_at": "2026-07-01 08:00:00",
                                "place": "上海",
                                "detail": "已入中国仓",
                            },
                            {
                                "happened_at": "2026-07-20 08:00:00",
                                "place": "ONTARIO",
                                "detail": "已签收",
                            },
                        ],
                        "pod_webimgurl": "http://example.test/pod.jpg",
                    },
                }
            )
        ]
    )
    service = ChaoHongQueryService(
        ChaoHongClient(session=session, retries=0)
    )
    details = service.fetch_tracking_details("FBA11111")
    assert details.snapshot.pickup_time == "2026-07-01"
    assert details.snapshot.signed_time == "2026-07-20"
    assert details.snapshot.pod_status == "已提供"
    assert any(event.attachment for event in details.events)

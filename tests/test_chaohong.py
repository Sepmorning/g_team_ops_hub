import requests

from anda_tracker.chaohong import ChaoHongClient, ChaoHongQueryService
from anda_tracker.models import QueryStatus


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


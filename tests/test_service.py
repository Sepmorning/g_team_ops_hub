from anda_tracker.errors import AuthenticationError, NetworkError
from anda_tracker.models import QueryStatus
from anda_tracker.service import AndaQueryService


class StubClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.batches = []

    def query_batch(self, batch):
        self.batches.append(list(batch))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_query_splits_batches_preserves_order_and_marks_missing():
    client = StubClient(
        [
            [{"fbaCode": "FBA111", "latestTraceTime": "2026-01-01", "latestTraceName": "已开船"}],
            [{"fbaCode": "FBA333", "stateName": "运输中"}],
        ]
    )
    service = AndaQueryService(client, batch_size=2, request_interval=0, sleeper=lambda _: None)
    results = service.query_many(["FBA111", "FBA222", "FBA333"])
    assert client.batches == [["FBA111", "FBA222"], ["FBA333"]]
    assert [item.fba for item in results] == ["FBA111", "FBA222", "FBA333"]
    assert [item.status for item in results] == [QueryStatus.SUCCESS, QueryStatus.NOT_FOUND, QueryStatus.SUCCESS]


def test_failed_batch_does_not_block_next_batch():
    client = StubClient(
        [
            NetworkError("临时网络错误"),
            [{"fbaCode": "FBA333", "latestTraceName": "已到港"}],
        ]
    )
    service = AndaQueryService(client, batch_size=2, request_interval=0, sleeper=lambda _: None)
    results = service.query_many(["FBA111", "FBA222", "FBA333"])
    assert results[0].status == QueryStatus.FAILED
    assert results[0].error_category == "network"
    assert results[1].status == QueryStatus.FAILED
    assert results[2].status == QueryStatus.SUCCESS


def test_expired_anda_session_relogs_once_and_retries_batch():
    client = StubClient(
        [
            AuthenticationError("会话失效"),
            [{"fbaCode": "FBA111", "latestTraceName": "已到港"}],
        ]
    )
    relogins = []
    service = AndaQueryService(
        client,
        batch_size=50,
        request_interval=0,
        reauthenticate=lambda: relogins.append("login"),
    )
    results = service.query_many(["FBA111"])
    assert relogins == ["login"]
    assert client.batches == [["FBA111"], ["FBA111"]]
    assert results[0].status == QueryStatus.SUCCESS

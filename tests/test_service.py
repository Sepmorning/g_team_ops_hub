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


class DetailStubClient(StubClient):
    def __init__(self, responses, trace_groups):
        super().__init__(responses)
        self.trace_groups = trace_groups
        self.trace_numbers = []

    def get_trace_list(self, trace_no):
        self.trace_numbers.append(trace_no)
        return self.trace_groups


class ExpiringDetailStubClient(DetailStubClient):
    def __init__(self, responses, trace_responses):
        super().__init__(responses, [])
        self.trace_responses = list(trace_responses)

    def get_trace_list(self, trace_no):
        self.trace_numbers.append(trace_no)
        response = self.trace_responses.pop(0)
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


def test_expired_anda_session_relogs_once_and_retries_tracking_detail():
    record = {
        "fbaCode": "FBA11111",
        "traceNo": "TRACE-1",
        "latestTraceName": "已到港",
    }
    client = ExpiringDetailStubClient(
        [[record]],
        [
            AuthenticationError("会话失效"),
            [
                {
                    "list": [
                        {
                            "traceTime": "2026-07-03 10:00:00",
                            "traceName": "已到港",
                        }
                    ]
                }
            ],
        ],
    )
    relogins = []
    service = AndaQueryService(
        client,
        request_interval=0,
        reauthenticate=lambda: relogins.append("login"),
    )

    service.query_many(["FBA11111"])
    details = service.fetch_tracking_details("FBA11111")

    assert relogins == ["login"]
    assert client.trace_numbers == ["TRACE-1", "TRACE-1"]
    assert details.snapshot.actual_arrival == "2026-07-03"


def test_anda_detail_uses_structured_fields_and_full_timeline():
    record = {
        "fbaCode": "FBA11111",
        "traceNo": "TRACE-1",
        "latestTraceTime": "2026-07-03 10:00:00",
        "latestTraceName": "已到港",
        "warehouseInTime": "2026-06-01 08:00:00",
        "etd": "2026-06-10",
        "atd": "2026-06-11",
        "eta": "2026-07-02",
        "ata": "2026-07-03",
        "fbaWarehouseCode": "ONT8",
        "vesselName": "TEST",
        "voyageNo": "001E",
    }
    client = DetailStubClient(
        [[record]],
        [
            {
                "list": [
                    {
                        "traceTime": "2026-06-01 08:00:00",
                        "traceName": "已入仓(港前)",
                    },
                    {
                        "traceTime": "2026-07-03 10:00:00",
                        "traceName": "已到港",
                    },
                ]
            }
        ],
    )
    service = AndaQueryService(
        client,
        request_interval=0,
        sleeper=lambda _: None,
    )
    service.query_many(["FBA11111"])
    details = service.fetch_tracking_details("FBA11111")

    assert client.trace_numbers == ["TRACE-1"]
    assert details.snapshot.transport_ref == "TEST / 001E"
    assert details.snapshot.pickup_time == "2026-06-01"
    assert details.snapshot.actual_departure == "2026-06-11"
    assert details.snapshot.actual_arrival == "2026-07-03"
    assert len(details.events) == 2

import json

import pytest
import requests

from anda_tracker.airscript import (
    AirScriptClient,
    AirScriptConfig,
    AIRSCRIPT_RICH_WRITE_BATCH_SIZE,
    AIRSCRIPT_WRITE_BATCH_SIZE,
    validate_webhook_url,
)
from anda_tracker.errors import AuthenticationError, ConfigurationError, NetworkError, ResponseError
from anda_tracker.models import (
    QueryStatus,
    TrackingEvent,
    TrackingResult,
    TrackingSnapshot,
)


WEBHOOK = "https://www.kdocs.cn/api/v3/ide/file/file-id/script/script-id/sync_task"


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def config(token="placeholder-airscript-token"):
    return AirScriptConfig(
        "https://www.kdocs.cn/l/share123",
        WEBHOOK,
        token,
    )


def finished(result):
    if isinstance(result, dict) and result.get("success") is True:
        result = {
            "schemaVersion": 5,
            "detailSheetName": "US-轨迹明细",
            **result,
        }
    return {"status": "finished", "error": "", "data": {"result": result}}


def tracking(fba, event, status=QueryStatus.SUCCESS):
    return TrackingResult(
        fba=fba,
        status=status,
        carrier="安达",
        latest_time="2026-07-20 10:00",
        latest_event=event,
    )


def test_webhook_must_be_official_sync_task_url():
    assert validate_webhook_url(WEBHOOK) == WEBHOOK
    with pytest.raises(ConfigurationError):
        validate_webhook_url("http://www.kdocs.cn/api/v3/ide/file/x/script/y/sync_task")
    with pytest.raises(ConfigurationError):
        validate_webhook_url("https://example.com/api/v3/ide/file/x/script/y/sync_task")
    with pytest.raises(ConfigurationError):
        validate_webhook_url("https://www.kdocs.cn/not-a-webhook")


def test_validate_reads_auto_detected_columns_and_never_places_token_in_body():
    session = FakeSession(
        [
            FakeResponse(
                body=finished(
                    {
                        "success": True,
                        "sheetName": "US-FBA",
                        "columns": {
                            "fba": "E",
                            "carrier": "G",
                            "route": "Q",
                            "completion": "F",
                        },
                    }
                )
            )
        ]
    )
    client = AirScriptClient(config(), session=session)
    binding = client.validate()
    assert binding.fba_column == "E"
    assert binding.route_column == "Q"
    assert binding.completion_column == "F"
    assert binding.carrier_column == "G"
    _, kwargs = session.calls[0]
    assert kwargs["headers"]["AirScript-Token"] == "placeholder-airscript-token"
    assert "placeholder-airscript-token" not in json.dumps(kwargs["json"])
    assert kwargs["json"]["Context"]["argv"]["action"] == "validate"


def test_validate_accepts_json_string_result():
    result = json.dumps(
        {
            "success": True,
            "schemaVersion": 5,
            "detailSheetName": "US-轨迹明细",
            "sheetName": "US-FBA",
            "columns": {
                "fba": "A",
                "carrier": "C",
                "route": "I",
                "completion": "B",
            },
        }
    )
    client = AirScriptClient(config(), session=FakeSession([FakeResponse(body=finished(result))]))
    assert client.validate().route_column == "I"


def test_pending_fbas_are_read_in_pages_and_deduplicated():
    session = FakeSession(
        [
            FakeResponse(
                body=finished(
                    {
                        "success": True,
                        "fbas": [
                            {"fba": "FBA11111", "carrier": "安达物流"},
                            {"fba": "FBA22222", "carrier": "超鸿"},
                        ],
                        "hasMore": True,
                        "nextOffset": 2,
                    }
                )
            ),
            FakeResponse(
                body=finished(
                    {
                        "success": True,
                        "fbas": [
                            {"fba": "FBA22222", "carrier": "超鸿"},
                            {"fba": "FBA33333", "carrier": "易通美森"},
                        ],
                        "hasMore": False,
                        "nextOffset": 4,
                    }
                )
            ),
        ]
    )
    client = AirScriptClient(config(), session=session)
    values = client.list_pending_tracking_items(page_size=2)
    assert [(item.fba, item.carrier) for item in values] == [
        ("FBA11111", "安达物流"),
        ("FBA22222", "超鸿"),
        ("FBA33333", "易通美森"),
    ]
    argv = [call[1]["json"]["Context"]["argv"] for call in session.calls]
    assert [item["action"] for item in argv] == ["list_pending", "list_pending"]
    assert [item["offset"] for item in argv] == [0, 2]


def test_sync_sends_only_successful_results_and_maps_summary():
    session = FakeSession(
        [
            FakeResponse(
                body=finished(
                    {
                        "success": True,
                        "updated": ["FBA11111"],
                        "unchanged": ["FBA22222"],
                        "notInSheet": ["FBA33333"],
                        "duplicateRows": ["FBA44444"],
                        "failures": [],
                    }
                )
            )
        ]
    )
    client = AirScriptClient(config(), session=session)
    summary = client.sync_tracking_results(
        [
            tracking("FBA11111", "已到港"),
            tracking("FBA00000", "冲突", QueryStatus.CONFLICT),
        ]
    )
    assert summary.updated == ["FBA11111"]
    assert summary.not_in_sheet == ["FBA33333"]
    assert summary.duplicate_rows == ["FBA44444"]
    assert summary.skipped == ["FBA00000"]
    items = session.calls[0][1]["json"]["Context"]["argv"]["items"]
    assert items == [{"fba": "FBA11111", "route": "2026-07-20 10:00 已到港"}]


def test_sync_total_is_unlimited_and_webhook_calls_are_split_to_fifty():
    response_one = {
        "success": True,
        "updated": [f"FBA{index:05d}" for index in range(50)],
        "unchanged": [],
        "notInSheet": [],
        "duplicateRows": [],
        "failures": [],
    }
    response_two = {
        "success": True,
        "updated": ["FBA00050"],
        "unchanged": [],
        "notInSheet": [],
        "duplicateRows": [],
        "failures": [],
    }
    session = FakeSession(
        [FakeResponse(body=finished(response_one)), FakeResponse(body=finished(response_two))]
    )
    client = AirScriptClient(config(), session=session)
    results = [
        tracking(f"FBA{index:05d}", "事件")
        for index in range(AIRSCRIPT_WRITE_BATCH_SIZE + 1)
    ]
    summary = client.sync_tracking_results(results)
    assert len(session.calls) == 2
    assert [
        len(call[1]["json"]["Context"]["argv"]["items"])
        for call in session.calls
    ] == [50, 1]
    assert all(
        call[1]["json"]["Context"]["argv"]["action"] == "sync"
        for call in session.calls
    )
    assert len(summary.updated) == 51


def test_rich_sync_sends_snapshot_and_deduplicated_event_payload():
    session = FakeSession(
        [
            FakeResponse(
                body=finished(
                    {
                        "success": True,
                        "updated": ["FBA11111"],
                        "unchanged": [],
                        "notInSheet": [],
                        "duplicateRows": [],
                        "failures": [],
                        "conflicts": ["FBA11111：到港"],
                        "eventsAdded": 1,
                        "eventsUpdated": 2,
                        "eventsUnchanged": 3,
                    }
                )
            )
        ]
    )
    result = TrackingResult(
        fba="FBA11111",
        status=QueryStatus.SUCCESS,
        carrier="安达",
        latest_time="2026-07-20 10:00",
        latest_event="已到港",
        snapshot=TrackingSnapshot(
            current_phase="干线运输",
            current_node="实际到达",
            actual_arrival="2026-07-20",
        ),
        events=(
            TrackingEvent(
                event_id="event-1",
                fba="FBA11111",
                carrier="安达",
                event_time="2026-07-20 10:00",
                phase="干线运输",
                node="实际到达",
                event_type="实际",
                content="已到港",
            ),
        ),
    )
    client = AirScriptClient(config(), session=session)
    summary = client.sync_tracking_results([result])

    argv = session.calls[0][1]["json"]["Context"]["argv"]
    assert argv["action"] == "sync_tracking"
    assert argv["items"][0]["main"]["actual_arrival"] == "2026-07-20"
    assert argv["items"][0]["main"]["route"] == "2026-07-20 10:00 已到港"
    assert argv["items"][0]["events"][0]["event_id"] == "event-1"
    assert summary.conflicts == ["FBA11111：到港"]
    assert summary.events_added == 1
    assert summary.events_updated == 2
    assert summary.events_unchanged == 3


def test_rich_sync_batches_are_kept_small():
    count = AIRSCRIPT_RICH_WRITE_BATCH_SIZE + 1
    responses = [
        FakeResponse(
            body=finished(
                {
                    "success": True,
                    "updated": [],
                    "unchanged": [],
                    "notInSheet": [],
                    "duplicateRows": [],
                    "failures": [],
                }
            )
        )
        for _ in range(2)
    ]
    session = FakeSession(responses)
    values = [
        TrackingResult(
            fba=f"FBA{index:05d}",
            status=QueryStatus.SUCCESS,
            carrier="安达",
            latest_time="2026-07-20",
            latest_event="已受理",
            snapshot=TrackingSnapshot(current_phase="接收"),
        )
        for index in range(count)
    ]
    AirScriptClient(config(), session=session).sync_tracking_results(values)
    assert [
        len(call[1]["json"]["Context"]["argv"]["items"])
        for call in session.calls
    ] == [AIRSCRIPT_RICH_WRITE_BATCH_SIZE, 1]


def test_old_airscript_schema_is_rejected_with_upgrade_message():
    body = {
        "status": "finished",
        "error": "",
        "data": {
            "result": {
                "success": True,
                "schemaVersion": 2,
                "detailSheetName": "物流轨迹明细",
                "sheetName": "US-FBA",
                "columns": {
                    "fba": "A",
                    "carrier": "B",
                    "route": "C",
                    "completion": "D",
                },
            }
        },
    }
    client = AirScriptClient(config(), session=FakeSession([FakeResponse(body=body)]))
    with pytest.raises(ResponseError, match="版本过旧"):
        client.validate()


def test_authentication_and_script_errors_are_classified():
    client = AirScriptClient(config(), session=FakeSession([FakeResponse(status_code=403)]))
    with pytest.raises(AuthenticationError):
        client.validate()

    body = finished({"success": False, "message": "第一行没有找到FBA表头"})
    client = AirScriptClient(config(), session=FakeSession([FakeResponse(body=body)]))
    with pytest.raises(ResponseError, match="FBA表头"):
        client.validate()


def test_network_failure_is_retried_and_classified(monkeypatch):
    monkeypatch.setattr("anda_tracker.airscript.time.sleep", lambda *_args: None)
    session = FakeSession([requests.ConnectionError("offline"), requests.ConnectionError("offline")])
    client = AirScriptClient(config(), session=session, retries=1)
    with pytest.raises(NetworkError):
        client.validate()
    assert len(session.calls) == 2

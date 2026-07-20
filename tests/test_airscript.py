import json

import pytest
import requests

from anda_tracker.airscript import (
    AirScriptClient,
    AirScriptConfig,
    MAX_AIRSCRIPT_ITEMS,
    validate_webhook_url,
)
from anda_tracker.errors import AuthenticationError, ConfigurationError, NetworkError, ResponseError
from anda_tracker.models import QueryStatus, TrackingResult


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
                        "columns": {"fba": "E", "route": "Q"},
                    }
                )
            )
        ]
    )
    client = AirScriptClient(config(), session=session)
    binding = client.validate()
    assert binding.fba_column == "E"
    assert binding.route_column == "Q"
    _, kwargs = session.calls[0]
    assert kwargs["headers"]["AirScript-Token"] == "placeholder-airscript-token"
    assert "placeholder-airscript-token" not in json.dumps(kwargs["json"])
    assert kwargs["json"]["Context"]["argv"]["action"] == "validate"


def test_validate_accepts_json_string_result():
    result = json.dumps(
        {
            "success": True,
            "sheetName": "US-FBA",
            "columns": {"fba": "A", "route": "I"},
        }
    )
    client = AirScriptClient(config(), session=FakeSession([FakeResponse(body=finished(result))]))
    assert client.validate().route_column == "I"


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


def test_sync_enforces_system_safety_limit():
    client = AirScriptClient(config(), session=FakeSession([]))
    results = [tracking(f"FBA{index:05d}", "事件") for index in range(MAX_AIRSCRIPT_ITEMS + 1)]
    with pytest.raises(ConfigurationError, match="50"):
        client.sync_tracking_results(results)


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

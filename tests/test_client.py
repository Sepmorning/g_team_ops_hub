import requests

from g_team_ops.client import AndaClient, TRACKING_QUERY_FIELDS
from g_team_ops.errors import AuthenticationError, NetworkError, ResponseError


class FakeResponse:
    def __init__(self, status_code=200, payload=None, json_error=False):
        self.status_code = status_code
        self.payload = payload
        self.json_error = json_error

    def json(self):
        if self.json_error:
            raise ValueError("bad json")
        return self.payload


class FakeSession:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_login_success_keeps_token_only_in_memory():
    session = FakeSession([FakeResponse(payload={"success": True, "result": {"code": "SUCCESS", "token": "token-x"}})])
    client = AndaClient(session=session, retries=0)
    client.login("placeholder-user", "placeholder-password")
    assert client.token == "token-x"
    sent = session.calls[0][1]["json"]
    assert sent["password"] != "placeholder-password"


def test_http_auth_failure_is_classified():
    client = AndaClient(session=FakeSession([FakeResponse(status_code=401)]), retries=0)
    try:
        client.login("placeholder-user", "placeholder-password")
    except AuthenticationError as exc:
        assert exc.category == "authentication"
    else:
        raise AssertionError("expected AuthenticationError")


def test_timeout_retries_a_finite_number_of_times():
    session = FakeSession([requests.Timeout(), requests.Timeout(), requests.Timeout()])
    client = AndaClient(session=session, retries=2, backoff_seconds=0, sleeper=lambda _: None)
    try:
        client.login("placeholder-user", "placeholder-password")
    except NetworkError as exc:
        assert exc.category == "network"
    else:
        raise AssertionError("expected NetworkError")
    assert len(session.calls) == 3


def test_invalid_json_is_response_error_without_retry():
    session = FakeSession([FakeResponse(json_error=True)])
    client = AndaClient(session=session, retries=2, sleeper=lambda _: None)
    try:
        client.login("placeholder-user", "placeholder-password")
    except ResponseError as exc:
        assert exc.category == "response"
    else:
        raise AssertionError("expected ResponseError")
    assert len(session.calls) == 1


def test_malformed_login_result_is_safely_classified():
    session = FakeSession([FakeResponse(payload={"success": True, "result": []})])
    client = AndaClient(session=session, retries=0)
    try:
        client.login("placeholder-user", "placeholder-password")
    except AuthenticationError as exc:
        assert exc.category == "authentication"
    else:
        raise AssertionError("expected AuthenticationError")


def test_anda_query_explicitly_requests_fba_and_full_tracking_fields():
    from g_team_ops.service import AndaQueryService
    from g_team_ops.models import QueryStatus

    class ProjectedSession:
        def post(self, url, **kwargs):
            payload = kwargs["json"]
            assert payload["conditionDtos"][0]["value"] == "FBA12345"
            assert payload["conditionDtos"][1] == {
                "field": "clientReturnStatus", "operator": "not_equal", "value": 20}
            row = {"jobId": "job-test", "shipmentId": "shipment-test"}
            # Reproduce the new API: unrequested columns are omitted, even for a hit.
            values = {"fbaCode": "FBA12345", "traceNo": "trace-test",
                "latestTraceTime": "2026-09-12 09:38:00", "latestTraceName": "已提柜，待拆柜"}
            row.update({key: value for key, value in values.items() if key in payload.get("fields", [])})
            assert set(TRACKING_QUERY_FIELDS) <= set(payload.get("fields", []))
            return FakeResponse(payload={"success": True, "result": {"records": [row]}})

    client = AndaClient(session=ProjectedSession(), retries=0)
    client.token = "test-token"
    service = AndaQueryService(client, request_interval=0)
    result = service.query_many(["FBA12345"])[0]
    assert result.status == QueryStatus.SUCCESS
    assert result.latest_event == "已提柜，待拆柜"
    assert service.last_records["FBA12345"]["traceNo"] == "trace-test"

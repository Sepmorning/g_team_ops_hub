import requests

from anda_tracker.client import AndaClient
from anda_tracker.errors import AuthenticationError, NetworkError, ResponseError


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

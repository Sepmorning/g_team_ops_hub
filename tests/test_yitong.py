import requests
from datetime import date, timedelta

from g_team_ops.errors import AuthenticationError, NetworkError
from g_team_ops.models import QueryStatus
from g_team_ops.yitong import CaptchaChallenge, YiTongClient, YiTongQueryService


class FakeResponse:
    def __init__(self, payload=None, status_code=200, content=b"", content_type="application/json"):
        self.payload = payload
        self.status_code = status_code
        self.content = content
        self.headers = {"Content-Type": content_type}

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, request_outcomes=None, get_outcomes=None):
        self.request_outcomes = list(request_outcomes or [])
        self.get_outcomes = list(get_outcomes or [])
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        outcome = self.request_outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def get(self, url, **kwargs):
        self.calls.append(("GET_IMAGE", url, kwargs))
        outcome = self.get_outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def challenge():
    return CaptchaChallenge("identity-x", True, b"png", "company", "logo")


def test_fetches_company_config_and_captcha_image():
    session = FakeSession(
        request_outcomes=[
            FakeResponse({"code": 2000, "model": {"identity": "id-x", "verifiCode": True}})
        ],
        get_outcomes=[FakeResponse(content=b"image", content_type="image/png")],
    )
    result = YiTongClient(session=session, retries=0).fetch_captcha()
    assert result.identity == "id-x"
    assert result.image_bytes == b"image"
    assert session.calls[0][2]["json"] == {"webSite": "http://c.etton-log.com"}


def test_login_sends_manual_captcha_and_keeps_returned_token():
    session = FakeSession(request_outcomes=[FakeResponse({"code": 2000, "token": "token-x"})])
    client = YiTongClient(session=session, retries=0)
    client.login("placeholder-user", "placeholder-password", "AB12", challenge())
    sent = session.calls[0][2]["json"]
    assert sent["verificationCode"] == "AB12"
    assert sent["identity"] == "identity-x"
    assert client.token == "token-x"


def test_validates_saved_token_using_bearer_header():
    session = FakeSession(request_outcomes=[FakeResponse({"code": 2000, "model": {}})])
    client = YiTongClient(session=session, retries=0)
    client.token = "saved-token"
    client.validate_token()
    assert session.calls[0][2]["headers"]["Authorization"] == "Bearer saved-token"


def test_query_maps_fba_list_and_latest_route():
    session = FakeSession(
        request_outcomes=[
            FakeResponse(
                {
                    "code": 2000,
                    "list": [
                        {
                            "fbaNoList": '["FBA111", "FBA222"]',
                            "routerTime": "2026-07-19 10:00",
                            "routerInformation": "已到港",
                        }
                    ],
                }
            )
        ]
    )
    client = YiTongClient(session=session, retries=0)
    client.token = "token"
    results = YiTongQueryService(client, sleeper=lambda _: None).query_many(
        ["FBA111", "FBA222", "FBA333"]
    )
    assert [item.status for item in results] == [
        QueryStatus.SUCCESS,
        QueryStatus.SUCCESS,
        QueryStatus.NOT_FOUND,
    ]
    assert results[0].latest_event == "已到港"
    payload = session.calls[0][2]["json"]
    assert payload["queryNoType"] == "fbaNo"
    assert payload["queryNos"] == "FBA111\nFBA222\nFBA333"
    assert payload["waybillStatus"] == 0
    assert payload["orderStatus"] == 0
    assert payload["waybillStatusList"] == ""
    assert payload["queryOrderTimeType"] == "waybillDate"
    assert payload["queryTime1"] == (date.today() - timedelta(days=365)).isoformat()


def test_query_without_token_is_authentication_failure():
    results = YiTongQueryService(YiTongClient(retries=0)).query_many(["FBA111"])
    assert results[0].status == QueryStatus.FAILED
    assert results[0].error_category == AuthenticationError.category


def test_network_retry_is_finite():
    session = FakeSession(request_outcomes=[requests.Timeout(), requests.Timeout()])
    client = YiTongClient(session=session, retries=1, backoff_seconds=0, sleeper=lambda _: None)
    client.token = "token"
    try:
        client.validate_token()
    except NetworkError:
        pass
    else:
        raise AssertionError("expected NetworkError")
    assert len(session.calls) == 2


def test_full_router_activities_are_normalized_after_list_query():
    session = FakeSession(
        request_outcomes=[
            FakeResponse(
                {
                    "code": 2000,
                    "list": [
                        {
                            "fbaNoList": '["FBA11111"]',
                            "orderId": "ORDER-1",
                            "waybillNo": "YT-1",
                            "routerTime": "2026-07-20 08:00:00",
                            "routerInformation": "已到港",
                            "customerChannel": "美森限时达",
                        }
                    ],
                }
            ),
            FakeResponse(
                {
                    "code": 2000,
                    "list": [
                        {
                            "timestamp": "2026-06-01 08:00:00",
                            "content": "进仓",
                        },
                        {
                            "timestamp": "2026-06-05 08:00:00",
                            "content": "已开船，ETA：2026-07-20",
                        },
                        {
                            "timestamp": "2026-07-20 08:00:00",
                            "content": "到港",
                        },
                    ],
                    "domain": {"fbaWhCode": "ONT8"},
                }
            ),
        ]
    )
    client = YiTongClient(session=session, retries=0)
    client.token = "token"
    service = YiTongQueryService(client, request_interval=0)
    service.query_many(["FBA11111"])
    details = service.fetch_tracking_details("FBA11111")

    assert session.calls[1][2]["json"] == {"orderIds": ["ORDER-1"]}
    assert details.snapshot.pickup_time == "2026-06-01"
    assert details.snapshot.actual_departure == "2026-06-05"
    assert details.snapshot.actual_arrival == "2026-07-20"

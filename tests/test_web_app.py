import re

from fastapi.testclient import TestClient

from anda_tracker.models import QueryStatus, TrackingResult
from anda_tracker.airscript import AirScriptConfig, PendingTrackingItem
from anda_tracker.errors import NetworkError
from anda_tracker.storage import ProjectDatabase
from anda_tracker.web.app import create_app
from anda_tracker.web.services import (
    CarrierConnectionStatus,
    QueryCoordinator,
    WebQueryResponse,
    carrier_key_from_sheet,
)


def csrf_from(response) -> str:
    match = re.search(r'name="csrf-token" content="([^"]+)"', response.text)
    assert match
    return match.group(1)


def bootstrap_and_login(client: TestClient, username="admin", password="AdminPass123"):
    setup = client.get("/setup")
    csrf = csrf_from(setup)
    created = client.post(
        "/setup",
        data={
            "csrf_token": csrf,
            "username": username,
            "display_name": "管理员",
            "password": password,
            "confirm_password": password,
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    login = client.get("/login")
    csrf = csrf_from(login)
    response = client.post(
        "/login",
        data={"csrf_token": csrf, "username": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    return csrf_from(dashboard)


def test_web_setup_login_and_private_pages(tmp_path):
    app = create_app(tmp_path / "data")
    with TestClient(app) as client:
        assert client.get("/").url.path == "/setup"
        bootstrap_and_login(client)
        assert "物流查询" in client.get("/tracking").text
        assert "货代连接" in client.get("/carriers").text
        assert "我的店铺" in client.get("/shops").text
        assert "请先添加店铺" in client.get("/inventory").text
        assert "账号管理" in client.get("/admin").text


def test_query_api_reuses_coordinator_and_returns_all_results(tmp_path):
    app = create_app(tmp_path / "data")
    with TestClient(app) as client:
        csrf = bootstrap_and_login(client)
        called = {}

        def fake_query(user_id, fbas, airscript_config):
            called.update(
                user_id=user_id,
                fbas=fbas,
                airscript_config=airscript_config,
            )
            return WebQueryResponse(
                results=[
                    TrackingResult(
                        fba=fba,
                        status=QueryStatus.SUCCESS,
                        carrier="安达",
                        latest_time="2026-07-21",
                        latest_event="已签收",
                    )
                    for fba in fbas
                ]
            )

        app.state.coordinator.query = fake_query
        response = client.post(
            "/api/tracking/query",
            headers={"X-CSRF-Token": csrf},
            json={
                "input": "FBA12345678,FBA87654321,FBA12345678",
                "sync_wps": False,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert [item["fba"] for item in data["results"]] == [
            "FBA12345678",
            "FBA87654321",
        ]
        assert data["input"]["duplicates"] == ["FBA12345678"]
        assert called["fbas"] == ["FBA12345678", "FBA87654321"]
        assert called["airscript_config"] is None


def test_api_rejects_missing_csrf(tmp_path):
    app = create_app(tmp_path / "data")
    with TestClient(app) as client:
        bootstrap_and_login(client)
        response = client.post("/api/tracking/query", json={"input": "FBA12345678"})
        assert response.status_code == 403


def test_user_can_only_see_own_connection_username(tmp_path):
    data_dir = tmp_path / "data"
    app = create_app(data_dir)
    with TestClient(app) as client:
        bootstrap_and_login(client)
        admin = app.state.users.list_users()[0]
        member = app.state.users.create_user(
            "member", "成员", "MemberPass123", must_change_password=False
        )
        ProjectDatabase(data_dir / "app.db", admin.id).save_credentials(
            "anda", "admin-carrier", "admin-secret"
        )
        ProjectDatabase(data_dir / "app.db", admin.id).save_credentials(
            "yitong", "admin-yitong", "yitong-secret"
        )
        ProjectDatabase(data_dir / "app.db", member.id).save_credentials(
            "anda", "member-carrier", "member-secret"
        )
        page = client.get("/carriers")
        assert "admin-carrier" in page.text
        assert "member-carrier" not in page.text
        assert "admin-secret" not in page.text
        assert "未检查" in page.text
        credentials = client.get(
            "/api/carriers/credentials",
            headers={"X-CSRF-Token": csrf_from(page)},
        )
        assert credentials.status_code == 200
        assert credentials.headers["cache-control"] == "no-store, max-age=0"
        assert credentials.json()["credentials"] == {
            "anda": {
                "username": "admin-carrier",
                "password": "admin-secret",
            },
            "yitong": {
                "username": "admin-yitong",
                "password": "yitong-secret",
            },
        }


def test_member_page_never_contains_admin_carrier_when_member_has_no_config(tmp_path):
    data_dir = tmp_path / "data"
    app = create_app(data_dir)
    with TestClient(app) as client:
        bootstrap_and_login(client)
        admin = app.state.users.list_users()[0]
        app.state.users.create_user(
            "member", "成员", "MemberPass123", must_change_password=False
        )
        ProjectDatabase(data_dir / "app.db", admin.id).save_credentials(
            "anda", "admin-only-carrier", "admin-secret"
        )
        csrf = csrf_from(client.get("/dashboard"))
        client.post("/logout", data={"csrf_token": csrf})
        login = client.get("/login")
        client.post(
            "/login",
            data={
                "csrf_token": csrf_from(login),
                "username": "member",
                "password": "MemberPass123",
            },
        )
        page = client.get("/carriers")
        assert "admin-only-carrier" not in page.text
        assert "当前账号尚未配置安达" in page.text
        credentials = client.get(
            "/api/carriers/credentials",
            headers={"X-CSRF-Token": csrf_from(page)},
        )
        assert credentials.json()["credentials"] == {
            "anda": None,
            "yitong": None,
        }


def test_only_tracking_page_starts_live_carrier_check_on_page_open(tmp_path):
    app = create_app(tmp_path / "data")
    with TestClient(app) as client:
        bootstrap_and_login(client)
        carriers = client.get("/carriers")
        tracking = client.get("/tracking")
        assert "initializeCarrierPage();" in carriers.text
        assert "本页面不会自动登录货代" in carriers.text
        assert "正在后台自动登录或检查三家货代" in tracking.text
        assert "本次共享表更新明细" in tracking.text
        assert "updated_cells" in tracking.text
        assert "仅“物流最后更新时间”刷新，未标蓝" in tracking.text
        assert "\ncheckCarriers(null,true).catch(()=>{});" in tracking.text
        assert "未检查" in carriers.text
        assert "待实时检查" in tracking.text
        assert 'id="ytUser" name="carrier_yitong_account" readonly' in carriers.text
        assert 'id="ytPass" name="carrier_yitong_secret" type="password" readonly' in carriers.text
        assert 'autocomplete="new-password"' in carriers.text
        assert "guardCredentialInputs();" in carriers.text
        assert "setTimeout(applySavedCredentials,delay)" in carriers.text


def test_saving_and_deleting_one_carrier_preserves_other_recent_statuses(
    tmp_path, monkeypatch
):
    app = create_app(tmp_path / "data")
    with TestClient(app) as client:
        bootstrap_and_login(client)
        page = client.get("/carriers")
        csrf = csrf_from(page)
        user_id = app.state.users.list_users()[0].id
        coordinator = app.state.coordinator
        coordinator.remember_status(
            user_id, CarrierConnectionStatus("安达", True, "原检查")
        )
        coordinator.remember_status(
            user_id, CarrierConnectionStatus("超鸿", True, "接口可用")
        )
        coordinator.remember_status(
            user_id, CarrierConnectionStatus("易通", True, "登录有效")
        )

        def fake_login(instance, _username, _password):
            instance.token = "new-anda-token"

        monkeypatch.setattr(
            "anda_tracker.web.app.AndaClient.login",
            fake_login,
        )
        saved = client.post(
            "/api/carriers/anda",
            headers={"X-CSRF-Token": csrf},
            json={"username": "new-anda", "password": "new-secret"},
        )
        assert saved.status_code == 200
        after_save = {
            item["carrier"]: item
            for item in client.get(
                "/api/carriers/status",
                headers={"X-CSRF-Token": csrf},
            ).json()["statuses"]
        }
        assert all(item["checked"] for item in after_save.values())
        assert after_save["安达"]["message"] == "登录成功"
        assert after_save["超鸿"]["message"] == "接口可用"
        assert after_save["易通"]["message"] == "登录有效"

        deleted = client.delete(
            "/api/carriers/anda",
            headers={"X-CSRF-Token": csrf},
        )
        assert deleted.status_code == 200
        after_delete = {
            item["carrier"]: item
            for item in client.get(
                "/api/carriers/status",
                headers={"X-CSRF-Token": csrf},
            ).json()["statuses"]
        }
        assert after_delete["安达"]["checked"] is False
        assert after_delete["超鸿"]["checked"] is True
        assert after_delete["易通"]["checked"] is True


def test_carrier_status_read_is_side_effect_free_and_validation_uses_post(
    tmp_path,
):
    app = create_app(tmp_path / "data")
    with TestClient(app) as client:
        csrf = bootstrap_and_login(client)
        calls = []

        def fake_configured(user_id):
            calls.append(("configured", user_id))
            return [
                CarrierConnectionStatus(
                    "安达", True, "已配置账号", checked=False
                )
            ]

        def fake_validate(user_id):
            calls.append(("validate", user_id))
            return [CarrierConnectionStatus("安达", True, "登录成功")]

        app.state.coordinator.configured_status = fake_configured
        app.state.coordinator.validate_all = fake_validate

        snapshot = client.get(
            "/api/carriers/status?live=1",
            headers={"X-CSRF-Token": csrf},
        )
        validation = client.post(
            "/api/carriers/validation",
            headers={"X-CSRF-Token": csrf},
        )

        assert snapshot.status_code == 200
        assert snapshot.json()["statuses"][0]["checked"] is False
        assert validation.status_code == 200
        assert validation.json()["statuses"][0]["checked"] is True
        assert [name for name, _user_id in calls] == [
            "configured",
            "validate",
        ]


def test_temporary_password_forces_change_before_private_pages(tmp_path):
    app = create_app(tmp_path / "data")
    with TestClient(app) as client:
        bootstrap_and_login(client)
        app.state.users.create_user(
            "member", "成员", "Temporary123", must_change_password=True
        )
        csrf = csrf_from(client.get("/dashboard"))
        client.post("/logout", data={"csrf_token": csrf})
        login = client.get("/login")
        response = client.post(
            "/login",
            data={
                "csrf_token": csrf_from(login),
                "username": "member",
                "password": "Temporary123",
            },
            follow_redirects=False,
        )
        assert response.headers["location"] == "/change-password"
        assert client.get("/tracking", follow_redirects=False).headers["location"] == "/change-password"


def test_automatic_shop_task_stops_when_required_carrier_is_disconnected(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    app = create_app(data_dir)
    with TestClient(app) as client:
        csrf = bootstrap_and_login(client)
        account = app.state.users.list_users()[0]
        database = ProjectDatabase(data_dir / "app.db", account.id)
        shop = database.save_shop(
            "测试店铺",
            AirScriptConfig(
                "https://www.kdocs.cn/l/store",
                "https://www.kdocs.cn/api/v3/ide/file/f/script/s/sync_task",
                "store-token",
            ),
        )
        site = database.save_shop_country(
            shop.id,
            "美国",
            "测试店铺-美国",
            country_code="US",
            fba_sheet_name="US-FBA",
            detail_sheet_name="US-轨迹明细",
        )

        class FakeAirScriptClient:
            def __init__(self, _config, retries=0):
                pass

            def list_pending_tracking_items(self):
                return [PendingTrackingItem("FBA11111", "易通物流")]

        monkeypatch.setattr("anda_tracker.web.app.AirScriptClient", FakeAirScriptClient)
        validated = {}

        def fake_validate(_user_id, required):
            validated["required"] = required
            return [
                CarrierConnectionStatus("易通", False, "登录已过期"),
            ]

        app.state.coordinator.validate_required = fake_validate
        response = client.post(
            f"/api/shops/{shop.id}/tracking-sync",
            headers={"X-CSRF-Token": csrf},
            json={"country_id": site.id},
        )
        assert response.status_code == 409
        assert "自动任务已停止" in response.json()["message"]
        assert response.json()["carrier_statuses"][0]["connected"] is False
        assert validated["required"] == {"yitong"}


def test_automatic_shop_task_reads_pending_then_queries_and_syncs(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    app = create_app(data_dir)
    with TestClient(app) as client:
        csrf = bootstrap_and_login(client)
        account = app.state.users.list_users()[0]
        database = ProjectDatabase(data_dir / "app.db", account.id)
        shop = database.save_shop(
            "测试店铺",
            AirScriptConfig(
                "https://www.kdocs.cn/l/store",
                "https://www.kdocs.cn/api/v3/ide/file/f/script/s/sync_task",
                "store-token",
            ),
        )
        site = database.save_shop_country(
            shop.id,
            "美国",
            "测试店铺-美国",
            country_code="US",
            fba_sheet_name="US-FBA",
            detail_sheet_name="US-轨迹明细",
        )
        validated = {}

        def fake_validate(_user_id, required):
            validated["required"] = required
            return [
                CarrierConnectionStatus("安达", True, "登录成功"),
                CarrierConnectionStatus("超鸿", True, "接口可用"),
            ]

        app.state.coordinator.validate_required = fake_validate

        class FakeAirScriptClient:
            def __init__(self, _config, retries=0):
                pass

            def list_pending_tracking_items(self):
                return [
                    PendingTrackingItem("FBA11111", "安达物流"),
                    PendingTrackingItem("FBA22222", "超鸿-美西"),
                ]

        monkeypatch.setattr("anda_tracker.web.app.AirScriptClient", FakeAirScriptClient)
        called = {}

        def fake_query(user_id, items, airscript_config):
            called.update(user_id=user_id, items=items, config=airscript_config)
            return WebQueryResponse(
                [
                    TrackingResult(item.fba, QueryStatus.SUCCESS, carrier="安达")
                    for item in items
                ]
            )

        app.state.coordinator.query_routed = fake_query
        response = client.post(
            f"/api/shops/{shop.id}/tracking-sync",
            headers={"X-CSRF-Token": csrf},
            json={"country_id": site.id},
        )
        assert response.status_code == 200
        assert response.json()["pending_count"] == 2
        assert [item.fba for item in called["items"]] == ["FBA11111", "FBA22222"]
        assert validated["required"] == {"anda", "chaohong"}
        assert called["config"].api_token == "store-token"
        assert called["config"].sheet_name == "US-FBA"
        assert called["config"].detail_sheet_name == "US-轨迹明细"


def test_shop_discovery_saves_sites_with_one_stable_listing_prefix(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    app = create_app(data_dir)
    with TestClient(app) as client:
        csrf = bootstrap_and_login(client)
        account = app.state.users.list_users()[0]
        database = ProjectDatabase(data_dir / "app.db", account.id)
        shop = database.save_shop(
            "纯粹测试",
            AirScriptConfig("https://www.kdocs.cn/l/store", "", ""),
        )
        database.save_listing_connection(
            shop.id,
            "https://www.kdocs.cn/api/v3/ide/file/f/script/listing/sync_task",
            "listing-token",
        )

        class FakeListingAirScriptClient:
            def __init__(self, _config, retries=0):
                pass

            def discover_sheets(self):
                return [
                    {"id": "listing-us", "name": "纯粹-美国"},
                    {"id": "main-us", "name": "US-FBA"},
                    {"id": "detail-us", "name": "US-轨迹明细"},
                    {"id": "listing-ca", "name": "纯粹-加拿大"},
                    {"id": "main-ca", "name": "CA-FBA"},
                    {"id": "detail-ca", "name": "CA-轨迹明细"},
                ]

        monkeypatch.setattr(
            "anda_tracker.web.app.ListingAirScriptClient",
            FakeListingAirScriptClient,
        )
        response = client.post(
            f"/api/shops/{shop.id}/discover-sites",
            headers={"X-CSRF-Token": csrf},
            json={},
        )

        assert response.status_code == 200
        assert response.json()["confirmation_required"] is True
        assert response.json()["available_listing_prefixes"] == ["纯粹"]
        assert response.json()["sites"] == []

        confirmed = client.post(
            f"/api/shops/{shop.id}/discover-sites",
            headers={"X-CSRF-Token": csrf},
            json={"confirm_listing_prefix": "纯粹"},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["confirmation_required"] is False
        sites = confirmed.json()["sites"]
        assert [item["country_code"] for item in sites] == ["US", "CA"]
        assert sites[1]["listing_sheet_name"] == "纯粹-加拿大"
        assert sites[1]["fba_sheet_name"] == "CA-FBA"
        assert sites[1]["detail_sheet_name"] == "CA-轨迹明细"
        assert sites[1]["listing_ready"] is True
        assert sites[1]["tracking_ready"] is False
        assert database.get_shop(shop.id).listing_prefix == "纯粹"


def test_routed_query_only_calls_carrier_named_in_sheet(tmp_path):
    coordinator = QueryCoordinator(
        tmp_path / "app.db",
        tmp_path / "settings.json",
    )
    called = []

    class FakeAndaService:
        carrier = "安达"

        def query_many(self, fbas):
            called.append(list(fbas))
            return [
                TrackingResult(
                    fba,
                    QueryStatus.SUCCESS,
                    carrier="安达",
                    latest_event="已到港",
                )
                for fba in fbas
            ]

    coordinator._anda_service = lambda _database: FakeAndaService()
    items = [
        PendingTrackingItem("FBA11111", "美国安达物流"),
        PendingTrackingItem("FBA22222", ""),
        PendingTrackingItem("FBA33333", "安达/易通"),
    ]
    response = coordinator.query_routed("member", items)

    assert called == [["FBA11111"]]
    assert [item.status for item in response.results] == [
        QueryStatus.SUCCESS,
        QueryStatus.FAILED,
        QueryStatus.FAILED,
    ]
    assert response.results[1].error_category == "carrier_configuration"
    assert carrier_key_from_sheet("超鸿-美西") == "chaohong"
    assert carrier_key_from_sheet("易通物流") == "yitong"
    assert carrier_key_from_sheet("安达和超鸿") == ""


def test_routed_query_rejects_wrong_fba_from_carrier(tmp_path):
    coordinator = QueryCoordinator(
        tmp_path / "app.db",
        tmp_path / "settings.json",
    )

    class WrongResultService:
        carrier = "安达"

        def query_many(self, _fbas):
            return [
                TrackingResult(
                    "FBA_WRONG",
                    QueryStatus.SUCCESS,
                    carrier="安达",
                )
            ]

    coordinator._anda_service = lambda _database: WrongResultService()
    response = coordinator.query_routed(
        "member",
        [PendingTrackingItem("FBA_EXPECTED", "安达")],
    )

    assert len(response.results) == 1
    assert response.results[0].fba == "FBA_EXPECTED"
    assert response.results[0].status == QueryStatus.FAILED
    assert response.results[0].error_category == "invalid_response"


def test_routed_query_falls_back_only_for_not_found_and_reports_conflict(
    tmp_path, monkeypatch
):
    coordinator = QueryCoordinator(
        tmp_path / "app.db",
        tmp_path / "settings.json",
    )
    calls = {"安达": [], "超鸿": [], "易通": []}

    class FakeService:
        def __init__(self, carrier, statuses):
            self.carrier = carrier
            self.statuses = statuses

        def query_many(self, fbas):
            calls[self.carrier].append(list(fbas))
            return [
                TrackingResult(
                    fba=fba,
                    status=self.statuses[fba],
                    carrier=self.carrier,
                    latest_event=(
                        f"{self.carrier}已到港"
                        if self.statuses[fba] == QueryStatus.SUCCESS
                        else ""
                    ),
                    error_category=(
                        "network"
                        if self.statuses[fba] == QueryStatus.FAILED
                        else ""
                    ),
                    error_message=(
                        "网络失败"
                        if self.statuses[fba] == QueryStatus.FAILED
                        else ""
                    ),
                )
                for fba in fbas
            ]

    yitong_statuses = {
        "FBA_PRIMARY": QueryStatus.SUCCESS,
        "FBA_FALLBACK": QueryStatus.NOT_FOUND,
        "FBA_CONFLICT": QueryStatus.NOT_FOUND,
        "FBA_INCONCLUSIVE": QueryStatus.NOT_FOUND,
        "FBA_FAILED": QueryStatus.FAILED,
    }
    anda_statuses = {
        "FBA_FALLBACK": QueryStatus.SUCCESS,
        "FBA_CONFLICT": QueryStatus.SUCCESS,
        "FBA_INCONCLUSIVE": QueryStatus.SUCCESS,
    }
    chaohong_statuses = {
        "FBA_FALLBACK": QueryStatus.NOT_FOUND,
        "FBA_CONFLICT": QueryStatus.SUCCESS,
        "FBA_INCONCLUSIVE": QueryStatus.FAILED,
    }
    coordinator._yitong_service = lambda _database: FakeService(
        "易通", yitong_statuses
    )
    coordinator._anda_service = lambda _database: FakeService(
        "安达", anda_statuses
    )
    monkeypatch.setattr(
        "anda_tracker.web.services.ChaoHongQueryService",
        lambda _client: FakeService("超鸿", chaohong_statuses),
    )

    response = coordinator.query_routed(
        "member",
        [
            PendingTrackingItem("FBA_PRIMARY", "易通"),
            PendingTrackingItem("FBA_FALLBACK", "易通"),
            PendingTrackingItem("FBA_CONFLICT", "易通"),
            PendingTrackingItem("FBA_INCONCLUSIVE", "易通"),
            PendingTrackingItem("FBA_FAILED", "易通"),
        ],
    )

    assert calls["易通"] == [
        [
            "FBA_PRIMARY",
            "FBA_FALLBACK",
            "FBA_CONFLICT",
            "FBA_INCONCLUSIVE",
            "FBA_FAILED",
        ]
    ]
    assert calls["安达"] == [
        ["FBA_FALLBACK", "FBA_CONFLICT", "FBA_INCONCLUSIVE"]
    ]
    assert calls["超鸿"] == [
        ["FBA_FALLBACK", "FBA_CONFLICT", "FBA_INCONCLUSIVE"]
    ]
    assert [item.status for item in response.results] == [
        QueryStatus.SUCCESS,
        QueryStatus.SUCCESS,
        QueryStatus.CONFLICT,
        QueryStatus.PARTIAL,
        QueryStatus.FAILED,
    ]
    assert response.results[1].carrier == "安达"
    assert response.results[2].carrier == "安达 / 超鸿"
    assert response.results[3].carrier == "安达"
    assert "暂时无法排除货代冲突" in response.results[3].error_message
    assert response.results[4].carrier == "易通"


def test_manual_query_uses_only_live_connected_carriers_and_reuses_anda_login(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "app.db"
    database = ProjectDatabase(database_path, "member")
    database.save_credentials("anda", "anda-user", "anda-password")
    coordinator = QueryCoordinator(database_path, tmp_path / "settings.json")
    login_calls = []

    def fake_anda_login(client, username, password):
        login_calls.append((username, password))
        client.token = "validated-token"

    def fake_anda_query(_client, fbas):
        return [
            {
                "fbaCode": fba,
                "latestTraceTime": "2026-07-24 10:00",
                "latestTraceName": "已到港",
            }
            for fba in fbas
        ]

    def fail_chaohong(_client, _fbas):
        raise NetworkError("超鸿暂时不可用")

    monkeypatch.setattr(
        "anda_tracker.web.services.AndaClient.login",
        fake_anda_login,
    )
    monkeypatch.setattr(
        "anda_tracker.web.services.AndaClient.query_batch",
        fake_anda_query,
    )
    monkeypatch.setattr(
        "anda_tracker.web.services.ChaoHongClient.query_batch",
        fail_chaohong,
    )

    statuses = coordinator.validate_all("member")
    assert [item.connected for item in statuses] == [True, False, False]
    assert all(item.checked for item in coordinator.configured_status("member"))
    assert all(
        not item.checked for item in coordinator.configured_status("other-member")
    )

    response = coordinator.query("member", ["FBA12345678"])
    assert response.results[0].status == QueryStatus.SUCCESS
    assert response.results[0].carrier == "安达"
    assert login_calls == [("anda-user", "anda-password")]
    recent_statuses = coordinator.configured_status("member")
    assert [item.connected for item in recent_statuses] == [True, False, False]
    assert all(item.checked for item in recent_statuses)


def test_temporary_yitong_network_failure_does_not_delete_saved_session(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    app = create_app(data_dir)
    with TestClient(app):
        account = app.state.users.create_user(
            "member", "成员", "MemberPass123", must_change_password=False
        )
        database = ProjectDatabase(data_dir / "app.db", account.id)
        database.save_session_token("yitong", "still-valid-token")
        monkeypatch.setattr(
            "anda_tracker.web.services.ChaoHongClient.query_batch",
            lambda _self, _fbas: [],
        )

        def fail_for_network(_self):
            raise NetworkError("网络暂时不可用")

        monkeypatch.setattr(
            "anda_tracker.web.services.YiTongClient.validate_token",
            fail_for_network,
        )
        statuses = app.state.coordinator.validate_all(account.id)
        assert statuses[2].connected is False
        assert database.load_session_token("yitong") == "still-valid-token"


def test_unexpected_query_error_is_logged_without_leaking_request_body(tmp_path):
    data_dir = tmp_path / "data"
    app = create_app(data_dir)
    with TestClient(app) as client:
        csrf = bootstrap_and_login(client)

        def broken_query(*_args):
            raise RuntimeError("internal-query-marker")

        app.state.coordinator.query = broken_query
        response = client.post(
            "/api/tracking/query",
            headers={"X-CSRF-Token": csrf},
            json={"input": "FBA12345678", "sync_wps": False},
        )
        assert response.status_code == 500
        log_text = (data_dir / "logs" / "web.log").read_text(encoding="utf-8")
        assert "manual_tracking_query_unexpected" in log_text
        assert "FBA12345678" not in log_text

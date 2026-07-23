import re

from fastapi.testclient import TestClient

from anda_tracker.models import QueryStatus, TrackingResult
from anda_tracker.airscript import AirScriptConfig
from anda_tracker.errors import NetworkError
from anda_tracker.storage import ProjectDatabase
from anda_tracker.web.app import create_app
from anda_tracker.web.services import CarrierConnectionStatus, WebQueryResponse


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
        assert "模块尚未开始开发" in client.get("/inventory").text
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
        ProjectDatabase(data_dir / "app.db", member.id).save_credentials(
            "anda", "member-carrier", "member-secret"
        )
        page = client.get("/carriers")
        assert "admin-carrier" in page.text
        assert "member-carrier" not in page.text
        assert "admin-secret" not in page.text


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


def test_temporary_password_forces_change_before_private_pages(tmp_path):
    app = create_app(tmp_path / "data")
    with TestClient(app) as client:
        bootstrap_and_login(client)
        admin = app.state.users.list_users()[0]
        member = app.state.users.create_user(
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


def test_automatic_shop_task_stops_when_any_carrier_is_disconnected(tmp_path):
    data_dir = tmp_path / "data"
    app = create_app(data_dir)
    with TestClient(app) as client:
        csrf = bootstrap_and_login(client)
        account = app.state.users.list_users()[0]
        shop = ProjectDatabase(data_dir / "app.db", account.id).save_shop(
            "测试店铺",
            AirScriptConfig(
                "https://www.kdocs.cn/l/store",
                "https://www.kdocs.cn/api/v3/ide/file/f/script/s/sync_task",
                "store-token",
            ),
        )
        app.state.coordinator.validate_all = lambda _user_id: [
            CarrierConnectionStatus("安达", True, "登录成功"),
            CarrierConnectionStatus("超鸿", True, "接口可用"),
            CarrierConnectionStatus("易通", False, "登录已过期"),
        ]
        response = client.post(
            f"/api/shops/{shop.id}/tracking-sync",
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 409
        assert "自动任务已停止" in response.json()["message"]
        assert response.json()["carrier_statuses"][2]["connected"] is False


def test_automatic_shop_task_reads_pending_then_queries_and_syncs(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    app = create_app(data_dir)
    with TestClient(app) as client:
        csrf = bootstrap_and_login(client)
        account = app.state.users.list_users()[0]
        shop = ProjectDatabase(data_dir / "app.db", account.id).save_shop(
            "测试店铺",
            AirScriptConfig(
                "https://www.kdocs.cn/l/store",
                "https://www.kdocs.cn/api/v3/ide/file/f/script/s/sync_task",
                "store-token",
            ),
        )
        app.state.coordinator.validate_all = lambda _user_id: [
            CarrierConnectionStatus("安达", True, "登录成功"),
            CarrierConnectionStatus("超鸿", True, "接口可用"),
            CarrierConnectionStatus("易通", True, "登录有效"),
        ]

        class FakeAirScriptClient:
            def __init__(self, _config, retries=0):
                pass

            def list_pending_fbas(self):
                return ["FBA11111", "FBA22222"]

        monkeypatch.setattr("anda_tracker.web.app.AirScriptClient", FakeAirScriptClient)
        called = {}

        def fake_query(user_id, fbas, airscript_config):
            called.update(user_id=user_id, fbas=fbas, config=airscript_config)
            return WebQueryResponse(
                [
                    TrackingResult(fba, QueryStatus.SUCCESS, carrier="安达")
                    for fba in fbas
                ]
            )

        app.state.coordinator.query = fake_query
        response = client.post(
            f"/api/shops/{shop.id}/tracking-sync",
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200
        assert response.json()["pending_count"] == 2
        assert called["fbas"] == ["FBA11111", "FBA22222"]
        assert called["config"].api_token == "store-token"


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

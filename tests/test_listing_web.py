import re

from fastapi.testclient import TestClient

from anda_tracker.airscript import AirScriptConfig
from anda_tracker.listing import (
    ListingAirScriptBinding,
    ListingRow,
    ListingSyncSummary,
    ParsedListingExport,
    TARGET_HEADERS,
)
from anda_tracker.storage import ProjectDatabase
from anda_tracker.web.app import create_app


def csrf_from(response) -> str:
    match = re.search(r'name="csrf-token" content="([^"]+)"', response.text)
    assert match
    return match.group(1)


def bootstrap_and_login(client: TestClient):
    setup = client.get("/setup")
    client.post(
        "/setup",
        data={
            "csrf_token": csrf_from(setup),
            "username": "admin",
            "display_name": "管理员",
            "password": "AdminPass123",
            "confirm_password": "AdminPass123",
        },
    )
    login = client.get("/login")
    client.post(
        "/login",
        data={
            "csrf_token": csrf_from(login),
            "username": "admin",
            "password": "AdminPass123",
        },
    )
    return csrf_from(client.get("/inventory"))


def test_listing_preview_and_apply_are_scoped_to_selected_shop_country(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    app = create_app(data_dir)
    with TestClient(app) as client:
        csrf = bootstrap_and_login(client)
        account = app.state.users.list_users()[0]
        database = ProjectDatabase(data_dir / "app.db", account.id)
        shop = database.save_shop(
            "纯粹",
            AirScriptConfig(
                "https://www.kdocs.cn/l/share",
                "https://www.kdocs.cn/api/v3/ide/file/f/script/logistics/sync_task",
                "logistics-token",
            ),
        )
        database.save_listing_connection(
            shop.id,
            "https://www.kdocs.cn/api/v3/ide/file/f/script/listing/sync_task",
            "listing-token",
        )
        country = database.save_shop_country(shop.id, "美国", "纯粹-美国")
        parsed = ParsedListingExport(
            sheet_name="sheet1",
            header_row=1,
            headers=(),
            rows=(
                ListingRow(
                    source_row=2,
                    msku="SKU-1",
                    asin="B012345678",
                    rating=4.4,
                    review_count=123,
                    yesterday_ad_spend=2.5,
                    fba_available=20,
                    reserved=5,
                    inbound=9,
                    sales_7d=7,
                    sales_14d=15,
                    sales_30d=31,
                    system_monthly_sales=30,
                ),
            ),
        )
        monkeypatch.setattr(
            "anda_tracker.modules.inventory.router.parse_listing_export",
            lambda _data: parsed,
        )
        monkeypatch.setattr(
            "anda_tracker.modules.inventory.router.ListingAirScriptClient.validate",
            lambda _self: ListingAirScriptBinding(
                "纯粹-美国",
                2,
                {header: "A" for header in TARGET_HEADERS},
            ),
        )
        sync_calls = {}

        def fake_sync(_self, rows, data_date):
            sync_calls.update(rows=rows, data_date=data_date)
            return ListingSyncSummary(updated=["SKU-1"])

        monkeypatch.setattr(
            "anda_tracker.modules.inventory.router.ListingAirScriptClient.sync",
            fake_sync,
        )

        config = client.get(
            f"/api/inventory/config?shop_id={shop.id}",
            headers={"X-CSRF-Token": csrf},
        )
        assert config.status_code == 200
        assert config.json()["countries"][0]["sheet_name"] == "纯粹-美国"
        assert "listing-token" not in config.text

        preview = client.post(
            "/api/inventory/imports/preview",
            headers={"X-CSRF-Token": csrf},
            data={
                "shop_id": shop.id,
                "country_id": country.id,
                "data_date": "2026-07-26",
            },
            files={
                "file": (
                    "Listing20260726-test.xlsx",
                    b"fake-xlsx-for-mocked-parser",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert preview.status_code == 200
        assert preview.json()["row_count"] == 1
        assert preview.json()["sample"][0]["review_count"] == 123

        applied = client.post(
            "/api/inventory/imports/apply",
            headers={"X-CSRF-Token": csrf},
            json={"preview_id": preview.json()["preview_id"]},
        )
        assert applied.status_code == 200
        assert applied.json()["summary"]["updated"] == ["SKU-1"]
        assert sync_calls["data_date"] == "2026-07-26"
        assert sync_calls["rows"][0].reserved == 5

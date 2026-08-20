import re

from fastapi.testclient import TestClient

from g_team_ops.airscript import AirScriptConfig
from g_team_ops.listing import (
    ListingAirScriptBinding,
    ListingRow,
    ListingSyncSummary,
    ParsedListingExport,
    TARGET_HEADERS,
)
from g_team_ops.storage import ProjectDatabase
from g_team_ops.web.app import create_app


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
                    price=19.99,
                    rating=4.4,
                    review_count=123,
                    yesterday_ad_spend=2.5,
                    ad_spend_7d=12.5,
                    ad_spend_14d=24.5,
                    ad_spend_30d=48.5,
                    fba_available=20,
                    reserved=5,
                    inbound=9,
                    sales_7d=7,
                    sales_14d=15,
                    sales_30d=31,
                    revenue_7d=140,
                    revenue_14d=300,
                    revenue_30d=620,
                ),
            ),
        )
        monkeypatch.setattr(
            "g_team_ops.modules.inventory.router.parse_listing_export",
            lambda _data: parsed,
        )
        monkeypatch.setattr(
            "g_team_ops.modules.inventory.router.ListingAirScriptClient.validate",
            lambda _self: ListingAirScriptBinding(
                "纯粹-美国",
                2,
                {header: "A" for header in TARGET_HEADERS},
                rule_version="R1.0",
                configured_formula_rows=1,
            ),
        )
        monkeypatch.setattr(
            "g_team_ops.modules.inventory.router.ListingAirScriptClient.setup_rules",
            lambda _self: ListingAirScriptBinding(
                "纯粹-美国",
                2,
                {header: "A" for header in TARGET_HEADERS},
                rule_version="R1.0",
                configured_formula_rows=1,
                manual_override_rows=1,
            ),
        )
        sync_calls = {}

        before = [{
            "targetType": "cell",
            "sheetName": "纯粹-美国",
            "matchHeader": "MSKU",
            "matchValue": "SKU-1",
            "itemKey": "SKU-1",
            "field": "rating_review",
            "cellAddress": "H3",
            "value": "4.3/120",
            "comparableValue": "4.3/120",
        }]
        after = [{
            **before[0],
            "value": "4.4/123",
            "comparableValue": "4.4/123",
        }]
        monkeypatch.setattr(
            "g_team_ops.modules.inventory.router.ListingAirScriptClient.snapshot_rows",
            lambda _self, _rows: before,
        )
        monkeypatch.setattr(
            "g_team_ops.modules.inventory.router.ListingAirScriptClient.snapshot_targets",
            lambda _self, _targets: after,
        )

        def fake_sync(
            _self,
            rows,
            data_date,
            preconditions=None,
            expected_rule_version="",
        ):
            sync_calls.update(rows=rows, data_date=data_date)
            assert preconditions == before
            assert expected_rule_version == "R1.0"
            return ListingSyncSummary(updated=["SKU-1"])

        monkeypatch.setattr(
            "g_team_ops.modules.inventory.router.ListingAirScriptClient.sync",
            fake_sync,
        )

        config = client.get(
            f"/api/inventory/config?shop_id={shop.id}",
            headers={"X-CSRF-Token": csrf},
        )
        assert config.status_code == 200
        assert config.json()["countries"][0]["sheet_name"] == "纯粹-美国"
        assert "listing-token" not in config.text

        rules = client.post(
            f"/api/inventory/countries/{country.id}/rules/setup",
            headers={"X-CSRF-Token": csrf},
            json={"shop_id": shop.id},
        )
        assert rules.status_code == 200
        assert "R1.0" in rules.json()["message"]
        assert "保留人工最终月销 1 行" in rules.json()["message"]

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
        assert preview.json()["sample"][0]["rating_review"] == "4.4/123"
        assert preview.json()["rules"]["version"] == "R1.0"

        applied = client.post(
            "/api/inventory/imports/apply",
            headers={"X-CSRF-Token": csrf},
            json={"preview_id": preview.json()["preview_id"]},
        )
        assert applied.status_code == 200
        assert applied.json()["summary"]["updated"] == ["SKU-1"]
        assert applied.json()["operation"]["reversible"] is True
        assert sync_calls["data_date"] == "2026-07-26"
        assert sync_calls["rows"][0].reserved == 5

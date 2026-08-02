import sqlite3

from anda_tracker.airscript import AirScriptConfig
from anda_tracker.storage import (
    ProjectDatabase,
    SYSTEM_MAX_QUERY_COUNT,
    protect_secret,
)


def test_password_is_dpapi_encrypted_inside_project_database(tmp_path):
    database_path = tmp_path / "data" / "app.db"
    database = ProjectDatabase(database_path)
    database.save_credentials("anda", "placeholder-user", "placeholder-secret")

    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT username, password_ciphertext FROM carrier_credentials"
        ).fetchone()
    assert row[0] == "placeholder-user"
    assert "placeholder-secret" not in row[1]
    loaded = database.load_credentials("anda")
    assert loaded is not None
    assert loaded.password == "placeholder-secret"


def test_system_query_limit_defaults_to_and_cannot_exceed_50(tmp_path):
    database = ProjectDatabase(tmp_path / "app.db")
    assert database.query_batch_size() == SYSTEM_MAX_QUERY_COUNT == 50
    with sqlite3.connect(database.path) as connection:
        connection.execute(
            "UPDATE system_settings SET setting_value='500' WHERE setting_key='max_query_count'"
        )
    assert database.query_batch_size() == 50


def test_yitong_password_and_session_token_are_encrypted(tmp_path):
    database = ProjectDatabase(tmp_path / "app.db")
    database.save_credentials("yitong", "placeholder-user", "placeholder-password")
    database.save_session_token("yitong", "placeholder-token")
    with sqlite3.connect(database.path) as connection:
        password_row = connection.execute(
            "SELECT password_ciphertext FROM carrier_credentials WHERE carrier='yitong'"
        ).fetchone()
        token_row = connection.execute(
            "SELECT token_ciphertext FROM carrier_sessions WHERE carrier='yitong'"
        ).fetchone()
    assert "placeholder-password" not in password_row[0]
    assert "placeholder-token" not in token_row[0]
    assert database.load_credentials("yitong").password == "placeholder-password"
    assert database.load_session_token("yitong") == "placeholder-token"


def test_multiple_shops_are_encrypted_and_isolated_by_profile(tmp_path):
    path = tmp_path / "app.db"
    first = ProjectDatabase(path, profile_id="user-one")
    second = ProjectDatabase(path, profile_id="user-two")
    first_shop = first.save_shop(
        "美国一店",
        AirScriptConfig(
            "https://www.kdocs.cn/l/share-one",
            "https://www.kdocs.cn/api/v3/ide/file/f1/script/s1/sync_task",
            "token-one",
        ),
    )
    first.save_shop(
        "美国二店",
        AirScriptConfig(
            "https://www.kdocs.cn/l/share-two",
            "https://www.kdocs.cn/api/v3/ide/file/f2/script/s2/sync_task",
            "token-two",
        ),
    )
    second.save_shop(
        "其他店铺",
        AirScriptConfig(
            "https://www.kdocs.cn/l/share-three",
            "https://www.kdocs.cn/api/v3/ide/file/f3/script/s3/sync_task",
            "token-three",
        ),
    )
    assert [shop.name for shop in first.list_shops()] == ["美国一店", "美国二店"]
    assert [shop.name for shop in second.list_shops()] == ["其他店铺"]
    assert second.get_shop(first_shop.id) is None
    with sqlite3.connect(path) as connection:
        ciphertext = connection.execute(
            "SELECT api_token_ciphertext FROM shops WHERE id=?", (first_shop.id,)
        ).fetchone()[0]
    assert "token-one" not in ciphertext


def test_shop_basic_information_can_be_saved_before_module_connections(tmp_path):
    database = ProjectDatabase(tmp_path / "app.db", profile_id="owner")
    shop = database.save_shop(
        "纯粹",
        AirScriptConfig("https://www.kdocs.cn/l/share-one", "", ""),
    )

    loaded = database.get_shop(shop.id)
    assert loaded is not None
    assert loaded.name == "纯粹"
    assert loaded.config.share_url == "https://www.kdocs.cn/l/share-one"
    assert loaded.config.webhook_url == ""
    assert loaded.config.api_token == ""
    assert loaded.listing_prefix == ""

    database.save_shop_listing_prefix(shop.id, "纯粹")
    assert database.get_shop(shop.id).listing_prefix == "纯粹"


def test_legacy_airscript_config_migrates_to_default_shop(tmp_path):
    path = tmp_path / "app.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE airscript_settings (
                profile_id TEXT PRIMARY KEY,
                share_url TEXT NOT NULL,
                webhook_url TEXT NOT NULL,
                api_token_ciphertext TEXT NOT NULL,
                sheet_name TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO airscript_settings VALUES (?, ?, ?, ?, ?, ?)",
            (
                "owner",
                "https://www.kdocs.cn/l/legacy",
                "https://www.kdocs.cn/api/v3/ide/file/f/script/s/sync_task",
                protect_secret("legacy-token"),
                "US-FBA",
                "2026-07-23T00:00:00+00:00",
            ),
        )
    shops = ProjectDatabase(path, profile_id="owner").list_shops()
    assert len(shops) == 1
    assert shops[0].name == "默认店铺"
    assert shops[0].config.api_token == "legacy-token"


def test_tracking_detail_cache_is_local_and_isolated_by_profile(tmp_path):
    path = tmp_path / "app.db"
    first = ProjectDatabase(path, profile_id="user-one")
    second = ProjectDatabase(path, profile_id="user-two")
    payload = {
        "snapshot": {"latest_time": "2026-07-20", "latest_event": "已到港"},
        "events": [],
    }
    first.save_tracking_cache(
        "anda",
        "FBA11111",
        1,
        "2026-07-20",
        "已到港",
        payload,
    )

    cached = first.load_tracking_cache("anda", "FBA11111")
    assert cached is not None
    assert cached[:3] == (1, "2026-07-20", "已到港")
    assert cached[3] == payload
    assert second.load_tracking_cache("anda", "FBA11111") is None


def test_listing_connection_and_country_mapping_are_encrypted_and_isolated(tmp_path):
    path = tmp_path / "app.db"
    first = ProjectDatabase(path, profile_id="user-one")
    second = ProjectDatabase(path, profile_id="user-two")
    shop = first.save_shop(
        "纯粹",
        AirScriptConfig(
            "https://www.kdocs.cn/l/share-one",
            "https://www.kdocs.cn/api/v3/ide/file/f1/script/logistics/sync_task",
            "logistics-token",
        ),
    )
    first.save_listing_connection(
        shop.id,
        "https://www.kdocs.cn/api/v3/ide/file/f1/script/listing/sync_task",
        "listing-secret-token",
    )
    country = first.save_shop_country(
        shop.id,
        "美国",
        "纯粹-美国",
        country_code="US",
        fba_sheet_name="US-FBA",
        detail_sheet_name="US-轨迹明细",
    )

    loaded = first.load_listing_connection(shop.id)
    assert loaded is not None
    assert loaded.api_token == "listing-secret-token"
    loaded_country = first.get_shop_country(country.id)
    assert loaded_country.sheet_name == "纯粹-美国"
    assert loaded_country.country_code == "US"
    assert loaded_country.fba_sheet_name == "US-FBA"
    assert loaded_country.detail_sheet_name == "US-轨迹明细"
    assert second.load_listing_connection(shop.id) is None
    assert second.get_shop_country(country.id) is None
    with sqlite3.connect(path) as connection:
        ciphertext = connection.execute(
            "SELECT api_token_ciphertext FROM listing_connections"
        ).fetchone()[0]
    assert "listing-secret-token" not in ciphertext

    first.delete_shop(shop.id)
    assert first.load_listing_connection(shop.id) is None
    assert first.get_shop_country(country.id) is None

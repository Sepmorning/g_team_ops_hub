import sqlite3

from anda_tracker.airscript import AirScriptConfig
from anda_tracker.storage import ProjectDatabase, SYSTEM_MAX_QUERY_COUNT
from anda_tracker.wps import WpsCredentials, WpsSheetBinding, WpsTokens


def test_password_is_dpapi_encrypted_inside_project_database(tmp_path):
    database_path = tmp_path / "data" / "app.db"
    database = ProjectDatabase(database_path)
    database.save_anda_credentials("placeholder-user", "placeholder-secret")

    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT username, password_ciphertext FROM carrier_credentials"
        ).fetchone()
    assert row[0] == "placeholder-user"
    assert "placeholder-secret" not in row[1]
    loaded = database.load_anda_credentials()
    assert loaded is not None
    assert loaded.password == "placeholder-secret"


def test_system_query_limit_defaults_to_and_cannot_exceed_50(tmp_path):
    database = ProjectDatabase(tmp_path / "app.db")
    assert database.max_query_count() == SYSTEM_MAX_QUERY_COUNT == 50
    with sqlite3.connect(database.path) as connection:
        connection.execute(
            "UPDATE system_settings SET setting_value='500' WHERE setting_key='max_query_count'"
        )
    assert database.max_query_count() == 50


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


def test_wps_appkey_and_oauth_tokens_are_encrypted(tmp_path):
    database = ProjectDatabase(tmp_path / "app.db")
    credentials = WpsCredentials(
        "placeholder-appid",
        "placeholder-appkey",
        "https://www.kdocs.cn/l/file123",
        fba_col=4,
        route_col=24,
    )
    database.save_wps_credentials(credentials)
    database.save_wps_tokens(WpsTokens("access-secret", "refresh-secret", 1234567890))
    database.save_wps_binding(WpsSheetBinding("file123", 9, "US-FBA", 100, 24, 4, 24))
    with sqlite3.connect(database.path) as connection:
        row = connection.execute(
            """
            SELECT app_secret_ciphertext, access_token_ciphertext, refresh_token_ciphertext
            FROM wps_settings
            """
        ).fetchone()
    assert "placeholder-appkey" not in row[0]
    assert "access-secret" not in row[1]
    assert "refresh-secret" not in row[2]
    assert database.load_wps_credentials().app_secret == "placeholder-appkey"
    assert database.load_wps_credentials().fba_col == 4
    assert database.load_wps_credentials().route_col == 24
    assert database.load_wps_tokens().refresh_token == "refresh-secret"
    assert database.load_wps_binding().worksheet_name == "US-FBA"
    assert database.load_wps_binding().fba_col == 4
    assert database.load_wps_binding().route_col == 24


def test_airscript_token_is_encrypted_and_config_can_be_loaded(tmp_path):
    database = ProjectDatabase(tmp_path / "app.db")
    config = AirScriptConfig(
        share_url="https://www.kdocs.cn/l/share123",
        webhook_url=(
            "https://www.kdocs.cn/api/v3/ide/file/file-id/"
            "script/script-id/sync_task"
        ),
        api_token="placeholder-airscript-secret",
    )
    database.save_airscript_config(config)
    with sqlite3.connect(database.path) as connection:
        row = connection.execute(
            "SELECT api_token_ciphertext FROM airscript_settings"
        ).fetchone()
    assert "placeholder-airscript-secret" not in row[0]
    loaded = database.load_airscript_config()
    assert loaded == config


def test_deleting_airscript_config_does_not_touch_legacy_wps_settings(tmp_path):
    database = ProjectDatabase(tmp_path / "app.db")
    database.save_airscript_config(
        AirScriptConfig(
            "https://www.kdocs.cn/l/share123",
            "https://www.kdocs.cn/api/v3/ide/file/file/script/script/sync_task",
            "placeholder-token",
        )
    )
    database.delete_airscript_config()
    assert database.load_airscript_config() is None

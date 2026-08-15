"""建立并接管既有应用数据库结构。"""

from __future__ import annotations

import secrets

from alembic import op
import sqlalchemy as sa


revision = "0001_legacy_schema"
down_revision = None
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table_name: str) -> set[str]:
    return {
        str(item["name"])
        for item in sa.inspect(bind).get_columns(table_name)
    }


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    if "users" not in tables:
        op.create_table(
            "users",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("username", sa.Text(), nullable=False),
            sa.Column("username_normalized", sa.Text(), nullable=False, unique=True),
            sa.Column("display_name", sa.Text(), nullable=False),
            sa.Column("password_hash", sa.Text(), nullable=False),
            sa.Column("role", sa.Text(), nullable=False),
            sa.Column("is_active", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("must_change_password", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
            sa.Column("last_login_at", sa.Text()),
            sa.CheckConstraint("role IN ('admin', 'user')", name="ck_users_role"),
        )
    if "carrier_credentials" not in tables:
        op.create_table(
            "carrier_credentials",
            sa.Column("profile_id", sa.Text(), primary_key=True),
            sa.Column("carrier", sa.Text(), primary_key=True),
            sa.Column("username", sa.Text(), nullable=False),
            sa.Column("password_ciphertext", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
        )
    if "system_settings" not in tables:
        op.create_table(
            "system_settings",
            sa.Column("setting_key", sa.Text(), primary_key=True),
            sa.Column("setting_value", sa.Text(), nullable=False),
        )
    if "carrier_sessions" not in tables:
        op.create_table(
            "carrier_sessions",
            sa.Column("profile_id", sa.Text(), primary_key=True),
            sa.Column("carrier", sa.Text(), primary_key=True),
            sa.Column("token_ciphertext", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
        )
    if "tracking_detail_cache" not in tables:
        op.create_table(
            "tracking_detail_cache",
            sa.Column("profile_id", sa.Text(), primary_key=True),
            sa.Column("carrier", sa.Text(), primary_key=True),
            sa.Column("fba", sa.Text(), primary_key=True),
            sa.Column("schema_version", sa.Integer(), nullable=False),
            sa.Column("latest_time", sa.Text(), nullable=False),
            sa.Column("latest_event", sa.Text(), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
        )
    if "shops" not in tables:
        op.create_table(
            "shops",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("profile_id", sa.Text(), nullable=False),
            sa.Column("name", sa.Text(collation="NOCASE"), nullable=False),
            sa.Column("share_url", sa.Text(), nullable=False),
            sa.Column("webhook_url", sa.Text(), nullable=False),
            sa.Column("api_token_ciphertext", sa.Text(), nullable=False),
            sa.Column("sheet_name", sa.Text(), nullable=False),
            sa.Column("listing_prefix", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
            sa.UniqueConstraint("profile_id", "name", name="uq_shops_profile_name"),
        )
    elif "listing_prefix" not in _columns(bind, "shops"):
        op.add_column(
            "shops",
            sa.Column("listing_prefix", sa.Text(), nullable=False, server_default=""),
        )
    if "listing_connections" not in tables:
        op.create_table(
            "listing_connections",
            sa.Column("profile_id", sa.Text(), primary_key=True),
            sa.Column("shop_id", sa.Text(), primary_key=True),
            sa.Column("webhook_url", sa.Text(), nullable=False),
            sa.Column("api_token_ciphertext", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
        )
    if "shop_countries" not in tables:
        op.create_table(
            "shop_countries",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("profile_id", sa.Text(), nullable=False),
            sa.Column("shop_id", sa.Text(), nullable=False),
            sa.Column("country_name", sa.Text(collation="NOCASE"), nullable=False),
            sa.Column("sheet_name", sa.Text(collation="NOCASE"), nullable=False),
            sa.Column("country_code", sa.Text(), nullable=False, server_default=""),
            sa.Column("fba_sheet_name", sa.Text(), nullable=False, server_default=""),
            sa.Column("detail_sheet_name", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
            sa.UniqueConstraint(
                "profile_id", "shop_id", "country_name",
                name="uq_shop_countries_profile_shop_country",
            ),
            sa.UniqueConstraint(
                "profile_id", "shop_id", "sheet_name",
                name="uq_shop_countries_profile_shop_sheet",
            ),
        )
    else:
        columns = _columns(bind, "shop_countries")
        for name in ("country_code", "fba_sheet_name", "detail_sheet_name"):
            if name not in columns:
                op.add_column(
                    "shop_countries",
                    sa.Column(name, sa.Text(), nullable=False, server_default=""),
                )

    bind.execute(
        sa.text(
            "INSERT OR IGNORE INTO system_settings(setting_key, setting_value) "
            "VALUES('max_query_count', '50')"
        )
    )
    tables = _tables(bind)
    if "airscript_settings" in tables:
        legacy_rows = bind.execute(
            sa.text(
                "SELECT profile_id, share_url, webhook_url, "
                "api_token_ciphertext, sheet_name, updated_at "
                "FROM airscript_settings AS legacy "
                "WHERE NOT EXISTS ("
                "SELECT 1 FROM shops WHERE shops.profile_id=legacy.profile_id)"
            )
        ).fetchall()
        for row in legacy_rows:
            bind.execute(
                sa.text(
                    "INSERT INTO shops(id, profile_id, name, share_url, "
                    "webhook_url, api_token_ciphertext, sheet_name, "
                    "listing_prefix, created_at, updated_at) "
                    "VALUES(:id, :profile_id, '默认店铺', :share_url, "
                    ":webhook_url, :token, :sheet_name, '', :created_at, :updated_at)"
                ),
                {
                    "id": secrets.token_hex(16),
                    "profile_id": row[0],
                    "share_url": row[1],
                    "webhook_url": row[2],
                    "token": row[3],
                    "sheet_name": row[4],
                    "created_at": row[5],
                    "updated_at": row[5],
                },
            )


def downgrade() -> None:
    raise RuntimeError("既有业务数据库基线不得降级删除")

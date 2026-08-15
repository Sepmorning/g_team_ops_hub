"""新增操作历史、审计、恢复快照、资源锁和备份目录。"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_operations_safety"
down_revision = "0001_legacy_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operation_batches",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("module_name", sa.String(40), nullable=False),
        sa.Column("operation_type", sa.String(60), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("shop_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("country_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("resource_key", sa.String(160), nullable=False, server_default=""),
        sa.Column("idempotency_key", sa.String(120)),
        sa.Column("rollback_of_batch_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("reversible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("summary_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("error_category", sa.String(60), nullable=False, server_default=""),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.Column("started_at", sa.String(40), nullable=False, server_default=""),
        sa.Column("finished_at", sa.String(40), nullable=False, server_default=""),
        sa.UniqueConstraint(
            "profile_id", "idempotency_key",
            name="uq_operation_batches_profile_idempotency",
        ),
    )
    op.create_index(
        "ix_operation_batches_profile_created",
        "operation_batches",
        ["profile_id", "created_at"],
    )
    op.create_index(
        "ix_operation_batches_profile_shop",
        "operation_batches",
        ["profile_id", "shop_id"],
    )
    op.create_table(
        "operation_items",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "batch_id", sa.String(32),
            sa.ForeignKey("operation_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("item_key", sa.String(180), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("summary_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("error_category", sa.String(60), nullable=False, server_default=""),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_index(
        "ix_operation_items_batch", "operation_items", ["batch_id", "created_at"]
    )
    op.create_table(
        "operation_changes",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "batch_id", sa.String(32),
            sa.ForeignKey("operation_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("item_key", sa.String(180), nullable=False, server_default=""),
        sa.Column("target_type", sa.String(40), nullable=False),
        sa.Column("sheet_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("match_header", sa.String(100), nullable=False, server_default=""),
        sa.Column("match_value", sa.String(180), nullable=False, server_default=""),
        sa.Column("field_name", sa.String(100), nullable=False),
        sa.Column("cell_address", sa.String(40), nullable=False, server_default=""),
        sa.Column("old_value_json", sa.Text(), nullable=False),
        sa.Column("new_value_json", sa.Text(), nullable=False),
        sa.Column("old_value_hash", sa.String(64), nullable=False),
        sa.Column("new_value_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_index(
        "ix_operation_changes_batch", "operation_changes", ["batch_id", "created_at"]
    )
    op.create_table(
        "operation_events",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "batch_id", sa.String(32),
            sa.ForeignKey("operation_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("details_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_index(
        "ix_operation_events_batch", "operation_events", ["batch_id", "created_at"]
    )
    op.create_table(
        "resource_locks",
        sa.Column("resource_key", sa.String(160), primary_key=True),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("batch_id", sa.String(32), nullable=False),
        sa.Column("owner_token", sa.String(64), nullable=False),
        sa.Column("acquired_at", sa.String(40), nullable=False),
        sa.Column("heartbeat_at", sa.String(40), nullable=False),
        sa.Column("expires_at", sa.String(40), nullable=False),
    )
    op.create_index(
        "ix_resource_locks_profile", "resource_locks", ["profile_id", "expires_at"]
    )
    op.create_table(
        "backup_catalog",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("file_name", sa.String(180), nullable=False, unique=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(80), nullable=False),
        sa.Column("integrity_result", sa.String(40), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False, server_default="system"),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_index(
        "ix_backup_catalog_created", "backup_catalog", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_backup_catalog_created", table_name="backup_catalog")
    op.drop_table("backup_catalog")
    op.drop_index("ix_resource_locks_profile", table_name="resource_locks")
    op.drop_table("resource_locks")
    op.drop_index("ix_operation_events_batch", table_name="operation_events")
    op.drop_table("operation_events")
    op.drop_index("ix_operation_changes_batch", table_name="operation_changes")
    op.drop_table("operation_changes")
    op.drop_index("ix_operation_items_batch", table_name="operation_items")
    op.drop_table("operation_items")
    op.drop_index("ix_operation_batches_profile_shop", table_name="operation_batches")
    op.drop_index("ix_operation_batches_profile_created", table_name="operation_batches")
    op.drop_table("operation_batches")

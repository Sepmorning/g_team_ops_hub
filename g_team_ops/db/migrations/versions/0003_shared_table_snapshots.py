"""持久化共享表写入前快照。"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_shared_table_snapshots"
down_revision = "0002_operations_safety"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operation_snapshots",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "batch_id",
            sa.String(32),
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
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("comparable_value_json", sa.Text(), nullable=False),
        sa.Column("value_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_index(
        "ix_operation_snapshots_batch",
        "operation_snapshots",
        ["batch_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_operation_snapshots_batch",
        table_name="operation_snapshots",
    )
    op.drop_table("operation_snapshots")

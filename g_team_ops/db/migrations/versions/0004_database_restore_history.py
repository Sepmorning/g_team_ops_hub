"""记录维护模式整库恢复历史。"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_database_restore_history"
down_revision = "0003_shared_table_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "database_restores",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("backup_file_name", sa.String(180), nullable=False),
        sa.Column("backup_sha256", sa.String(64), nullable=False),
        sa.Column("safety_backup_file_name", sa.String(180), nullable=False),
        sa.Column("safety_backup_sha256", sa.String(64), nullable=False),
        sa.Column("restored_by", sa.String(64), nullable=False),
        sa.Column("result", sa.String(40), nullable=False),
        sa.Column("restored_at", sa.String(40), nullable=False),
    )
    op.create_index(
        "ix_database_restores_created",
        "database_restores",
        ["restored_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_database_restores_created",
        table_name="database_restores",
    )
    op.drop_table("database_restores")

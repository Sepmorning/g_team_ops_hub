from __future__ import annotations

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class OperationBatchModel(Base):
    __tablename__ = "operation_batches"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "idempotency_key",
            name="uq_operation_batches_profile_idempotency",
        ),
        Index(
            "ix_operation_batches_profile_created",
            "profile_id",
            "created_at",
        ),
        Index(
            "ix_operation_batches_profile_shop",
            "profile_id",
            "shop_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(64), nullable=False)
    module_name: Mapped[str] = mapped_column(String(40), nullable=False)
    operation_type: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    shop_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    country_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    resource_key: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    idempotency_key: Mapped[str | None] = mapped_column(String(120))
    rollback_of_batch_id: Mapped[str] = mapped_column(
        String(32), nullable=False, default=""
    )
    reversible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error_category: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    finished_at: Mapped[str] = mapped_column(String(40), nullable=False, default="")


class OperationItemModel(Base):
    __tablename__ = "operation_items"
    __table_args__ = (
        Index("ix_operation_items_batch", "batch_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("operation_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    profile_id: Mapped[str] = mapped_column(String(64), nullable=False)
    item_key: Mapped[str] = mapped_column(String(180), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error_category: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class OperationChangeModel(Base):
    __tablename__ = "operation_changes"
    __table_args__ = (
        Index("ix_operation_changes_batch", "batch_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("operation_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    profile_id: Mapped[str] = mapped_column(String(64), nullable=False)
    item_key: Mapped[str] = mapped_column(String(180), nullable=False, default="")
    target_type: Mapped[str] = mapped_column(String(40), nullable=False)
    sheet_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    match_header: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    match_value: Mapped[str] = mapped_column(String(180), nullable=False, default="")
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    cell_address: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    old_value_json: Mapped[str] = mapped_column(Text, nullable=False)
    new_value_json: Mapped[str] = mapped_column(Text, nullable=False)
    old_value_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    new_value_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class OperationSnapshotModel(Base):
    __tablename__ = "operation_snapshots"
    __table_args__ = (
        Index("ix_operation_snapshots_batch", "batch_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("operation_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    profile_id: Mapped[str] = mapped_column(String(64), nullable=False)
    item_key: Mapped[str] = mapped_column(String(180), nullable=False, default="")
    target_type: Mapped[str] = mapped_column(String(40), nullable=False)
    sheet_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    match_header: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    match_value: Mapped[str] = mapped_column(String(180), nullable=False, default="")
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    cell_address: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    comparable_value_json: Mapped[str] = mapped_column(Text, nullable=False)
    value_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class OperationEventModel(Base):
    __tablename__ = "operation_events"
    __table_args__ = (
        Index("ix_operation_events_batch", "batch_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("operation_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    profile_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    details_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ResourceLockModel(Base):
    __tablename__ = "resource_locks"
    __table_args__ = (
        Index("ix_resource_locks_profile", "profile_id", "expires_at"),
    )

    resource_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(64), nullable=False)
    batch_id: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_token: Mapped[str] = mapped_column(String(64), nullable=False)
    acquired_at: Mapped[str] = mapped_column(String(40), nullable=False)
    heartbeat_at: Mapped[str] = mapped_column(String(40), nullable=False)
    expires_at: Mapped[str] = mapped_column(String(40), nullable=False)


class BackupCatalogModel(Base):
    __tablename__ = "backup_catalog"
    __table_args__ = (
        UniqueConstraint("file_name", name="uq_backup_catalog_file_name"),
        Index("ix_backup_catalog_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    file_name: Mapped[str] = mapped_column(String(180), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(80), nullable=False)
    integrity_result: Mapped[str] = mapped_column(String(40), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)




class DatabaseRestoreModel(Base):
    __tablename__ = "database_restores"
    __table_args__ = (
        Index("ix_database_restores_created", "restored_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    backup_file_name: Mapped[str] = mapped_column(String(180), nullable=False)
    backup_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    safety_backup_file_name: Mapped[str] = mapped_column(String(180), nullable=False)
    safety_backup_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    restored_by: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[str] = mapped_column(String(40), nullable=False)
    restored_at: Mapped[str] = mapped_column(String(40), nullable=False)

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


OPERATION_STATUSES = {
    "prepared",
    "running",
    "applied",
    "partial",
    "failed",
    "interrupted",
    "uncertain",
    "rollback_running",
    "rolled_back",
    "rollback_partial",
    "reconciled",
    "reconciled_no_change",
}

TERMINAL_OPERATION_STATUSES = {
    "applied",
    "partial",
    "failed",
    "interrupted",
    "uncertain",
    "rolled_back",
    "rollback_partial",
    "reconciled",
    "reconciled_no_change",
}


@dataclass(frozen=True)
class OperationBatch:
    id: str
    profile_id: str
    module_name: str
    operation_type: str
    status: str
    shop_id: str
    country_id: str
    resource_key: str
    idempotency_key: str
    rollback_of_batch_id: str
    reversible: bool
    summary: dict[str, Any]
    error_category: str
    error_message: str
    created_at: str
    updated_at: str
    started_at: str
    finished_at: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "module": self.module_name,
            "operation_type": self.operation_type,
            "status": self.status,
            "shop_id": self.shop_id,
            "country_id": self.country_id,
            "resource_key": self.resource_key,
            "rollback_of_batch_id": self.rollback_of_batch_id,
            "reversible": self.reversible,
            "summary": self.summary,
            "error_category": self.error_category,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass(frozen=True)
class OperationEvent:
    id: str
    event_type: str
    message: str
    details: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class OperationItem:
    id: str
    item_key: str
    status: str
    summary: dict[str, Any]
    error_category: str
    error_message: str
    created_at: str


@dataclass(frozen=True)
class OperationChange:
    id: str
    item_key: str
    target_type: str
    sheet_name: str
    match_header: str
    match_value: str
    field_name: str
    cell_address: str
    old_value: Any
    new_value: Any
    old_value_hash: str
    new_value_hash: str
    created_at: str


@dataclass(frozen=True)
class OperationSnapshot:
    id: str
    item_key: str
    target_type: str
    sheet_name: str
    match_header: str
    match_value: str
    field_name: str
    cell_address: str
    value: Any
    comparable_value: Any
    value_hash: str
    created_at: str


@dataclass(frozen=True)
class OperationDetails:
    batch: OperationBatch
    events: list[OperationEvent] = field(default_factory=list)
    items: list[OperationItem] = field(default_factory=list)
    changes: list[OperationChange] = field(default_factory=list)
    snapshots: list[OperationSnapshot] = field(default_factory=list)


@dataclass(frozen=True)
class ResourceLock:
    resource_key: str
    profile_id: str
    batch_id: str
    owner_token: str
    acquired_at: str
    heartbeat_at: str
    expires_at: str


@dataclass(frozen=True)
class BackupRecord:
    id: str
    file_name: str
    sha256: str
    size_bytes: int
    reason: str
    integrity_result: str
    created_by: str
    created_at: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "file_name": self.file_name,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "reason": self.reason,
            "integrity_result": self.integrity_result,
            "created_by": self.created_by,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class DatabaseRestoreRecord:
    id: str
    backup_file_name: str
    backup_sha256: str
    safety_backup_file_name: str
    safety_backup_sha256: str
    restored_by: str
    result: str
    restored_at: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "backup_file_name": self.backup_file_name,
            "backup_sha256": self.backup_sha256,
            "safety_backup_file_name": self.safety_backup_file_name,
            "safety_backup_sha256": self.safety_backup_sha256,
            "restored_by": self.restored_by,
            "result": self.result,
            "restored_at": self.restored_at,
        }

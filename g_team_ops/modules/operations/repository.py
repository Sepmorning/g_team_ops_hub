from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from ...db.migration import upgrade_database
from ...db.models import (
    BackupCatalogModel,
    DatabaseRestoreModel,
    OperationBatchModel,
    OperationChangeModel,
    OperationEventModel,
    OperationItemModel,
    OperationSnapshotModel,
    ResourceLockModel,
)
from ...db.runtime import create_database_engine
from ...errors import ConfigurationError
from .models import (
    BackupRecord,
    DatabaseRestoreRecord,
    OPERATION_STATUSES,
    OperationBatch,
    OperationChange,
    OperationDetails,
    OperationEvent,
    OperationItem,
    OperationSnapshot,
    ResourceLock,
    TERMINAL_OPERATION_STATUSES,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_value(value: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


def _hash_json(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class OperationRepository:
    """按profile隔离的操作历史、审计、恢复快照和资源锁仓储。"""

    def __init__(self, database_path: Path):
        self.path = Path(database_path)
        upgrade_database(self.path)
        self.engine = create_database_engine(self.path)

    @staticmethod
    def _batch(row) -> OperationBatch:
        return OperationBatch(
            id=str(row["id"]),
            profile_id=str(row["profile_id"]),
            module_name=str(row["module_name"]),
            operation_type=str(row["operation_type"]),
            status=str(row["status"]),
            shop_id=str(row["shop_id"] or ""),
            country_id=str(row["country_id"] or ""),
            resource_key=str(row["resource_key"] or ""),
            idempotency_key=str(row["idempotency_key"] or ""),
            rollback_of_batch_id=str(row["rollback_of_batch_id"] or ""),
            reversible=bool(row["reversible"]),
            summary=_json_object(str(row["summary_json"] or "{}")),
            error_category=str(row["error_category"] or ""),
            error_message=str(row["error_message"] or ""),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            started_at=str(row["started_at"] or ""),
            finished_at=str(row["finished_at"] or ""),
        )

    def create_batch(
        self,
        profile_id: str,
        module_name: str,
        operation_type: str,
        *,
        shop_id: str = "",
        country_id: str = "",
        resource_key: str = "",
        idempotency_key: str = "",
        rollback_of_batch_id: str = "",
        reversible: bool = False,
        summary: dict[str, Any] | None = None,
    ) -> OperationBatch:
        profile_id = profile_id.strip()
        module_name = module_name.strip().lower()
        operation_type = operation_type.strip().lower()
        if not profile_id or not module_name or not operation_type:
            raise ConfigurationError("操作用户、模块和类型不能为空")
        if len(module_name) > 40 or len(operation_type) > 60:
            raise ConfigurationError("操作模块或类型名称过长")
        normalized_idempotency = idempotency_key.strip() or None
        if normalized_idempotency:
            existing = self.get_by_idempotency(profile_id, normalized_idempotency)
            if existing:
                return existing
        batch_id = secrets.token_hex(16)
        timestamp = _iso()
        values = {
            "id": batch_id,
            "profile_id": profile_id,
            "module_name": module_name,
            "operation_type": operation_type,
            "status": "prepared",
            "shop_id": shop_id.strip(),
            "country_id": country_id.strip(),
            "resource_key": resource_key.strip(),
            "idempotency_key": normalized_idempotency,
            "rollback_of_batch_id": rollback_of_batch_id.strip(),
            "reversible": bool(reversible),
            "summary_json": _json(summary or {}),
            "error_category": "",
            "error_message": "",
            "created_at": timestamp,
            "updated_at": timestamp,
            "started_at": "",
            "finished_at": "",
        }
        try:
            with self.engine.begin() as connection:
                connection.execute(insert(OperationBatchModel).values(**values))
                connection.execute(
                    insert(OperationEventModel).values(
                        id=secrets.token_hex(16), batch_id=batch_id,
                        profile_id=profile_id, event_type="prepared",
                        message="操作批次已创建", details_json="{}",
                        created_at=timestamp,
                    )
                )
        except IntegrityError:
            if normalized_idempotency:
                existing = self.get_by_idempotency(profile_id, normalized_idempotency)
                if existing:
                    return existing
            raise
        batch = self.get_batch(profile_id, batch_id)
        assert batch is not None
        return batch

    def get_by_idempotency(self, profile_id: str, idempotency_key: str) -> OperationBatch | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(OperationBatchModel).where(
                    OperationBatchModel.profile_id == profile_id,
                    OperationBatchModel.idempotency_key == idempotency_key,
                )
            ).mappings().first()
        return None if row is None else self._batch(row)

    def get_batch(self, profile_id: str, batch_id: str) -> OperationBatch | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(OperationBatchModel).where(
                    OperationBatchModel.profile_id == profile_id,
                    OperationBatchModel.id == batch_id,
                )
            ).mappings().first()
        return None if row is None else self._batch(row)

    def list_batches(self, profile_id: str, *, limit: int = 100, module_name: str = "", shop_id: str = "") -> list[OperationBatch]:
        statement = select(OperationBatchModel).where(OperationBatchModel.profile_id == profile_id)
        if module_name:
            statement = statement.where(OperationBatchModel.module_name == module_name.strip().lower())
        if shop_id:
            statement = statement.where(OperationBatchModel.shop_id == shop_id)
        statement = statement.order_by(OperationBatchModel.created_at.desc()).limit(max(1, min(200, int(limit))))
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [self._batch(row) for row in rows]

    def list_uncertain_batches(
        self,
        profile_id: str,
        *,
        limit: int = 100,
    ) -> list[OperationBatch]:
        statement = (
            select(OperationBatchModel)
            .where(
                OperationBatchModel.profile_id == profile_id,
                OperationBatchModel.status == "uncertain",
            )
            .order_by(OperationBatchModel.created_at.desc())
            .limit(max(1, min(200, int(limit))))
        )
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [self._batch(row) for row in rows]

    def update_status(self, profile_id: str, batch_id: str, status: str, *, summary: dict[str, Any] | None = None, error_category: str = "", error_message: str = "") -> OperationBatch:
        status = status.strip().lower()
        if status not in OPERATION_STATUSES:
            raise ConfigurationError("操作批次状态无效")
        timestamp = _iso()
        values: dict[str, Any] = {"status": status, "updated_at": timestamp, "error_category": error_category.strip(), "error_message": error_message.strip()}
        if summary is not None:
            values["summary_json"] = _json(summary)
        if status in {"running", "rollback_running"}:
            values["started_at"] = timestamp
        if status in TERMINAL_OPERATION_STATUSES:
            values["finished_at"] = timestamp
        with self.engine.begin() as connection:
            changed = connection.execute(
                update(OperationBatchModel).where(OperationBatchModel.profile_id == profile_id, OperationBatchModel.id == batch_id).values(**values)
            )
            if changed.rowcount != 1:
                raise ConfigurationError("操作批次不存在或不属于当前用户")
            connection.execute(insert(OperationEventModel).values(id=secrets.token_hex(16), batch_id=batch_id, profile_id=profile_id, event_type=status, message=error_message.strip(), details_json=_json(summary or {}), created_at=timestamp))
        batch = self.get_batch(profile_id, batch_id)
        assert batch is not None
        return batch

    def set_reversible(
        self,
        profile_id: str,
        batch_id: str,
        reversible: bool,
    ) -> OperationBatch:
        with self.engine.begin() as connection:
            changed = connection.execute(
                update(OperationBatchModel)
                .where(
                    OperationBatchModel.profile_id == profile_id,
                    OperationBatchModel.id == batch_id,
                )
                .values(reversible=bool(reversible), updated_at=_iso())
            )
            if changed.rowcount != 1:
                raise ConfigurationError("操作批次不存在或不属于当前用户")
        batch = self.get_batch(profile_id, batch_id)
        assert batch is not None
        return batch

    def take_over_interrupted(
        self,
        profile_id: str,
        batch_id: str,
    ) -> OperationBatch:
        """由用户确认原任务已经停止后，释放该批次资源并转入安全续作状态。"""
        batch = self.get_batch(profile_id, batch_id)
        if batch is None:
            raise ConfigurationError("操作批次不存在或不属于当前用户")
        if batch.status not in {"running", "rollback_running"}:
            raise ConfigurationError("只有仍显示运行中的任务可以执行中断接管")
        timestamp = _iso()
        with self.engine.begin() as connection:
            active_lock = connection.execute(
                select(ResourceLockModel).where(
                    ResourceLockModel.profile_id == profile_id,
                    ResourceLockModel.batch_id == batch_id,
                )
            ).mappings().first()
            if active_lock and str(active_lock["expires_at"]) > timestamp:
                raise ConfigurationError(
                    "任务锁仍在有效期内，原任务可能仍在运行，暂不能接管"
                )
            snapshot_count = int(
                connection.execute(
                    select(func.count())
                    .select_from(OperationSnapshotModel)
                    .where(
                        OperationSnapshotModel.profile_id == profile_id,
                        OperationSnapshotModel.batch_id == batch_id,
                    )
                ).scalar_one()
            )
            if batch.status == "rollback_running":
                status = "rollback_partial"
                message = "恢复任务已中断；可从原操作继续安全恢复"
            elif snapshot_count:
                status = "uncertain"
                message = "写入任务已中断；请先只读核对共享表实际结果"
            else:
                status = "interrupted"
                message = "任务在写前快照完成前中断；可以重新发起原任务"
            summary = {
                **batch.summary,
                "interrupted_takeover": True,
                "snapshot_count": snapshot_count,
            }
            changed = connection.execute(
                update(OperationBatchModel)
                .where(
                    OperationBatchModel.profile_id == profile_id,
                    OperationBatchModel.id == batch_id,
                    OperationBatchModel.status == batch.status,
                )
                .values(
                    status=status,
                    summary_json=_json(summary),
                    error_category="interrupted",
                    error_message=message,
                    updated_at=timestamp,
                    finished_at=timestamp,
                )
            )
            if changed.rowcount != 1:
                raise ConfigurationError("任务状态已经变化，请刷新后重新处理")
            connection.execute(
                delete(ResourceLockModel).where(
                    ResourceLockModel.profile_id == profile_id,
                    ResourceLockModel.batch_id == batch_id,
                )
            )
            connection.execute(
                insert(OperationEventModel).values(
                    id=secrets.token_hex(16),
                    batch_id=batch_id,
                    profile_id=profile_id,
                    event_type="interrupted_takeover",
                    message=message,
                    details_json=_json({"snapshot_count": snapshot_count}),
                    created_at=timestamp,
                )
            )
        recovered = self.get_batch(profile_id, batch_id)
        assert recovered is not None
        return recovered

    def reconcile_uncertain(
        self,
        profile_id: str,
        batch_id: str,
        *,
        changed: bool,
        summary: dict[str, Any],
        changes: list[dict[str, Any]] | None = None,
    ) -> OperationBatch:
        batch = self.get_batch(profile_id, batch_id)
        if batch is None:
            raise ConfigurationError("操作批次不存在或不属于当前用户")
        if batch.status != "uncertain":
            raise ConfigurationError("只有状态不确定的批次可以执行核对")
        status = "reconciled" if changed else "reconciled_no_change"
        timestamp = _iso()
        change_rows = self._change_rows(batch_id, profile_id, changes or [], timestamp)
        with self.engine.begin() as connection:
            changed_row = connection.execute(
                update(OperationBatchModel)
                .where(
                    OperationBatchModel.profile_id == profile_id,
                    OperationBatchModel.id == batch_id,
                    OperationBatchModel.status == "uncertain",
                )
                .values(
                    status=status,
                    reversible=bool(changed),
                    summary_json=_json(summary),
                    error_category="",
                    error_message="",
                    updated_at=timestamp,
                    finished_at=timestamp,
                )
            )
            if changed_row.rowcount != 1:
                raise ConfigurationError("批次状态已经变化，请刷新后重新核对")
            if change_rows:
                connection.execute(insert(OperationChangeModel), change_rows)
                item_keys = sorted(
                    {
                        str(item.get("item_key") or "")
                        for item in (changes or [])
                        if item.get("item_key")
                    }
                )
                for item_key in item_keys:
                    count = sum(
                        item.get("item_key") == item_key
                        for item in (changes or [])
                    )
                    connection.execute(
                        insert(OperationItemModel).values(
                            id=secrets.token_hex(16),
                            batch_id=batch_id,
                            profile_id=profile_id,
                            item_key=item_key,
                            status="observed_during_reconciliation",
                            summary_json=_json({"change_count": count}),
                            error_category="",
                            error_message="",
                            created_at=timestamp,
                        )
                    )
            connection.execute(
                insert(OperationEventModel).values(
                    id=secrets.token_hex(16),
                    batch_id=batch_id,
                    profile_id=profile_id,
                    event_type=status,
                    message="状态不确定批次已完成只读核对",
                    details_json=_json(summary),
                    created_at=timestamp,
                )
            )
        reconciled = self.get_batch(profile_id, batch_id)
        assert reconciled is not None
        return reconciled

    def record_event(self, profile_id: str, batch_id: str, event_type: str, *, message: str = "", details: dict[str, Any] | None = None) -> None:
        if self.get_batch(profile_id, batch_id) is None:
            raise ConfigurationError("操作批次不存在或不属于当前用户")
        with self.engine.begin() as connection:
            connection.execute(insert(OperationEventModel).values(id=secrets.token_hex(16), batch_id=batch_id, profile_id=profile_id, event_type=event_type.strip().lower(), message=message.strip(), details_json=_json(details or {}), created_at=_iso()))

    def record_item(self, profile_id: str, batch_id: str, item_key: str, status: str, *, summary: dict[str, Any] | None = None, error_category: str = "", error_message: str = "") -> None:
        if self.get_batch(profile_id, batch_id) is None:
            raise ConfigurationError("操作批次不存在或不属于当前用户")
        with self.engine.begin() as connection:
            connection.execute(insert(OperationItemModel).values(id=secrets.token_hex(16), batch_id=batch_id, profile_id=profile_id, item_key=item_key.strip(), status=status.strip().lower(), summary_json=_json(summary or {}), error_category=error_category.strip(), error_message=error_message.strip(), created_at=_iso()))

    def record_change(self, profile_id: str, batch_id: str, *, target_type: str, field_name: str, old_value: Any, new_value: Any, item_key: str = "", sheet_name: str = "", match_header: str = "", match_value: str = "", cell_address: str = "") -> None:
        if self.get_batch(profile_id, batch_id) is None:
            raise ConfigurationError("操作批次不存在或不属于当前用户")
        with self.engine.begin() as connection:
            connection.execute(insert(OperationChangeModel).values(id=secrets.token_hex(16), batch_id=batch_id, profile_id=profile_id, item_key=item_key.strip(), target_type=target_type.strip().lower(), sheet_name=sheet_name.strip(), match_header=match_header.strip(), match_value=match_value.strip(), field_name=field_name.strip(), cell_address=cell_address.strip(), old_value_json=_json(old_value), new_value_json=_json(new_value), old_value_hash=_hash_json(old_value), new_value_hash=_hash_json(new_value), created_at=_iso()))

    def record_changes(
        self,
        profile_id: str,
        batch_id: str,
        changes: list[dict[str, Any]],
    ) -> None:
        if not changes:
            return
        if self.get_batch(profile_id, batch_id) is None:
            raise ConfigurationError("操作批次不存在或不属于当前用户")
        timestamp = _iso()
        rows = self._change_rows(batch_id, profile_id, changes, timestamp)
        with self.engine.begin() as connection:
            connection.execute(insert(OperationChangeModel), rows)

    @staticmethod
    def _change_rows(
        batch_id: str,
        profile_id: str,
        changes: list[dict[str, Any]],
        timestamp: str,
    ) -> list[dict[str, Any]]:
        rows = []
        for change in changes:
            old_value = change.get("old_value")
            new_value = change.get("new_value")
            rows.append(
                {
                    "id": secrets.token_hex(16),
                    "batch_id": batch_id,
                    "profile_id": profile_id,
                    "item_key": str(change.get("item_key") or "").strip(),
                    "target_type": str(change.get("target_type") or "").strip().lower(),
                    "sheet_name": str(change.get("sheet_name") or "").strip(),
                    "match_header": str(change.get("match_header") or "").strip(),
                    "match_value": str(change.get("match_value") or "").strip(),
                    "field_name": str(change.get("field_name") or "").strip(),
                    "cell_address": str(change.get("cell_address") or "").strip(),
                    "old_value_json": _json(old_value),
                    "new_value_json": _json(new_value),
                    "old_value_hash": _hash_json(old_value),
                    "new_value_hash": _hash_json(new_value),
                    "created_at": timestamp,
                }
            )
        return rows

    def record_snapshots(
        self,
        profile_id: str,
        batch_id: str,
        snapshots: list[dict[str, Any]],
    ) -> None:
        """在外部写入开始前，持久化该批次的原始共享表值。"""
        if not snapshots:
            return
        if self.get_batch(profile_id, batch_id) is None:
            raise ConfigurationError("操作批次不存在或不属于当前用户")
        timestamp = _iso()
        rows = []
        for snapshot in snapshots:
            value = snapshot.get("value")
            comparable = snapshot.get("comparableValue", value)
            rows.append(
                {
                    "id": secrets.token_hex(16),
                    "batch_id": batch_id,
                    "profile_id": profile_id,
                    "item_key": str(snapshot.get("itemKey") or "").strip(),
                    "target_type": str(
                        snapshot.get("targetType") or ""
                    ).strip().lower(),
                    "sheet_name": str(snapshot.get("sheetName") or "").strip(),
                    "match_header": str(
                        snapshot.get("matchHeader") or ""
                    ).strip(),
                    "match_value": str(
                        snapshot.get("matchValue") or ""
                    ).strip(),
                    "field_name": str(snapshot.get("field") or "").strip(),
                    "cell_address": str(
                        snapshot.get("cellAddress") or ""
                    ).strip(),
                    "value_json": _json(value),
                    "comparable_value_json": _json(comparable),
                    "value_hash": _hash_json(value),
                    "created_at": timestamp,
                }
            )
        with self.engine.begin() as connection:
            connection.execute(insert(OperationSnapshotModel), rows)

    def latest_reversible_batch(
        self,
        profile_id: str,
        *,
        module_name: str,
        shop_id: str,
        country_id: str = "",
    ) -> OperationBatch | None:
        restored_ids = select(OperationBatchModel.rollback_of_batch_id).where(
            OperationBatchModel.profile_id == profile_id,
            OperationBatchModel.status == "rolled_back",
            OperationBatchModel.rollback_of_batch_id != "",
        )
        statement = (
            select(OperationBatchModel)
            .where(
                OperationBatchModel.profile_id == profile_id,
                OperationBatchModel.module_name == module_name.strip().lower(),
                OperationBatchModel.shop_id == shop_id,
                OperationBatchModel.reversible.is_(True),
                OperationBatchModel.status.in_(
                    ("applied", "partial", "reconciled")
                ),
                OperationBatchModel.rollback_of_batch_id == "",
                OperationBatchModel.id.not_in(restored_ids),
            )
            .order_by(OperationBatchModel.created_at.desc())
            .limit(1)
        )
        if country_id:
            statement = statement.where(OperationBatchModel.country_id == country_id)
        with self.engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        return None if row is None else self._batch(row)

    def details(self, profile_id: str, batch_id: str) -> OperationDetails:
        batch = self.get_batch(profile_id, batch_id)
        if batch is None:
            raise ConfigurationError("操作批次不存在或不属于当前用户")
        with self.engine.connect() as connection:
            events = connection.execute(select(OperationEventModel).where(OperationEventModel.profile_id == profile_id, OperationEventModel.batch_id == batch_id).order_by(OperationEventModel.created_at)).mappings().all()
            items = connection.execute(select(OperationItemModel).where(OperationItemModel.profile_id == profile_id, OperationItemModel.batch_id == batch_id).order_by(OperationItemModel.created_at)).mappings().all()
            changes = connection.execute(select(OperationChangeModel).where(OperationChangeModel.profile_id == profile_id, OperationChangeModel.batch_id == batch_id).order_by(OperationChangeModel.created_at)).mappings().all()
            snapshots = connection.execute(select(OperationSnapshotModel).where(OperationSnapshotModel.profile_id == profile_id, OperationSnapshotModel.batch_id == batch_id).order_by(OperationSnapshotModel.created_at)).mappings().all()
        return OperationDetails(
            batch=batch,
            events=[OperationEvent(str(row["id"]), str(row["event_type"]), str(row["message"] or ""), _json_object(str(row["details_json"] or "{}")), str(row["created_at"])) for row in events],
            items=[OperationItem(str(row["id"]), str(row["item_key"]), str(row["status"]), _json_object(str(row["summary_json"] or "{}")), str(row["error_category"] or ""), str(row["error_message"] or ""), str(row["created_at"])) for row in items],
            changes=[OperationChange(str(row["id"]), str(row["item_key"] or ""), str(row["target_type"]), str(row["sheet_name"] or ""), str(row["match_header"] or ""), str(row["match_value"] or ""), str(row["field_name"]), str(row["cell_address"] or ""), _json_value(str(row["old_value_json"])), _json_value(str(row["new_value_json"])), str(row["old_value_hash"]), str(row["new_value_hash"]), str(row["created_at"])) for row in changes],
            snapshots=[OperationSnapshot(str(row["id"]), str(row["item_key"] or ""), str(row["target_type"]), str(row["sheet_name"] or ""), str(row["match_header"] or ""), str(row["match_value"] or ""), str(row["field_name"]), str(row["cell_address"] or ""), _json_value(str(row["value_json"])), _json_value(str(row["comparable_value_json"])), str(row["value_hash"]), str(row["created_at"])) for row in snapshots],
        )

    def acquire_lock(self, profile_id: str, resource_key: str, batch_id: str, *, lease_seconds: int = 15 * 60) -> ResourceLock:
        resource_key = resource_key.strip()
        if not resource_key or len(resource_key) > 160:
            raise ConfigurationError("资源锁标识无效")
        now = _now()
        values = {"resource_key": resource_key, "profile_id": profile_id, "batch_id": batch_id, "owner_token": secrets.token_hex(24), "acquired_at": _iso(now), "heartbeat_at": _iso(now), "expires_at": _iso(now + timedelta(seconds=max(30, min(3600, lease_seconds))))}
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(select(ResourceLockModel).where(ResourceLockModel.resource_key == resource_key)).mappings().first()
                if existing and str(existing["expires_at"]) > _iso(now):
                    connection.rollback()
                    raise ConfigurationError("该店铺正在执行其他写入任务，请等待当前任务完成")
                if existing:
                    connection.execute(delete(ResourceLockModel).where(ResourceLockModel.resource_key == resource_key))
                connection.execute(insert(ResourceLockModel).values(**values))
                connection.commit()
            except Exception:
                if connection.in_transaction():
                    connection.rollback()
                raise
        return ResourceLock(**values)

    def heartbeat_lock(self, profile_id: str, resource_key: str, owner_token: str, *, lease_seconds: int = 15 * 60) -> ResourceLock:
        now = _now()
        expires_at = _iso(now + timedelta(seconds=max(30, min(3600, lease_seconds))))
        with self.engine.begin() as connection:
            changed = connection.execute(update(ResourceLockModel).where(ResourceLockModel.profile_id == profile_id, ResourceLockModel.resource_key == resource_key, ResourceLockModel.owner_token == owner_token).values(heartbeat_at=_iso(now), expires_at=expires_at))
            if changed.rowcount != 1:
                raise ConfigurationError("任务锁已失效，请停止写入并核对操作状态")
            row = connection.execute(select(ResourceLockModel).where(ResourceLockModel.resource_key == resource_key)).mappings().one()
        return ResourceLock(**dict(row))

    def release_lock(self, profile_id: str, resource_key: str, owner_token: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(delete(ResourceLockModel).where(ResourceLockModel.profile_id == profile_id, ResourceLockModel.resource_key == resource_key, ResourceLockModel.owner_token == owner_token))

    @staticmethod
    def _backup(row) -> BackupRecord:
        return BackupRecord(str(row["id"]), str(row["file_name"]), str(row["sha256"]), int(row["size_bytes"]), str(row["reason"]), str(row["integrity_result"]), str(row["created_by"]), str(row["created_at"]))

    def add_backup(self, *, file_name: str, sha256: str, size_bytes: int, reason: str, integrity_result: str, created_by: str) -> BackupRecord:
        existing = self.get_backup_by_file_name(file_name)
        if existing is not None:
            if existing.sha256 != sha256 or existing.size_bytes != int(size_bytes):
                raise ConfigurationError("同名数据库备份记录与文件校验信息不一致")
            return existing
        backup_id = secrets.token_hex(16)
        try:
            with self.engine.begin() as connection:
                connection.execute(insert(BackupCatalogModel).values(id=backup_id, file_name=file_name, sha256=sha256, size_bytes=int(size_bytes), reason=reason, integrity_result=integrity_result, created_by=created_by, created_at=_iso()))
        except IntegrityError:
            existing = self.get_backup_by_file_name(file_name)
            if existing is None or existing.sha256 != sha256:
                raise
            return existing
        record = self.get_backup(backup_id)
        assert record is not None
        return record

    def get_backup(self, backup_id: str) -> BackupRecord | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(BackupCatalogModel).where(BackupCatalogModel.id == backup_id)).mappings().first()
        return None if row is None else self._backup(row)

    def get_backup_by_file_name(self, file_name: str) -> BackupRecord | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(BackupCatalogModel).where(
                    BackupCatalogModel.file_name == file_name
                )
            ).mappings().first()
        return None if row is None else self._backup(row)

    def list_backups(self, limit: int = 100) -> list[BackupRecord]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(BackupCatalogModel).order_by(BackupCatalogModel.created_at.desc()).limit(max(1, min(200, int(limit))))).mappings().all()
        return [self._backup(row) for row in rows]

    def delete_backup_record(self, backup_id: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                delete(BackupCatalogModel).where(BackupCatalogModel.id == backup_id)
            )

    @staticmethod
    def _database_restore(row) -> DatabaseRestoreRecord:
        return DatabaseRestoreRecord(
            str(row["id"]),
            str(row["backup_file_name"]),
            str(row["backup_sha256"]),
            str(row["safety_backup_file_name"]),
            str(row["safety_backup_sha256"]),
            str(row["restored_by"]),
            str(row["result"]),
            str(row["restored_at"]),
        )

    def add_database_restore(
        self,
        *,
        backup_file_name: str,
        backup_sha256: str,
        safety_backup_file_name: str,
        safety_backup_sha256: str,
        restored_by: str,
        result: str = "success",
    ) -> DatabaseRestoreRecord:
        restore_id = secrets.token_hex(16)
        with self.engine.begin() as connection:
            connection.execute(
                insert(DatabaseRestoreModel).values(
                    id=restore_id,
                    backup_file_name=backup_file_name,
                    backup_sha256=backup_sha256,
                    safety_backup_file_name=safety_backup_file_name,
                    safety_backup_sha256=safety_backup_sha256,
                    restored_by=restored_by,
                    result=result,
                    restored_at=_iso(),
                )
            )
        with self.engine.connect() as connection:
            row = connection.execute(
                select(DatabaseRestoreModel).where(
                    DatabaseRestoreModel.id == restore_id
                )
            ).mappings().one()
        return self._database_restore(row)

    def list_database_restores(
        self,
        *,
        limit: int = 100,
    ) -> list[DatabaseRestoreRecord]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(DatabaseRestoreModel)
                .order_by(DatabaseRestoreModel.restored_at.desc())
                .limit(max(1, min(200, int(limit))))
            ).mappings().all()
        return [self._database_restore(row) for row in rows]

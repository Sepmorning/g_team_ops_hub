from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from ...errors import CarrierError, ConfigurationError, ResponseError
from .models import OperationBatch, OperationChange
from .repository import OperationRepository


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _entry_key(entry: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(entry.get("targetType") or "").strip().lower(),
        str(entry.get("sheetName") or "").strip(),
        str(entry.get("matchValue") or "").strip(),
        str(entry.get("field") or "").strip(),
    )


def validate_snapshots(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        raise ResponseError("AirScript快照结果结构无效")
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for raw in values:
        if not isinstance(raw, dict):
            raise ResponseError("AirScript快照项目结构无效")
        entry = dict(raw)
        key = _entry_key(entry)
        if not all(key):
            raise ResponseError("AirScript快照缺少目标定位信息")
        if key in seen:
            raise ResponseError("AirScript返回了重复的快照目标")
        seen.add(key)
        result.append(entry)
    return result


def snapshot_changes(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    before_map = {_entry_key(item): item for item in validate_snapshots(before)}
    after_map = {_entry_key(item): item for item in validate_snapshots(after)}
    if set(before_map) != set(after_map):
        raise ResponseError("AirScript写入后的快照目标集合发生变化，操作状态需要人工核对")
    changes: list[dict[str, Any]] = []
    for key, old in before_map.items():
        new = after_map[key]
        old_comparable = old.get("comparableValue", old.get("value"))
        new_comparable = new.get("comparableValue", new.get("value"))
        if _canonical(old_comparable) == _canonical(new_comparable):
            continue
        changes.append(
            {
                "item_key": str(new.get("itemKey") or old.get("itemKey") or ""),
                "target_type": key[0],
                "sheet_name": key[1],
                "match_header": str(
                    new.get("matchHeader") or old.get("matchHeader") or ""
                ),
                "match_value": key[2],
                "field_name": key[3],
                "cell_address": str(
                    new.get("cellAddress") or old.get("cellAddress") or ""
                ),
                "old_value": old.get("value"),
                "new_value": new.get("value"),
            }
        )
    return changes


def script_changes(values: list[OperationChange]) -> list[dict[str, Any]]:
    return [
        {
            "targetType": item.target_type,
            "sheetName": item.sheet_name,
            "matchHeader": item.match_header,
            "matchValue": item.match_value,
            "field": item.field_name,
            "cellAddress": item.cell_address,
            "itemKey": item.item_key,
            "oldValue": item.old_value,
            "newValue": item.new_value,
        }
        for item in values
    ]


def _select_changes(
    values: list[OperationChange],
    selected_change_ids: list[str] | None,
) -> list[OperationChange]:
    if selected_change_ids is None:
        return list(values)
    if not isinstance(selected_change_ids, list):
        raise ConfigurationError("change_ids必须是变更ID数组")
    normalized = [str(value).strip() for value in selected_change_ids]
    if not normalized or any(not value for value in normalized):
        raise ConfigurationError("请至少选择一个要恢复的变更项")
    if len(normalized) != len(set(normalized)):
        raise ConfigurationError("恢复变更ID不能重复")
    if len(normalized) > 5000:
        raise ConfigurationError("单次最多选择5000个恢复变更项")
    requested = set(normalized)
    selected = [item for item in values if item.id in requested]
    if len(selected) != len(requested):
        raise ConfigurationError("部分恢复变更不存在或不属于当前操作")
    return selected


def _decorate_change_results(
    result: dict[str, Any],
    values: list[OperationChange],
) -> dict[str, list[dict[str, Any]]]:
    decorated: dict[str, list[dict[str, Any]]] = {}
    for key in ("ready", "alreadyApplied", "applied", "conflicts", "failures"):
        entries: list[dict[str, Any]] = []
        raw_entries = result.get(key, [])
        if not isinstance(raw_entries, list):
            raw_entries = []
        for raw in raw_entries:
            entry = dict(raw) if isinstance(raw, dict) else {"message": str(raw)}
            try:
                index = int(entry.get("index"))
            except (TypeError, ValueError):
                entries.append(entry)
                continue
            if 0 <= index < len(values):
                change = values[index]
                entry.update(
                    {
                        "change_id": change.id,
                        "item_key": change.item_key,
                        "sheet_name": change.sheet_name,
                        "field_name": change.field_name,
                        "old_value": change.old_value,
                        "new_value": change.new_value,
                    }
                )
            entries.append(entry)
        decorated[key] = entries
    return decorated


@dataclass(frozen=True)
class GuardedWriteResult:
    batch: OperationBatch
    business_result: Any
    reused: bool = False


class SharedTableOperationManager:
    """为共享表写入提供快照、审计、幂等和跨模块工作簿锁。"""

    def __init__(self, repository: OperationRepository):
        self.repository = repository

    @staticmethod
    def resource_key(profile_id: str, shop_id: str) -> str:
        return f"workbook:{profile_id}:{shop_id}"

    def execute(
        self,
        *,
        profile_id: str,
        module_name: str,
        operation_type: str,
        shop_id: str,
        country_id: str,
        idempotency_key: str,
        snapshot_before: Callable[[], list[dict[str, Any]]],
        apply: Callable[[list[dict[str, Any]]], Any],
        snapshot_after: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
        serialize_result: Callable[[Any], dict[str, Any]],
        restore_result: Callable[[dict[str, Any]], Any],
        is_partial: Callable[[Any], bool],
        initial_summary: dict[str, Any] | None = None,
    ) -> GuardedWriteResult:
        resource_key = self.resource_key(profile_id, shop_id)
        batch = self.repository.create_batch(
            profile_id,
            module_name,
            operation_type,
            shop_id=shop_id,
            country_id=country_id,
            resource_key=resource_key,
            idempotency_key=idempotency_key,
            reversible=False,
            summary=initial_summary or {},
        )
        if batch.status != "prepared":
            saved = batch.summary.get("business_result")
            if not isinstance(saved, dict):
                raise ConfigurationError("该请求已有操作记录，请到操作历史核对结果")
            return GuardedWriteResult(batch, restore_result(saved), reused=True)

        lock = None
        before: list[dict[str, Any]] = []
        changes: list[dict[str, Any]] = []
        try:
            try:
                lock = self.repository.acquire_lock(
                    profile_id,
                    resource_key,
                    batch.id,
                    lease_seconds=3600,
                )
            except ConfigurationError as exc:
                self.repository.update_status(
                    profile_id,
                    batch.id,
                    "failed",
                    error_category="concurrency",
                    error_message=exc.user_message,
                )
                raise
            self.repository.update_status(profile_id, batch.id, "running")
            before = validate_snapshots(snapshot_before())
            self.repository.record_snapshots(profile_id, batch.id, before)
            self.repository.record_event(
                profile_id,
                batch.id,
                "snapshot_ready",
                message="共享表写前快照已完成",
                details={
                    "target_count": len(before),
                    "persisted": True,
                },
            )
            self.repository.heartbeat_lock(
                profile_id,
                resource_key,
                lock.owner_token,
                lease_seconds=3600,
            )
            business_result = apply(before)
            after = validate_snapshots(snapshot_after(before))
            changes = snapshot_changes(before, after)
            self._record_changes(profile_id, batch.id, changes)
            serialized = serialize_result(business_result)
            summary = {
                **(initial_summary or {}),
                "change_count": len(changes),
                "business_result": serialized,
            }
            self.repository.set_reversible(profile_id, batch.id, bool(changes))
            status = "partial" if is_partial(business_result) else "applied"
            batch = self.repository.update_status(
                profile_id,
                batch.id,
                status,
                summary=summary,
            )
            return GuardedWriteResult(batch, business_result)
        except CarrierError as exc:
            status = "failed"
            if before:
                try:
                    after = validate_snapshots(snapshot_after(before))
                    changes = snapshot_changes(before, after)
                    self._record_changes(profile_id, batch.id, changes)
                    if changes:
                        self.repository.set_reversible(profile_id, batch.id, True)
                        status = "partial"
                except Exception:
                    status = "uncertain"
            current = self.repository.get_batch(profile_id, batch.id)
            if current is not None and current.status not in {
                "failed", "partial", "uncertain"
            }:
                self.repository.update_status(
                    profile_id,
                    batch.id,
                    status,
                    summary={
                        **(initial_summary or {}),
                        "change_count": len(changes),
                    },
                    error_category=getattr(exc, "category", "shared_table"),
                    error_message=exc.user_message,
                )
            raise
        except Exception:
            current = self.repository.get_batch(profile_id, batch.id)
            if current is not None and current.status not in {
                "failed", "partial", "uncertain"
            }:
                self.repository.update_status(
                    profile_id,
                    batch.id,
                    "uncertain" if before else "failed",
                    summary={
                        **(initial_summary or {}),
                        "change_count": len(changes),
                    },
                    error_category="unexpected",
                    error_message="共享表写入发生未预期错误",
                )
            raise
        finally:
            if lock is not None:
                self.repository.release_lock(
                    profile_id,
                    resource_key,
                    lock.owner_token,
                )

    def _record_changes(
        self,
        profile_id: str,
        batch_id: str,
        changes: list[dict[str, Any]],
    ) -> None:
        self.repository.record_changes(profile_id, batch_id, changes)
        item_keys = sorted(
            {str(item.get("item_key") or "") for item in changes if item.get("item_key")}
        )
        for item_key in item_keys:
            count = sum(item.get("item_key") == item_key for item in changes)
            self.repository.record_item(
                profile_id,
                batch_id,
                item_key,
                "updated",
                summary={"change_count": count},
            )

    def restore(
        self,
        *,
        profile_id: str,
        original: OperationBatch,
        client: Any,
        idempotency_key: str,
        selected_change_ids: list[str] | None = None,
    ) -> tuple[OperationBatch, dict[str, Any]]:
        details = self.repository.details(profile_id, original.id)
        if not details.changes or not original.reversible:
            raise ConfigurationError("该操作没有可恢复的共享表变更")
        selected_changes = _select_changes(
            details.changes,
            selected_change_ids,
        )
        changes = script_changes(selected_changes)
        selection_summary = {
            "selected_change_ids": [item.id for item in selected_changes],
            "selected_count": len(selected_changes),
            "total_change_count": len(details.changes),
        }
        resource_key = self.resource_key(profile_id, original.shop_id)
        batch = self.repository.create_batch(
            profile_id,
            original.module_name,
            "restore",
            shop_id=original.shop_id,
            country_id=original.country_id,
            resource_key=resource_key,
            idempotency_key=idempotency_key,
            rollback_of_batch_id=original.id,
            reversible=False,
            summary={"source_batch_id": original.id, **selection_summary},
        )
        if batch.status != "prepared":
            return batch, batch.summary
        lock = None
        try:
            lock = self.repository.acquire_lock(
                profile_id,
                resource_key,
                batch.id,
                lease_seconds=3600,
            )
            self.repository.update_status(profile_id, batch.id, "rollback_running")
            result = client.apply_changes(changes, direction="rollback")
            restored_changes = self._restored_changes(selected_changes, result)
            self._record_changes(profile_id, batch.id, restored_changes)
            decorated = _decorate_change_results(result, selected_changes)
            summary = {
                "source_batch_id": original.id,
                **selection_summary,
                "applied": len(decorated["applied"]),
                "already_applied": len(decorated["alreadyApplied"]),
                "conflicts": decorated["conflicts"],
                "failures": decorated["failures"],
            }
            status = "rollback_partial"
            if not summary["conflicts"] and not summary["failures"]:
                if len(selected_changes) == len(details.changes):
                    status = "rolled_back"
                else:
                    try:
                        remaining = client.inspect_changes(
                            script_changes(details.changes),
                            direction="rollback",
                        )
                        remaining_decorated = _decorate_change_results(
                            remaining,
                            details.changes,
                        )
                        summary.update(
                            {
                                "remaining_ready": remaining_decorated["ready"],
                                "remaining_conflicts": remaining_decorated["conflicts"],
                                "remaining_failures": remaining_decorated["failures"],
                                "remaining_unknown": False,
                            }
                        )
                        if not any(
                            summary[key]
                            for key in (
                                "remaining_ready",
                                "remaining_conflicts",
                                "remaining_failures",
                            )
                        ):
                            status = "rolled_back"
                    except Exception:
                        summary["remaining_unknown"] = True
            batch = self.repository.update_status(
                profile_id,
                batch.id,
                status,
                summary=summary,
            )
            return batch, summary
        except CarrierError as exc:
            partial = getattr(exc, "partial_change_result", None)
            if not isinstance(partial, dict):
                partial = {
                    "applied": [],
                    "alreadyApplied": [],
                    "conflicts": [],
                    "failures": [],
                }
            restored_changes = self._restored_changes(selected_changes, partial)
            self._record_changes(profile_id, batch.id, restored_changes)
            decorated = _decorate_change_results(partial, selected_changes)
            self.repository.update_status(
                profile_id,
                batch.id,
                "rollback_partial",
                summary={
                    "source_batch_id": original.id,
                    **selection_summary,
                    "applied": len(decorated["applied"]),
                    "already_applied": len(decorated["alreadyApplied"]),
                    "conflicts": decorated["conflicts"],
                    "failures": decorated["failures"],
                },
                error_category=getattr(exc, "category", "shared_table"),
                error_message=exc.user_message,
            )
            raise
        except Exception:
            current = self.repository.get_batch(profile_id, batch.id)
            if current is not None and current.status not in {
                "rolled_back",
                "rollback_partial",
                "uncertain",
            }:
                self.repository.update_status(
                    profile_id,
                    batch.id,
                    "rollback_partial",
                    summary={
                        "source_batch_id": original.id,
                        **selection_summary,
                        "remaining_unknown": True,
                    },
                    error_category="unexpected",
                    error_message="共享表恢复发生未预期错误；可重新预览后安全续作",
                )
            raise
        finally:
            if lock is not None:
                self.repository.release_lock(
                    profile_id,
                    resource_key,
                    lock.owner_token,
                )

    @staticmethod
    def _restored_changes(
        original_changes: list[OperationChange],
        result: dict[str, Any],
    ) -> list[dict[str, Any]]:
        applied_indexes = {
            int(item.get("index"))
            for item in result.get("applied", [])
            if isinstance(item, dict) and str(item.get("index", "")).isdigit()
        }
        restored_changes = []
        for index, original_change in enumerate(original_changes):
            if index not in applied_indexes:
                continue
            restored_changes.append(
                {
                    "item_key": original_change.item_key,
                    "target_type": original_change.target_type,
                    "sheet_name": original_change.sheet_name,
                    "match_header": original_change.match_header,
                    "match_value": original_change.match_value,
                    "field_name": original_change.field_name,
                    "cell_address": original_change.cell_address,
                    "old_value": original_change.new_value,
                    "new_value": original_change.old_value,
                }
            )
        return restored_changes

    def preview_restore(
        self,
        profile_id: str,
        original: OperationBatch,
        client: Any,
        selected_change_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        details = self.repository.details(profile_id, original.id)
        if not details.changes or not original.reversible:
            raise ConfigurationError("该操作没有可恢复的共享表变更")
        selected_changes = _select_changes(
            details.changes,
            selected_change_ids,
        )
        result = client.inspect_changes(
            script_changes(selected_changes),
            direction="rollback",
        )
        decorated = _decorate_change_results(result, selected_changes)
        return {
            "batch": original.to_payload(),
            "selected_count": len(selected_changes),
            "total_change_count": len(details.changes),
            "ready": decorated["ready"],
            "already_applied": decorated["alreadyApplied"],
            "conflicts": decorated["conflicts"],
            "failures": decorated["failures"],
        }

    def preview_uncertain(
        self,
        profile_id: str,
        batch: OperationBatch,
        client: Any,
    ) -> dict[str, Any]:
        if batch.status != "uncertain":
            raise ConfigurationError("只有状态不确定的批次可以执行核对")
        details = self.repository.details(profile_id, batch.id)
        if details.changes:
            raise ConfigurationError("该批次已经存在核对差异，请刷新操作历史")
        if not details.snapshots:
            raise ConfigurationError("该批次没有持久化写前快照，无法自动核对")
        targets = [
            {
                "targetType": item.target_type,
                "sheetName": item.sheet_name,
                "matchHeader": item.match_header,
                "matchValue": item.match_value,
                "field": item.field_name,
                "cellAddress": item.cell_address,
                "itemKey": item.item_key,
            }
            for item in details.snapshots
        ]
        current = validate_snapshots(client.snapshot_targets(targets))
        before = [
            {
                "targetType": item.target_type,
                "sheetName": item.sheet_name,
                "matchHeader": item.match_header,
                "matchValue": item.match_value,
                "field": item.field_name,
                "cellAddress": item.cell_address,
                "itemKey": item.item_key,
                "value": item.value,
                "comparableValue": item.comparable_value,
            }
            for item in details.snapshots
        ]
        changes = snapshot_changes(before, current)
        return {
            "batch": batch.to_payload(),
            "snapshot_count": len(before),
            "changed_count": len(changes),
            "changes": changes,
        }

    def confirm_uncertain(
        self,
        profile_id: str,
        batch: OperationBatch,
        client: Any,
    ) -> tuple[OperationBatch, dict[str, Any]]:
        preview = self.preview_uncertain(profile_id, batch, client)
        changes = preview["changes"]
        summary = {
            "snapshot_count": preview["snapshot_count"],
            "change_count": len(changes),
            "reconciled_from": "persistent_before_snapshot",
        }
        reconciled = self.repository.reconcile_uncertain(
            profile_id,
            batch.id,
            changed=bool(changes),
            summary=summary,
            changes=changes,
        )
        return reconciled, summary

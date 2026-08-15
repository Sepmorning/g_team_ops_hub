import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from g_team_ops.db.backup import DatabaseBackupService
from g_team_ops.db.backup import OfflineDatabaseRestoreService
from g_team_ops.db.migration import upgrade_database
from g_team_ops.db.runtime import connect_sqlite
from g_team_ops.airscript import AirScriptConfig
from g_team_ops.errors import ConfigurationError
from g_team_ops.maintenance import database_maintenance_lock
from g_team_ops.modules.operations.repository import OperationRepository
from g_team_ops.modules.operations.shared_table import SharedTableOperationManager
from g_team_ops.storage import ProjectDatabase
from g_team_ops.web.app import create_app


def _csrf(response) -> str:
    match = re.search(r'name="csrf-token" content="([^"]+)"', response.text)
    assert match
    return match.group(1)


def _bootstrap(client: TestClient) -> str:
    setup = client.get("/setup")
    csrf = _csrf(setup)
    created = client.post(
        "/setup",
        data={
            "csrf_token": csrf,
            "username": "admin",
            "display_name": "管理员",
            "password": "AdminPass123",
            "confirm_password": "AdminPass123",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    login = client.get("/login")
    response = client.post(
        "/login",
        data={
            "csrf_token": _csrf(login),
            "username": "admin",
            "password": "AdminPass123",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    return _csrf(client.get("/operations"))


def test_legacy_database_is_backed_up_and_migrated_without_data_loss(tmp_path):
    path = tmp_path / "data" / "app.db"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE carrier_credentials (
                profile_id TEXT NOT NULL,
                carrier TEXT NOT NULL,
                username TEXT NOT NULL,
                password_ciphertext TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(profile_id, carrier)
            )
            """
        )
        connection.execute(
            "INSERT INTO carrier_credentials VALUES "
            "('owner', 'anda', 'legacy-user', 'encrypted-placeholder', 'now')"
        )

    upgrade_database(path)

    with sqlite3.connect(path) as connection:
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0]
        username = connection.execute(
            "SELECT username FROM carrier_credentials"
        ).fetchone()[0]
        operation_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='operation_batches'"
        ).fetchone()
        snapshot_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='operation_snapshots'"
        ).fetchone()
        backup_row = connection.execute(
            "SELECT file_name, integrity_result FROM backup_catalog "
            "WHERE reason='migration'"
        ).fetchone()
    assert revision == "0004_database_restore_history"
    assert username == "legacy-user"
    assert operation_table == (1,)
    assert snapshot_table == (1,)
    assert backup_row[1] == "ok"
    assert (path.parent / "backups" / backup_row[0]).exists()


def test_unified_sqlite_connections_enable_durable_pragmas(tmp_path):
    path = tmp_path / "app.db"
    upgrade_database(path)
    connection = connect_sqlite(path)
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 10_000
    finally:
        connection.close()


def test_web_app_refuses_to_start_during_database_maintenance(tmp_path):
    data_dir = tmp_path / "data"
    with database_maintenance_lock(data_dir):
        with pytest.raises(RuntimeError, match="数据库维护锁"):
            create_app(data_dir)
    assert not (data_dir / ".database-maintenance.lock").exists()


def test_operation_history_is_idempotent_isolated_and_records_snapshots(tmp_path):
    repository = OperationRepository(tmp_path / "app.db")
    first = repository.create_batch(
        "user-one",
        "inventory",
        "listing_apply",
        shop_id="shop-one",
        idempotency_key="request-one",
        reversible=True,
    )
    duplicate = repository.create_batch(
        "user-one",
        "inventory",
        "listing_apply",
        shop_id="shop-one",
        idempotency_key="request-one",
        reversible=True,
    )
    repository.create_batch(
        "user-two",
        "tracking",
        "tracking_sync",
        idempotency_key="request-one",
    )
    repository.record_item(
        "user-one",
        first.id,
        "MSKU-LOCAL",
        "updated",
    )
    repository.record_change(
        "user-one",
        first.id,
        target_type="cell",
        sheet_name="店铺-美国",
        match_header="MSKU",
        match_value="MSKU-LOCAL",
        item_key="MSKU-LOCAL",
        field_name="FBA可售",
        old_value=10,
        new_value=12,
        cell_address="O3",
    )
    repository.update_status(
        "user-one",
        first.id,
        "applied",
        summary={"updated": 1},
    )

    assert duplicate.id == first.id
    assert [item.id for item in repository.list_batches("user-one")] == [first.id]
    assert len(repository.list_batches("user-two")) == 1
    details = repository.details("user-one", first.id)
    assert details.batch.reversible is True
    assert details.items[0].item_key == "MSKU-LOCAL"
    assert details.changes[0].old_value == 10
    assert details.changes[0].new_value == 12
    assert details.changes[0].old_value_hash != details.changes[0].new_value_hash
    with pytest.raises(ConfigurationError, match="不属于当前用户"):
        repository.details("user-two", first.id)


def test_persistent_resource_lock_blocks_competing_writer(tmp_path):
    repository = OperationRepository(tmp_path / "app.db")
    first = repository.create_batch("user", "tracking", "sync")
    second = repository.create_batch("user", "inventory", "apply")
    lock = repository.acquire_lock(
        "user",
        "shop:shared-workbook",
        first.id,
    )
    with pytest.raises(ConfigurationError, match="正在执行"):
        repository.acquire_lock(
            "user",
            "shop:shared-workbook",
            second.id,
        )
    repository.heartbeat_lock(
        "user",
        lock.resource_key,
        lock.owner_token,
    )
    repository.release_lock("user", lock.resource_key, lock.owner_token)
    replacement = repository.acquire_lock(
        "user",
        "shop:shared-workbook",
        second.id,
    )
    assert replacement.batch_id == second.id


def test_interrupted_takeover_waits_for_lock_expiry_and_chooses_safe_state(tmp_path):
    repository = OperationRepository(tmp_path / "app.db")
    running = repository.create_batch(
        "user",
        "tracking",
        "sync",
        resource_key="workbook:user:shop",
    )
    lock = repository.acquire_lock(
        "user",
        running.resource_key,
        running.id,
        lease_seconds=3600,
    )
    running = repository.update_status("user", running.id, "running")
    repository.record_snapshots(
        "user",
        running.id,
        [{
            "targetType": "cell",
            "sheetName": "US-FBA",
            "matchHeader": "FBA号",
            "matchValue": "FBA1",
            "itemKey": "FBA1",
            "field": "route",
            "value": "old",
        }],
    )

    with pytest.raises(ConfigurationError, match="仍在有效期"):
        repository.take_over_interrupted("user", running.id)

    with repository.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE resource_locks SET expires_at='2000-01-01T00:00:00+00:00' "
            "WHERE owner_token=?",
            (lock.owner_token,),
        )
    taken_over = repository.take_over_interrupted("user", running.id)
    assert taken_over.status == "uncertain"
    assert taken_over.summary["snapshot_count"] == 1
    replacement = repository.acquire_lock(
        "user",
        running.resource_key,
        running.id,
    )
    repository.release_lock("user", replacement.resource_key, replacement.owner_token)

    before_snapshot = repository.create_batch("user", "inventory", "apply")
    repository.update_status("user", before_snapshot.id, "running")
    assert repository.take_over_interrupted(
        "user", before_snapshot.id
    ).status == "interrupted"

    rollback = repository.create_batch(
        "user",
        "inventory",
        "restore",
        rollback_of_batch_id=before_snapshot.id,
    )
    repository.update_status("user", rollback.id, "rollback_running")
    assert repository.take_over_interrupted(
        "user", rollback.id
    ).status == "rollback_partial"


def test_online_backup_can_be_verified_and_restored_to_isolated_copy(tmp_path):
    path = tmp_path / "data" / "app.db"
    repository = OperationRepository(path)
    batch = repository.create_batch("user", "operations", "test")
    repository.update_status("user", batch.id, "applied")
    service = DatabaseBackupService(
        path,
        path.parent / "backups",
        repository,
    )
    backup = service.create_backup(reason="test", created_by="user")

    assert service.verify_backup(backup.id).sha256 == backup.sha256
    assert not any(
        item.name.startswith(".") or item.name.endswith(("-wal", "-shm"))
        for item in (path.parent / "backups").iterdir()
    )
    restored = service.restore_backup_to(
        backup.id,
        tmp_path / "restored" / "app.db",
    )
    with sqlite3.connect(restored) as connection:
        assert connection.execute(
            "SELECT status FROM operation_batches WHERE id=?",
            (batch.id,),
        ).fetchone()[0] == "applied"
    assert not any(
        item.name.endswith(("-wal", "-shm"))
        for item in (path.parent / "backups").iterdir()
    )
    with pytest.raises(ConfigurationError, match="运行中的app.db"):
        service.restore_backup_to(backup.id, path, overwrite=True)


def test_daily_backup_is_once_per_day_and_only_prunes_automatic_backups(tmp_path):
    path = tmp_path / "data" / "app.db"
    repository = OperationRepository(path)
    service = DatabaseBackupService(path, path.parent / "backups", repository)
    manual = service.create_backup(reason="manual", created_by="admin")
    first, removed = service.ensure_daily_backup(keep=1)
    second, second_removed = service.ensure_daily_backup(keep=1)

    assert first.id == second.id
    assert removed == []
    assert second_removed == []
    assert repository.get_backup(manual.id) is not None
    assert repository.get_backup(first.id) is not None


def test_daily_backup_health_detects_missing_latest_file(tmp_path):
    path = tmp_path / "data" / "app.db"
    repository = OperationRepository(path)
    service = DatabaseBackupService(path, path.parent / "backups", repository)

    assert service.health_status()["ok"] is False
    backup, _removed = service.ensure_daily_backup()
    healthy = service.health_status()
    assert healthy["ok"] is True
    assert healthy["latest"]["id"] == backup.id

    (path.parent / "backups" / backup.file_name).unlink()
    unhealthy = service.health_status()
    assert unhealthy["ok"] is False
    assert "文件缺失" in unhealthy["message"]


def test_daily_backup_prunes_only_old_automatic_files(tmp_path):
    path = tmp_path / "data" / "app.db"
    repository = OperationRepository(path)
    service = DatabaseBackupService(path, path.parent / "backups", repository)
    manual = service.create_backup(reason="manual", created_by="admin")
    old = service.create_backup(reason="scheduled_daily", created_by="system")
    current = service.create_backup(reason="scheduled_daily", created_by="system")
    with repository.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE backup_catalog SET created_at='2000-01-01T00:00:00+00:00' "
            "WHERE id=?",
            (old.id,),
        )
    _created, removed = service.ensure_daily_backup(keep=1)

    assert old.file_name in removed
    assert not (path.parent / "backups" / old.file_name).exists()
    assert repository.get_backup(old.id) is None
    assert repository.get_backup(current.id) is not None
    assert repository.get_backup(manual.id) is not None


def test_offline_restore_creates_safety_backup_and_records_history(tmp_path):
    path = tmp_path / "data" / "app.db"
    repository = OperationRepository(path)
    backup_service = DatabaseBackupService(
        path,
        path.parent / "backups",
        repository,
    )
    original = repository.create_batch("user", "operations", "before")
    repository.update_status("user", original.id, "applied")
    target = backup_service.create_backup(reason="manual", created_by="admin")
    later = repository.create_batch("user", "operations", "after")
    repository.update_status("user", later.id, "applied")
    repository.engine.dispose()
    del backup_service
    del repository

    restore_service = OfflineDatabaseRestoreService(
        path,
        path.parent / "backups",
    )
    with pytest.raises(ConfigurationError, match="确认文字"):
        restore_service.restore(target.id, confirmation="wrong")
    restored = restore_service.restore(
        target.id,
        confirmation="RESTORE DATABASE",
        restored_by="admin",
    )

    restored_repository = OperationRepository(path)
    assert restored_repository.get_batch("user", original.id) is not None
    assert restored_repository.get_batch("user", later.id) is None
    assert restored["safety_backup"]["reason"] == "pre_restore"
    history = restored_repository.list_database_restores()
    assert history[0].backup_file_name == target.file_name
    assert history[0].safety_backup_file_name == restored["safety_backup"]["file_name"]
    assert not any(
        item.name.endswith(("-wal", "-shm"))
        for item in (path.parent / "backups").iterdir()
    )


def test_operations_web_api_is_user_scoped_and_admin_can_create_backup(tmp_path):
    app = create_app(tmp_path / "data")
    with TestClient(app) as client:
        csrf = _bootstrap(client)
        admin = app.state.users.list_users()[0]
        own = app.state.operations.create_batch(
            admin.id,
            "inventory",
            "preview",
        )
        app.state.operations.create_batch(
            "another-user",
            "tracking",
            "hidden",
        )
        listing = client.get(
            "/api/operations",
            headers={"X-CSRF-Token": csrf},
        )
        assert listing.status_code == 200
        assert [item["id"] for item in listing.json()["operations"]] == [own.id]

        created = client.post(
            "/api/backups",
            headers={
                "X-CSRF-Token": csrf,
                "Idempotency-Key": "web-backup-one",
            },
            json={"reason": "web-test"},
        )
        assert created.status_code == 200
        backup_id = created.json()["backup"]["id"]
        verified = client.post(
            f"/api/backups/{backup_id}/verify",
            headers={"X-CSRF-Token": csrf},
        )
        assert verified.status_code == 200
        assert verified.json()["backup"]["integrity_result"] == "ok"
        health = client.get(
            "/api/backups/health",
            headers={"X-CSRF-Token": csrf},
        )
        assert health.status_code == 200
        assert health.json()["health"]["ok"] is True


def test_take_over_web_api_requires_exact_confirmation_and_user_scope(tmp_path):
    app = create_app(tmp_path / "data")
    with TestClient(app) as client:
        csrf = _bootstrap(client)
        admin = app.state.users.list_users()[0]
        running = app.state.operations.create_batch(
            admin.id,
            "tracking",
            "manual_tracking_sync",
        )
        app.state.operations.update_status(admin.id, running.id, "running")
        hidden = app.state.operations.create_batch(
            "another-user",
            "tracking",
            "manual_tracking_sync",
        )
        app.state.operations.update_status("another-user", hidden.id, "running")

        rejected = client.post(
            f"/api/operations/{running.id}/take-over",
            headers={"X-CSRF-Token": csrf},
            json={"confirm": "wrong"},
        )
        assert rejected.status_code == 400
        accepted = client.post(
            f"/api/operations/{running.id}/take-over",
            headers={"X-CSRF-Token": csrf},
            json={"confirm": "原任务已停止"},
        )
        assert accepted.status_code == 200
        assert accepted.json()["operation"]["status"] == "interrupted"
        cross_user = client.post(
            f"/api/operations/{hidden.id}/take-over",
            headers={"X-CSRF-Token": csrf},
            json={"confirm": "原任务已停止"},
        )
        assert cross_user.status_code == 400


def test_guarded_write_records_exact_diff_and_restore_is_audited(tmp_path):
    repository = OperationRepository(tmp_path / "app.db")
    manager = SharedTableOperationManager(repository)
    before = [{
        "targetType": "cell",
        "sheetName": "店铺-美国",
        "matchHeader": "MSKU",
        "matchValue": "SKU-1",
        "itemKey": "SKU-1",
        "field": "rating",
        "cellAddress": "H3",
        "value": 4.3,
        "comparableValue": "4.3",
    }]
    after = [{**before[0], "value": 4.4, "comparableValue": "4.4"}]
    guarded = manager.execute(
        profile_id="user",
        module_name="inventory",
        operation_type="listing_import",
        shop_id="shop",
        country_id="country",
        idempotency_key="apply-one",
        snapshot_before=lambda: before,
        apply=lambda preconditions: {"updated": ["SKU-1"], "before": len(preconditions)},
        snapshot_after=lambda _targets: after,
        serialize_result=lambda value: value,
        restore_result=lambda value: value,
        is_partial=lambda _value: False,
    )

    assert guarded.batch.status == "applied"
    assert guarded.batch.reversible is True
    details = repository.details("user", guarded.batch.id)
    assert details.snapshots[0].value == 4.3
    assert details.snapshots[0].comparable_value == "4.3"
    assert details.changes[0].old_value == 4.3
    assert details.changes[0].new_value == 4.4
    assert details.items[0].item_key == "SKU-1"

    class FakeRestoreClient:
        def inspect_changes(self, changes, *, direction):
            assert direction == "rollback"
            return {
                "ready": [{"index": 0, "itemKey": "SKU-1"}],
                "alreadyApplied": [],
                "conflicts": [],
                "failures": [],
            }

        def apply_changes(self, changes, *, direction):
            assert direction == "rollback"
            assert changes[0]["newValue"] == 4.4
            return {
                "applied": [{"index": 0, "itemKey": "SKU-1"}],
                "alreadyApplied": [],
                "conflicts": [],
                "failures": [],
            }

    preview = manager.preview_restore("user", guarded.batch, FakeRestoreClient())
    assert len(preview["ready"]) == 1
    restored, summary = manager.restore(
        profile_id="user",
        original=guarded.batch,
        client=FakeRestoreClient(),
        idempotency_key="restore-one",
    )
    assert restored.status == "rolled_back"
    assert summary["applied"] == 1
    restore_details = repository.details("user", restored.id)
    assert restore_details.changes[0].old_value == 4.4
    assert restore_details.changes[0].new_value == 4.3
    assert repository.latest_reversible_batch(
        "user",
        module_name="inventory",
        shop_id="shop",
        country_id="country",
    ) is None


def test_guarded_write_persists_snapshot_before_failed_external_write(tmp_path):
    repository = OperationRepository(tmp_path / "app.db")
    manager = SharedTableOperationManager(repository)
    before = [{
        "targetType": "cell",
        "sheetName": "US-FBA",
        "matchHeader": "FBA号",
        "matchValue": "FBA12345",
        "itemKey": "FBA12345",
        "field": "route",
        "cellAddress": "G3",
        "value": "写入前轨迹",
        "comparableValue": "写入前轨迹",
    }]

    def fail_apply(_preconditions):
        raise ConfigurationError("模拟外部写入失败")

    with pytest.raises(ConfigurationError, match="模拟外部写入失败"):
        manager.execute(
            profile_id="user",
            module_name="tracking",
            operation_type="manual_tracking_sync",
            shop_id="shop",
            country_id="country",
            idempotency_key="failed-write",
            snapshot_before=lambda: before,
            apply=fail_apply,
            snapshot_after=lambda _targets: before,
            serialize_result=lambda value: value,
            restore_result=lambda value: value,
            is_partial=lambda _value: False,
        )

    failed = repository.get_by_idempotency("user", "failed-write")
    assert failed is not None
    assert failed.status == "failed"
    details = repository.details("user", failed.id)
    assert details.snapshots[0].match_value == "FBA12345"
    assert details.snapshots[0].value == "写入前轨迹"
    assert details.changes == []


def test_unexpected_restore_failure_is_not_left_running(tmp_path):
    repository = OperationRepository(tmp_path / "app.db")
    manager = SharedTableOperationManager(repository)
    original = repository.create_batch(
        "user",
        "inventory",
        "listing_import",
        shop_id="shop",
        country_id="country",
        reversible=True,
    )
    repository.record_change(
        "user",
        original.id,
        target_type="cell",
        sheet_name="店铺-美国",
        match_header="MSKU",
        match_value="SKU-1",
        item_key="SKU-1",
        field_name="rating",
        old_value=4.3,
        new_value=4.4,
    )
    original = repository.update_status("user", original.id, "applied")

    class BrokenClient:
        def apply_changes(self, _changes, *, direction):
            assert direction == "rollback"
            raise RuntimeError("unexpected")

    with pytest.raises(RuntimeError, match="unexpected"):
        manager.restore(
            profile_id="user",
            original=original,
            client=BrokenClient(),
            idempotency_key="restore-unexpected",
        )

    failed = repository.get_by_idempotency("user", "restore-unexpected")
    assert failed is not None
    assert failed.status == "rollback_partial"
    assert "安全续作" in failed.error_message


def test_selective_restore_can_resume_until_all_changes_are_recovered(tmp_path):
    repository = OperationRepository(tmp_path / "app.db")
    manager = SharedTableOperationManager(repository)
    original = repository.create_batch(
        "user",
        "inventory",
        "listing_import",
        shop_id="shop",
        country_id="country",
        reversible=True,
    )
    for item_key, old_value, new_value in (
        ("SKU-1", 10, 12),
        ("SKU-2", 20, 22),
    ):
        repository.record_change(
            "user",
            original.id,
            target_type="cell",
            sheet_name="店铺-美国",
            match_header="MSKU",
            match_value=item_key,
            item_key=item_key,
            field_name="FBA可售",
            old_value=old_value,
            new_value=new_value,
        )
    original = repository.update_status("user", original.id, "applied")
    details = repository.details("user", original.id)

    class StatefulRestoreClient:
        def __init__(self):
            self.current = {"SKU-1": 12, "SKU-2": 22}

        def inspect_changes(self, changes, *, direction):
            assert direction == "rollback"
            result = {
                "ready": [],
                "alreadyApplied": [],
                "conflicts": [],
                "failures": [],
            }
            for index, change in enumerate(changes):
                current = self.current[change["itemKey"]]
                if current == change["newValue"]:
                    result["ready"].append({"index": index})
                elif current == change["oldValue"]:
                    result["alreadyApplied"].append({"index": index})
                else:
                    result["conflicts"].append({"index": index})
            return result

        def apply_changes(self, changes, *, direction):
            inspected = self.inspect_changes(changes, direction=direction)
            applied = []
            for item in inspected["ready"]:
                index = item["index"]
                change = changes[index]
                self.current[change["itemKey"]] = change["oldValue"]
                applied.append({"index": index})
            return {**inspected, "ready": [], "applied": applied}

    client = StatefulRestoreClient()
    preview = manager.preview_restore("user", original, client)
    assert {item["change_id"] for item in preview["ready"]} == {
        item.id for item in details.changes
    }

    first, first_summary = manager.restore(
        profile_id="user",
        original=original,
        client=client,
        idempotency_key="restore-first-only",
        selected_change_ids=[details.changes[0].id],
    )
    assert first.status == "rollback_partial"
    assert len(first_summary["remaining_ready"]) == 1
    assert repository.latest_reversible_batch(
        "user", module_name="inventory", shop_id="shop"
    ).id == original.id

    second, second_summary = manager.restore(
        profile_id="user",
        original=original,
        client=client,
        idempotency_key="restore-second-only",
        selected_change_ids=[details.changes[1].id],
    )
    assert second.status == "rolled_back"
    assert second_summary["remaining_ready"] == []
    assert repository.latest_reversible_batch(
        "user", module_name="inventory", shop_id="shop"
    ) is None
    with pytest.raises(ConfigurationError, match="不存在或不属于"):
        manager.preview_restore(
            "user",
            original,
            client,
            ["not-an-owned-change"],
        )


@pytest.mark.parametrize(
    ("current_value", "expected_status", "expected_changes"),
    [
        ("写入前轨迹", "reconciled_no_change", 0),
        ("可能已经写入的新轨迹", "reconciled", 1),
    ],
)
def test_uncertain_batch_can_be_reconciled_read_only(
    tmp_path,
    current_value,
    expected_status,
    expected_changes,
):
    repository = OperationRepository(tmp_path / "app.db")
    manager = SharedTableOperationManager(repository)
    batch = repository.create_batch(
        "user",
        "tracking",
        "manual_tracking_sync",
        shop_id="shop",
        country_id="country",
    )
    snapshot = {
        "targetType": "cell",
        "sheetName": "US-FBA",
        "matchHeader": "FBA号",
        "matchValue": "FBA12345",
        "itemKey": "FBA12345",
        "field": "route",
        "cellAddress": "G2",
        "value": "写入前轨迹",
        "comparableValue": "写入前轨迹",
    }
    repository.record_snapshots("user", batch.id, [snapshot])
    batch = repository.update_status("user", batch.id, "uncertain")

    class ReadOnlyClient:
        def snapshot_targets(self, targets):
            assert targets[0]["matchValue"] == "FBA12345"
            return [{
                **snapshot,
                "value": current_value,
                "comparableValue": current_value,
            }]

    preview = manager.preview_uncertain("user", batch, ReadOnlyClient())
    assert preview["changed_count"] == expected_changes
    reconciled, summary = manager.confirm_uncertain(
        "user",
        batch,
        ReadOnlyClient(),
    )
    assert reconciled.status == expected_status
    assert reconciled.reversible is bool(expected_changes)
    assert summary["change_count"] == expected_changes
    assert len(repository.details("user", batch.id).changes) == expected_changes
    latest = repository.latest_reversible_batch(
        "user",
        module_name="tracking",
        shop_id="shop",
        country_id="country",
    )
    assert (latest.id if latest else None) == (
        batch.id if expected_changes else None
    )


def test_restore_web_api_previews_and_applies_only_owned_latest_batch(
    tmp_path,
    monkeypatch,
):
    data_dir = tmp_path / "data"
    app = create_app(data_dir)
    with TestClient(app) as client:
        csrf = _bootstrap(client)
        admin = app.state.users.list_users()[0]
        database = ProjectDatabase(data_dir / "app.db", admin.id)
        shop = database.save_shop(
            "纯粹",
            AirScriptConfig(
                "https://www.kdocs.cn/l/share",
                "https://www.kdocs.cn/api/v3/ide/file/f/script/logistics/sync_task",
                "logistics-token",
            ),
        )
        country = database.save_shop_country(
            shop.id,
            "美国",
            "纯粹-美国",
            country_code="US",
            fba_sheet_name="US-FBA",
            detail_sheet_name="US-轨迹明细",
        )
        original = app.state.operations.create_batch(
            admin.id,
            "tracking",
            "manual_tracking_sync",
            shop_id=shop.id,
            country_id=country.id,
            reversible=True,
        )
        app.state.operations.record_change(
            admin.id,
            original.id,
            target_type="cell",
            sheet_name="US-FBA",
            match_header="FBA号",
            match_value="FBA12345",
            item_key="FBA12345",
            field_name="route",
            old_value="旧轨迹",
            new_value="新轨迹",
            cell_address="G2",
        )
        app.state.operations.update_status(admin.id, original.id, "applied")

        monkeypatch.setattr(
            "g_team_ops.modules.operations.router.AirScriptClient.inspect_changes",
            lambda _self, _changes, direction: {
                "ready": [{"index": 0, "itemKey": "FBA12345"}],
                "alreadyApplied": [],
                "conflicts": [],
                "failures": [],
            },
        )
        monkeypatch.setattr(
            "g_team_ops.modules.operations.router.AirScriptClient.apply_changes",
            lambda _self, _changes, direction: {
                "applied": [{"index": 0, "itemKey": "FBA12345"}],
                "alreadyApplied": [],
                "conflicts": [],
                "failures": [],
            },
        )

        preview = client.post(
            f"/api/operations/{original.id}/restore-preview",
            headers={"X-CSRF-Token": csrf},
            json={},
        )
        assert preview.status_code == 200
        assert len(preview.json()["ready"]) == 1
        change_id = preview.json()["ready"][0]["change_id"]
        restored = client.post(
            f"/api/operations/{original.id}/restore",
            headers={
                "X-CSRF-Token": csrf,
                "Idempotency-Key": "web-restore-one",
            },
            json={"change_ids": [change_id]},
        )
        assert restored.status_code == 200
        assert restored.json()["summary"]["applied"] == 1
        assert restored.json()["summary"]["selected_change_ids"] == [change_id]


def test_uncertain_reconciliation_web_api_is_user_scoped_and_read_only(
    tmp_path,
    monkeypatch,
):
    data_dir = tmp_path / "data"
    app = create_app(data_dir)
    with TestClient(app) as client:
        csrf = _bootstrap(client)
        admin = app.state.users.list_users()[0]
        database = ProjectDatabase(data_dir / "app.db", admin.id)
        shop = database.save_shop(
            "纯粹",
            AirScriptConfig(
                "https://www.kdocs.cn/l/share",
                "https://www.kdocs.cn/api/v3/ide/file/f/script/logistics/sync_task",
                "logistics-token",
            ),
        )
        country = database.save_shop_country(
            shop.id,
            "美国",
            "纯粹-美国",
            country_code="US",
            fba_sheet_name="US-FBA",
            detail_sheet_name="US-轨迹明细",
        )
        uncertain = app.state.operations.create_batch(
            admin.id,
            "tracking",
            "manual_tracking_sync",
            shop_id=shop.id,
            country_id=country.id,
        )
        app.state.operations.record_snapshots(
            admin.id,
            uncertain.id,
            [{
                "targetType": "cell",
                "sheetName": "US-FBA",
                "matchHeader": "FBA号",
                "matchValue": "FBA12345",
                "itemKey": "FBA12345",
                "field": "route",
                "cellAddress": "G2",
                "value": "旧轨迹",
                "comparableValue": "旧轨迹",
            }],
        )
        app.state.operations.update_status(admin.id, uncertain.id, "uncertain")
        monkeypatch.setattr(
            "g_team_ops.modules.operations.router.AirScriptClient.snapshot_targets",
            lambda _self, _targets: [{
                "targetType": "cell",
                "sheetName": "US-FBA",
                "matchHeader": "FBA号",
                "matchValue": "FBA12345",
                "itemKey": "FBA12345",
                "field": "route",
                "cellAddress": "G2",
                "value": "新轨迹",
                "comparableValue": "新轨迹",
            }],
        )

        preview = client.post(
            f"/api/operations/{uncertain.id}/reconcile-preview",
            headers={"X-CSRF-Token": csrf},
            json={},
        )
        assert preview.status_code == 200
        assert preview.json()["changed_count"] == 1
        confirmed = client.post(
            f"/api/operations/{uncertain.id}/reconcile",
            headers={"X-CSRF-Token": csrf},
            json={},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["operation"]["status"] == "reconciled"
        assert confirmed.json()["operation"]["reversible"] is True
        monkeypatch.setattr(
            "g_team_ops.modules.operations.router.AirScriptClient.inspect_changes",
            lambda _self, _changes, direction: {
                "ready": [{"index": 0, "itemKey": "FBA12345"}],
                "alreadyApplied": [],
                "conflicts": [],
                "failures": [],
            },
        )
        restore_preview = client.post(
            f"/api/operations/{uncertain.id}/restore-preview",
            headers={"X-CSRF-Token": csrf},
            json={},
        )
        assert restore_preview.status_code == 200
        assert len(restore_preview.json()["ready"]) == 1

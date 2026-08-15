from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..errors import ConfigurationError
from ..modules.operations.models import BackupRecord
from ..modules.operations.repository import OperationRepository
from .migration import upgrade_database
from .runtime import connect_sqlite


def _remove_sqlite_auxiliary_files(path: Path) -> None:
    """关闭只读备份连接后清理其WAL/SHM辅助文件。"""
    for candidate in (Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass


def _remove_sqlite_temporary_files(path: Path) -> None:
    """清理临时SQLite主文件及其WAL/SHM辅助文件。"""
    _remove_sqlite_auxiliary_files(path)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


class DatabaseBackupService:
    """使用SQLite在线备份API创建并验证本机一致性快照。"""

    def __init__(self, database_path: Path, backup_dir: Path, catalog: OperationRepository):
        self.database_path = Path(database_path).resolve()
        self.backup_dir = Path(backup_dir).resolve()
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.catalog = catalog

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _integrity(path: Path) -> str:
        if not path.exists() or not path.is_file():
            raise ConfigurationError("数据库备份文件不存在")
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)
        try:
            messages = [str(row[0]) for row in connection.execute("PRAGMA integrity_check").fetchall()]
        finally:
            connection.close()
        if messages != ["ok"]:
            raise ConfigurationError("数据库完整性检查失败：" + "；".join(messages[:5]))
        return "ok"

    def create_backup(self, *, reason: str, created_by: str) -> BackupRecord:
        reason = reason.strip() or "manual"
        if len(reason) > 80:
            raise ConfigurationError("备份原因不能超过80位")
        upgrade_database(self.database_path)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        final_path = self.backup_dir / f"app-{timestamp}.db"
        temporary_path = self.backup_dir / f".{final_path.name}.tmp"
        try:
            source = connect_sqlite(self.database_path)
            destination = sqlite3.connect(temporary_path, timeout=10)
            try:
                source.backup(destination, pages=256, sleep=0.05)
                destination.commit()
            finally:
                destination.close()
                source.close()
            integrity = self._integrity(temporary_path)
            os.replace(temporary_path, final_path)
            return self.catalog.add_backup(file_name=final_path.name, sha256=self._sha256(final_path), size_bytes=final_path.stat().st_size, reason=reason, integrity_result=integrity, created_by=created_by)
        finally:
            _remove_sqlite_temporary_files(temporary_path)

    def ensure_daily_backup(
        self,
        *,
        created_by: str = "system",
        keep: int = 14,
    ) -> tuple[BackupRecord, list[str]]:
        """每天首次启动创建自动备份，并只轮换自动备份。"""
        keep = max(1, min(90, int(keep)))
        today = datetime.now().astimezone().date()
        automatic = [
            item for item in self.catalog.list_backups(limit=200)
            if item.reason == "scheduled_daily"
        ]
        current = next(
            (
                item for item in automatic
                if datetime.fromisoformat(item.created_at).astimezone().date() == today
            ),
            None,
        )
        created = current or self.create_backup(
            reason="scheduled_daily",
            created_by=created_by,
        )
        automatic = [
            item for item in self.catalog.list_backups(limit=200)
            if item.reason == "scheduled_daily"
        ]
        removed: list[str] = []
        for stale in automatic[keep:]:
            path = self._path_for(stale)
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            self.catalog.delete_backup_record(stale.id)
            removed.append(stale.file_name)
        return created, removed

    def health_status(self, *, max_age_hours: int = 36) -> dict[str, object]:
        """返回无需读取数据库正文的自动备份健康摘要。"""
        age_limit = max(1, int(max_age_hours))
        automatic = [
            item for item in self.catalog.list_backups(limit=200)
            if item.reason == "scheduled_daily"
        ]
        if not automatic:
            return {
                "ok": False,
                "level": "warning",
                "message": "尚无每日自动备份，请检查启动日志或立即手工备份",
                "automatic_count": 0,
                "latest": None,
            }
        latest = automatic[0]
        path = self._path_for(latest)
        issues: list[str] = []
        try:
            created_at = datetime.fromisoformat(latest.created_at)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - created_at.astimezone(timezone.utc)
        except ValueError:
            age = timedelta.max
            issues.append("最近备份时间无效")
        if not path.exists():
            issues.append("最近备份文件缺失")
        elif path.stat().st_size != latest.size_bytes:
            issues.append("最近备份文件大小与目录记录不一致")
        if latest.integrity_result != "ok":
            issues.append("最近备份未通过完整性检查")
        if age > timedelta(hours=age_limit):
            issues.append(f"最近自动备份已超过{age_limit}小时")
        if age < timedelta(minutes=-5):
            issues.append("最近备份时间晚于本机当前时间")
        return {
            "ok": not issues,
            "level": "ok" if not issues else "warning",
            "message": (
                "最近每日自动备份状态正常"
                if not issues
                else "；".join(issues)
            ),
            "automatic_count": len(automatic),
            "latest": latest.to_payload(),
        }

    def _path_for(self, record: BackupRecord) -> Path:
        candidate = (self.backup_dir / record.file_name).resolve()
        if candidate.parent != self.backup_dir:
            raise ConfigurationError("备份文件路径无效")
        return candidate

    def verify_backup(self, backup_id: str) -> BackupRecord:
        record = self.catalog.get_backup(backup_id)
        if record is None:
            raise ConfigurationError("数据库备份记录不存在")
        path = self._path_for(record)
        try:
            if self._sha256(path) != record.sha256:
                raise ConfigurationError("数据库备份校验值不一致，禁止恢复")
            self._integrity(path)
            return record
        finally:
            _remove_sqlite_auxiliary_files(path)

    def restore_backup_to(
        self,
        backup_id: str,
        destination_path: Path,
        *,
        overwrite: bool = False,
    ) -> Path:
        """验证后恢复到隔离文件；正式app.db只能在维护模式中替换。"""
        record = self.verify_backup(backup_id)
        source_path = self._path_for(record)
        destination_path = Path(destination_path).resolve()
        if destination_path == self.database_path:
            raise ConfigurationError(
                "运行中的app.db不能在线替换，请使用维护模式恢复"
            )
        if destination_path.exists() and not overwrite:
            raise ConfigurationError("恢复目标已存在，未覆盖任何文件")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = destination_path.parent / (
            f".{destination_path.name}.restore.tmp"
        )
        try:
            source = sqlite3.connect(
                f"file:{source_path.as_posix()}?mode=ro",
                uri=True,
                timeout=10,
            )
            destination = sqlite3.connect(temporary_path, timeout=10)
            try:
                source.backup(destination, pages=256, sleep=0.05)
                destination.commit()
            finally:
                destination.close()
                source.close()
            self._integrity(temporary_path)
            os.replace(temporary_path, destination_path)
            return destination_path
        finally:
            _remove_sqlite_temporary_files(temporary_path)
            _remove_sqlite_auxiliary_files(source_path)


class OfflineDatabaseRestoreService:
    """Web服务停机时验证、保护并恢复正式SQLite数据库。"""

    def __init__(self, database_path: Path, backup_dir: Path):
        self.database_path = Path(database_path).resolve()
        self.backup_dir = Path(backup_dir).resolve()

    def _catalog(self) -> OperationRepository:
        return OperationRepository(self.database_path)

    def list_backups(self) -> list[BackupRecord]:
        return self._catalog().list_backups(limit=200)

    def preview(self, backup_id: str) -> dict[str, object]:
        catalog = self._catalog()
        service = DatabaseBackupService(
            self.database_path,
            self.backup_dir,
            catalog,
        )
        record = service.verify_backup(backup_id)
        return {
            "backup": record.to_payload(),
            "database_path": str(self.database_path),
            "safety_backup_will_be_created": True,
        }

    def _prepare_exclusive_restore(self) -> None:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self.database_path, timeout=1)
            connection.execute("PRAGMA busy_timeout=1000")
            checkpoint = connection.execute(
                "PRAGMA wal_checkpoint(TRUNCATE)"
            ).fetchone()
            if checkpoint and int(checkpoint[0]) != 0:
                raise ConfigurationError(
                    "数据库仍被其他进程使用；请完全关闭GTeamOpsHub和源码服务"
                )
            connection.execute("BEGIN EXCLUSIVE")
            connection.rollback()
        except sqlite3.OperationalError as exc:
            raise ConfigurationError(
                "无法取得数据库独占锁；请完全关闭GTeamOpsHub和源码服务"
            ) from exc
        finally:
            if connection is not None:
                connection.close()

    def _restore_file_into_database(self, source_path: Path) -> None:
        source = sqlite3.connect(
            f"file:{source_path.as_posix()}?mode=ro",
            uri=True,
            timeout=10,
        )
        destination = sqlite3.connect(self.database_path, timeout=10)
        try:
            source.backup(destination, pages=256, sleep=0.05)
            destination.commit()
        finally:
            destination.close()
            source.close()
            _remove_sqlite_auxiliary_files(source_path)

    def restore(
        self,
        backup_id: str,
        *,
        confirmation: str,
        restored_by: str = "maintenance",
    ) -> dict[str, object]:
        if confirmation.strip() != "RESTORE DATABASE":
            raise ConfigurationError("恢复确认文字不正确，未修改数据库")
        catalog = self._catalog()
        service = DatabaseBackupService(
            self.database_path,
            self.backup_dir,
            catalog,
        )
        target = service.verify_backup(backup_id)
        safety = service.create_backup(
            reason="pre_restore",
            created_by=restored_by,
        )
        target_path = service._path_for(target)
        safety_path = service._path_for(safety)
        catalog.engine.dispose()
        self._prepare_exclusive_restore()
        self._restore_file_into_database(target_path)
        try:
            upgrade_database(self.database_path)
            DatabaseBackupService._integrity(self.database_path)
            restored_catalog = OperationRepository(self.database_path)
            restored_catalog.add_backup(
                file_name=target.file_name,
                sha256=target.sha256,
                size_bytes=target.size_bytes,
                reason=target.reason,
                integrity_result=target.integrity_result,
                created_by=target.created_by,
            )
            restored_catalog.add_backup(
                file_name=safety.file_name,
                sha256=safety.sha256,
                size_bytes=safety.size_bytes,
                reason=safety.reason,
                integrity_result=safety.integrity_result,
                created_by=safety.created_by,
            )
            restored_catalog.add_database_restore(
                backup_file_name=target.file_name,
                backup_sha256=target.sha256,
                safety_backup_file_name=safety.file_name,
                safety_backup_sha256=safety.sha256,
                restored_by=restored_by,
            )
            DatabaseBackupService._integrity(self.database_path)
        except Exception:
            self._restore_file_into_database(safety_path)
            upgrade_database(self.database_path)
            DatabaseBackupService._integrity(self.database_path)
            raise
        return {
            "backup": target.to_payload(),
            "safety_backup": safety.to_payload(),
            "database_path": str(self.database_path),
            "result": "success",
        }

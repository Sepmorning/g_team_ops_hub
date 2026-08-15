from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import socket
import sys
from pathlib import Path

from .db.backup import OfflineDatabaseRestoreService
from .errors import ConfigurationError


def default_data_dir() -> Path:
    root = (
        Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parents[1]
    )
    return root / "data"


def web_service_is_running(host: str = "127.0.0.1", port: int = 8765) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def _service(data_dir: Path) -> OfflineDatabaseRestoreService:
    resolved = Path(data_dir).resolve()
    return OfflineDatabaseRestoreService(
        resolved / "app.db",
        resolved / "backups",
    )


@contextmanager
def database_maintenance_lock(data_dir: Path):
    data_dir = Path(data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / ".database-maintenance.lock"
    try:
        stream = path.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise ConfigurationError(
            "数据库维护锁已存在；请确认没有其他维护任务或异常中断的恢复"
        ) from exc
    try:
        stream.write(json.dumps({"pid": os.getpid()}, ensure_ascii=False))
        stream.flush()
        yield
    finally:
        stream.close()
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="GTeamOpsMaintenance",
        description="G组运营工作台离线数据库维护工具",
    )
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list-backups", help="列出本机数据库备份")
    preview = subparsers.add_parser("preview-restore", help="验证并预览备份")
    preview.add_argument("--backup-id", required=True)
    restore = subparsers.add_parser("restore", help="停机恢复正式app.db")
    restore.add_argument("--backup-id", required=True)
    restore.add_argument("--confirm", required=True)
    restore.add_argument("--restored-by", default=os.environ.get("USERNAME", "maintenance"))
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    service = _service(arguments.data_dir)
    try:
        if arguments.command == "list-backups":
            payload = {"ok": True, "backups": [item.to_payload() for item in service.list_backups()]}
        elif arguments.command == "preview-restore":
            payload = {"ok": True, **service.preview(arguments.backup_id)}
        else:
            with database_maintenance_lock(arguments.data_dir):
                if web_service_is_running():
                    raise ConfigurationError(
                        "检测到127.0.0.1:8765仍在监听；请先完全关闭GTeamOpsHub和源码服务"
                    )
                payload = {
                    "ok": True,
                    **service.restore(
                        arguments.backup_id,
                        confirmation=arguments.confirm,
                        restored_by=arguments.restored_by,
                    ),
                }
    except ConfigurationError as exc:
        print(json.dumps({"ok": False, "message": exc.user_message}, ensure_ascii=False))
        return 2
    except Exception:
        print(json.dumps({"ok": False, "message": "数据库维护发生未预期错误；未确认成功前不要启动正式程序"}, ensure_ascii=False))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

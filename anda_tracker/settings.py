from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AppSettings:
    batch_size: int = 20
    request_interval: float = 1.5
    retries: int = 2

    @classmethod
    def load(cls, path: Path) -> "AppSettings":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                batch_size=min(50, max(1, int(data.get("batch_size", 20)))),
                request_interval=min(30.0, max(0.5, float(data.get("request_interval", 1.5)))),
                retries=min(4, max(0, int(data.get("retries", 2)))),
            )
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")

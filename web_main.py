from __future__ import annotations

import sys
import os
import threading
import webbrowser
from pathlib import Path

import uvicorn

from g_team_ops.web import create_app


HOST = "127.0.0.1"
PORT = 8765


def app_data_dir() -> Path:
    root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    return root / "data"


app = create_app(app_data_dir())


def main() -> None:
    url = f"http://{HOST}:{PORT}"
    if os.environ.get("G_TEAM_OPS_NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"G组运营工作台已启动：{url}")
    print("请保持此窗口开启；关闭窗口即停止本机后端。")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info", access_log=False)


if __name__ == "__main__":
    main()

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOGGER_NAME = "fba_tracker"


def configure_logging(data_dir: Path) -> logging.Logger:
    """配置本地滚动日志；只记录运行事件，不记录密码、令牌或请求正文。"""
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = (log_dir / "web.log").resolve()
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    for handler in list(logger.handlers):
        if isinstance(handler, RotatingFileHandler):
            if Path(handler.baseFilename).resolve() == log_path:
                return logger
            logger.removeHandler(handler)
            handler.close()

    handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger.addHandler(handler)
    return logger

"""Shared logging setup for crawler platform entry points."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from platform.config import LOG_DIR


DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def configure_file_logging(
    filename: str,
    *,
    level: int = logging.INFO,
    force: bool = False,
) -> Path:
    """Configure root logging to write both console and project log file output."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / filename

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_path, encoding="utf-8"),
    ]

    logging.basicConfig(
        level=level,
        format=DEFAULT_FORMAT,
        handlers=handlers,
        force=force,
    )
    return log_path


def route_uvicorn_logs_to_root() -> None:
    """Let uvicorn loggers use the root handlers configured above."""
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.propagate = True


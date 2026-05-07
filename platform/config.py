"""Configuration for crawler platform services."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", "123456"),
    "database": os.getenv("DB_NAME", "test"),
}
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

CRAWLER_MODULES = {
    "afu": "afu.afu",
    "doubao": "doubao.doubao",
    "deepseek": "deepseek.deepseek",
    "yuanbao": "yuanbao.yuanbao",
}

DISPATCH_INTERVAL = int(os.getenv("DISPATCH_INTERVAL", "5"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "100"))
EXECUTE_CRAWLERS = os.getenv("EXECUTE_CRAWLERS", "0") == "1"
CONSUMER_MAX_RETRIES = int(os.getenv("CONSUMER_MAX_RETRIES", "3"))

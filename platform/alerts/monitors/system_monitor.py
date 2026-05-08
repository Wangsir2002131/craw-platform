"""Monitor for system-related alerts."""

from __future__ import annotations

import logging
from typing import Any

from platform.alerts.alert_levels import AlertCategory, AlertLevel
from platform.alerts.monitors.base import BaseMonitor
from platform.config import DB_CONFIG
from platform.store.db_store import TaskMasterStatusStore

logger = logging.getLogger(__name__)


class SystemMonitor(BaseMonitor):
    """Monitor system resource usage and health.

    Checks:
    - Memory usage > memory_threshold (RED), default 80%
    - Database connection failure (ERROR)
    """

    def __init__(
        self,
        *args: Any,
        memory_threshold: float = 0.8,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.memory_threshold = max(0.1, min(1.0, float(memory_threshold)))
        self.db_store = TaskMasterStatusStore(DB_CONFIG)

    def check(self) -> None:
        """Check system metrics and trigger alerts if thresholds exceeded."""
        self._check_memory_usage()
        self._check_database_connection()

    # ------------------------------------------------------------------
    #  Memory usage check
    # ------------------------------------------------------------------

    def _check_memory_usage(self) -> None:
        """检查系统内存使用率 — 触发红色告警"""
        try:
            import psutil
        except ImportError:
            logger.debug("psutil not installed, skipping memory check")
            return

        try:
            memory = psutil.virtual_memory()
            memory_percent = memory.percent / 100.0

            if memory_percent > self.memory_threshold:
                self.alert_manager.trigger(
                    name="memory_usage_high",
                    level=AlertLevel.RED,
                    category=AlertCategory.SYSTEM,
                    message=(
                        f"系统内存使用率超过 {self.memory_threshold * 100:.0f}%: "
                        f"当前 {memory_percent * 100:.1f}%"
                    ),
                    metadata={
                        "memory_percent": round(memory_percent, 4),
                        "threshold": self.memory_threshold,
                        "total_gb": round(memory.total / (1024 ** 3), 2),
                        "available_gb": round(memory.available / (1024 ** 3), 2),
                        "used_gb": round(memory.used / (1024 ** 3), 2),
                    },
                    suppress_seconds=120,
                )
        except Exception as e:
            logger.warning("memory usage check failed: %s", e)

    # ------------------------------------------------------------------
    #  Database connection check
    # ------------------------------------------------------------------

    def _check_database_connection(self) -> None:
        """检查数据库连接 — 触发错误告警"""
        try:
            db_store = self.db_store
            with db_store.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except Exception as e:
            self.alert_manager.trigger(
                name="database_connection_failure",
                level=AlertLevel.ERROR,
                category=AlertCategory.SYSTEM,
                message=f"数据库连接失败: {e}",
                metadata={
                    "error": str(e),
                    "db_host": DB_CONFIG.get("host", "unknown"),
                    "db_name": DB_CONFIG.get("database", DB_CONFIG.get("db", "unknown")),
                },
                suppress_seconds=60,
            )

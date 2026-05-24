"""Monitor for system-related alerts."""

from __future__ import annotations

import logging
import os
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
    - Database connection failure (RED)
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
        self._db_failure_count = 0

    def check(self) -> None:
        """Check system metrics and trigger alerts if thresholds exceeded."""
        self._check_memory_usage()
        self._check_database_connection()

    # ------------------------------------------------------------------
    #  Memory usage check
    # ------------------------------------------------------------------

    def _check_memory_usage(self) -> None:
        """检查系统内存使用率 — 触发红色告警。

        优先使用 psutil，如未安装或调用失败则回退到 /proc/meminfo 或 Windows GlobalMemoryStatus。
        注意：psutil 可能安装但 virtual_memory() 在部分环境抛异常（如权限不足），
        此时也走回退方案，避免 memory_percent 未绑定导致 UnboundLocalError。
        """
        memory_percent: float | None = None
        total_gb: float | None = None
        available_gb: float | None = None
        used_gb: float | None = None

        # 尝试 psutil
        try:
            import psutil
            memory = psutil.virtual_memory()
            memory_percent = memory.percent / 100.0
            total_gb = round(memory.total / (1024 ** 3), 2)
            available_gb = round(memory.available / (1024 ** 3), 2)
            used_gb = round(memory.used / (1024 ** 3), 2)
        except ImportError:
            logger.debug("psutil not installed, using fallback for memory info")
        except Exception:
            logger.debug("psutil call failed (permission/OS error), trying fallback")

        # 回退方案
        if memory_percent is None:
            mem_info = self._get_memory_info_fallback()
            if mem_info is None:
                logger.debug("unable to read memory info (no psutil, no fallback)")
                return
            memory_percent, total_gb, available_gb, used_gb = mem_info

        try:
            if memory_percent > self.memory_threshold:
                self.alert_manager.trigger(
                    name="memory_usage_high",
                    level=AlertLevel.RED,
                    category=AlertCategory.SYSTEM,
                    message=(
                        f"系统内存使用率超过 {self.memory_threshold * 100:.0f}%: "
                        f"当前 <strong style=\"color:#e53935\">{memory_percent * 100:.1f}%</strong>"
                    ),
                    metadata={
                        "memory_percent": round(memory_percent, 4),
                        "threshold": self.memory_threshold,
                        "total_gb": total_gb,
                        "available_gb": available_gb,
                        "used_gb": used_gb,
                    },
                    suppress_seconds=120,
                )
            else:
                # 内存恢复正常：消除告警
                self.alert_manager.auto_resolve("memory_usage_high")
        except Exception as e:
            logger.warning("memory usage check failed: %s", e)

    @staticmethod
    def _get_memory_info_fallback() -> tuple[float, float, float, float] | None:
        """不使用 psutil 时获取内存信息的回退方案。

        Returns (memory_percent, total_gb, available_gb, used_gb) or None.
        """
        # Linux: /proc/meminfo
        try:
            with open("/proc/meminfo", "r") as f:
                meminfo = {}
                for line in f:
                    parts = line.split(":")
                    if len(parts) >= 2:
                        key = parts[0].strip()
                        val = parts[1].strip().split()[0]
                        try:
                            meminfo[key] = int(val)
                        except ValueError:
                            pass
                total_kb = meminfo.get("MemTotal", 0)
                available_kb = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))
                if total_kb > 0:
                    used_kb = total_kb - available_kb
                    percent = used_kb / total_kb
                    return (
                        round(percent, 4),
                        round(total_kb / (1024 ** 2), 2),
                        round(available_kb / (1024 ** 2), 2),
                        round(used_kb / (1024 ** 2), 2),
                    )
        except (OSError, FileNotFoundError):
            pass

        # Windows: kernel32 GlobalMemoryStatusEx
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            mem_status = MEMORYSTATUSEX()
            mem_status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem_status)):
                percent = mem_status.dwMemoryLoad / 100.0
                total_gb = round(mem_status.ullTotalPhys / (1024 ** 3), 2)
                avail_gb = round(mem_status.ullAvailPhys / (1024 ** 3), 2)
                used_gb = round((mem_status.ullTotalPhys - mem_status.ullAvailPhys) / (1024 ** 3), 2)
                return (round(percent, 4), total_gb, avail_gb, used_gb)
        except (ImportError, AttributeError, OSError):
            pass

        return None

    # ------------------------------------------------------------------
    #  Database connection check
    # ------------------------------------------------------------------

    def _check_database_connection(self) -> None:
        """检查数据库连接 — 红色告警（原错误告警已合并）。"""
        # 首次检查：从 DB 恢复告警状态，确保重启后能检测到状态变化
        if self._db_failure_count == 0 and not getattr(self, '_db_health_loaded', False):
            self._load_db_health_state()
            self._db_health_loaded = True

        try:
            db_store = self.db_store
            with db_store.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            if self._db_failure_count > 0:
                # DB 恢复：消除告警
                self.alert_manager.auto_resolve("database_connection_failure")
                logger.info("database connection recovered after %d failure(s)", self._db_failure_count)
            self._db_failure_count = 0
        except Exception as e:
            self._db_failure_count += 1
            self.alert_manager.trigger(
                name="database_connection_failure",
                level=AlertLevel.RED,
                category=AlertCategory.SYSTEM,
                message=f"数据库连接失败（第 {self._db_failure_count} 次）: {e}",
                metadata={
                    "error": str(e),
                    "failure_count": self._db_failure_count,
                    "db_host": DB_CONFIG.get("host", "unknown"),
                    "db_name": DB_CONFIG.get("database", DB_CONFIG.get("db", "unknown")),
                },
                suppress_seconds=60,
            )

    def _load_db_health_state(self) -> None:
        """从 alert_events 表恢复 _db_failure_count。

        若 DB 中存在 database_connection_failure 告警，则设置 failure_count=1，
        下一次连接成功时即可触发 auto_resolve。
        """
        try:
            with self.db_store.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM alert_events WHERE name = %s AND acknowledged = 0",
                    ("database_connection_failure",),
                )
                if cursor.fetchone():
                    self._db_failure_count = 1
                    logger.debug("database connection alert found in db, setting failure_count=1")
        except Exception:
            logger.debug("failed to load db health state from db", exc_info=True)

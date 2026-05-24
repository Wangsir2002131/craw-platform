"""Monitor for account-related alerts."""

from __future__ import annotations

import logging
from typing import Any

from platform.alerts.alert_levels import AlertCategory, AlertLevel
from platform.alerts.monitors.base import BaseMonitor
from platform.config import DB_CONFIG
from platform.store.db_store import TaskMasterStatusStore

logger = logging.getLogger(__name__)


class AccountMonitor(BaseMonitor):
    """Monitor account availability and health.

    Checks:
    - Available account count below threshold (YELLOW)
    - Account in error/disabled state (RED)
    """

    def __init__(
        self,
        *args: Any,
        available_threshold: int = 5,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.available_threshold = max(5, int(available_threshold))
        self.db_store = TaskMasterStatusStore(DB_CONFIG)
        self._low_platform_states: set[str] = set()
        self._low_platform_counts: dict[str, int] = {}
        self._error_account_states: set[str] = set()
        self._error_account_details: dict[str, dict] = {}

    def reset_states(self) -> None:
        """Reset per-account alert states and clean up old alerts."""
        for key in self._low_platform_states:
            self.alert_manager.auto_resolve(f"account_available_low:{key}")
        for key in self._error_account_states:
            self.alert_manager.auto_resolve(f"account_error_state:{key}")
        self._low_platform_states.clear()
        self._low_platform_counts.clear()
        self._error_account_states.clear()
        self._error_account_details.clear()

    def check(self) -> None:
        """Check account metrics and trigger alerts if thresholds exceeded."""
        self._check_available_accounts()
        self._check_error_accounts()

    def _check_available_accounts(self) -> None:
        """检查可用账号数量 - 触发黄色告警，恢复后自动消除。"""
        try:
            with self.db_store.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        platform_name,
                        SUM(CASE WHEN account_status = 'available' THEN 1 ELSE 0 END) AS available_count
                    FROM account_master
                    GROUP BY platform_name
                    """
                )
                rows = cursor.fetchall() or []

            current_low: set[str] = set()
            current_counts: dict[str, int] = {}
            for row in rows:
                platform = row.get("platform_name", "unknown")
                available = int(row.get("available_count") or 0)
                if available < self.available_threshold:
                    current_low.add(platform)
                    current_counts[platform] = available
                    prev_count = self._low_platform_counts.get(platform)
                    if platform not in self._low_platform_states or prev_count != available:
                        self.alert_manager.trigger(
                            name=f"account_available_low:{platform}",
                            level=AlertLevel.YELLOW,
                            category=AlertCategory.ACCOUNT,
                            message=f"平台 {platform} 可用账号不足: <strong style=\"color:#e53935\">{available}</strong> < {self.available_threshold}",
                            metadata={
                                "platform_name": platform,
                                "available_count": available,
                                "threshold": self.available_threshold,
                            },
                        )

            # 首次检查：从 DB 恢复内存状态，确保重启后能检测到状态变化
            if not self._low_platform_states and not getattr(self, '_low_platform_loaded', False):
                self._load_low_platform_states()
                self._low_platform_loaded = True

            # 自动消除已恢复的可用账号不足告警
            resolved_keys = self._low_platform_states - current_low
            for key in resolved_keys:
                self.alert_manager.auto_resolve(f"account_available_low:{key}")
                logger.info("account available low alert resolved: %s", key)

            self._low_platform_states = current_low
            self._low_platform_counts = current_counts
        except Exception as e:
            logger.warning("account available check failed: %s", e)

    def _check_error_accounts(self) -> None:
        """检查错误/禁用状态账号 - 触发红色告警，恢复后自动消除。"""
        try:
            with self.db_store.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        id,
                        account_name,
                        platform_name,
                        account_status,
                        disabled_reason
                    FROM account_master
                    WHERE account_status IN ('error', 'disabled')
                    """,
                )
                rows = cursor.fetchall() or []

            current_error: set[str] = set()
            for row in rows:
                account_name = row.get("account_name") or str(row.get("id", "unknown"))
                platform = row.get("platform_name", "unknown")
                key = f"{platform}:{account_name}"
                current_error.add(key)
                current_status = row.get("account_status", "")
                current_reason = row.get("disabled_reason") or ""
                prev_details = self._error_account_details.get(key, {})
                status_changed = (
                    key not in self._error_account_states
                    or prev_details.get("status") != current_status
                    or prev_details.get("reason") != current_reason
                )
                if status_changed:
                    self._error_account_details[key] = {"status": current_status, "reason": current_reason}
                    self.alert_manager.trigger(
                        name=f"account_error_state:{key}",
                        level=AlertLevel.RED,
                        category=AlertCategory.ACCOUNT,
                        message=f"账号异常: {account_name}（{platform}）状态={current_status}",
                        metadata={
                            "account_id": row.get("id"),
                            "account_name": account_name,
                            "platform_name": platform,
                            "account_status": current_status,
                            "disabled_reason": current_reason,
                        },
                    )

            # 首次检查：从 DB 恢复内存状态，确保重启后能检测到状态变化
            if not self._error_account_states and not getattr(self, '_error_account_loaded', False):
                self._load_error_account_states()
                self._error_account_loaded = True

            # 自动消除已恢复的错误账号告警
            resolved_keys = self._error_account_states - current_error
            for key in resolved_keys:
                self.alert_manager.auto_resolve(f"account_error_state:{key}")
                logger.info("account error state alert resolved: %s", key)

            self._error_account_states = current_error
        except Exception as e:
            logger.warning("error account check failed: %s", e)

    # ------------------------------------------------------------------
    #  状态恢复工具方法（从 DB 重建内存状态，处理重启后内存丢失）
    # ------------------------------------------------------------------

    def _load_low_platform_states(self) -> None:
        """从 alert_events 表恢复 _low_platform_states。"""
        try:
            with self.db_store.cursor() as cursor:
                cursor.execute(
                    "SELECT name FROM alert_events WHERE name LIKE %s AND acknowledged = 0",
                    ("account_available_low:%",),
                )
                rows = cursor.fetchall() or []
            for row in rows:
                key = row["name"][len("account_available_low:"):]
                if key:
                    self._low_platform_states.add(key)
            if self._low_platform_states:
                logger.debug("loaded %d low platform states from db", len(self._low_platform_states))
        except Exception:
            logger.debug("failed to load low platform states from db", exc_info=True)

    def _load_error_account_states(self) -> None:
        """从 alert_events 表恢复 _error_account_states。"""
        try:
            with self.db_store.cursor() as cursor:
                cursor.execute(
                    "SELECT name FROM alert_events WHERE name LIKE %s AND acknowledged = 0",
                    ("account_error_state:%",),
                )
                rows = cursor.fetchall() or []
            for row in rows:
                key = row["name"][len("account_error_state:"):]
                if key:
                    self._error_account_states.add(key)
            if self._error_account_states:
                logger.debug("loaded %d error account states from db", len(self._error_account_states))
        except Exception:
            logger.debug("failed to load error account states from db", exc_info=True)


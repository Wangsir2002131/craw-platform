"""Monitor for account-related alerts."""

from __future__ import annotations

import logging
from typing import Any

from craw_platform.alerts.alert_levels import AlertCategory, AlertLevel
from craw_platform.alerts.monitors.base import BaseMonitor
from craw_platform.config import DB_CONFIG
from craw_platform.store.db_store import TaskMasterStatusStore

logger = logging.getLogger(__name__)


class AccountMonitor(BaseMonitor):
    """Monitor account availability and health.

    Checks:
    - Available account count below threshold (YELLOW)
    - Account in error/disabled state (RED)
    - Account error rate > 30% (ERROR)
    """

    def __init__(
        self,
        *args: Any,
        available_threshold: int = 5,
        error_rate_threshold: float = 0.3,
        lookback_seconds: int = 600,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.available_threshold = max(1, int(available_threshold))
        self.error_rate_threshold = error_rate_threshold
        self.lookback_seconds = lookback_seconds
        self.db_store = TaskMasterStatusStore(DB_CONFIG)
        self._low_platform_states: set[str] = set()
        self._error_account_states: set[str] = set()
        self._high_error_rate_states: set[str] = set()

    def reset_states(self) -> None:
        """Reset per-account alert states so next check re-evaluates from scratch."""
        self._low_platform_states.clear()
        self._error_account_states.clear()
        self._high_error_rate_states.clear()

    def check(self) -> None:
        """Check account metrics and trigger alerts if thresholds exceeded."""
        self._check_available_accounts()
        self._check_error_accounts()
        self._check_account_error_rate()

    def _check_available_accounts(self) -> None:
        """检查可用账号数量 - 触发黄色告警"""
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
            for row in rows:
                platform = row.get("platform_name", "unknown")
                available = int(row.get("available_count") or 0)
                if available < self.available_threshold:
                    current_low.add(platform)
                    if platform not in self._low_platform_states:
                        self.alert_manager.trigger(
                            name=f"account_available_low:{platform}",
                            level=AlertLevel.YELLOW,
                            category=AlertCategory.ACCOUNT,
                            message=f"平台 {platform} 可用账号不足: {available} < {self.available_threshold}",
                            metadata={
                                "platform_name": platform,
                                "available_count": available,
                                "threshold": self.available_threshold,
                            },
                        )
            self._low_platform_states = current_low
        except Exception as e:
            logger.warning("account available check failed: %s", e)

    def _check_error_accounts(self) -> None:
        """检查错误/禁用状态账号 - 触发红色告警"""
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
                if key not in self._error_account_states:
                    self.alert_manager.trigger(
                        name=f"account_error_state:{key}",
                        level=AlertLevel.RED,
                        category=AlertCategory.ACCOUNT,
                        message=f"账号异常: {account_name}（{platform}）状态={row.get('account_status')}",
                        metadata={
                            "account_id": row.get("id"),
                            "account_name": account_name,
                            "platform_name": platform,
                            "account_status": row.get("account_status"),
                            "disabled_reason": row.get("disabled_reason"),
                        },
                    )
            self._error_account_states = current_error
        except Exception as e:
            logger.warning("error account check failed: %s", e)

    def _check_account_error_rate(self) -> None:
        """检查账号错误率 - 触发错误预警"""
        try:
            with self.db_store.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        a.id,
                        a.account_name,
                        a.platform_name,
                        COUNT(sl.id) AS total_ops,
                        SUM(CASE WHEN sl.new_status IN ('error', 'disabled') THEN 1 ELSE 0 END) AS error_ops
                    FROM account_master a
                    LEFT JOIN account_status_log sl
                        ON a.id = sl.account_id
                        AND sl.created_at >= DATE_SUB(NOW(), INTERVAL %s SECOND)
                    GROUP BY a.id, a.account_name, a.platform_name
                    HAVING total_ops > 0
                    """,
                    (self.lookback_seconds,),
                )
                rows = cursor.fetchall() or []

            current_high: set[str] = set()
            for row in rows:
                total_ops = int(row.get("total_ops") or 0)
                error_ops = int(row.get("error_ops") or 0)
                if total_ops > 0:
                    error_rate = error_ops / total_ops
                    account_name = row.get("account_name") or str(row.get("id", "unknown"))
                    platform = row.get("platform_name", "unknown")
                    key = f"{platform}:{account_name}"
                    if error_rate > self.error_rate_threshold:
                        current_high.add(key)
                        if key not in self._high_error_rate_states:
                            self.alert_manager.trigger(
                                name=f"account_error_rate:{key}",
                                level=AlertLevel.ERROR,
                                category=AlertCategory.ACCOUNT,
                                message=f"账号错误率超过阈值 {self.error_rate_threshold:.1%}: {account_name}（{platform}）当前 {error_rate:.1%}",
                                metadata={
                                    "account_id": row.get("id"),
                                    "account_name": account_name,
                                    "platform_name": platform,
                                    "total_ops": total_ops,
                                    "error_ops": error_ops,
                                    "error_rate": round(error_rate, 4),
                                    "threshold": self.error_rate_threshold,
                                },
                            )
            self._high_error_rate_states = current_high
        except Exception as e:
            logger.warning("account error rate check failed: %s", e)
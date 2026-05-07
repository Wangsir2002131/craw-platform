"""Alert rules for system monitoring."""

from __future__ import annotations

from dataclasses import dataclass

from platform.alerts.alert_levels import AlertCategory, AlertLevel
from platform.alerts.rules.base import BaseAlertRule


@dataclass
class SystemAlertRule(BaseAlertRule):
    """Rule for system-related alerts."""

    category: AlertCategory = AlertCategory.SYSTEM
    level: AlertLevel = AlertLevel.YELLOW

    @staticmethod
    def memory_high_rule(threshold: float = 0.8) -> SystemAlertRule:
        """内存使用率高告警规则（红色），默认超过 80%"""
        return SystemAlertRule(
            name="memory_usage_high",
            level=AlertLevel.RED,
            params={"memory_threshold": threshold},
            description=f"系统内存使用率超过 {threshold:.0%} 时触发红色告警",
        )

    @staticmethod
    def db_connection_failure_rule() -> SystemAlertRule:
        """数据库连接失败告警规则（错误级别）"""
        return SystemAlertRule(
            name="database_connection_failure",
            level=AlertLevel.ERROR,
            params={},
            description="数据库 SELECT 1 探测失败时触发错误告警",
        )

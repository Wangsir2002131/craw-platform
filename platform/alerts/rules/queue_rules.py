"""Alert rules for queue monitoring."""

from __future__ import annotations

from dataclasses import dataclass

from platform.alerts.alert_levels import AlertCategory, AlertLevel
from platform.alerts.rules.base import BaseAlertRule


@dataclass
class QueueAlertRule(BaseAlertRule):
    """Rule for queue-related alerts."""

    category: AlertCategory = AlertCategory.QUEUE
    level: AlertLevel = AlertLevel.YELLOW

    @staticmethod
    def length_warning_rule(threshold: int = 100) -> QueueAlertRule:
        """队列长度警告规则（黄色），默认超过 100"""
        return QueueAlertRule(
            name="queue_length_warning",
            level=AlertLevel.YELLOW,
            params={"length_threshold": threshold},
            description=f"队列积压超过 {threshold} 条时触发黄色告警",
        )

    @staticmethod
    def length_critical_rule(threshold: int = 500) -> QueueAlertRule:
        """队列长度危险规则（红色），默认超过 500"""
        return QueueAlertRule(
            name="queue_length_critical",
            level=AlertLevel.RED,
            params={"length_threshold": threshold},
            description=f"队列积压超过 {threshold} 条时触发红色告警",
        )

    @staticmethod
    def redis_connection_error_rule() -> QueueAlertRule:
        """Redis 连接异常告警规则（红色）"""
        return QueueAlertRule(
            name="redis_connection_error",
            level=AlertLevel.RED,
            params={},
            description="Redis Ping 无响应或连接中断时触发红色告警",
        )

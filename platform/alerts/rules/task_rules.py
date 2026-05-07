"""Alert rules for task monitoring."""

from __future__ import annotations

from dataclasses import dataclass

from platform.alerts.alert_levels import AlertCategory, AlertLevel
from platform.alerts.rules.base import BaseAlertRule


@dataclass
class TaskAlertRule(BaseAlertRule):
    """Rule for task-related alerts.

    Provides static factory methods for common task alert rules.
    """

    category: AlertCategory = AlertCategory.TASK
    level: AlertLevel = AlertLevel.YELLOW

    @staticmethod
    def timeout_rule(timeout_seconds: int = 300) -> TaskAlertRule:
        """任务执行超时告警规则（黄色）"""
        return TaskAlertRule(
            name="task_timeout",
            level=AlertLevel.YELLOW,
            params={"timeout_seconds": timeout_seconds},
            description=f"任务执行超过 {timeout_seconds} 秒未完成时触发黄色告警",
        )

    @staticmethod
    def failure_rate_rule(threshold: float = 0.1) -> TaskAlertRule:
        """任务失败率告警规则（红色），默认 10%"""
        return TaskAlertRule(
            name="task_failure_rate",
            level=AlertLevel.RED,
            params={"failure_rate_threshold": threshold},
            description=f"任务失败率超过 {threshold:.0%} 时触发红色告警",
        )

    @staticmethod
    def error_rate_rule(threshold: float = 0.3) -> TaskAlertRule:
        """任务错误率预警规则（错误级别），默认 30%"""
        return TaskAlertRule(
            name="task_error_rate",
            level=AlertLevel.ERROR,
            params={"error_rate_threshold": threshold},
            description=f"任务错误率超过 {threshold:.0%} 时触发错误告警",
        )

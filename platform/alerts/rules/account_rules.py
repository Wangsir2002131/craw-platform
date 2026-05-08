"""Alert rules for account monitoring."""

from __future__ import annotations

from dataclasses import dataclass

from platform.alerts.alert_levels import AlertCategory, AlertLevel
from platform.alerts.rules.base import BaseAlertRule


@dataclass
class AccountAlertRule(BaseAlertRule):
    """Rule for account-related alerts."""

    category: AlertCategory = AlertCategory.ACCOUNT
    level: AlertLevel = AlertLevel.YELLOW

    @staticmethod
    def available_low_rule(threshold: int = 5) -> AccountAlertRule:
        """可用账号数量低告警规则（黄色），默认低于 5 个"""
        return AccountAlertRule(
            name="account_available_low",
            level=AlertLevel.YELLOW,
            params={"available_threshold": threshold},
            description=f"平台可用账号数量低于 {threshold} 个时触发黄色告警",
        )

    @staticmethod
    def error_state_rule() -> AccountAlertRule:
        """账号异常状态告警规则（红色）"""
        return AccountAlertRule(
            name="account_error_state",
            level=AlertLevel.RED,
            params={},
            description="账号进入 error 或 disabled 状态时触发红色告警",
        )

    @staticmethod
    def error_rate_rule(threshold: float = 0.3) -> AccountAlertRule:
        """账号错误率预警规则（错误级别），默认 30%"""
        return AccountAlertRule(
            name="account_error_rate",
            level=AlertLevel.ERROR,
            params={"error_rate_threshold": threshold},
            description=f"账号操作错误率超过 {threshold:.0%} 时触发错误告警",
        )

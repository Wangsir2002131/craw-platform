"""Alert levels and severity definitions."""

from __future__ import annotations

from enum import Enum
from typing import Final


class AlertLevel(str, Enum):
    """Alert severity levels in ascending order — ERROR merged into RED."""

    YELLOW = "yellow"
    RED = "red"

    def priority(self) -> int:
        """Return numeric priority for sorting (higher = more severe)."""
        return {
            AlertLevel.YELLOW: 1,
            AlertLevel.RED: 2,
        }.get(self, 0)


class AlertCategory(str, Enum):
    """Alert categories for classification."""

    TASK = "task"
    QUEUE = "queue"
    ACCOUNT = "account"
    SYSTEM = "system"


ALERT_LEVELS: Final[tuple[AlertLevel, ...]] = (
    AlertLevel.YELLOW,
    AlertLevel.RED,
)

ALERT_CATEGORY_DISPLAY: Final[dict[AlertCategory, str]] = {
    AlertCategory.TASK: "任务告警",
    AlertCategory.QUEUE: "队列告警",
    AlertCategory.ACCOUNT: "账号告警",
    AlertCategory.SYSTEM: "系统告警",
}

ALERT_LEVEL_DISPLAY: Final[dict[AlertLevel, str]] = {
    AlertLevel.YELLOW: "🟡 黄色告警",
    AlertLevel.RED: "🔴 红色告警（含错误告警）",
}

ALERT_LEVEL_WEIGHT: Final[dict[AlertLevel, int]] = {
    AlertLevel.YELLOW: 1,
    AlertLevel.RED: 2,
}
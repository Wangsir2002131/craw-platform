"""Base alert rule definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from craw_platform.alerts.alert_levels import AlertCategory, AlertLevel


@dataclass
class BaseAlertRule:
    """Base class for all alert rules.

    Each concrete rule defines:
    - A unique name (e.g. "task_timeout")
    - The alert category and severity level
    - Configurable threshold parameters
    - Whether the rule is currently enabled
    """

    name: str                                          # 规则唯一名称
    category: AlertCategory                            # 所属类别（TASK/QUEUE/ACCOUNT/SYSTEM）
    level: AlertLevel = AlertLevel.YELLOW              # 默认告警级别
    params: dict[str, Any] = field(default_factory=dict)  # 阈值参数
    enabled: bool = True                               # 是否启用
    description: str = ""                               # 规则描述

    def to_dict(self) -> dict[str, Any]:
        """Serialize rule to JSON-compatible dict."""
        return {
            "name": self.name,
            "category": self.category.value,
            "level": self.level.value,
            "params": self.params,
            "enabled": self.enabled,
            "description": self.description,
        }

    def is_threshold_exceeded(self, current_value: float, threshold: float) -> bool:
        """Check if current value exceeds the threshold (for upper-bound checks)."""
        return current_value > threshold

    def is_below_threshold(self, current_value: float, threshold: float) -> bool:
        """Check if current value is below the threshold (for lower-bound checks like available accounts)."""
        return current_value < threshold

"""Alert monitoring and notification module for the crawler platform.

Exports:
    AlertManager - Central alert manager (singleton)
    AlertEvent - Alert event dataclass
    AlertLevel / AlertCategory - Severity and category enums
    BaseMonitor - Abstract base for monitors
    BaseNotifier - Abstract base for notification channels
    TaskMonitor / QueueMonitor / AccountMonitor / SystemMonitor - Concrete monitors
    TaskAlertRule / QueueAlertRule / AccountAlertRule / SystemAlertRule - Rule definitions
"""

from craw_platform.alerts.alert_levels import (
    ALERT_CATEGORY_DISPLAY,
    ALERT_LEVEL_DISPLAY,
    ALERT_LEVELS,
    ALERT_LEVEL_WEIGHT,
    AlertCategory,
    AlertLevel,
)
from craw_platform.alerts.alert_manager import (
    AlertEvent,
    AlertManager,
    get_alert_manager,
)
from craw_platform.alerts.monitors import (
    AccountMonitor,
    QueueMonitor,
    SystemMonitor,
    TaskMonitor,
)
from craw_platform.alerts.monitors.base import BaseMonitor
from craw_platform.alerts.notifiers import (
    BaseNotifier,
    ConsoleNotifier,
    LogNotifier,
)
from craw_platform.alerts.rules import (
    AccountAlertRule,
    QueueAlertRule,
    SystemAlertRule,
    TaskAlertRule,
)

__all__ = [
    # Core
    "AlertEvent",
    "AlertManager",
    "get_alert_manager",
    # Levels
    "AlertLevel",
    "AlertCategory",
    "ALERT_LEVELS",
    "ALERT_LEVEL_DISPLAY",
    "ALERT_CATEGORY_DISPLAY",
    "ALERT_LEVEL_WEIGHT",
    # Monitors
    "BaseMonitor",
    "TaskMonitor",
    "QueueMonitor",
    "AccountMonitor",
    "SystemMonitor",
    # Notifiers
    "BaseNotifier",
    "LogNotifier",
    "ConsoleNotifier",
    # Rules
    "TaskAlertRule",
    "QueueAlertRule",
    "AccountAlertRule",
    "SystemAlertRule",
]
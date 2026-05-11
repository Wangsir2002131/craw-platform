"""Log-based alert notifier using Python's logging framework."""

from __future__ import annotations

import logging

from craw_platform.alerts.alert_manager import AlertEvent
from craw_platform.alerts.notifiers.base import BaseNotifier

logger = logging.getLogger(__name__)


class LogNotifier(BaseNotifier):
    """Delivers alert notifications via the standard logging system.

    Maps alert levels to log severity:
        YELLOW / RED → WARNING
        ERROR        → ERROR
    """

    def __init__(self, *, enabled: bool = True, logger_name: str | None = None) -> None:
        super().__init__(name="log", enabled=enabled)
        self._target_logger = logging.getLogger(logger_name or "platform.alerts")

    def notify(self, event: AlertEvent) -> bool:
        message = self.format_message(event)
        log_level = logging.WARNING if event.level.value != "error" else logging.ERROR
        self._target_logger.log(log_level, "\n%s", message)
        return True

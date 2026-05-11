"""Console / stdout alert notifier for development and debugging."""

from __future__ import annotations

import sys

from craw_platform.alerts.alert_manager import AlertEvent
from craw_platform.alerts.alert_levels import ALERT_CATEGORY_DISPLAY, ALERT_LEVEL_DISPLAY
from craw_platform.alerts.notifiers.base import BaseNotifier


class ConsoleNotifier(BaseNotifier):
    """Prints formatted alert events to stdout (or a configurable stream).

    Useful during development or when running in a terminal.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        stream: object | None = None,
        use_colour: bool = True,
    ) -> None:
        super().__init__(name="console", enabled=enabled)
        self.stream = stream or sys.stdout
        self.use_colour = bool(use_colour)

    def notify(self, event: AlertEvent) -> bool:
        level_display = ALERT_LEVEL_DISPLAY.get(event.level, event.level.value)
        category_display = ALERT_CATEGORY_DISPLAY.get(event.category, event.category.value)

        if self.use_colour:
            prefix = {
                "yellow": "\033[33m",  # yellow
                "red": "\033[31m",     # red
                "error": "\033[35m",   # magenta
            }.get(event.level.value, "")
            reset = "\033[0m" if prefix else ""
        else:
            prefix = ""
            reset = ""

        lines = [
            f"{'=' * 60}",
            f"{prefix}[{level_display}] {category_display}: {event.name}{reset}",
            f"  消息: {event.message}",
            f"  触发时间: {event.triggered_at}",
            f"  事件ID: {event.id}",
        ]
        if event.metadata:
            lines.append(f"  元数据: {event.metadata}")
        lines.append(f"{'=' * 60}")

        self._safe_print("\n".join(lines))
        return True

    def _safe_print(self, message: str) -> None:
        encoding = getattr(self.stream, "encoding", None) or sys.getdefaultencoding()
        safe_message = message.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe_message, file=self.stream, flush=True)

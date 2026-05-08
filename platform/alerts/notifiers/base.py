"""Base notifier for alert notifications."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from platform.alerts.alert_manager import AlertEvent

logger = logging.getLogger(__name__)


class BaseNotifier(ABC):
    """Abstract base class for alert notification channels.

    Each notifier represents a delivery channel (e.g. log, console, email, webhook).
    Subclasses must implement the ``notify`` method.
    """

    def __init__(self, *, name: str, enabled: bool = True) -> None:
        self.name = name
        self.enabled = bool(enabled)

    @abstractmethod
    def notify(self, event: AlertEvent) -> bool:
        """Send notification for the given alert event.

        Returns True on success, False on failure.
        """
        raise NotImplementedError

    def format_message(self, event: AlertEvent) -> str:
        """Format an alert event into a human-readable message string."""
        lines = [
            f"[{event.level.value.upper()}] [{event.category.value}] {event.name}",
            f"  Message: {event.message}",
            f"  Time: {event.triggered_at}",
            f"  Event ID: {event.id}",
        ]
        if event.metadata:
            for key, value in event.metadata.items():
                lines.append(f"  {key}: {value}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, enabled={self.enabled})"
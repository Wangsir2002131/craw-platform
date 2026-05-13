"""Base monitor for alert rules."""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod

from platform.alerts.alert_manager import AlertManager, get_alert_manager

logger = logging.getLogger(__name__)


class BaseMonitor(ABC):
    """Abstract base class for alert monitors.

    Each monitor runs in a background thread and periodically checks
    its target metrics, triggering alerts when thresholds are exceeded.
    """

    def __init__(
        self,
        alert_manager: AlertManager | None = None,
        *,
        interval: int = 10,
        enabled: bool = True,
    ) -> None:
        self.alert_manager = alert_manager or get_alert_manager()
        self.interval = max(1, int(interval))
        self.enabled = bool(enabled)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._check_count = 0
        self._error_count = 0

    @abstractmethod
    def check(self) -> None:
        """Perform the monitoring check and trigger alerts if needed.

        Subclasses must implement this method.
        """
        raise NotImplementedError

    def reset_states(self) -> None:
        """Reset any in-memory state tracking (e.g. previous alert states).

        Override in subclasses that maintain state between check cycles.
        """

    def force_check(self) -> None:
        """Run check() immediately without resetting state.

        Whether to reset state before checking is the caller's responsibility
        (e.g. the force-check API resets states explicitly when clear_history=True).
        Decoupling the two operations prevents duplicate alerts when history is
        preserved across refreshes (clear_history=False).
        """
        if self.enabled:
            self.check()

    @property
    def is_running(self) -> bool:
        """Check if the monitor thread is active."""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Start the monitor in a daemon background thread."""
        if self.is_running:
            logger.warning("%s is already running", self.monitor_name)
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name=f"alert-monitor-{self.monitor_name}",
        )
        self._thread.start()
        logger.info("%s started with interval=%ds", self.monitor_name, self.interval)

    def stop(self, *, timeout: float = 5.0) -> None:
        """Stop the monitor gracefully."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        logger.info("%s stopped (checks=%d errors=%d)", self.monitor_name, self._check_count, self._error_count)

    @property
    def monitor_name(self) -> str:
        """Return the human-readable monitor name."""
        return self.__class__.__name__

    def _run_loop(self) -> None:
        """Internal loop that calls check() at the configured interval."""
        logger.info("%s loop started", self.monitor_name)
        while not self._stop_event.is_set():
            try:
                if self.enabled:
                    self.check()
                    self._check_count += 1
            except Exception:
                self._error_count += 1
                logger.exception("%s check failed", self.monitor_name)

            self._stop_event.wait(self.interval)
"""Central alert manager for the crawler platform."""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, TYPE_CHECKING

from platform.alerts.alert_levels import (
    AlertCategory,
    AlertLevel,
    ALERT_CATEGORY_DISPLAY,
    ALERT_LEVEL_DISPLAY,
)

if TYPE_CHECKING:
    from platform.alerts.notifiers.base import BaseNotifier

logger = logging.getLogger(__name__)


@dataclass
class AlertEvent:
    """Represents a triggered alert event."""

    id: str
    name: str
    level: AlertLevel
    category: AlertCategory
    message: str
    triggered_at: str
    metadata: dict[str, Any] = field(default_factory=dict)
    acknowledged: bool = False
    acknowledged_at: str | None = None
    acknowledged_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "level": self.level.value,
            "level_display": ALERT_LEVEL_DISPLAY.get(self.level, self.level.value),
            "category": self.category.value,
            "category_display": ALERT_CATEGORY_DISPLAY.get(self.category, self.category.value),
            "message": self.message,
            "triggered_at": self.triggered_at,
            "metadata": self.metadata,
            "acknowledged": self.acknowledged,
            "acknowledged_at": self.acknowledged_at,
            "acknowledged_by": self.acknowledged_by,
        }


class AlertManager:
    """Central alert management for the crawler platform.

    Provides:
    - Alert configuration management
    - Notifier registration and dispatch
    - Alert event triggering and storage
    - Alert acknowledgement
    - Alert event querying with filters
    """

    def __init__(
        self,
        *,
        client: Any | None = None,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._client = client
        self._client_factory = client_factory
        self._configs: dict[str, dict[str, Any]] = {}
        self._events: list[AlertEvent] = []
        self._lock = threading.RLock()
        self._max_events: int = 1000
        self._max_tracking_entries: int = 2000
        self._alert_counters: dict[str, int] = {}
        self._suppress_intervals: dict[str, int] = {}
        self._last_triggered_at: dict[str, datetime] = {}
        self._notifiers: list[BaseNotifier] = []

    def register_config(self, name: str, *, enabled: bool = True, params: dict[str, Any] | None = None) -> None:
        """Register or update an alert configuration."""
        with self._lock:
            self._configs[name] = {
                "enabled": enabled,
                "params": params or {},
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

    def get_config(self, name: str) -> dict[str, Any] | None:
        """Get alert configuration by name."""
        return self._configs.get(name)

    def list_configs(self) -> dict[str, dict[str, Any]]:
        """Get all alert configurations."""
        return dict(self._configs)

    def set_suppress_interval(self, name: str, seconds: int) -> None:
        """Set minimum interval between repeated alerts of the same name."""
        with self._lock:
            self._suppress_intervals[name] = max(0, int(seconds))

    def trigger(
        self,
        name: str,
        level: AlertLevel,
        category: AlertCategory,
        message: str,
        *,
        metadata: dict[str, Any] | None = None,
        suppress_seconds: int | None = None,
    ) -> AlertEvent | None:
        config = self._configs.get(name)
        if config is not None and not config.get("enabled", True):
            return None

        now = datetime.now(timezone.utc)
        suppress_secs = suppress_seconds or self._suppress_intervals.get(name, 0)

        with self._lock:
            if suppress_secs > 0:
                last_triggered = self._last_triggered_at.get(name)
                if last_triggered is not None:
                    elapsed = (now - last_triggered).total_seconds()
                    if elapsed < suppress_secs:
                        logger.debug("alert %s suppressed, elapsed=%.1fs < suppress=%ds", name, elapsed, suppress_secs)
                        return None

            event = AlertEvent(
                id=str(uuid.uuid4()),
                name=name,
                level=level,
                category=category,
                message=message,
                triggered_at=datetime.now(timezone.utc).isoformat(),
                metadata=dict(metadata or {}),
            )

            self._events.append(event)
            if len(self._events) > self._max_events:
                self._events = self._events[-self._max_events :]
            self._last_triggered_at[name] = now
            self._alert_counters[name] = self._alert_counters.get(name, 0) + 1

            # 防止 _last_triggered_at 和 _alert_counters 无限增长
            if len(self._last_triggered_at) > self._max_tracking_entries:
                # 保留最近触发的条目，淘汰最旧的
                sorted_items = sorted(self._last_triggered_at.items(), key=lambda x: x[1])
                to_remove = sorted_items[: len(sorted_items) - self._max_tracking_entries]
                for key, _ in to_remove:
                    del self._last_triggered_at[key]
                    self._alert_counters.pop(key, None)

        logger.debug(
            "alert triggered: %s [%s] %s: %s",
            ALERT_LEVEL_DISPLAY.get(level, level.value),
            ALERT_CATEGORY_DISPLAY.get(category, category.value),
            name,
            message,
        )

        self._notify_all(event)

        return event

    # ------------------------------------------------------------------
    #  Notifier management
    # ------------------------------------------------------------------

    def register_notifier(self, notifier: BaseNotifier) -> None:
        """Register a notification channel.

        Registered notifiers receive every triggered alert event.
        Duplicate registrations are ignored.
        """
        with self._lock:
            if notifier not in self._notifiers:
                self._notifiers.append(notifier)
                logger.info("notifier registered: %s", notifier.name)

    def unregister_notifier(self, notifier: BaseNotifier) -> None:
        """Unregister a previously registered notification channel."""
        with self._lock:
            if notifier in self._notifiers:
                self._notifiers.remove(notifier)
                logger.info("notifier unregistered: %s", notifier.name)

    def list_notifiers(self) -> list[dict[str, Any]]:
        """List all registered notifiers with their status."""
        with self._lock:
            return [
                {"name": n.name, "type": n.__class__.__name__, "enabled": n.enabled}
                for n in self._notifiers
            ]

    def _notify_all(self, event: AlertEvent) -> None:
        """Dispatch an alert event to all registered notifiers."""
        with self._lock:
            notifiers = list(self._notifiers)
        for notifier in notifiers:
            try:
                if notifier.enabled:
                    notifier.notify(event)
            except Exception:
                logger.exception("notifier %s failed for event %s", notifier.name, event.id)

    # ------------------------------------------------------------------
    #  Alert acknowledgement
    # ------------------------------------------------------------------

    def acknowledge(
        self,
        event_id: str,
        *,
        acknowledged_by: str = "system",
    ) -> bool:
        """Acknowledge an alert event by ID. Returns True if found and acknowledged."""
        with self._lock:
            for event in reversed(self._events):
                if event.id == event_id:
                    if event.acknowledged:
                        return False
                    event.acknowledged = True
                    event.acknowledged_at = datetime.now(timezone.utc).isoformat()
                    event.acknowledged_by = acknowledged_by
                    logger.info("alert acknowledged: id=%s name=%s by=%s", event_id, event.name, acknowledged_by)
                    return True
        return False

    def acknowledge_all(
        self,
        *,
        category: AlertCategory | None = None,
        level: AlertLevel | None = None,
        acknowledged_by: str = "system",
    ) -> int:
        """Acknowledge all matching unacknowledged alerts. Returns count acknowledged."""
        count = 0
        with self._lock:
            for event in reversed(self._events):
                if event.acknowledged:
                    continue
                if category is not None and event.category != category:
                    continue
                if level is not None and event.level != level:
                    continue
                event.acknowledged = True
                event.acknowledged_at = datetime.now(timezone.utc).isoformat()
                event.acknowledged_by = acknowledged_by
                count += 1
        if count:
            logger.info("acknowledged %d alerts", count)
        return count

    def list_events(
        self,
        *,
        category: AlertCategory | None = None,
        level: AlertLevel | None = None,
        acknowledged: bool | None = None,
        limit: int = 100,
    ) -> list[AlertEvent]:
        """List alert events with optional filters (most recent first)."""
        with self._lock:
            events = list(reversed(self._events))

        if category is not None:
            events = [e for e in events if e.category == category]
        if level is not None:
            events = [e for e in events if e.level == level]
        if acknowledged is not None:
            events = [e for e in events if e.acknowledged == acknowledged]

        return events[:limit]

    def get_unacknowledged_count(
            self,
            *,
            category: AlertCategory | None = None,
            level: AlertLevel | None = None,
    ) -> int:
        """Get count of unacknowledged alerts matching filters."""
        return len(self.list_events(category=category, level=level, acknowledged=False, limit=self._max_events))

    def get_counter(self, name: str) -> int:
        """Get trigger count for a specific alert name."""
        return self._alert_counters.get(name, 0)

    def get_summary(self) -> dict[str, Any]:
        """Get alert summary for dashboard display."""
        with self._lock:
            total = len(self._events)
            unacked = sum(1 for e in self._events if not e.acknowledged)
            counts_by_level: dict[str, int] = {}
            counts_by_category: dict[str, int] = {}
            for event in self._events:
                level_key = event.level.value
                cat_key = event.category.value
                counts_by_level[level_key] = counts_by_level.get(level_key, 0) + 1
                counts_by_category[cat_key] = counts_by_category.get(cat_key, 0) + 1
            counters = dict(self._alert_counters)
            latest = [e.to_dict() for e in list(reversed(self._events))[:10]]

        return {
            "total_events": total,
            "unacknowledged": unacked,
            "by_level": counts_by_level,
            "by_category": counts_by_category,
            "counters": counters,
            "latest_events": latest,
        }

    def clear_events(self, *, before: datetime | None = None) -> int:
        """Clear old alert events. If before is None, clears all."""
        with self._lock:
            if before is None:
                cleared = len(self._events)
                self._events.clear()
                return cleared

            original = len(self._events)
            self._events = [e for e in self._events if e.triggered_at >= before.isoformat()]
            return original - len(self._events)


_alert_manager = AlertManager()

# ---------------------------------------------------------------------------
#  Global monitor registry — populated by main_server at startup
# ---------------------------------------------------------------------------

_monitor_registry: list[Any] = []


def register_monitor(monitor: Any) -> None:
    """Register a monitor instance for force-check / reset operations."""
    if monitor not in _monitor_registry:
        _monitor_registry.append(monitor)


def get_monitors() -> list[Any]:
    """Return all registered monitor instances."""
    return list(_monitor_registry)


def get_alert_manager() -> AlertManager:
    """Get the global alert manager singleton instance."""
    return _alert_manager

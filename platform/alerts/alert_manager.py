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
    from platform.store.alert_event_store import AlertEventStore

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
    resolved: bool = False
    resolved_at: str | None = None
    resolved_by: str | None = None

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
            "resolved": self.resolved,
            "resolved_at": self.resolved_at,
            "resolved_by": self.resolved_by,
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
        event_store: AlertEventStore | None = None,
    ) -> None:
        self._client = client
        self._client_factory = client_factory
        self._event_store = event_store
        self._configs: dict[str, dict[str, Any]] = {}
        self._events: list[AlertEvent] = []
        self._lock = threading.RLock()
        self._max_events: int = 1000
        self._max_tracking_entries: int = 2000
        self._alert_counters: dict[str, int] = {}
        self._suppress_intervals: dict[str, int] = {}
        self._last_triggered_at: dict[str, datetime] = {}
        self._notifiers: list[BaseNotifier] = []

    def configure_event_store(self, event_store: AlertEventStore) -> None:
        """Set the database-backed event store for persistent alert storage."""
        self._event_store = event_store
        # 确保表存在
        try:
            event_store.ensure_table()
            logger.info("alert event store configured and table ensured")
        except Exception:
            logger.exception("failed to ensure alert_events table")

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

    def _get_matching_config(self, name: str) -> dict[str, Any] | None:
        config = self._configs.get(name)
        if config is not None:
            return config
        base_name = name.split(":", 1)[0]
        if base_name != name:
            return self._configs.get(base_name)
        return None

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
        """触发告警事件。同一名称的告警重复触发时更新现有事件而非新增。

        合并规则：
        - 若已存在同名且同 category/job 的未确认事件，则原地更新 triggered_at、message、metadata
        - suppress_seconds 控制最小触发间隔，避免刷屏
        - 抑制期内调用不更新事件时间但可更新 metadata（静默刷新）
        """
        config = self._get_matching_config(name)
        if config is not None and not config.get("enabled", True):
            return None

        now = datetime.now(timezone.utc)
        suppress_secs = suppress_seconds or self._suppress_intervals.get(name, 0)
        meta = dict(metadata or {})

        with self._lock:
            # ---- 抑制检查 ----
            if suppress_secs > 0:
                last_triggered = self._last_triggered_at.get(name)
                if last_triggered is not None:
                    elapsed = (now - last_triggered).total_seconds()
                    if elapsed < suppress_secs:
                        # 抑制期内：静默更新已有事件的 metadata（仅内存），不修改时间
                        self._merge_metadata_to_existing(name, category, meta)
                        logger.debug("alert %s suppressed, elapsed=%.1fs < suppress=%ds", name, elapsed, suppress_secs)
                        return None

            # ---- 去重合并：查找同名未确认事件 ----
            existing = self._find_existing_event(name, category)
            if existing is not None:
                existing.message = message
                existing.metadata = {**existing.metadata, **meta}
                existing.triggered_at = now.isoformat()
                self._last_triggered_at[name] = now
                self._alert_counters[name] = self._alert_counters.get(name, 0) + 1

                # 同步更新数据库
                self._update_event_in_store(existing)

                logger.debug(
                    "alert updated: %s [%s] %s: %s",
                    ALERT_LEVEL_DISPLAY.get(level, level.value),
                    ALERT_CATEGORY_DISPLAY.get(category, category.value),
                    name, message,
                )
                self._notify_all(existing)
                return existing

            # ---- 新建告警事件 ----
            event = AlertEvent(
                id=str(uuid.uuid4()),
                name=name,
                level=level,
                category=category,
                message=message,
                triggered_at=now.isoformat(),
                metadata=meta,
            )

            # 写入数据库（持久化存储）
            self._insert_event_to_store(event)

            # 保留内存缓存作为降级方案（兼容无数据库场景）
            self._events.append(event)
            if len(self._events) > self._max_events:
                self._events = self._events[-self._max_events :]
            self._last_triggered_at[name] = now
            self._alert_counters[name] = self._alert_counters.get(name, 0) + 1

            # 防止 _last_triggered_at 和 _alert_counters 无限增长
            self._trim_tracking_entries()

        logger.debug(
            "alert triggered: %s [%s] %s: %s",
            ALERT_LEVEL_DISPLAY.get(level, level.value),
            ALERT_CATEGORY_DISPLAY.get(category, category.value),
            name, message,
        )

        self._notify_all(event)
        return event

    # ------------------------------------------------------------------
    #  Internal helpers for trigger() merge logic
    # ------------------------------------------------------------------

    def _find_existing_event(self, name: str, category: AlertCategory) -> AlertEvent | None:
        """查找同名、同类别的未确认告警事件 — 优先查数据库，内存作为降级方案。

        服务重启后内存为空，必须从数据库查询才能正确去重，避免重复入库。
        """
        # 优先从数据库查找
        if self._event_store is not None:
            try:
                row = self._event_store.find_unacknowledged_by_name(
                    name, category.value
                )
                if row and row.get("id"):
                    return self._row_to_event(row)
            except Exception:
                logger.exception("failed to find existing event in database, falling back to memory")

        # 降级：内存查找
        for event in reversed(self._events):
            if event.name == name and event.category == category and not event.resolved:
                return event
        return None

    def _merge_metadata_to_existing(
        self, name: str, category: AlertCategory, meta: dict[str, Any]
    ) -> None:
        """抑制期内静默合并 metadata 到已有事件（内存 + 数据库同步更新）。"""
        existing = self._find_existing_event(name, category)
        if existing is not None and meta:
            existing.metadata = {**existing.metadata, **meta}
            # 同步更新数据库
            if self._event_store is not None:
                try:
                    self._event_store.update_alert_event(existing.to_dict())
                except Exception:
                    logger.exception("failed to update metadata in database during suppression")

    def _insert_event_to_store(self, event: AlertEvent) -> None:
        """持久化新建事件到数据库。"""
        if self._event_store is not None:
            try:
                self._event_store.insert_alert_event(event.to_dict())
            except Exception:
                logger.exception("failed to persist alert event %s to database", event.id)

    def _update_event_in_store(self, event: AlertEvent) -> None:
        """持久化更新已有事件到数据库。"""
        if self._event_store is not None:
            try:
                self._event_store.update_alert_event(event.to_dict())
            except Exception:
                logger.exception("failed to update alert event %s in database", event.id)

    def _trim_tracking_entries(self) -> None:
        """淘汰最旧的追踪条目，防止内存泄漏。"""
        if len(self._last_triggered_at) > self._max_tracking_entries:
            sorted_items = sorted(self._last_triggered_at.items(), key=lambda x: x[1])
            to_remove = sorted_items[: len(sorted_items) - self._max_tracking_entries]
            for key, _ in to_remove:
                del self._last_triggered_at[key]
                self._alert_counters.pop(key, None)

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
        # 优先使用数据库
        if self._event_store is not None:
            try:
                ok = self._event_store.acknowledge_alert_event(event_id, acknowledged_by)
                if ok:
                    logger.info("alert acknowledged: id=%s by=%s", event_id, acknowledged_by)
                return ok
            except Exception:
                logger.exception("failed to acknowledge alert event %s via database", event_id)

        # 降级：内存查找
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
        # 优先使用数据库
        if self._event_store is not None:
            try:
                count = self._event_store.acknowledge_alert_events(
                    category=category.value if category else None,
                    level=level.value if level else None,
                    acknowledged_by=acknowledged_by,
                )
                if count:
                    logger.info("acknowledged %d alerts", count)
                return count
            except Exception:
                logger.exception("failed to acknowledge all alert events via database")

        # 降级：内存遍历
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

    def delete_event(self, event_id: str) -> bool:
        """Delete an alert event by ID from both DB and memory. Returns True if deleted."""
        deleted = False
        # 优先从数据库删除
        if self._event_store is not None:
            try:
                deleted = self._event_store.delete_alert_event(event_id)
                if deleted:
                    logger.info("alert deleted: id=%s", event_id)
            except Exception:
                logger.exception("failed to delete alert event %s via database", event_id)

        # 同步清除内存缓存
        with self._lock:
            self._events = [e for e in self._events if e.id != event_id]

        return deleted

    def auto_resolve(self, name: str) -> int:
        """自动恢复指定名称的告警（精确匹配+前缀匹配）。"""
        resolved = 0
        if self._event_store is not None:
            try:
                resolved = self._event_store.auto_resolve_by_name(name)
            except Exception:
                logger.exception("failed to auto-resolve alert by name '%s' via database", name)

        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            memory_resolved = 0
            for event in self._events:
                if event.resolved:
                    continue
                if event.name == name or event.name.startswith(name + ":"):
                    event.resolved = True
                    event.resolved_at = now
                    event.resolved_by = "system"
                    memory_resolved += 1
            resolved = max(resolved, memory_resolved)

        if resolved:
            logger.info("auto-resolved %d alert(s) matching prefix '%s'", resolved, name)
        return resolved

    def has_active_alert(self, name: str) -> bool:
        """Return True if an unresolved alert exists for the exact name or prefix."""
        if self._event_store is not None:
            try:
                return bool(self._event_store.has_active_alert_event(name))
            except Exception:
                logger.exception("failed to check active alert by name '%s' via database", name)
        with self._lock:
            return any(not e.resolved and (e.name == name or e.name.startswith(name + ":")) for e in self._events)

    def resolve_alerts(self, name: str) -> int:
        """Backward-compatible alias for auto_resolve()."""
        return self.auto_resolve(name)


    def list_events(
        self,
        *,
        category: AlertCategory | None = None,
        level: AlertLevel | None = None,
        acknowledged: bool | None = None,
        limit: int = 100,
    ) -> list[AlertEvent]:
        """List alert events with optional filters (most recent first)."""
        # 优先从数据库读取
        if self._event_store is not None:
            try:
                rows = self._event_store.list_alert_events(
                    category=category.value if category else None,
                    level=level.value if level else None,
                    acknowledged=acknowledged,
                    limit=limit,
                )
                return [self._row_to_event(row) for row in rows]
            except Exception:
                logger.exception("failed to list alert events from database, falling back to memory")

        # 降级：内存读取
        with self._lock:
            events = list(reversed(self._events))

        if category is not None:
            events = [e for e in events if e.category == category]
        if level is not None:
            events = [e for e in events if e.level == level]
        if acknowledged is not None:
            events = [e for e in events if e.acknowledged == acknowledged]

        return events[:limit]

    @staticmethod
    def _row_to_event(row: dict[str, Any]) -> AlertEvent:
        """Convert a database row dict to an AlertEvent.

        Backward compatibility: legacy 'error' level (pre-merge) is mapped to 'red'.
        """
        level_str = row["level"]
        if level_str == "error":
            level_str = "red"
        return AlertEvent(
            id=row["id"],
            name=row["name"],
            level=AlertLevel(level_str),
            category=AlertCategory(row["category"]),
            message=row["message"],
            triggered_at=row["triggered_at"],
            metadata=row.get("metadata", {}),
            acknowledged=row.get("acknowledged", False),
            acknowledged_at=row.get("acknowledged_at"),
            acknowledged_by=row.get("acknowledged_by"),
            resolved=row.get("resolved", False),
            resolved_at=row.get("resolved_at"),
            resolved_by=row.get("resolved_by"),
        )

    def get_unacknowledged_count(
            self,
            *,
            category: AlertCategory | None = None,
            level: AlertLevel | None = None,
    ) -> int:
        """Get count of unacknowledged alerts matching filters."""
        if self._event_store is not None:
            try:
                return self._event_store.count_unacknowledged_alert_events(
                    category=category.value if category else None,
                    level=level.value if level else None,
                )
            except Exception:
                logger.exception("failed to count unacknowledged alert events from database")
        return len(self.list_events(category=category, level=level, acknowledged=False, limit=self._max_events))

    def get_counter(self, name: str) -> int:
        """Get trigger count for a specific alert name."""
        if self._event_store is not None:
            try:
                return self._event_store.count_alert_events_by_name(name)
            except Exception:
                logger.exception("failed to get alert counter from database for %s", name)
        return self._alert_counters.get(name, 0)

    def get_summary(self) -> dict[str, Any]:
        """Get alert summary for dashboard display."""
        # 优先从数据库读取
        if self._event_store is not None:
            try:
                summary = self._event_store.get_alert_event_summary(latest_limit=10)
                # 将 latest_events 中的 dict 转换为 AlertEvent.to_dict 兼容格式
                if "latest_events" in summary:
                    summary["latest_events"] = [
                        self._row_to_event(row).to_dict() for row in summary["latest_events"]
                    ]
                return summary
            except Exception:
                logger.exception("failed to get alert summary from database, falling back to memory")

        # 降级：内存统计
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
        # 优先从数据库清除
        db_cleared = 0
        if self._event_store is not None:
            try:
                db_cleared = self._event_store.clear_alert_events(before=before)
            except Exception:
                logger.exception("failed to clear alert events from database")

        # 同步清除内存缓存
        with self._lock:
            if before is None:
                cleared = len(self._events)
                self._events.clear()
                return max(cleared, db_cleared)

            original = len(self._events)
            self._events = [e for e in self._events if e.triggered_at >= before.isoformat()]
            return max(original - len(self._events), db_cleared)


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

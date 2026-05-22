"""Test double for database-backed alert event storage."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class FakeAlertEventStore:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def insert_alert_event(self, event: dict[str, Any]) -> None:
        self.events.append(dict(event))

    def list_alert_events(
        self,
        *,
        category: str | None = None,
        level: str | None = None,
        acknowledged: bool | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = list(reversed(self.events))
        if category is not None:
            rows = [row for row in rows if row.get("category") == category]
        if level is not None:
            rows = [row for row in rows if row.get("level") == level]
        if acknowledged is not None:
            rows = [row for row in rows if bool(row.get("acknowledged")) == acknowledged]
        return [dict(row) for row in rows[:limit]]

    def acknowledge_alert_event(self, event_id: str, acknowledged_by: str) -> bool:
        for row in reversed(self.events):
            if row.get("id") == event_id:
                if row.get("acknowledged"):
                    return False
                row["acknowledged"] = True
                row["acknowledged_at"] = datetime.now(timezone.utc).isoformat()
                row["acknowledged_by"] = acknowledged_by
                return True
        return False

    def acknowledge_alert_events(
        self,
        *,
        category: str | None = None,
        level: str | None = None,
        acknowledged_by: str = "system",
    ) -> int:
        count = 0
        now = datetime.now(timezone.utc).isoformat()
        for row in self.events:
            if row.get("acknowledged"):
                continue
            if category is not None and row.get("category") != category:
                continue
            if level is not None and row.get("level") != level:
                continue
            row["acknowledged"] = True
            row["acknowledged_at"] = now
            row["acknowledged_by"] = acknowledged_by
            count += 1
        return count

    def count_unacknowledged_alert_events(
        self,
        *,
        category: str | None = None,
        level: str | None = None,
    ) -> int:
        return len(self.list_alert_events(category=category, level=level, acknowledged=False, limit=10_000))

    def count_alert_events_by_name(self, name: str) -> int:
        return sum(1 for row in self.events if row.get("name") == name)

    def latest_alert_triggered_at(self, name: str) -> datetime | None:
        matches = [row for row in self.events if row.get("name") == name]
        if not matches:
            return None
        value = matches[-1].get("triggered_at")
        if isinstance(value, datetime):
            return value
        if not value:
            return None
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    def has_active_alert_event(self, name: str) -> bool:
        return any(row.get("name") == name and not row.get("resolved") for row in self.events)

    def list_active_alert_event_names(self, name_prefix: str | None = None) -> set[str]:
        return {
            str(row.get("name"))
            for row in self.events
            if not row.get("resolved")
            and row.get("name")
            and (name_prefix is None or str(row.get("name")).startswith(name_prefix))
        }

    def resolve_alert_events(self, name_prefix: str) -> int:
        count = 0
        now = datetime.now(timezone.utc).isoformat()
        for row in self.events:
            if not str(row.get("name", "")).startswith(name_prefix) or row.get("resolved"):
                continue
            row["resolved"] = True
            row["resolved_at"] = now
            row["resolved_by"] = "system"
            if not row.get("acknowledged"):
                row["acknowledged"] = True
                row["acknowledged_at"] = now
                row["acknowledged_by"] = "system:resolved"
            count += 1
        return count

    def clear_alert_events(self, *, before: datetime | None = None) -> int:
        if before is None:
            count = len(self.events)
            self.events.clear()
            return count
        kept = []
        removed = 0
        for row in self.events:
            raw_value = row.get("triggered_at")
            value = raw_value if isinstance(raw_value, datetime) else datetime.fromisoformat(str(raw_value))
            if value is not None and value < before:
                removed += 1
            else:
                kept.append(row)
        self.events = kept
        return removed

    def get_alert_event_summary(self, *, latest_limit: int = 10) -> dict[str, Any]:
        by_level: dict[str, int] = {}
        by_category: dict[str, int] = {}
        counters: dict[str, int] = {}
        for row in self.events:
            by_level[str(row.get("level"))] = by_level.get(str(row.get("level")), 0) + 1
            by_category[str(row.get("category"))] = by_category.get(str(row.get("category")), 0) + 1
            counters[str(row.get("name"))] = counters.get(str(row.get("name")), 0) + 1
        return {
            "total_events": len(self.events),
            "unacknowledged": sum(1 for row in self.events if not row.get("acknowledged")),
            "active_events": sum(1 for row in self.events if not row.get("resolved")),
            "by_level": by_level,
            "by_category": by_category,
            "counters": counters,
            "latest_events": self.list_alert_events(limit=latest_limit),
        }

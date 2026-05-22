"""Alert active/resolved state behavior."""

from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from platform.alerts.alert_levels import AlertCategory, AlertLevel
from platform.alerts.alert_manager import AlertManager
from platform.alerts.monitors.system_monitor import SystemMonitor
from tests.alerts.fake_alert_event_store import FakeAlertEventStore


class TestAlertStateResolution(unittest.TestCase):
    def test_duplicate_active_alert_is_not_retriggered_until_resolved(self) -> None:
        manager = AlertManager(event_store=FakeAlertEventStore())

        manager.trigger(
            name="memory_usage_high",
            level=AlertLevel.RED,
            category=AlertCategory.SYSTEM,
            message="high",
        )
        manager.trigger(
            name="memory_usage_high",
            level=AlertLevel.RED,
            category=AlertCategory.SYSTEM,
            message="still high",
        )

        self.assertEqual(1, len(manager.list_events()))
        self.assertTrue(manager.has_active_alert("memory_usage_high"))

        manager.resolve_alerts("memory_usage_high")
        self.assertFalse(manager.has_active_alert("memory_usage_high"))

        manager.trigger(
            name="memory_usage_high",
            level=AlertLevel.RED,
            category=AlertCategory.SYSTEM,
            message="high again",
        )

        events = manager.list_events()
        self.assertEqual(2, len(events))
        self.assertFalse(events[0].resolved)
        self.assertTrue(events[1].resolved)

    def test_system_memory_alert_resolves_when_usage_returns_under_threshold(self) -> None:
        manager = AlertManager(event_store=FakeAlertEventStore())
        monitor = SystemMonitor(alert_manager=manager, memory_threshold=0.8)
        fake_psutil = SimpleNamespace()

        fake_psutil.virtual_memory = lambda: SimpleNamespace(
            percent=85.0,
            total=10 * 1024 ** 3,
            available=1 * 1024 ** 3,
            used=9 * 1024 ** 3,
        )
        with patch.dict(sys.modules, {"psutil": fake_psutil}):
            monitor._check_memory_usage()

        event = manager.list_events(category=AlertCategory.SYSTEM)[0]
        self.assertEqual("memory_usage_high", event.name)
        self.assertFalse(event.resolved)

        fake_psutil.virtual_memory = lambda: SimpleNamespace(
            percent=65.0,
            total=10 * 1024 ** 3,
            available=3 * 1024 ** 3,
            used=7 * 1024 ** 3,
        )
        with patch.dict(sys.modules, {"psutil": fake_psutil}):
            monitor._check_memory_usage()

        event = manager.list_events(category=AlertCategory.SYSTEM)[0]
        self.assertTrue(event.resolved)
        self.assertEqual(0, manager.get_unacknowledged_count(category=AlertCategory.SYSTEM))


if __name__ == "__main__":
    unittest.main()

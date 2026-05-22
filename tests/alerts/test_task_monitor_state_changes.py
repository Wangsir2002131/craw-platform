"""Task alert state-change behavior."""

from __future__ import annotations

import sys
import os
import unittest
from contextlib import contextmanager
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from platform.alerts.alert_manager import AlertManager
from platform.alerts.alert_levels import AlertCategory
from platform.alerts.monitors.task_monitor import TaskMonitor
from tests.alerts.fake_alert_event_store import FakeAlertEventStore


class FakeCursor:
    def __init__(self, store: FakeTaskStore) -> None:
        self.store = store

    def execute(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self.store.rows)


class FakeTaskStore:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.active_states: dict[str, dict[str, Any]] = {}
        self.ensure_count = 0

    @contextmanager
    def cursor(self):
        yield FakeCursor(self)

    def ensure_task_alert_state_table(self) -> None:
        self.ensure_count += 1

    def list_active_task_alert_states(self, alert_type: str = "task_timeout") -> dict[str, dict[str, Any]]:
        return {
            key: dict(value)
            for key, value in self.active_states.items()
            if value.get("alert_type") == alert_type
            and value.get("alert_state") == "timeout"
            and value.get("resolved_at") is None
        }

    def upsert_task_alert_state(self, metadata: dict[str, Any]) -> None:
        self.active_states[metadata["alert_key"]] = {
            "alert_key": metadata["alert_key"],
            "alert_type": metadata.get("alert_type", "task_timeout"),
            "alert_state": "timeout",
            "product_llm_task_id": metadata["task_id"],
            "question_id": metadata.get("question_id", ""),
            "question_name": metadata.get("question_name"),
            "queue_name": metadata.get("queue_name"),
            "round_num": metadata.get("round_num"),
            "elapsed_seconds": metadata.get("elapsed_seconds", 0),
            "timeout_seconds": metadata.get("timeout_seconds", 0),
            "resolved_at": None,
        }

    def resolve_task_alert_state(self, alert_key: str, alert_type: str = "task_timeout") -> int:
        state = self.active_states.get(alert_key)
        if not state or state.get("alert_type") != alert_type or state.get("alert_state") != "timeout":
            return 0
        state["alert_state"] = "recovered"
        state["resolved_at"] = "now"
        return 1


class TestTaskMonitorStateChanges(unittest.TestCase):
    def test_same_task_question_only_adds_event_when_state_changes(self) -> None:
        manager = AlertManager(event_store=FakeAlertEventStore())
        monitor = TaskMonitor(alert_manager=manager, timeout_seconds=300)
        store = FakeTaskStore()
        monitor.db_store = store

        store.rows = [
            {
                "product_llm_task_id": "task-1",
                "question_id": "question-1",
                "queue_name": "queue:afu",
                "round_num": 1,
                "question_name": "Question",
                "elapsed_seconds": 400,
            }
        ]
        monitor.check()
        monitor.check()

        self.assertEqual(2, store.ensure_count)
        events = manager.list_events(category=AlertCategory.TASK)
        self.assertEqual(1, len(events))
        self.assertEqual("task_timeout:task-1:question-1", events[0].name)
        self.assertFalse(events[0].resolved)
        self.assertEqual(1, manager.get_unacknowledged_count(category=AlertCategory.TASK))

        store.rows = []
        monitor.check()

        events = manager.list_events(category=AlertCategory.TASK)
        self.assertEqual(2, len(events))
        self.assertEqual("task_timeout_recovered:task-1:question-1", events[0].name)
        self.assertEqual("task_timeout:task-1:question-1", events[1].name)
        self.assertTrue(events[0].resolved)
        self.assertTrue(events[1].resolved)
        self.assertEqual(0, manager.get_unacknowledged_count(category=AlertCategory.TASK))


if __name__ == "__main__":
    unittest.main()

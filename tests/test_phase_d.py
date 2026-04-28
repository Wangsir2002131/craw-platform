"""Phase D tests for scheduling strategy and priority dispatching."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta

from platform.dispatcher.master_dispatcher import MasterDispatcher
from platform.dispatcher.schedule_strategy import ScheduleStrategy
from platform.queue.redis_store import RedisQueueStore
from platform.store.strategy_config_store import StrategyConfigStore


class FakePriorityRedis:
    def __init__(self) -> None:
        self.sorted_sets: dict[str, dict[str, int]] = {}

    def zadd(self, key: str, mapping: dict[str, int]) -> int:
        self.sorted_sets.setdefault(key, {}).update(mapping)
        return len(mapping)

    def zpopmax(self, key: str, count: int = 1):
        values = self.sorted_sets.get(key, {})
        if not values:
            return []
        ordered = sorted(values.items(), key=lambda item: item[1], reverse=True)[:count]
        for payload, _ in ordered:
            values.pop(payload, None)
        return ordered

    def ping(self) -> bool:
        return True


class FakeStore:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.status_updates: list[tuple[int, str, dict]] = []
        self.next_id = 1

    def fetch_pending_llm_tasks(self, limit: int = 100) -> list[dict]:
        return [
            {
                "ProductLlmTaskId": "090a71b5-e9ea-11f0-a151-1c34da64f830",
                "ProductId": 10,
                "LlmKey": "afu",
                "MaxRounds": 1,
                "QuestionId": 101,
                "QuestionName": "low priority",
                "PriorityScore": 20,
            },
            {
                "ProductLlmTaskId": "090a71b5-e9ea-11f0-a151-1c34da64f831",
                "ProductId": 20,
                "LlmKey": "afu",
                "MaxRounds": 1,
                "QuestionId": 102,
                "QuestionName": "high priority",
                "PriorityScore": 80,
            },
        ][:limit]

    def create_task_record(self, task_unit: dict) -> int:
        task_id = self.next_id
        self.next_id += 1
        self.created.append({**task_unit, "task_id": task_id})
        return task_id

    def create_or_get_task_record(self, task_unit: dict) -> tuple[int, bool]:
        task_id = self.create_task_record(task_unit)
        return task_id, True

    def update_status(self, task_id: int, status: str, **kwargs) -> None:
        self.status_updates.append((task_id, status, kwargs))


class FakeCursor:
    def __init__(self) -> None:
        self.lastrowid = 7
        self.executed: list[tuple[str, tuple]] = []
        self.row = {
            "id": 7,
            "config_name": "default",
            "config_payload": json.dumps({"default_priority": 55}),
            "enabled": 1,
            "created_at": None,
            "updated_at": None,
        }

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return dict(self.row)

    def fetchall(self):
        return [dict(self.row)]

    def close(self) -> None:
        return None


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_obj = FakeCursor()
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> FakeCursor:
        return self.cursor_obj

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class TestPhaseD(unittest.TestCase):
    def test_schedule_strategy_calculates_bounded_priority(self):
        strategy = ScheduleStrategy(
            {
                "model_weights": {"afu": 10},
                "product_weights": {"10": 5},
                "age_boost_per_hour": 2,
                "round_penalty": 3,
            }
        )
        priority = strategy.calculate_priority(
            {
                "PriorityScore": 90,
                "LlmKey": "afu",
                "ProductId": 10,
                "round_num": 2,
                "CreatedTime": datetime.now() - timedelta(hours=3),
            }
        )

        self.assertEqual(100, priority)

    def test_master_dispatcher_uses_sorted_set_priority_queue(self):
        fake_redis = FakePriorityRedis()
        queue_store = RedisQueueStore(client=fake_redis)
        db_store = FakeStore()
        dispatcher = MasterDispatcher(db_store=db_store, queue_store=queue_store)

        dispatched = dispatcher.dispatch_once()
        first = dispatcher.pop_priority_task("queue:afu")
        second = dispatcher.pop_priority_task("queue:afu")

        self.assertEqual(2, dispatched)
        self.assertEqual("high priority", first["question_name"])
        self.assertEqual("low priority", second["question_name"])
        self.assertEqual([20, 80], [item["priority"] for item in db_store.created])

    def test_strategy_config_store_persists_and_reads_config(self):
        connection = FakeConnection()
        store = StrategyConfigStore(connection_factory=lambda: connection)

        store.ensure_table()
        config_id = store.save_config("default", {"default_priority": 55})
        config = store.get_config("default")
        active_config = store.get_active_config()

        self.assertEqual(7, config_id)
        self.assertEqual(55, config["config_payload"]["default_priority"])
        self.assertEqual({"default_priority": 55}, active_config)
        self.assertGreaterEqual(connection.commits, 4)
        self.assertTrue(any("CREATE TABLE IF NOT EXISTS schedule_strategy_config" in sql for sql, _ in connection.cursor_obj.executed))


if __name__ == "__main__":
    unittest.main()

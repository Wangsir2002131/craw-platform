"""Phase D tests for scheduling strategy and priority dispatching."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta

from platform.dispatcher.master_dispatcher import MasterDispatcher
from platform.dispatcher.schedule_strategy import ScheduleStrategy
from platform.queue.redis_store import RedisQueueStore
from platform.queue.strategy_store import QueueStrategyStore
from platform.store.strategy_config_store import StrategyConfigStore


class FakePriorityRedis:
    def __init__(self) -> None:
        self.data: dict[str, list[str]] = {}
        self.values: dict[str, str] = {}
        self.sorted_sets: dict[str, dict[str, int]] = {}

    def rpush(self, key: str, value: str) -> int:
        self.data.setdefault(key, []).append(value)
        return len(self.data[key])

    def lpop(self, key: str):
        values = self.data.get(key, [])
        if not values:
            return None
        return values.pop(0)

    def llen(self, key: str) -> int:
        return len(self.data.get(key, []))

    def lindex(self, key: str, index: int):
        values = self.data.get(key, [])
        if not values:
            return None
        try:
            return values[index]
        except IndexError:
            return None

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

    def zrem(self, key: str, member: str) -> int:
        values = self.sorted_sets.get(key, {})
        if member not in values:
            return 0
        values.pop(member, None)
        return 1

    def zrange(self, key: str, start: int, end: int):
        values = self.sorted_sets.get(key, {})
        ordered = sorted(values.items(), key=lambda item: item[1])
        members = [member for member, _ in ordered]
        if end == -1:
            return members[start:]
        return members[start : end + 1]

    def lrem(self, key: str, count: int, value: str) -> int:
        values = self.data.get(key, [])
        if not values:
            return 0
        removed = 0
        new_values = []
        for item in values:
            if item == value and (count == 0 or removed < count):
                removed += 1
                continue
            new_values.append(item)
        self.data[key] = new_values
        return removed

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.values[key] = value
        return True

    def get(self, key: str):
        return self.values.get(key)

    def delete(self, key: str) -> int:
        deleted = 0
        if key in self.data:
            self.data.pop(key, None)
            deleted += 1
        if key in self.sorted_sets:
            self.sorted_sets.pop(key, None)
            deleted += 1
        if key in self.values:
            self.values.pop(key, None)
            deleted += 1
        return deleted

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
    def test_collect_product_ids_reads_list_and_priority_zset(self):
        fake_redis = FakePriorityRedis()
        queue_store = RedisQueueStore(client=fake_redis)

        list_payload = queue_store._serialize(
            {
                "message_type": "task",
                "product_llm_task_id": "task-list-1",
                "queue_name": "queue:afu",
                "priority": 50,
            }
        )
        zset_payload = queue_store._serialize(
            {
                "message_type": "task",
                "product_llm_task_id": "task-zset-1",
                "queue_name": "queue:doubao",
                "priority": 80,
            }
        )
        fake_redis.rpush("queue:afu", list_payload)
        fake_redis.zadd("queue:doubao:priority", {zset_payload: 80})

        task_ids = queue_store.collect_product_llm_task_ids()

        self.assertEqual(["task-list-1", "task-zset-1"], task_ids)

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
        self.assertEqual(2, len(queue_store.list_messages("queue:afu")))
        first = dispatcher.pop_priority_task("queue:afu")
        second = queue_store.pop("queue:afu")

        self.assertEqual(2, dispatched)
        self.assertEqual("high priority", first["question_name"])
        self.assertEqual("low priority", second["question_name"])
        self.assertEqual([20, 80], [item["priority"] for item in db_store.created])

    def test_normalize_model_queues_moves_legacy_zset_messages_back_to_list(self):
        fake_redis = FakePriorityRedis()
        queue_store = RedisQueueStore(client=fake_redis)
        high_payload = queue_store._serialize(
            {
                "message_type": "task",
                "product_llm_task_id": "task-priority-1",
                "question_id": "q-1",
                "round_num": 1,
                "queue_name": "queue:doubao",
                "priority": 80,
                "task_id": 1001,
            }
        )
        low_payload = queue_store._serialize(
            {
                "message_type": "task",
                "product_llm_task_id": "task-normal-1",
                "question_id": "q-2",
                "round_num": 1,
                "queue_name": "queue:doubao",
                "priority": 50,
                "task_id": 1002,
            }
        )
        fake_redis.zadd("queue:doubao:priority", {high_payload: 80, low_payload: 50})

        queue_store.normalize_model_queues(min_priority_queue_score=51)

        list_messages = queue_store.list_messages("queue:doubao")
        priority_messages = queue_store.list_priority_messages("queue:doubao")
        self.assertEqual(2, len(list_messages))
        self.assertEqual([], priority_messages)
        self.assertCountEqual(["task-priority-1", "task-normal-1"], [item["product_llm_task_id"] for item in list_messages])

    def test_update_product_task_priorities_lowers_list_and_priority_index_together(self):
        fake_redis = FakePriorityRedis()
        queue_store = RedisQueueStore(client=fake_redis)
        message = {
            "message_type": "task",
            "product_llm_task_id": "task-lower-1",
            "question_id": "q-lower-1",
            "round_num": 1,
            "queue_name": "queue:doubao",
            "priority": 80,
            "task_id": 2001,
        }
        payload = queue_store._serialize(message)
        fake_redis.rpush("queue:doubao", payload)
        fake_redis.zadd("queue:doubao:priority", {payload: 80})

        queue_store.update_product_task_priorities(["task-lower-1"], delta=-40, min_priority_queue_score=51)

        list_messages = queue_store.list_messages("queue:doubao")
        priority_messages = queue_store.list_priority_messages("queue:doubao")
        self.assertEqual(40, list_messages[0]["priority"])
        self.assertEqual([], priority_messages)

    def test_count_priority_messages_reads_from_list(self):
        fake_redis = FakePriorityRedis()
        queue_store = RedisQueueStore(client=fake_redis)
        fake_redis.rpush(
            "queue:doubao",
            queue_store._serialize(
                {
                    "message_type": "task",
                    "product_llm_task_id": "task-a",
                    "priority": 50,
                }
            ),
        )
        fake_redis.rpush(
            "queue:doubao",
            queue_store._serialize(
                {
                    "message_type": "task",
                    "product_llm_task_id": "task-b",
                    "priority": 80,
                }
            ),
        )

        self.assertEqual(1, queue_store.count_priority_messages("queue:doubao"))

    def test_queue_strategy_store_reads_and_writes_strategy(self):
        fake_redis = FakePriorityRedis()
        store = QueueStrategyStore(queue_store=RedisQueueStore(client=fake_redis))

        self.assertEqual("fifo", store.get_strategy())
        store.set_strategy("priority_lifo")
        self.assertEqual("priority", store.get_strategy())

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

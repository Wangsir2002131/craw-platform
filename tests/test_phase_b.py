"""Phase B integration tests with fake queue and execution dependencies."""

from __future__ import annotations

import sys
import types
import unittest
from datetime import datetime

from platform.consumers.afu_consumer import AfuConsumer
from platform.consumers.deepseek_consumer import DeepseekConsumer
from platform.consumers.doubao_consumer import DoubaoConsumer
from platform.consumers.yuanbao_consumer import YuanbaoConsumer
from platform.dispatcher.master_dispatcher import MasterDispatcher
from platform.dispatcher.time_window import TimeWindowController
from platform.queue.protocol import DEAD_LETTER_QUEUE_NAME, RESULT_QUEUE_NAME, build_task_message, get_queue_name
from platform.queue.redis_store import RedisQueueStore
from platform.queue.strategy_store import QueueStrategyStore
from platform.tasks.result_listener import ResultListener


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, list[str]] = {}
        self.values: dict[str, str] = {}
        self.sorted_sets: dict[str, dict[str, int]] = {}

    def lpush(self, key: str, value: str) -> int:
        self.data.setdefault(key, []).insert(0, value)
        return len(self.data[key])

    def rpush(self, key: str, value: str) -> int:
        self.data.setdefault(key, []).append(value)
        return len(self.data[key])

    def lpop(self, key: str):
        values = self.data.get(key, [])
        if not values:
            return None
        return values.pop(0)

    def rpop(self, key: str):
        values = self.data.get(key, [])
        if not values:
            return None
        return values.pop()

    def blpop(self, key: str, timeout: int = 0):
        value = self.lpop(key)
        if value is None:
            return None
        return key, value

    def brpop(self, key: str, timeout: int = 0):
        value = self.rpop(key)
        if value is None:
            return None
        return key, value

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

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.values[key] = value
        return True

    def get(self, key: str):
        return self.values.get(key)

    def delete(self, key: str) -> int:
        deleted = 0
        if key in self.values:
            self.values.pop(key, None)
            deleted += 1
        if key in self.data:
            self.data.pop(key, None)
            deleted += 1
        if key in self.sorted_sets:
            self.sorted_sets.pop(key, None)
            deleted += 1
        return deleted

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

    def zcount(self, key: str, min_score: int, max_score: int) -> int:
        values = self.sorted_sets.get(key, {})
        return sum(1 for score in values.values() if min_score <= score <= max_score)

    def zremrangebyscore(self, key: str, min_score: int, max_score: int) -> int:
        values = self.sorted_sets.get(key, {})
        to_remove = [member for member, score in values.items() if min_score <= score <= max_score]
        for member in to_remove:
            values.pop(member, None)
        return len(to_remove)

    def zrem(self, key: str, member: str) -> int:
        values = self.sorted_sets.get(key, {})
        if member not in values:
            return 0
        values.pop(member, None)
        return 1

    def lrem(self, key: str, count: int, value: str) -> int:
        values = self.data.get(key, [])
        if not values:
            return 0
        removed = 0
        if count >= 0:
            new_values = []
            for item in values:
                if item == value and (count == 0 or removed < count):
                    removed += 1
                    continue
                new_values.append(item)
            self.data[key] = new_values
            return removed
        to_remove = abs(count)
        kept = []
        for item in reversed(values):
            if item == value and removed < to_remove:
                removed += 1
                continue
            kept.append(item)
        self.data[key] = list(reversed(kept))
        return removed

    def expire(self, key: str, seconds: int) -> bool:
        return True

    def zrange(self, key: str, start: int, end: int):
        values = self.sorted_sets.get(key, {})
        ordered = sorted(values.items(), key=lambda item: item[1])
        members = [member for member, _ in ordered]
        if end == -1:
            return members[start:]
        return members[start : end + 1]

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
                "ProductLlmTaskId": "090a71b5-e9ea-11f0-a151-1c34da64f820",
                "ProductTaskId": 200,
                "ProductId": 300,
                "LlmKey": "afu",
                "MaxRounds": 2,
                "QuestionId": 400,
                "QuestionName": "phase b question",
                "PriorityScore": 70,
            }
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

    def get_task_by_id(self, task_id: int) -> dict | None:
        return next((row for row in self.created if row["task_id"] == task_id), None)

    def count_unfinished_task_units(self, product_llm_task_id: str) -> int:
        return 0

    def count_failed_task_units(self, product_llm_task_id: str) -> int:
        return 0

    def update_business_task_status(self, product_llm_task_id: str, status: str) -> None:
        return None


class TestPhaseB(unittest.TestCase):
    def setUp(self) -> None:
        self.fake_redis = FakeRedis()
        self.queue_store = RedisQueueStore(client=self.fake_redis)
        self.db_store = FakeStore()
        self.always_open_window = TimeWindowController(start_hour=0, end_hour=24)
        self.strategy_store = QueueStrategyStore(queue_store=self.queue_store)
        self.strategy_store.set_strategy("fifo")

    def test_master_dispatcher_publishes_to_queue(self):
        dispatcher = MasterDispatcher(db_store=self.db_store, queue_store=self.queue_store)
        dispatched = dispatcher.dispatch_once()

        self.assertEqual(2, dispatched)
        self.assertEqual(2, self.queue_store.length(get_queue_name("afu")))
        self.assertEqual("queued", self.db_store.status_updates[-1][1])

    def test_result_listener_updates_task_status(self):
        listener = ResultListener(queue_store=self.queue_store, db_store=self.db_store)
        self.queue_store.push(
            RESULT_QUEUE_NAME,
            {
                "message_type": "result",
                "task_id": 9,
                "queue_name": get_queue_name("afu"),
                "status": "completed",
                "result": {"success": True, "account_id": "acc-1"},
                "error": "",
            },
        )

        processed = listener.run(once=True)

        self.assertEqual(1, processed)
        self.assertEqual("completed", self.db_store.status_updates[-1][1])

    def test_time_window_controller(self):
        controller = TimeWindowController(start_hour=9, end_hour=18, weekdays={0, 1, 2, 3, 4})
        monday_morning = datetime(2026, 4, 20, 10, 0, 0)
        monday_early = datetime(2026, 4, 20, 8, 0, 0)
        saturday = datetime(2026, 4, 25, 10, 0, 0)

        self.assertTrue(controller.is_open(monday_morning))
        self.assertFalse(controller.is_open(monday_early))
        self.assertFalse(controller.is_open(saturday))
        self.assertEqual(3600, controller.seconds_until_open(monday_early))

    def test_all_consumers_push_results(self):
        consumer_specs = [
            ("afu", AfuConsumer, "fake_afu_worker", "AfuConsumer"),
            ("doubao", DoubaoConsumer, "fake_doubao_worker", "DoubaoConsumer"),
            ("deepseek", DeepseekConsumer, "fake_deepseek_worker", "DeepseekConsumer"),
            ("yuanbao", YuanbaoConsumer, "fake_yuanbao_worker", "YuanbaoConsumer"),
        ]

        for queue_key, consumer_cls, module_name, _ in consumer_specs:
            with self.subTest(queue_key=queue_key):
                fake_module = types.ModuleType(module_name)

                def execute_task(task_info, queue_key=queue_key):
                    return {
                        "success": True,
                        "answer": f"ok-{queue_key}",
                        "error": "",
                        "account_id": f"acc-{queue_key}",
                    }

                fake_module.execute_task = execute_task
                sys.modules[module_name] = fake_module

                message = build_task_message(
                    {
                        "product_llm_task_id": "090a71b5-e9ea-11f0-a151-1c34da64f821",
                        "question_id": 2,
                        "question_name": "hello",
                        "round_num": 1,
                        "queue_name": get_queue_name(queue_key),
                        "priority": 50,
                    },
                    task_id=99,
                )
                self.queue_store.push(get_queue_name(queue_key), message)

                consumer = consumer_cls(
                    queue_store=self.queue_store,
                    db_store=self.db_store,
                    crawler_module=module_name,
                    time_window=self.always_open_window,
                    strategy_store=self.strategy_store,
                )

                processed = consumer.run(once=True)
                self.assertEqual(1, processed)
                result_message = self.queue_store.pop(RESULT_QUEUE_NAME)
                self.assertIsNotNone(result_message)
                self.assertEqual("result", result_message["message_type"])
                self.assertEqual(99, result_message["task_id"])
                self.assertEqual("completed", result_message["status"])

    def test_end_to_end_queue_flow(self):
        fake_module = types.ModuleType("fake_afu_consumer_executor")

        def execute_task(task_info):
            return {
                "success": True,
                "answer": "done",
                "error": "",
                "account_id": "acc-1",
            }

        fake_module.execute_task = execute_task
        sys.modules["fake_afu_consumer_executor"] = fake_module

        dispatcher = MasterDispatcher(db_store=self.db_store, queue_store=self.queue_store)
        consumer = AfuConsumer(
            queue_store=self.queue_store,
            db_store=self.db_store,
            crawler_module="fake_afu_consumer_executor",
            time_window=self.always_open_window,
            strategy_store=self.strategy_store,
        )
        listener = ResultListener(queue_store=self.queue_store, db_store=self.db_store)

        dispatched = dispatcher.dispatch_once()
        consumed = consumer.run(once=True)
        listened = listener.run(once=True)

        self.assertEqual(2, dispatched)
        self.assertEqual(1, consumed)
        self.assertEqual(1, listened)
        statuses = [status for _, status, _ in self.db_store.status_updates]
        self.assertIn("queued", statuses)
        self.assertIn("running", statuses)
        self.assertIn("completed", statuses)

    def test_consumer_retries_then_succeeds(self):
        fake_module = types.ModuleType("fake_retry_worker")
        attempts = {"count": 0}

        def execute_task(_task_info):
            attempts["count"] += 1
            if attempts["count"] < 3:
                return {"success": False, "error": f"boom-{attempts['count']}"}
            return {"success": True, "answer": "done", "error": "", "account_id": "acc-1"}

        fake_module.execute_task = execute_task
        sys.modules["fake_retry_worker"] = fake_module

        consumer = AfuConsumer(
            queue_store=self.queue_store,
            db_store=self.db_store,
            crawler_module="fake_retry_worker",
            time_window=self.always_open_window,
            strategy_store=self.strategy_store,
        )
        self.queue_store.push(
            get_queue_name("afu"),
            build_task_message(
                {
                    "product_llm_task_id": "task-r1",
                    "question_id": "q-r1",
                    "question_name": "retry question",
                    "round_num": 1,
                    "queue_name": get_queue_name("afu"),
                    "priority": 50,
                },
                task_id=11,
            ),
        )

        self.assertTrue(consumer.consume_once())
        self.assertIsNone(self.queue_store.pop(RESULT_QUEUE_NAME))
        self.assertTrue(consumer.consume_once())
        self.assertIsNone(self.queue_store.pop(RESULT_QUEUE_NAME))
        self.assertTrue(consumer.consume_once())
        result_message = self.queue_store.pop(RESULT_QUEUE_NAME)

        self.assertEqual(3, attempts["count"])
        self.assertIsNotNone(result_message)
        self.assertEqual("completed", result_message["status"])

    def test_consumer_moves_to_dead_letter_after_retry_exhausted(self):
        fake_module = types.ModuleType("fake_dead_worker")

        def execute_task(_task_info):
            raise RuntimeError("fatal boom")

        fake_module.execute_task = execute_task
        sys.modules["fake_dead_worker"] = fake_module

        consumer = AfuConsumer(
            queue_store=self.queue_store,
            db_store=self.db_store,
            crawler_module="fake_dead_worker",
            time_window=self.always_open_window,
            strategy_store=self.strategy_store,
        )
        self.queue_store.push(
            get_queue_name("afu"),
            build_task_message(
                {
                    "product_llm_task_id": "task-r2",
                    "question_id": "q-r2",
                    "question_name": "dead letter question",
                    "round_num": 1,
                    "queue_name": get_queue_name("afu"),
                    "priority": 50,
                },
                task_id=12,
            ),
        )

        self.assertTrue(consumer.consume_once())
        self.assertTrue(consumer.consume_once())
        self.assertTrue(consumer.consume_once())
        self.assertTrue(consumer.consume_once())
        result_message = self.queue_store.pop(RESULT_QUEUE_NAME)
        dead_message = self.queue_store.pop(DEAD_LETTER_QUEUE_NAME)

        self.assertIsNotNone(result_message)
        self.assertEqual("failed", result_message["status"])
        self.assertIsNotNone(dead_message)
        self.assertEqual(3, dead_message["retry_count"])

    def test_consumer_skips_new_tasks_outside_time_window(self):
        fake_module = types.ModuleType("fake_closed_window_worker")

        def execute_task(_task_info):
            return {"success": True, "answer": "unexpected", "error": "", "account_id": "acc-1"}

        fake_module.execute_task = execute_task
        sys.modules["fake_closed_window_worker"] = fake_module

        consumer = AfuConsumer(
            queue_store=self.queue_store,
            db_store=self.db_store,
            crawler_module="fake_closed_window_worker",
            time_window=TimeWindowController(start_hour=9, end_hour=20),
            strategy_store=self.strategy_store,
        )
        consumer._current_time = lambda: datetime(2026, 4, 29, 20, 0, 0)
        self.queue_store.push(
            get_queue_name("afu"),
            build_task_message(
                {
                    "product_llm_task_id": "task-window-closed",
                    "question_id": "q-window-closed",
                    "question_name": "window closed question",
                    "round_num": 1,
                    "queue_name": get_queue_name("afu"),
                    "priority": 50,
                },
                task_id=21,
            ),
        )

        self.assertFalse(consumer.consume_once())
        self.assertIsNone(self.queue_store.pop(RESULT_QUEUE_NAME))
        self.assertEqual(1, self.queue_store.length(get_queue_name("afu")))

    def test_consumer_accepts_new_tasks_inside_time_window(self):
        fake_module = types.ModuleType("fake_open_window_worker")

        def execute_task(_task_info):
            return {"success": True, "answer": "ok", "error": "", "account_id": "acc-1"}

        fake_module.execute_task = execute_task
        sys.modules["fake_open_window_worker"] = fake_module

        consumer = AfuConsumer(
            queue_store=self.queue_store,
            db_store=self.db_store,
            crawler_module="fake_open_window_worker",
            time_window=TimeWindowController(start_hour=9, end_hour=20),
            strategy_store=self.strategy_store,
        )
        consumer._current_time = lambda: datetime(2026, 4, 29, 10, 0, 0)
        self.queue_store.push(
            get_queue_name("afu"),
            build_task_message(
                {
                    "product_llm_task_id": "task-window-open",
                    "question_id": "q-window-open",
                    "question_name": "window open question",
                    "round_num": 1,
                    "queue_name": get_queue_name("afu"),
                    "priority": 50,
                },
                task_id=22,
            ),
        )

        self.assertTrue(consumer.consume_once())
        result_message = self.queue_store.pop(RESULT_QUEUE_NAME)
        self.assertIsNotNone(result_message)
        self.assertEqual("completed", result_message["status"])

    def test_legacy_lifo_strategy_falls_back_to_fifo(self):
        fake_module = types.ModuleType("fake_fifo_worker")
        processed: list[str] = []

        def execute_task(task_info):
            processed.append(str(task_info["question_name"]))
            return {"success": True, "answer": "ok", "error": "", "account_id": "acc-1"}

        fake_module.execute_task = execute_task
        sys.modules["fake_fifo_worker"] = fake_module

        consumer = AfuConsumer(
            queue_store=self.queue_store,
            db_store=self.db_store,
            crawler_module="fake_fifo_worker",
            time_window=self.always_open_window,
            strategy_store=self.strategy_store,
        )
        self.queue_store.push(
            get_queue_name("afu"),
            build_task_message(
                {
                    "product_llm_task_id": "task-lifo-1",
                    "question_id": "q-lifo-1",
                    "question_name": "first",
                    "round_num": 1,
                    "queue_name": get_queue_name("afu"),
                    "priority": 50,
                },
                task_id=31,
            ),
        )
        self.queue_store.push(
            get_queue_name("afu"),
            build_task_message(
                {
                    "product_llm_task_id": "task-lifo-2",
                    "question_id": "q-lifo-2",
                    "question_name": "second",
                    "round_num": 1,
                    "queue_name": get_queue_name("afu"),
                    "priority": 50,
                },
                task_id=32,
            ),
        )

        self.strategy_store.set_strategy("lifo")
        self.assertTrue(consumer.consume_once())
        self.assertTrue(consumer.consume_once())
        self.assertEqual(["first", "second"], processed)

    def test_consumer_prefers_priority_queue_then_falls_back_to_fifo(self):
        fake_module = types.ModuleType("fake_priority_worker")
        processed: list[str] = []

        def execute_task(task_info):
            processed.append(str(task_info["question_name"]))
            return {"success": True, "answer": "ok", "error": "", "account_id": "acc-1"}

        fake_module.execute_task = execute_task
        sys.modules["fake_priority_worker"] = fake_module

        consumer = AfuConsumer(
            queue_store=self.queue_store,
            db_store=self.db_store,
            crawler_module="fake_priority_worker",
            time_window=self.always_open_window,
            strategy_store=self.strategy_store,
        )
        low_message = build_task_message(
            {
                "product_llm_task_id": "task-priority-low",
                "question_id": "q-priority-low",
                "question_name": "low",
                "round_num": 1,
                "queue_name": get_queue_name("afu"),
                "priority": 40,
            },
            task_id=41,
        )
        high_message = build_task_message(
            {
                "product_llm_task_id": "task-priority-high",
                "question_id": "q-priority-high",
                "question_name": "high",
                "round_num": 1,
                "queue_name": get_queue_name("afu"),
                "priority": 90,
            },
            task_id=42,
        )
        self.queue_store.push(get_queue_name("afu"), low_message)
        self.queue_store.push(get_queue_name("afu"), high_message)

        self.strategy_store.set_strategy("priority_fifo")
        self.assertTrue(consumer.consume_once())
        self.assertTrue(consumer.consume_once())
        self.assertEqual(["high", "low"], processed)

    def test_priority_isolated_per_queue(self):
        high_module = types.ModuleType("fake_isolated_high_worker")
        low_module = types.ModuleType("fake_isolated_low_worker")
        processed: list[str] = []

        def execute_high(task_info):
            processed.append(f"high:{task_info['question_name']}")
            return {"success": True, "answer": "ok", "error": "", "account_id": "acc-high"}

        def execute_low(task_info):
            processed.append(f"low:{task_info['question_name']}")
            return {"success": True, "answer": "ok", "error": "", "account_id": "acc-low"}

        high_module.execute_task = execute_high
        low_module.execute_task = execute_low
        sys.modules["fake_isolated_high_worker"] = high_module
        sys.modules["fake_isolated_low_worker"] = low_module

        afu_consumer = AfuConsumer(
            queue_store=self.queue_store,
            db_store=self.db_store,
            crawler_module="fake_isolated_low_worker",
            time_window=self.always_open_window,
            strategy_store=self.strategy_store,
        )
        deepseek_consumer = DeepseekConsumer(
            queue_store=self.queue_store,
            db_store=self.db_store,
            crawler_module="fake_isolated_high_worker",
            time_window=self.always_open_window,
            strategy_store=self.strategy_store,
        )
        low_message = build_task_message(
            {
                "product_llm_task_id": "task-global-low",
                "question_id": "q-global-low",
                "question_name": "low",
                "round_num": 1,
                "queue_name": get_queue_name("afu"),
                "priority": 60,
            },
            task_id=51,
        )
        high_message = build_task_message(
            {
                "product_llm_task_id": "task-global-high",
                "question_id": "q-global-high",
                "question_name": "high",
                "round_num": 1,
                "queue_name": get_queue_name("deepseek"),
                "priority": 90,
            },
            task_id=52,
        )
        self.queue_store.push(get_queue_name("afu"), low_message)
        self.queue_store.push(get_queue_name("deepseek"), high_message)

        self.strategy_store.set_strategy("priority")
        self.assertTrue(afu_consumer.consume_once())
        self.assertTrue(deepseek_consumer.consume_once())
        self.assertEqual(["low:low", "high:high"], processed)


if __name__ == "__main__":
    unittest.main()

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
from platform.tasks.result_listener import ResultListener


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, list[str]] = {}
        self.values: dict[str, str] = {}
        self.sorted_sets: dict[str, dict[str, int]] = {}

    def lpush(self, key: str, value: str) -> int:
        self.data.setdefault(key, []).insert(0, value)
        return len(self.data[key])

    def rpop(self, key: str):
        values = self.data.get(key, [])
        if not values:
            return None
        return values.pop()

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

    def test_master_dispatcher_publishes_to_queue(self):
        dispatcher = MasterDispatcher(db_store=self.db_store, queue_store=self.queue_store)
        dispatched = dispatcher.dispatch_once()

        self.assertEqual(2, dispatched)
        self.assertEqual(2, len(self.fake_redis.sorted_sets.get(f"{get_queue_name('afu')}:priority", {})))
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
        result_message = self.queue_store.pop(RESULT_QUEUE_NAME)
        dead_message = self.queue_store.pop(DEAD_LETTER_QUEUE_NAME)

        self.assertIsNotNone(result_message)
        self.assertEqual("failed", result_message["status"])
        self.assertIsNotNone(dead_message)
        self.assertEqual(2, dead_message["retry_count"])


if __name__ == "__main__":
    unittest.main()

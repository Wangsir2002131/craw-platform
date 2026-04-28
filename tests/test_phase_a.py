"""Phase A integration tests with fake database and crawler dependencies."""

from __future__ import annotations

import os
import sys
import types
import unittest

from platform.dispatcher.master_dispatcher import MasterDispatcher
from platform.dispatcher.result_collector import ResultCollector
from platform.dispatcher.task_expander import TaskExpander


class FakeStore:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.status_updates: list[tuple[int, str, dict]] = []
        self.business_status_updates: list[tuple[str, str]] = []
        self.next_id = 1
        self._units_by_key: dict[tuple[object, object, object], int] = {}

    def fetch_pending_llm_tasks(self, limit: int = 100) -> list[dict]:
        return [
            {
                "ProductLlmTaskId": "090a71b5-e9ea-11f0-a151-1c34da64f810",
                "ProductTaskId": 20,
                "ProductId": 30,
                "LlmKey": "afu",
                "MaxRounds": 2,
                "QuestionId": 40,
                "QuestionName": "test question",
                "PriorityScore": 80,
            }
        ][:limit]

    def create_task_record(self, task_unit: dict) -> int:
        task_id = self.next_id
        self.next_id += 1
        self.created.append({**task_unit, "task_id": task_id})
        return task_id

    def create_or_get_task_record(self, task_unit: dict) -> tuple[int, bool]:
        key = (
            task_unit["product_llm_task_id"],
            task_unit["question_id"],
            task_unit["round_num"],
        )
        existing_id = self._units_by_key.get(key)
        if existing_id is not None:
            return existing_id, False

        task_id = self.create_task_record(task_unit)
        self._units_by_key[key] = task_id
        return task_id, True

    def update_status(self, task_id: int, status: str, **kwargs) -> None:
        self.status_updates.append((task_id, status, kwargs))

    def get_task_by_id(self, task_id: int) -> dict:
        return next((row for row in self.created if row["task_id"] == task_id), None)

    def update_business_task_status(self, product_llm_task_id: str, status: str) -> None:
        self.business_status_updates.append((product_llm_task_id, status))

    def count_unfinished_task_units(self, product_llm_task_id: str) -> int:
        return sum(
            1
            for row in self.created
            if row["product_llm_task_id"] == product_llm_task_id
            and not any(update[0] == row["task_id"] and update[1] in {"completed", "failed", "cancelled"} for update in self.status_updates)
        )

    def count_failed_task_units(self, product_llm_task_id: str) -> int:
        return sum(
            1
            for row in self.created
            if row["product_llm_task_id"] == product_llm_task_id
            and any(update[0] == row["task_id"] and update[1] == "failed" for update in self.status_updates)
        )


class TestPhaseA(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["CRAWLER_EXECUTE_DRY_RUN"] = "1"

    def test_task_expander(self):
        expander = TaskExpander()
        result = expander.expand_task(
            {
                "ProductLlmTaskId": "090a71b5-e9ea-11f0-a151-1c34da64f811",
                "LlmKey": "deep-seek",
                "MaxRounds": 3,
                "QuestionId": 2,
                "QuestionName": "hello",
            }
        )
        self.assertEqual(3, len(result))
        self.assertEqual("queue:deepseek", result[0]["queue_name"])
        self.assertEqual(3, result[-1]["round_num"])

    def test_master_dispatcher_fetch(self):
        dispatcher = MasterDispatcher(db_store=FakeStore())
        tasks = dispatcher.fetch_pending_tasks()
        self.assertEqual(1, len(tasks))
        self.assertEqual("090a71b5-e9ea-11f0-a151-1c34da64f810", tasks[0]["ProductLlmTaskId"])

    def test_afu_executor(self):
        from afu.afu import execute_task

        result = execute_task(self._task_payload())
        self.assertTrue(result["success"])

    def test_doubao_executor(self):
        from doubao.doubao import execute_task

        result = execute_task(self._task_payload())
        self.assertTrue(result["success"])

    def test_deepseek_executor(self):
        from deepseek.deepseek import execute_task

        result = execute_task(self._task_payload())
        self.assertTrue(result["success"])

    def test_yuanbao_executor(self):
        from yuanbao.yuanbao import execute_task

        result = execute_task(self._task_payload())
        self.assertTrue(result["success"])

    def test_result_collector(self):
        store = FakeStore()
        collector = ResultCollector(store)
        self.assertTrue(collector.collect_result(1, {"success": True, "account_id": "acc-1"}))
        self.assertEqual("completed", store.status_updates[-1][1])
        self.assertFalse(collector.collect_result(2, {"success": False, "error": "boom"}))
        self.assertEqual("failed", store.status_updates[-1][1])

    def test_publish_marks_business_task_in_progress(self):
        store = FakeStore()
        dispatcher = MasterDispatcher(db_store=store, publish_to_queue=False)

        dispatched = dispatcher.dispatch_once()

        self.assertEqual(2, dispatched)
        self.assertTrue(any(status == "进行中" for _, status in store.business_status_updates))

    def test_result_collector_marks_business_task_completed_when_all_units_finish(self):
        store = FakeStore()
        task_id_1 = store.create_task_record(
            {
                "product_llm_task_id": "task-1",
                "question_id": "q-1",
                "round_num": 1,
                "queue_name": "queue:afu",
            }
        )
        task_id_2 = store.create_task_record(
            {
                "product_llm_task_id": "task-1",
                "question_id": "q-2",
                "round_num": 1,
                "queue_name": "queue:afu",
            }
        )
        collector = ResultCollector(store)

        collector.collect_result(task_id_1, {"success": True, "account_id": "acc-1"})
        self.assertFalse(any(status == "爬网完成" for _, status in store.business_status_updates))

        collector.collect_result(task_id_2, {"success": True, "account_id": "acc-1"})
        self.assertIn(("task-1", "爬网完成"), store.business_status_updates)

    def test_full_flow(self):
        fake_module = types.ModuleType("fake_afu_crawler")

        def execute_task(task_info):
            return {"success": True, "answer": "ok", "error": "", "account_id": "fake-account"}

        fake_module.execute_task = execute_task
        sys.modules["fake_afu_crawler"] = fake_module

        store = FakeStore()
        dispatcher = MasterDispatcher(
            db_store=store,
            crawler_modules={"afu": "fake_afu_crawler"},
            execute_crawlers=True,
        )
        dispatched = dispatcher.dispatch_once()

        self.assertEqual(2, dispatched)
        self.assertEqual(2, len(store.created))
        statuses = [status for _, status, _ in store.status_updates]
        self.assertIn("dispatched", statuses)
        self.assertIn("running", statuses)
        self.assertIn("completed", statuses)

    def test_dispatcher_skips_existing_task_units(self):
        store = FakeStore()
        dispatcher = MasterDispatcher(db_store=store, publish_to_queue=False)

        first_dispatched = dispatcher.dispatch_once()
        second_dispatched = dispatcher.dispatch_once()

        self.assertEqual(2, first_dispatched)
        self.assertEqual(0, second_dispatched)
        self.assertEqual(2, len(store.created))

    @staticmethod
    def _task_payload() -> dict:
        return {
            "product_llm_task_id": "090a71b5-e9ea-11f0-a151-1c34da64f812",
            "question_id": 1,
            "question_name": "test question",
            "round_num": 1,
        }


if __name__ == "__main__":
    unittest.main()

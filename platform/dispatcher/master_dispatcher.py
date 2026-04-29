"""Master dispatcher for unified task entry in Phase A."""

from __future__ import annotations

import logging
import time
import importlib
from datetime import datetime
from typing import Any

from platform.config import PRIORITY_QUEUE_MIN
from platform.dispatcher.result_collector import ResultCollector
from platform.dispatcher.schedule_strategy import ScheduleStrategy
from platform.dispatcher.task_expander import TaskExpander
from platform.queue.protocol import build_task_message
from platform.queue.redis_store import RedisQueueStore
from platform.queue.strategy_store import QueueStrategyStore
from platform.store.db_store import TaskMasterStatusStore

logger = logging.getLogger(__name__)


class MasterDispatcher:
    """Fetch pending product tasks, expand them, and publish execution units."""

    def __init__(
        self,
        db_config: dict[str, Any] | None = None,
        *,
        db_store: TaskMasterStatusStore | None = None,
        queue_store: RedisQueueStore | None = None,
        publish_to_queue: bool = True,
        schedule_strategy: ScheduleStrategy | None = None,
        use_priority_queue: bool = True,
        crawler_modules: dict[str, str] | None = None,
        execute_crawlers: bool = False,
        result_collector: ResultCollector | None = None,
    ) -> None:
        self.db_config = db_config or {}
        self.db_store = db_store or TaskMasterStatusStore(self.db_config)
        self.queue_store = queue_store or RedisQueueStore()
        self.expander = TaskExpander()
        self.publish_to_queue = publish_to_queue
        self.schedule_strategy = schedule_strategy or ScheduleStrategy()
        self.use_priority_queue = use_priority_queue
        self.crawler_modules = crawler_modules or {}
        self.execute_crawlers = execute_crawlers
        self.result_collector = result_collector or ResultCollector(self.db_store)
        self.priority_queue_min = PRIORITY_QUEUE_MIN

    def fetch_pending_tasks(self, limit: int = 100) -> list[dict[str, Any]]:
        """Fetch Status='未开始' tasks from ent_data_product_llm_task."""
        rows = self.db_store.fetch_pending_llm_tasks(limit=limit)
        seen: set[tuple[Any, Any]] = set()
        tasks: list[dict[str, Any]] = []

        for row in rows:
            key = (row.get("ProductLlmTaskId"), row.get("QuestionId") or row.get("QuestionName"))
            if key in seen:
                continue
            seen.add(key)
            tasks.append(row)

        return tasks

    def dispatch_once(self, limit: int = 100) -> int:
        """Execute one dispatch cycle and return dispatched unit count."""
        tasks = self.fetch_pending_tasks(limit=limit)
        dispatched = 0

        for task in tasks:
            for unit in self.expander.expand_task(task):
                unit["priority"] = self.schedule_strategy.calculate_priority({**task, **unit})
                if hasattr(self.db_store, "create_or_get_task_record"):
                    task_id, was_created = self.db_store.create_or_get_task_record(unit)
                else:
                    task_id = self.db_store.create_task_record(unit)
                    was_created = True

                if not was_created:
                    logger.debug(
                        "skip redispatch for existing task unit: product_llm_task_id=%s question_id=%s round_num=%s",
                        unit.get("product_llm_task_id"),
                        unit.get("question_id"),
                        unit.get("round_num"),
                    )
                    continue
                if self.execute_crawlers:
                    self.execute_task(task_id, unit)
                else:
                    self.publish_task(task_id, unit)
                dispatched += 1

        return dispatched

    def publish_task(self, task_id: int, task_unit: dict[str, Any]) -> dict[str, Any]:
        """Push one expanded task unit into its target model queue."""
        message = build_task_message(task_unit, task_id=task_id)
        if self.publish_to_queue:
            self.queue_store.push(message["queue_name"], message)
        self.db_store.update_status(task_id, "queued", dispatched_at=datetime.now())
        if hasattr(self.db_store, "update_business_task_status"):
            self.db_store.update_business_task_status(
                str(task_unit["product_llm_task_id"]),
                "进行中",
            )
        return message

    def execute_task(self, task_id: int, task_unit: dict[str, Any]) -> dict[str, Any]:
        """Execute one task unit immediately for legacy Phase A compatibility."""
        self.db_store.update_status(task_id, "dispatched", dispatched_at=datetime.now())
        self.db_store.update_status(task_id, "running", claimed_at=datetime.now())

        model_key = self._model_key_from_queue(task_unit["queue_name"])
        module_name = self.crawler_modules.get(model_key, model_key)
        crawler_module = importlib.import_module(module_name)
        task_info = {**task_unit, "task_id": task_id}
        result = crawler_module.execute_task(task_info)
        self.result_collector.collect_result(task_id, result)
        return result

    def pop_priority_task(self, queue_name: str) -> dict[str, Any] | None:
        """Pop the highest-priority task from one model queue."""
        return self.queue_store.pop_highest_priority(
            queue_name,
            min_priority_queue_score=self.priority_queue_min,
        )

    def priority_queue_name(self, queue_name: str) -> str:
        """Return the SortedSet queue name for a model queue."""
        return QueueStrategyStore.priority_queue_name(queue_name)

    def _push_priority_task(self, queue_name: str, message: dict[str, Any]) -> bool:
        return self.use_priority_queue and self._is_priority_message(message)

    def _is_priority_message(self, message: dict[str, Any]) -> bool:
        try:
            return int(message.get("priority", 50)) >= self.priority_queue_min
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _model_key_from_queue(queue_name: str) -> str:
        return queue_name.rsplit(":", 1)[-1].strip().lower()

    def run_forever(self, interval: int = 5, limit: int = 100) -> None:
        """Run dispatch cycles continuously until interrupted."""
        logger.info("master dispatcher started")
        try:
            while True:
                dispatched = self.dispatch_once(limit=limit)
                logger.info("dispatch cycle completed: %s units", dispatched)
                time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("master dispatcher stopped by user")

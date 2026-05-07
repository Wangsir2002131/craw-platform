"""Result queue listener for Phase B."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from platform.dispatcher.result_collector import ResultCollector
from platform.queue.protocol import MESSAGE_TYPE_RESULT, RESULT_QUEUE_NAME, parse_message_type
from platform.queue.redis_store import RedisQueueStore
from platform.store.db_store import TaskMasterStatusStore

logger = logging.getLogger(__name__)


class ResultListener:
    """Consume result queue messages and update task state."""

    def __init__(
        self,
        db_config: dict[str, Any] | None = None,
        *,
        queue_store: RedisQueueStore | None = None,
        db_store: TaskMasterStatusStore | None = None,
        result_collector: ResultCollector | None = None,
        result_queue_name: str = RESULT_QUEUE_NAME,
    ) -> None:
        self.queue_store = queue_store or RedisQueueStore()
        self.db_store = db_store or TaskMasterStatusStore(db_config or {})
        self.result_collector = result_collector or ResultCollector(self.db_store)
        self.result_queue_name = result_queue_name

    def run(
        self,
        *,
        once: bool = False,
        timeout: int = 5,
        idle_sleep: float = 1.0,
        stop_event: threading.Event | None = None,
    ) -> int:
        """Listen for results continuously or run a single iteration when once=True."""
        processed = 0
        while True:
            if stop_event is not None and stop_event.is_set():
                return processed
            handled = self.consume_once(timeout=timeout)
            if handled:
                processed += 1
            elif once:
                return processed
            else:
                time.sleep(idle_sleep)

            if once:
                return processed

    def consume_once(self, timeout: int = 5) -> bool:
        message = self.queue_store.blocking_pop(self.result_queue_name, timeout=timeout)
        if not message:
            return False

        if parse_message_type(message) != MESSAGE_TYPE_RESULT:
            raise ValueError(f"unexpected message type for result listener: {message.get('message_type')}")

        task_id = self._safe_int(message.get("task_id"))
        result = message.get("result")
        if not isinstance(result, dict):
            result = {
                "success": False,
                "error": str(message.get("error") or "missing result payload"),
            }

        if task_id is not None:
            self.result_collector.collect_result(task_id, result)
        return True

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

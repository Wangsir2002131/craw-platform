"""Shared queue consumer runtime helpers."""

from __future__ import annotations

import logging
import socket
import threading
import time
import traceback
from datetime import datetime
from typing import Any

from craw_platform.config import CONSUMER_END_HOUR, CONSUMER_MAX_RETRIES, CONSUMER_START_HOUR, PRIORITY_QUEUE_MIN, REDIS_URL
from craw_platform.dispatcher.time_window import TimeWindowController
from craw_platform.heartbeat.consumer_heartbeat import ConsumerHeartbeat
from craw_platform.queue.metrics import QueueMetricsStore
from craw_platform.queue.protocol import DEAD_LETTER_QUEUE_NAME, MESSAGE_TYPE_RESULT, RESULT_QUEUE_NAME
from craw_platform.queue.redis_store import RedisQueueStore
from craw_platform.queue.strategy_store import QueueStrategyStore
from craw_platform.store.db_store import TaskMasterStatusStore


logger = logging.getLogger(__name__)


class BaseQueueConsumer:
    """Common queue consumption flow with heartbeat and metrics."""

    def __init__(
        self,
        db_config: dict[str, Any] | None = None,
        *,
        queue_store: RedisQueueStore | None = None,
        db_store: TaskMasterStatusStore | None = None,
        crawler_module: str,
        queue_name: str,
        consumer_id: str | None = None,
        redis_url: str | None = None,
        max_retries: int | None = None,
        time_window: TimeWindowController | None = None,
        strategy_store: QueueStrategyStore | None = None,
    ) -> None:
        self.db_store = db_store or TaskMasterStatusStore(db_config or {})
        self.queue_store = queue_store or RedisQueueStore(redis_url=redis_url or REDIS_URL)
        self.crawler_module = crawler_module
        self.queue_name = queue_name
        self.consumer_id = consumer_id or self._build_consumer_id()
        self.redis_url = redis_url or REDIS_URL
        self.max_retries = CONSUMER_MAX_RETRIES if max_retries is None else max(0, int(max_retries))
        self.time_window = time_window or TimeWindowController(
            start_hour=CONSUMER_START_HOUR,
            end_hour=CONSUMER_END_HOUR,
        )
        self.strategy_store = strategy_store or QueueStrategyStore(queue_store=self.queue_store)
        self.metrics_store = QueueMetricsStore(
            redis_url=self.redis_url,
            client=self.queue_store._get_client(),
        )
        self.heartbeat = ConsumerHeartbeat(
            consumer_id=self.consumer_id,
            queue_name=self.queue_name,
            redis_url=self.redis_url,
            client=self.queue_store._get_client(),
        )
        self._waiting_logged = False
        self._window_closed_logged = False

    def run(
        self,
        *,
        once: bool = False,
        timeout: int = 5,
        idle_sleep: float = 1.0,
        stop_event: threading.Event | None = None,
    ) -> int:
        processed = 0
        logger.info(
            "consumer started: consumer_id=%s queue=%s once=%s",
            self.consumer_id,
            self.queue_name,
            once,
        )
        heartbeat_stop_event = threading.Event()
        external_stop_event = stop_event
        beat_thread = threading.Thread(
            target=self._heartbeat_loop,
            kwargs={"stop_event": heartbeat_stop_event},
            daemon=True,
            name=f"{self.consumer_id}-heartbeat",
        )
        beat_thread.start()
        try:
            while True:
                if external_stop_event is not None and external_stop_event.is_set():
                    logger.info(
                        "consumer stop requested: consumer_id=%s queue=%s",
                        self.consumer_id,
                        self.queue_name,
                    )
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
        finally:
            heartbeat_stop_event.set()
            beat_thread.join(timeout=1)
            self.heartbeat.clear()

    def consume_once(self, timeout: int = 5) -> bool:
        current_time = self._current_time()
        if not self.time_window.is_open(current_time):
            if not self._window_closed_logged:
                logger.info(
                    "consumer intake paused by time window: consumer_id=%s queue=%s current_time=%s window=%s-%s",
                    self.consumer_id,
                    self.queue_name,
                    current_time.isoformat(timespec="seconds"),
                    f"{self.time_window.start_hour:02d}:00",
                    f"{self.time_window.end_hour:02d}:00",
                )
                self._window_closed_logged = True
            self.heartbeat.beat(
                status="idle",
                extra={
                    "processed_last_minute": self.metrics_store.processed_last_minute(self.queue_name),
                    "window_open": False,
                    "next_open_seconds": self.time_window.seconds_until_open(current_time),
                },
            )
            return False

        self._window_closed_logged = False
        message = self._pop_message(timeout=timeout)
        if not message:
            if not self._waiting_logged:
                logger.info(
                    "waiting for task: consumer_id=%s queue=%s",
                    self.consumer_id,
                    self.queue_name,
                )
                self._waiting_logged = True
            self.heartbeat.beat(status="idle", extra={"processed_last_minute": self.metrics_store.processed_last_minute(self.queue_name)})
            return False

        raw_task_id = message.get("task_id")
        task_id = self._safe_int(raw_task_id)
        self._waiting_logged = False
        logger.info(
            "task received: consumer_id=%s queue=%s task_id=%s product_llm_task_id=%s question_id=%s round_num=%s",
            self.consumer_id,
            self.queue_name,
            task_id,
            message.get("product_llm_task_id"),
            message.get("question_id"),
            message.get("round_num"),
        )
        self.heartbeat.beat(status="busy", extra={"task_id": task_id})
        if task_id is not None:
            self.db_store.update_status(task_id, "running", claimed_at=self._now())
        result = self._execute_with_guard(message)
        success = bool(result.get("success"))
        retry_count = int(message.get("retry_count") or 0)
        if not success and self._should_retry(retry_count):
            self._retry_message(message, result)
            logger.warning(
                "task retry scheduled: consumer_id=%s queue=%s task_id=%s retry=%s/%s error=%s",
                self.consumer_id,
                self.queue_name,
                task_id,
                retry_count + 1,
                self.max_retries,
                str(result.get("error") or "unknown error"),
            )
            return True

        result_message = {
            "message_type": MESSAGE_TYPE_RESULT,
            "task_id": task_id,
            "queue_name": self.queue_name,
            "status": "completed" if success else "failed",
            "result": result,
            "error": str(result.get("error") or ""),
        }
        self.queue_store.push(RESULT_QUEUE_NAME, result_message)
        self.metrics_store.record_processed(self.queue_name, success=success, task_id=task_id)
        if not success:
            self._push_dead_letter(message, result)
        self.heartbeat.beat(
            status="running",
            extra={
                "last_task_id": task_id,
                "last_result": result_message["status"],
                "processed_last_minute": self.metrics_store.processed_last_minute(self.queue_name),
            },
        )
        logger.info(
            "task finished: consumer_id=%s queue=%s task_id=%s status=%s",
            self.consumer_id,
            self.queue_name,
            task_id,
            result_message["status"],
        )
        return True

    def _pop_message(self, timeout: int = 5) -> dict[str, Any] | None:
        strategy = self.strategy_store.get_strategy()
        if self.strategy_store.is_priority_strategy(strategy):
            queue_priority_state = self.queue_store.queue_priority_state(self.queue_name, default_priority=50)
            if queue_priority_state.get("has_elevated_priority") and not queue_priority_state.get("all_same"):
                priority_message = self._pop_priority_message()
                if priority_message:
                    return priority_message
        effective_timeout = self._effective_pop_timeout(timeout)
        if self.strategy_store.uses_lifo(strategy):
            normal_message = self._pop_normal_lifo(effective_timeout)
        else:
            normal_message = self._pop_normal_fifo(effective_timeout)

        return normal_message

    def _effective_pop_timeout(self, timeout: int) -> int:
        remaining_open = self.time_window.seconds_until_close(self._current_time())
        if remaining_open <= 0:
            return 1
        if timeout <= 0:
            return max(1, remaining_open)
        return max(1, min(timeout, remaining_open))

    def _pop_priority_message(self) -> dict[str, Any] | None:
        return self.queue_store.pop_highest_priority(
            self.queue_name,
            min_priority_queue_score=PRIORITY_QUEUE_MIN,
        )

    def _pop_normal_fifo(self, timeout: int) -> dict[str, Any] | None:
        return self.queue_store.blocking_pop(self.queue_name, timeout=timeout)

    def _pop_normal_lifo(self, timeout: int) -> dict[str, Any] | None:
        return self.queue_store.blocking_pop_latest(self.queue_name, timeout=timeout)

    def _heartbeat_loop(self, *, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            self.heartbeat.beat(
                status="running",
                extra={"processed_last_minute": self.metrics_store.processed_last_minute(self.queue_name)},
            )
            stop_event.wait(10)

    def _execute(self, message: dict[str, Any]) -> dict[str, Any]:
        import importlib

        module = importlib.import_module(self.crawler_module)
        execute_task = getattr(module, "execute_task", None)
        if execute_task is None:
            raise AttributeError(f"{self.crawler_module} does not define execute_task")
        return execute_task(message)

    def _execute_with_guard(self, message: dict[str, Any]) -> dict[str, Any]:
        try:
            result = self._execute(message)
        except Exception as exc:
            logger.exception(
                "consumer execution crashed: consumer_id=%s queue=%s task_id=%s",
                self.consumer_id,
                self.queue_name,
                message.get("task_id"),
            )
            return {
                "success": False,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=10),
            }

        if not isinstance(result, dict):
            return {
                "success": False,
                "error": f"invalid result type: {type(result).__name__}",
            }
        return result

    def _should_retry(self, retry_count: int) -> bool:
        return retry_count < self.max_retries

    def _retry_message(self, message: dict[str, Any], result: dict[str, Any]) -> None:
        next_retry = int(message.get("retry_count") or 0) + 1
        requeued = dict(message)
        requeued["retry_count"] = next_retry
        requeued["last_error"] = str(result.get("error") or "unknown crawler error")[:255]
        requeued["enqueued_at"] = self._utc_now_iso()
        self._push_task_message(requeued)

        task_id = self._safe_int(requeued.get("task_id"))
        if task_id is not None:
            self.db_store.update_status(
                task_id,
                "queued",
                retry_count=next_retry,
                fail_reason=str(result.get("error") or "unknown crawler error")[:255],
            )

    def _push_task_message(self, message: dict[str, Any]) -> None:
        self.queue_store.push(self.queue_name, message)

    @staticmethod
    def _is_priority_message(message: dict[str, Any]) -> bool:
        try:
            return int(message.get("priority", 50)) >= PRIORITY_QUEUE_MIN
        except (TypeError, ValueError):
            return False

    def _push_dead_letter(self, message: dict[str, Any], result: dict[str, Any]) -> None:
        dead_letter_message = {
            "message_type": "dead-letter",
            "queue_name": self.queue_name,
            "task_id": self._safe_int(message.get("task_id")),
            "product_llm_task_id": message.get("product_llm_task_id"),
            "question_id": message.get("question_id"),
            "round_num": message.get("round_num"),
            "retry_count": int(message.get("retry_count") or 0),
            "error": str(result.get("error") or "unknown crawler error")[:255],
            "failed_at": self._utc_now_iso(),
            "result": result,
            "original_message": message,
        }
        self.queue_store.push(DEAD_LETTER_QUEUE_NAME, dead_letter_message)

    def _build_consumer_id(self) -> str:
        host = socket.gethostname().lower() or "host"
        queue_key = self.queue_name.rsplit(":", 1)[-1]
        return f"{queue_key}-{host}-{int(time.time() * 1000)}"

    @staticmethod
    def _current_time() -> datetime:
        return datetime.now()

    @staticmethod
    def _now():
        return datetime.now()

    @staticmethod
    def _utc_now_iso() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

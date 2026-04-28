"""Shared queue consumer runtime helpers."""

from __future__ import annotations

import logging
import socket
import threading
import time
import traceback
from typing import Any

from platform.config import CONSUMER_MAX_RETRIES, REDIS_URL
from platform.heartbeat.consumer_heartbeat import ConsumerHeartbeat
from platform.queue.metrics import QueueMetricsStore
from platform.queue.protocol import DEAD_LETTER_QUEUE_NAME, MESSAGE_TYPE_RESULT, RESULT_QUEUE_NAME
from platform.queue.redis_store import RedisQueueStore
from platform.store.db_store import TaskMasterStatusStore


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
    ) -> None:
        self.db_store = db_store or TaskMasterStatusStore(db_config or {})
        self.queue_store = queue_store or RedisQueueStore(redis_url=redis_url or REDIS_URL)
        self.crawler_module = crawler_module
        self.queue_name = queue_name
        self.consumer_id = consumer_id or self._build_consumer_id()
        self.redis_url = redis_url or REDIS_URL
        self.max_retries = CONSUMER_MAX_RETRIES if max_retries is None else max(0, int(max_retries))
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
            "consumer started: consumer_id=%s queue=%s priority_queue=%s once=%s",
            self.consumer_id,
            self.queue_name,
            f"{self.queue_name}:priority",
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
        message = self._pop_message(timeout=timeout)
        if not message:
            if not self._waiting_logged:
                logger.info(
                    "waiting for task: consumer_id=%s queue=%s priority_queue=%s",
                    self.consumer_id,
                    self.queue_name,
                    f"{self.queue_name}:priority",
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
        client = self.queue_store._get_client()
        if hasattr(client, "zpopmax"):
            result = client.zpopmax(f"{self.queue_name}:priority", count=1)
            if result:
                payload, _ = result[0]
                return self.queue_store._deserialize(payload)

        return self.queue_store.blocking_pop(self.queue_name, timeout=timeout)

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
        client = self.queue_store._get_client()
        if hasattr(client, "zadd"):
            payload = self.queue_store._serialize(message)
            score = int(message.get("priority", 50))
            client.zadd(f"{self.queue_name}:priority", {payload: score})
            return
        self.queue_store.push(self.queue_name, message)

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
    def _now():
        from datetime import datetime

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

"""In-process consumer manager for dashboard-controlled scaling."""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from typing import Any

from platform.config import DB_CONFIG, REDIS_URL
from platform.consumers.afu_consumer import AfuConsumer
from platform.consumers.deepseek_consumer import DeepseekConsumer
from platform.consumers.doubao_consumer import DoubaoConsumer
from platform.consumers.yuanbao_consumer import YuanbaoConsumer
from platform.queue.protocol import get_queue_name


logger = logging.getLogger(__name__)


@dataclass
class ManagedConsumerWorker:
    worker_id: str
    model_key: str
    queue_name: str
    stop_event: threading.Event
    thread: threading.Thread
    consumer_id: str
    started_by_manager: bool = True


class ConsumerManager:
    """Manage model consumers as in-process worker threads."""

    _CONSUMER_CLASSES = {
        "afu": AfuConsumer,
        "deepseek": DeepseekConsumer,
        "doubao": DoubaoConsumer,
        "yuanbao": YuanbaoConsumer,
    }

    def __init__(
        self,
        db_config: dict[str, Any] | None = None,
        *,
        redis_url: str | None = None,
        default_consumer_count: int = 1,
    ) -> None:
        self.db_config = db_config or DB_CONFIG
        self.redis_url = redis_url or REDIS_URL
        self.default_consumer_count = max(0, int(default_consumer_count))
        self.enabled = False
        self._lock = threading.RLock()
        self._workers: dict[str, list[ManagedConsumerWorker]] = {
            model_key: [] for model_key in self._CONSUMER_CLASSES
        }
        self._desired_counts: dict[str, int] = {
            model_key: self.default_consumer_count for model_key in self._CONSUMER_CLASSES
        }

    def start_defaults(self) -> None:
        if not self.enabled:
            return
        for model_key in self._CONSUMER_CLASSES:
            self.scale_to(model_key, self._desired_counts[model_key])

    def configure(self, *, enabled: bool, default_consumer_count: int | None = None) -> None:
        with self._lock:
            self.enabled = bool(enabled)
            if default_consumer_count is not None:
                self.default_consumer_count = max(0, int(default_consumer_count))
                for model_key in self._desired_counts:
                    if not self._workers[model_key]:
                        self._desired_counts[model_key] = self.default_consumer_count

    def scale_to(self, model_key: str, count: int) -> dict[str, Any]:
        normalized_model = self._normalize_model_key(model_key)
        if not self.enabled:
            raise RuntimeError("managed consumers are disabled")
        desired = max(0, int(count))
        with self._lock:
            self._cleanup_locked(normalized_model)
            self._desired_counts[normalized_model] = desired
            running_workers = [worker for worker in self._workers[normalized_model] if not worker.stop_event.is_set()]
            delta = desired - len(running_workers)
            if delta > 0:
                for _ in range(delta):
                    self._spawn_worker_locked(normalized_model)
            elif delta < 0:
                for worker in running_workers[delta:]:
                    worker.stop_event.set()
                    logger.info(
                        "consumer drain requested: model=%s consumer_id=%s worker_id=%s",
                        normalized_model,
                        worker.consumer_id,
                        worker.worker_id,
                    )
            return self.status(model_key=normalized_model)

    def increment(self, model_key: str) -> dict[str, Any]:
        normalized_model = self._normalize_model_key(model_key)
        with self._lock:
            return self.scale_to(normalized_model, self._desired_counts[normalized_model] + 1)

    def decrement(self, model_key: str) -> dict[str, Any]:
        normalized_model = self._normalize_model_key(model_key)
        with self._lock:
            return self.scale_to(normalized_model, max(1, self._desired_counts[normalized_model] - 1))

    def status(self, model_key: str | None = None) -> dict[str, Any]:
        with self._lock:
            if model_key is not None:
                normalized_model = self._normalize_model_key(model_key)
                self._cleanup_locked(normalized_model)
                return self._model_status_locked(normalized_model)

            for key in self._workers:
                self._cleanup_locked(key)
            return {
                "managedEnabled": self.enabled,
                "models": {
                    key: self._model_status_locked(key)
                    for key in self._workers
                }
            }

    def shutdown(self, *, join_timeout: float = 3.0) -> None:
        with self._lock:
            workers = [worker for model_workers in self._workers.values() for worker in model_workers]
            for model_key in self._desired_counts:
                self._desired_counts[model_key] = 0
            for worker in workers:
                worker.stop_event.set()

        for worker in workers:
            worker.thread.join(timeout=join_timeout)

        with self._lock:
            for model_key in self._workers:
                self._cleanup_locked(model_key)

    def _spawn_worker_locked(self, model_key: str) -> None:
        consumer_class = self._CONSUMER_CLASSES[model_key]
        worker_id = f"{model_key}-{uuid.uuid4().hex[:8]}"
        stop_event = threading.Event()
        consumer = consumer_class(self.db_config, redis_url=self.redis_url)
        thread = threading.Thread(
            target=self._run_worker,
            kwargs={"consumer": consumer, "stop_event": stop_event, "worker_id": worker_id},
            daemon=True,
            name=f"consumer-{worker_id}",
        )
        worker = ManagedConsumerWorker(
            worker_id=worker_id,
            model_key=model_key,
            queue_name=getattr(consumer, "queue_name", self._queue_name_for_model(model_key)),
            stop_event=stop_event,
            thread=thread,
            consumer_id=consumer.consumer_id,
        )
        self._workers[model_key].append(worker)
        thread.start()
        logger.info(
            "consumer spawned: model=%s consumer_id=%s worker_id=%s queue=%s",
            model_key,
            worker.consumer_id,
            worker.worker_id,
            worker.queue_name,
        )

    def _run_worker(self, *, consumer: Any, stop_event: threading.Event, worker_id: str) -> None:
        try:
            consumer.run(stop_event=stop_event)
        except Exception:
            logger.exception("managed consumer crashed: worker_id=%s consumer_id=%s", worker_id, consumer.consumer_id)

    def _model_status_locked(self, model_key: str) -> dict[str, Any]:
        workers = self._workers[model_key]
        active_workers = [worker for worker in workers if worker.thread.is_alive()]
        running_workers = [worker for worker in active_workers if not worker.stop_event.is_set()]
        draining_workers = [worker for worker in active_workers if worker.stop_event.is_set()]
        return {
            "model": model_key,
            "queueName": self._queue_name_for_model(model_key),
            "managedEnabled": self.enabled,
            "desiredConsumers": self._desired_counts[model_key],
            "activeConsumers": len(running_workers),
            "drainingConsumers": len(draining_workers),
            "workerIds": [worker.worker_id for worker in active_workers],
            "consumerIds": [worker.consumer_id for worker in active_workers],
        }

    def _cleanup_locked(self, model_key: str) -> None:
        self._workers[model_key] = [
            worker
            for worker in self._workers[model_key]
            if worker.thread.is_alive()
        ]

    def _normalize_model_key(self, model_key: str) -> str:
        normalized = str(model_key or "").strip().lower()
        if normalized not in self._CONSUMER_CLASSES:
            raise KeyError(f"unsupported model key: {model_key}")
        return normalized

    @staticmethod
    def _queue_name_for_model(model_key: str) -> str:
        try:
            return get_queue_name(model_key)
        except KeyError:
            return f"queue:{model_key}"


_consumer_manager = ConsumerManager()


def get_consumer_manager() -> ConsumerManager:
    return _consumer_manager

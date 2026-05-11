"""External consumer supervisor for script-run model workers."""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from craw_platform.config import REDIS_URL
from craw_platform.queue.protocol import get_queue_name
from craw_platform.queue.redis_store import RedisQueueStore


logger = logging.getLogger(__name__)


@dataclass
class SupervisedWorker:
    worker_id: str
    consumer_id: str
    stop_event: threading.Event
    thread: threading.Thread


class ConsumerSupervisor:
    """Run one model's consumers as a script-owned scalable worker pool."""

    def __init__(
        self,
        *,
        model_key: str,
        consumer_factory: Callable[[], Any],
        redis_url: str | None = None,
        min_consumers: int = 1,
        timeout: int = 5,
        idle_sleep: float = 1.0,
    ) -> None:
        self.model_key = str(model_key).strip().lower()
        self.queue_name = get_queue_name(self.model_key)
        self.consumer_factory = consumer_factory
        self.redis_url = redis_url or REDIS_URL
        self.min_consumers = max(1, int(min_consumers))
        self.timeout = timeout
        self.idle_sleep = idle_sleep
        self.supervisor_id = self._build_supervisor_id()
        self._desired_consumers = self.min_consumers
        self._workers: list[SupervisedWorker] = []
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._queue_store = RedisQueueStore(redis_url=self.redis_url)

    @property
    def heartbeat_key(self) -> str:
        return f"heartbeat:consumer-supervisor:{self.model_key}"

    @property
    def control_queue_name(self) -> str:
        return f"control:consumer-supervisor:{self.model_key}"

    def run_forever(self) -> int:
        logger.info(
            "consumer supervisor started: model=%s queue=%s priority_queue=%s min_consumers=%s supervisor_id=%s",
            self.model_key,
            self.queue_name,
            f"{self.queue_name}:priority",
            self.min_consumers,
            self.supervisor_id,
        )
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name=f"{self.supervisor_id}-heartbeat",
        )
        control_thread = threading.Thread(
            target=self._control_loop,
            daemon=True,
            name=f"{self.supervisor_id}-control",
        )
        heartbeat_thread.start()
        control_thread.start()

        with self._lock:
            for _ in range(self.min_consumers):
                self._spawn_worker_locked()

        try:
            while not self._stop_event.is_set():
                time.sleep(0.5)
                with self._lock:
                    self._cleanup_workers_locked()
        except KeyboardInterrupt:
            logger.info("consumer supervisor interrupted: model=%s supervisor_id=%s", self.model_key, self.supervisor_id)
        finally:
            self._stop_event.set()
            with self._lock:
                for worker in self._workers:
                    worker.stop_event.set()
            for worker in list(self._workers):
                worker.thread.join(timeout=2)
            heartbeat_thread.join(timeout=1)
            control_thread.join(timeout=1)
            self._clear_heartbeat()
        return 0

    def _control_loop(self) -> None:
        while not self._stop_event.is_set():
            message = self._queue_store.blocking_pop(self.control_queue_name, timeout=2)
            if not message:
                continue
            command = str(message.get("command") or "").strip().lower()
            if command == "increment":
                with self._lock:
                    self._desired_consumers += 1
                    self._spawn_worker_locked()
            elif command == "decrement":
                with self._lock:
                    if self._desired_consumers <= self.min_consumers:
                        logger.info(
                            "consumer supervisor ignored decrement at minimum: model=%s desired=%s",
                            self.model_key,
                            self._desired_consumers,
                        )
                        continue
                    self._desired_consumers -= 1
                    self._request_worker_stop_locked()

    def _heartbeat_loop(self) -> None:
        while not self._stop_event.is_set():
            self._publish_heartbeat()
            self._stop_event.wait(5)
        self._clear_heartbeat()

    def _publish_heartbeat(self) -> None:
        with self._lock:
            self._cleanup_workers_locked()
            active_workers = [worker for worker in self._workers if worker.thread.is_alive()]
            draining_workers = [worker for worker in active_workers if worker.stop_event.is_set()]
            payload = {
                "supervisor_id": self.supervisor_id,
                "model": self.model_key,
                "queue_name": self.queue_name,
                "desired_consumers": self._desired_consumers,
                "active_consumers": len([worker for worker in active_workers if not worker.stop_event.is_set()]),
                "draining_consumers": len(draining_workers),
                "min_consumers": self.min_consumers,
                "worker_ids": [worker.worker_id for worker in active_workers],
                "consumer_ids": [worker.consumer_id for worker in active_workers],
                "timestamp": datetime.utcnow().isoformat(),
                "ttl_seconds": 15,
            }
        client = self._queue_store._get_client()
        client.set(self.heartbeat_key, json.dumps(payload), ex=15)

    def _clear_heartbeat(self) -> None:
        client = self._queue_store._get_client()
        client.delete(self.heartbeat_key)

    def _spawn_worker_locked(self) -> None:
        consumer = self.consumer_factory()
        stop_event = threading.Event()
        worker_id = f"{self.model_key}-{uuid.uuid4().hex[:8]}"
        thread = threading.Thread(
            target=self._run_worker,
            kwargs={"consumer": consumer, "stop_event": stop_event, "worker_id": worker_id},
            daemon=True,
            name=f"worker-{worker_id}",
        )
        worker = SupervisedWorker(
            worker_id=worker_id,
            consumer_id=consumer.consumer_id,
            stop_event=stop_event,
            thread=thread,
        )
        self._workers.append(worker)
        thread.start()
        logger.info(
            "supervisor spawned worker: model=%s worker_id=%s consumer_id=%s",
            self.model_key,
            worker.worker_id,
            worker.consumer_id,
        )

    def _request_worker_stop_locked(self) -> None:
        running_workers = [worker for worker in self._workers if worker.thread.is_alive() and not worker.stop_event.is_set()]
        if len(running_workers) <= self.min_consumers:
            return
        worker = running_workers[-1]
        worker.stop_event.set()
        logger.info(
            "supervisor draining worker: model=%s worker_id=%s consumer_id=%s",
            self.model_key,
            worker.worker_id,
            worker.consumer_id,
        )

    def _cleanup_workers_locked(self) -> None:
        self._workers = [worker for worker in self._workers if worker.thread.is_alive()]

    def _run_worker(self, *, consumer: Any, stop_event: threading.Event, worker_id: str) -> None:
        try:
            consumer.run(timeout=self.timeout, idle_sleep=self.idle_sleep, stop_event=stop_event)
        except Exception:
            logger.exception(
                "supervisor worker crashed: model=%s worker_id=%s consumer_id=%s",
                self.model_key,
                worker_id,
                consumer.consumer_id,
            )

    def _build_supervisor_id(self) -> str:
        host = socket.gethostname().lower() or "host"
        return f"{self.model_key}-supervisor-{host}-{uuid.uuid4().hex[:6]}"


class ExternalSupervisorRegistry:
    """Inspect and control script-run consumer supervisors through Redis."""

    def __init__(self, redis_url: str | None = None) -> None:
        self.redis_url = redis_url or REDIS_URL
        self._queue_store = RedisQueueStore(redis_url=self.redis_url)

    def list_supervisors(self) -> dict[str, dict[str, Any]]:
        client = self._queue_store._get_client()
        keys = []
        if hasattr(client, "scan_iter"):
            keys = list(client.scan_iter(match="heartbeat:consumer-supervisor:*"))
        elif hasattr(client, "keys"):
            keys = list(client.keys("heartbeat:consumer-supervisor:*"))

        supervisors: dict[str, dict[str, Any]] = {}
        for key in keys:
            payload = client.get(key)
            if payload is None:
                continue
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8")
            data = json.loads(payload)
            supervisors[str(data.get("model") or "")] = data
        return supervisors

    def get_supervisor(self, model_key: str) -> dict[str, Any] | None:
        return self.list_supervisors().get(str(model_key).strip().lower())

    def increment(self, model_key: str) -> dict[str, Any]:
        return self._send_command(model_key, "increment")

    def decrement(self, model_key: str) -> dict[str, Any]:
        return self._send_command(model_key, "decrement")

    def _send_command(self, model_key: str, command: str) -> dict[str, Any]:
        normalized_model = str(model_key).strip().lower()
        supervisor = self.get_supervisor(normalized_model)
        if not supervisor:
            raise RuntimeError(f"{normalized_model} consumer supervisor is not running")
        self._queue_store.push(
            f"control:consumer-supervisor:{normalized_model}",
            {
                "message_type": "control",
                "command": command,
                "model": normalized_model,
                "sent_at": datetime.utcnow().isoformat(),
            },
        )
        supervisor["pending_command"] = command
        return supervisor

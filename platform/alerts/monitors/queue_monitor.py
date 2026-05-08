"""Monitor for queue-related alerts."""

from __future__ import annotations

import logging
from typing import Any

from platform.alerts.alert_levels import AlertCategory, AlertLevel
from platform.alerts.monitors.base import BaseMonitor
from platform.config import REDIS_URL
from platform.queue.protocol import MODEL_QUEUE_NAMES
from platform.queue.redis_store import RedisQueueStore

logger = logging.getLogger(__name__)


class QueueMonitor(BaseMonitor):
    """Monitor queue metrics and trigger alerts.

    Checks:
    - Queue length > warning_threshold (YELLOW)
    - Queue length > critical_threshold (RED)
    - Redis connection error (RED)
    - Redis connection persistent failure (ERROR)
    """

    def __init__(
        self,
        *args: Any,
        warning_threshold: int = 100,
        critical_threshold: int = 500,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.warning_threshold = max(1, int(warning_threshold))
        self.critical_threshold = max(warning_threshold + 1, int(critical_threshold))
        self.queue_store = RedisQueueStore(redis_url=REDIS_URL)
        self._redis_failure_count = 0
        self._queue_states: dict[str, str] = {}

    def reset_states(self) -> None:
        """Reset per-queue alert states so next check re-evaluates from scratch."""
        self._queue_states.clear()
        self._redis_failure_count = 0

    def check(self) -> None:
        """Check queue metrics and trigger alerts if thresholds exceeded."""
        self._check_queue_lengths()
        self._check_redis_health()

    # ------------------------------------------------------------------
    #  Queue length checks
    # ------------------------------------------------------------------

    def _check_queue_lengths(self) -> None:
        """检查各模型队列长度"""
        for model_key, queue_name in MODEL_QUEUE_NAMES.items():
            try:
                length = self.queue_store.length(queue_name)
                self._evaluate_length(model_key, queue_name, length)
            except Exception as e:
                logger.warning("failed to check queue length for %s: %s", queue_name, e)

    def _evaluate_length(self, model_key: str, queue_name: str, length: int) -> None:
        """Evaluate queue length against thresholds and trigger alerts."""
        if length > self.critical_threshold:
            current_state = "critical"
        elif length > self.warning_threshold:
            current_state = "warning"
        else:
            current_state = "normal"

        previous_state = self._queue_states.get(model_key, "normal")
        self._queue_states[model_key] = current_state

        if current_state == previous_state:
            return

        if current_state == "critical":
            self.alert_manager.trigger(
                name=f"queue_length_critical:{model_key}",
                level=AlertLevel.RED,
                category=AlertCategory.QUEUE,
                message=f"队列 {model_key} 长度达到危险级别: {length} > {self.critical_threshold}",
                metadata={
                    "queue_name": queue_name,
                    "model_key": model_key,
                    "length": length,
                    "threshold": self.critical_threshold,
                },
            )
        elif current_state == "warning":
            self.alert_manager.trigger(
                name=f"queue_length_warning:{model_key}",
                level=AlertLevel.YELLOW,
                category=AlertCategory.QUEUE,
                message=f"队列 {model_key} 长度超过警告阈值: {length} > {self.warning_threshold}",
                metadata={
                    "queue_name": queue_name,
                    "model_key": model_key,
                    "length": length,
                    "threshold": self.warning_threshold,
                },
            )

    # ------------------------------------------------------------------
    #  Redis health check
    # ------------------------------------------------------------------

    def _check_redis_health(self) -> None:
        """Check Redis connectivity."""
        try:
            if self.queue_store.ping():
                self._redis_failure_count = 0          # reset counter on success
                return

            # ping() returned non-True (unusual — treated as soft failure)
            self._redis_failure_count += 1
            if self._redis_failure_count == 1:
                self.alert_manager.trigger(
                    name="redis_connection_error",
                    level=AlertLevel.RED,
                    category=AlertCategory.QUEUE,
                    message="Redis 连接异常：Ping 无响应",
                    metadata={"failure_count": self._redis_failure_count},
                    suppress_seconds=120,
                )
        except Exception as e:
            # ping() raised an exception — connection truly broken
            self._redis_failure_count += 1
            level = AlertLevel.RED if self._redis_failure_count <= 3 else AlertLevel.ERROR
            alert_name = (
                "redis_connection_error" if level == AlertLevel.RED else "redis_connection_failure"
            )
            self.alert_manager.trigger(
                name=alert_name,
                level=level,
                category=AlertCategory.QUEUE,
                message=f"Redis 连接失败（第 {self._redis_failure_count} 次）: {e}",
                metadata={
                    "error": str(e),
                    "failure_count": self._redis_failure_count,
                },
                suppress_seconds=120,
            )

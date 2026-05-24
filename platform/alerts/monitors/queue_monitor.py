"""Monitor for queue-related alerts."""

from __future__ import annotations

import logging
from typing import Any

from platform.alerts.alert_levels import AlertCategory, AlertLevel
from platform.alerts.monitors.base import BaseMonitor
from platform.config import DB_CONFIG, REDIS_URL
from platform.queue.protocol import MODEL_QUEUE_NAMES
from platform.queue.redis_store import RedisQueueStore
from platform.store.db_store import TaskMasterStatusStore

logger = logging.getLogger(__name__)


class QueueMonitor(BaseMonitor):
    """Monitor queue metrics and trigger alerts.

    Checks:
    - Queue length > warning_threshold (YELLOW) — 队列积压黄色告警
    - Queue length > critical_threshold (RED)   — 队列积压红色告警
    - Queue recovers to normal → auto-resolve    — 队列恢复时自动消除告警
    - Redis connection failure (RED)            — Redis 连接失败红色告警
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
        self.db_store = TaskMasterStatusStore(DB_CONFIG)
        self._redis_failure_count = 0
        self._queue_states: dict[str, str] = {}

    def reset_states(self) -> None:
        """Reset per-queue alert states so next check re-evaluates from scratch."""
        self._queue_states.clear()
        self._redis_failure_count = 0
        self._queue_states_loaded = False
        self._redis_health_loaded = False

    def check(self) -> None:
        """Check queue metrics and trigger alerts if thresholds exceeded."""
        self._check_queue_lengths()
        self._check_redis_health()

    # ------------------------------------------------------------------
    #  Queue length checks
    # ------------------------------------------------------------------

    def _check_queue_lengths(self) -> None:
        """检查各模型队列长度，状态变化时触发/消除告警。"""
        # 首次检查：从 DB 恢复队列告警状态，确保重启后能检测状态变化
        if not getattr(self, '_queue_states_loaded', False):
            self._load_queue_states()
            self._queue_states_loaded = True

        for model_key, queue_name in MODEL_QUEUE_NAMES.items():
            try:
                length = self.queue_store.length(queue_name)
                self._evaluate_length(model_key, queue_name, length)
            except Exception as e:
                logger.warning("failed to check queue length for %s: %s", queue_name, e)

    def _evaluate_length(self, model_key: str, queue_name: str, length: int) -> None:
        """Evaluate queue length against thresholds and trigger/resolve alerts.

        状态机：normal ↔ warning ↔ critical
        - 升级时触发告警（YELLOW/RED）
        - 降级恢复时自动消除旧告警（auto_resolve）
        - 同级别持续时不重复触发（由 alert_manager trigger 合并逻辑处理）
        """
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
            # 升级到危险级别：先清除旧的 warning 告警，再触发新的 critical
            self.alert_manager.auto_resolve(f"queue_length_warning:{model_key}")
            self.alert_manager.trigger(
                name=f"queue_length_critical:{model_key}",
                level=AlertLevel.RED,
                category=AlertCategory.QUEUE,
                message=f"队列 {model_key} 长度达到危险级别: <strong style=\"color:#e53935\">{length}</strong> > {self.critical_threshold}",
                metadata={
                    "queue_name": queue_name,
                    "model_key": model_key,
                    "length": length,
                    "threshold": self.critical_threshold,
                },
            )
        elif current_state == "warning":
            # 降级到警告级别：清除旧的 critical 告警，触发 warning
            if previous_state == "critical":
                self.alert_manager.auto_resolve(f"queue_length_critical:{model_key}")
            self.alert_manager.trigger(
                name=f"queue_length_warning:{model_key}",
                level=AlertLevel.YELLOW,
                category=AlertCategory.QUEUE,
                message=f"队列 {model_key} 长度超过警告阈值: <strong style=\"color:#e53935\">{length}</strong> > {self.warning_threshold}",
                metadata={
                    "queue_name": queue_name,
                    "model_key": model_key,
                    "length": length,
                    "threshold": self.warning_threshold,
                },
            )
        else:  # normal — 从 warning/critical 恢复
            # 完全恢复正常：清除该队列所有告警
            self.alert_manager.auto_resolve(f"queue_length_warning:{model_key}")
            self.alert_manager.auto_resolve(f"queue_length_critical:{model_key}")
            logger.info("queue %s recovered to normal (length=%d)", model_key, length)

    # ------------------------------------------------------------------
    #  Redis health check
    # ------------------------------------------------------------------

    def _check_redis_health(self) -> None:
        """Check Redis connectivity — 所有连接问题统一红色告警。"""
        # 首次检查：从 DB 恢复 Redis 告警状态，确保重启后能检测状态变化
        if not getattr(self, '_redis_health_loaded', False):
            self._load_redis_health_state()
            self._redis_health_loaded = True

        try:
            if self.queue_store.ping():
                if self._redis_failure_count > 0:
                    # Redis 恢复：自动消除告警
                    self.alert_manager.auto_resolve("redis_connection_error")
                    logger.info("Redis connection recovered after %d failure(s)", self._redis_failure_count)
                self._redis_failure_count = 0
                return

            # ping() returned non-True (unusual — treated as soft failure)
            self._redis_failure_count += 1
            level = AlertLevel.RED
            alert_name = "redis_connection_error"
            self.alert_manager.trigger(
                name=alert_name,
                level=level,
                category=AlertCategory.QUEUE,
                message=f"Redis 连接异常：Ping 无响应（第 {self._redis_failure_count} 次）",
                metadata={"failure_count": self._redis_failure_count},
                suppress_seconds=120,
            )
        except Exception as e:
            # ping() raised an exception — connection truly broken
            self._redis_failure_count += 1
            alert_name = "redis_connection_error"
            self.alert_manager.trigger(
                name=alert_name,
                level=AlertLevel.RED,
                category=AlertCategory.QUEUE,
                message=f"Redis 连接失败（第 {self._redis_failure_count} 次）: {e}",
                metadata={
                    "error": str(e),
                    "failure_count": self._redis_failure_count,
                },
                suppress_seconds=120,
            )

    # ------------------------------------------------------------------
    #  状态恢复工具方法（从 DB 重建内存状态，处理重启后内存丢失）
    # ------------------------------------------------------------------

    def _load_queue_states(self) -> None:
        """从 alert_events 表恢复 _queue_states。

        若 DB 中存在 queue_length_warning:X 或 queue_length_critical:X 告警，
        则推断对应队列的 previous_state 为 warning/critical，
        下一次正常检查时如已恢复即可触发 auto_resolve。
        """
        try:
            with self.db_store.cursor() as cursor:
                cursor.execute(
                    "SELECT name FROM alert_events WHERE (name LIKE %s OR name LIKE %s) AND acknowledged = 0",
                    ("queue_length_warning:%", "queue_length_critical:%"),
                )
                rows = cursor.fetchall() or []
            for row in rows:
                name = row["name"]
                if name.startswith("queue_length_critical:"):
                    key = name[len("queue_length_critical:"):]
                    self._queue_states[key] = "critical"
                elif name.startswith("queue_length_warning:"):
                    key = name[len("queue_length_warning:"):]
                    self._queue_states[key] = "warning"
            if self._queue_states:
                logger.debug("loaded %d queue states from db", len(self._queue_states))
        except Exception:
            logger.debug("failed to load queue states from db", exc_info=True)

    def _load_redis_health_state(self) -> None:
        """从 alert_events 表恢复 _redis_failure_count。

        若 DB 中存在 redis_connection_error 告警，则设置 failure_count=1，
        下一次 ping 成功时即可触发 auto_resolve。
        """
        try:
            with self.db_store.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM alert_events WHERE name = %s AND acknowledged = 0",
                    ("redis_connection_error",),
                )
                if cursor.fetchone():
                    self._redis_failure_count = 1
                    logger.debug("redis alert found in db, setting failure_count=1")
        except Exception:
            logger.debug("failed to load redis health state from db", exc_info=True)

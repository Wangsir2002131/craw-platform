"""Monitor for task-related alerts."""

from __future__ import annotations

import logging
from typing import Any

from platform.alerts.alert_levels import AlertCategory, AlertLevel
from platform.alerts.monitors.base import BaseMonitor
from platform.config import DB_CONFIG
from platform.store.db_store import TaskMasterStatusStore

logger = logging.getLogger(__name__)


class TaskMonitor(BaseMonitor):
    """Monitor task execution metrics and trigger alerts.

    Checks:
    - Task timeout per-task (YELLOW): fires once per task when it first crosses the threshold
    - Task failure rate (RED)
    - Task error rate (ERROR)
    """

    def __init__(
        self,
        *args: Any,
        timeout_seconds: int = 300,
        failure_rate_threshold: float = 0.1,
        error_rate_threshold: float = 0.3,
        lookback_seconds: int = 300,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.timeout_seconds = timeout_seconds
        self.failure_rate_threshold = failure_rate_threshold
        self.error_rate_threshold = error_rate_threshold
        self.lookback_seconds = lookback_seconds
        self.db_store = TaskMasterStatusStore(DB_CONFIG)
        # 跟踪已触发超时告警的任务，避免同一任务重复告警
        self._timed_out_task_ids: set[str] = set()

    def check(self) -> None:
        """Check task metrics and trigger alerts if thresholds exceeded."""
        self._check_timeout()
        self._check_failure_rate()
        self._check_error_rate()

    def reset_states(self) -> None:
        """Reset in-memory timeout tracking, used by force_check."""
        self._timed_out_task_ids.clear()

    def _check_timeout(self) -> None:
        """逐任务检查执行超时，仅在任务首次超过阈值时触发黄色告警。

        计时逻辑：NOW() - claimed_at = 单轮已运行时长
        """
        try:
            with self.db_store.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        t.product_llm_task_id,
                        t.question_id,
                        t.queue_name,
                        t.round_num,
                        q.QuestionName AS question_name,
                        TIMESTAMPDIFF(SECOND, t.claimed_at, NOW()) AS elapsed_seconds
                    FROM task_master_status t
                    LEFT JOIN ent_data_question q
                        ON t.question_id = q.QuestionId
                        AND q.Deleted = b'0'
                    WHERE t.execute_status = 'running'
                        AND t.claimed_at IS NOT NULL
                        AND TIMESTAMPDIFF(SECOND, t.claimed_at, NOW()) > %s
                    """,
                    (self.timeout_seconds,),
                )
                timed_out_rows = cursor.fetchall()

            current_timed_out_ids = {row["product_llm_task_id"] for row in timed_out_rows}

            # 新增超时任务：首次超过阈值才触发一次告警
            for row in timed_out_rows:
                task_id = row["product_llm_task_id"]
                if task_id not in self._timed_out_task_ids:
                    elapsed = int(row["elapsed_seconds"])
                    queue = row.get("queue_name", "unknown")
                    question_id = row.get("question_id") or ""
                    question_name = row.get("question_name") or "未获取到问题内容"
                    round_num = row.get("round_num")
                    self.alert_manager.trigger(
                        name=f"task_timeout:{task_id}",
                        level=AlertLevel.YELLOW,
                        category=AlertCategory.TASK,
                        message=(
                            f"任务超时未完成：单轮已运行 {elapsed} 秒"
                            f"（阈值 {self.timeout_seconds} 秒），队列: {queue}"
                        ),
                        metadata={
                            "task_id": task_id,
                            "question_id": question_id,
                            "question_name": question_name,
                            "round_num": round_num,
                            "queue_name": queue,
                            "elapsed_seconds": elapsed,
                            "timeout_seconds": self.timeout_seconds,
                        },
                    )
                    self._timed_out_task_ids.add(task_id)

            # 任务已完成/失败的，从跟踪集合移除，下次重新运行可再计时
            self._timed_out_task_ids &= current_timed_out_ids

        except Exception as e:
            logger.warning("task timeout check failed: %s", e)

    def _check_failure_rate(self) -> None:
        """检查任务失败率 - 触发红色告警"""
        try:
            with self.db_store.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN execute_status = 'failed' THEN 1 ELSE 0 END) AS failed
                    FROM task_master_status
                    WHERE updated_at >= DATE_SUB(NOW(), INTERVAL %s SECOND)
                        AND execute_status IN ('completed', 'failed')
                    """,
                    (self.lookback_seconds,),
                )
                row = cursor.fetchone()
                total = int(row.get("total") or 0)
                failed = int(row.get("failed") or 0)

            if total > 0:
                failure_rate = failed / total
                if failure_rate > self.failure_rate_threshold:
                    self.alert_manager.trigger(
                        name="task_failure_rate",
                        level=AlertLevel.RED,
                        category=AlertCategory.TASK,
                        message=f"任务失败率超过阈值: {failure_rate:.1%} (阈值: {self.failure_rate_threshold:.1%})，总计{total}个，失败{failed}个",
                        metadata={
                            "total": total,
                            "failed": failed,
                            "failure_rate": round(failure_rate, 4),
                            "threshold": self.failure_rate_threshold,
                            "lookback_seconds": self.lookback_seconds,
                        },
                        suppress_seconds=120,
                    )
        except Exception as e:
            logger.warning("task failure rate check failed: %s", e)

    def _check_error_rate(self) -> None:
        """检查任务错误率 - 触发错误预警"""
        try:
            with self.db_store.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN execute_status IN ('failed', 'error') THEN 1 ELSE 0 END) AS errors
                    FROM task_master_status
                    WHERE updated_at >= DATE_SUB(NOW(), INTERVAL %s SECOND)
                        AND execute_status IN ('completed', 'failed', 'error')
                    """,
                    (self.lookback_seconds,),
                )
                row = cursor.fetchone()
                total = int(row.get("total") or 0)
                errors = int(row.get("errors") or 0)

            if total > 0:
                error_rate = errors / total
                if error_rate > self.error_rate_threshold:
                    self.alert_manager.trigger(
                        name="task_error_rate",
                        level=AlertLevel.ERROR,
                        category=AlertCategory.TASK,
                        message=f"任务错误率超过阈值: {error_rate:.1%} (阈值: {self.error_rate_threshold:.1%})，总计{total}个，错误{errors}个",
                        metadata={
                            "total": total,
                            "errors": errors,
                            "error_rate": round(error_rate, 4),
                            "threshold": self.error_rate_threshold,
                            "lookback_seconds": self.lookback_seconds,
                        },
                        suppress_seconds=120,
                    )
        except Exception as e:
            logger.warning("task error rate check failed: %s", e)
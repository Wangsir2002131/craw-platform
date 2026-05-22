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
    - Task timeout per-task (YELLOW): fires once per task when it first crosses
      the threshold; auto-acknowledges when the task is no longer running.
    - Task failure rate (RED)
    """

    def __init__(
        self,
        *args: Any,
        timeout_seconds: int = 300,
        failure_rate_threshold: float = 0.1,
        lookback_seconds: int = 300,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.timeout_seconds = timeout_seconds
        self.failure_rate_threshold = failure_rate_threshold
        self.lookback_seconds = lookback_seconds
        self.db_store = TaskMasterStatusStore(DB_CONFIG)
        # 跟踪已触发超时告警的任务: (task_id, question_id) → event_id
        # 同一组合跨轮次合并为一条告警，所有行都不再 running 时自动 DELETE
        self._timed_out_alerts: dict[str, str] = {}

    def check(self) -> None:
        """Check task metrics and trigger alerts if thresholds exceeded."""
        self._check_timeout()
        self._check_failure_rate()

    def reset_states(self) -> None:
        """Reset in-memory timeout tracking, used by force_check.

        Delete all tracked alert events from DB before clearing, so they don't
        reappear after a force-check with clear_history.
        """
        for event_id in self._timed_out_alerts.values():
            try:
                self.alert_manager.delete_event(event_id)
            except Exception:
                pass
        self._timed_out_alerts.clear()

    def _check_timeout(self) -> None:
        """逐任务检查执行超时，仅在任务首次超过阈值时触发黄色告警。
        同一 task_id + question_id 合并为一条，不按轮次拆分。
        该组合下所有行都不再 running 时才从数据库删除告警。

        计时逻辑：NOW() - claimed_at = 单轮已运行时长
        """
        try:
            with self.db_store.cursor() as cursor:
                # 1) 查询所有超时中的 running 行
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
                timed_out_rows = cursor.fetchall() or []

                # 2) 查询所有 running 的 (task_id, question_id) 组合
                #    只要组合下还有行在 running，告警就不该被删除
                cursor.execute(
                    """
                    SELECT DISTINCT
                        t.product_llm_task_id,
                        t.question_id
                    FROM task_master_status t
                    WHERE t.execute_status = 'running'
                        AND t.claimed_at IS NOT NULL
                    """,
                )
                all_running = cursor.fetchall() or []

            # ---- 按 (task_id, question_id) 分组超时行 ----
            grouped: dict[str, list[dict[str, Any]]] = {}
            for row in timed_out_rows:
                task_id = row["product_llm_task_id"]
                question_id = row.get("question_id") or ""
                pair_key = f"{task_id}:{question_id}"
                grouped.setdefault(pair_key, []).append(row)

            # ---- 构建"仍活跃"集合，用于判断是否删除 ----
            active_pairs: set[str] = set()
            for row in all_running:
                task_id = row["product_llm_task_id"]
                question_id = row.get("question_id") or ""
                active_pairs.add(f"{task_id}:{question_id}")

            # ---- 触发 / 更新告警 ----
            for pair_key, rows in grouped.items():
                if pair_key not in self._timed_out_alerts:
                    # 取任意一行的基础信息
                    first = rows[0]
                    elapsed = int(first["elapsed_seconds"])
                    queue = first.get("queue_name", "unknown")
                    question_name = first.get("question_name") or "未获取到问题内容"
                    task_id = first["product_llm_task_id"]
                    question_id = first.get("question_id") or ""
                    # 收集所有超时轮次
                    timed_out_rounds = sorted({r["round_num"] for r in rows if r.get("round_num") is not None})
                    event = self.alert_manager.trigger(
                        name=f"task_timeout:{pair_key}",
                        level=AlertLevel.YELLOW,
                        category=AlertCategory.TASK,
                        message=(
                            f"任务超时未完成：最长单轮已运行 {elapsed} 秒"
                            f"（阈值 {self.timeout_seconds} 秒），"
                            f"超时轮次: {timed_out_rounds}，队列: {queue}"
                        ),
                        metadata={
                            "task_id": task_id,
                            "question_id": question_id,
                            "question_name": question_name,
                            "timed_out_rounds": timed_out_rounds,
                            "queue_name": queue,
                            "max_elapsed_seconds": elapsed,
                            "timeout_seconds": self.timeout_seconds,
                        },
                    )
                    if event is not None:
                        self._timed_out_alerts[pair_key] = event.id

            # ---- 删除已恢复的告警：组合下无任何 running 行 ----
            resolved_keys = set(self._timed_out_alerts.keys()) - active_pairs
            for pair_key in resolved_keys:
                event_id = self._timed_out_alerts.pop(pair_key, None)
                if event_id:
                    try:
                        self.alert_manager.delete_event(event_id)
                        logger.debug(
                            "task timeout alert auto-deleted: key=%s event_id=%s",
                            pair_key,
                            event_id,
                        )
                    except Exception:
                        logger.exception(
                            "failed to delete resolved timeout alert: %s",
                            event_id,
                        )

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

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
      the threshold; updates triggered_at on re-check; auto-deletes when resolved.
    - Task failure rate (RED): high failure rate across recent tasks.
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
        # 跟踪已触发超时告警的任务: pair_key → event_id
        self._timed_out_alerts: dict[str, str] = {}
        # 跟踪失败率告警是否已触发
        self._failure_rate_alert_active = False

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
        self._failure_rate_alert_active = False
        self._timed_out_loaded = False
        self._failure_rate_loaded = False

    def _check_timeout(self) -> None:
        """逐任务检查执行超时。

        任务状态联动：
        - running 且超时  → 触发/更新告警
        - 不再 running     → 自动删除告警
        - 同一 pair_key 的告警复用同一事件 ID，重复触发只更新时间/消息
        """
        # 首次检查：从 DB 恢复内存状态，确保重启后能检测到状态变化
        if not self._timed_out_alerts and not getattr(self, '_timed_out_loaded', False):
            self._load_timed_out_alerts()
            self._timed_out_loaded = True

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
                        t.execute_status,
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

            # ---- 构建"仍活跃"集合 ----
            active_pairs: set[str] = set()
            for row in all_running:
                task_id = row["product_llm_task_id"]
                question_id = row.get("question_id") or ""
                active_pairs.add(f"{task_id}:{question_id}")

            # ---- 触发 / 更新告警（状态联动）----
            for pair_key, rows in grouped.items():
                first = rows[0]
                elapsed = int(first["elapsed_seconds"])
                queue = first.get("queue_name", "unknown")
                question_name = first.get("question_name") or "未获取到问题内容"
                task_id = first["product_llm_task_id"]
                question_id = first.get("question_id") or ""
                timed_out_rounds = sorted({r["round_num"] for r in rows if r.get("round_num") is not None})
                status = first.get("execute_status", "running")

                if pair_key in self._timed_out_alerts:
                    # 告警已存在：原地更新消息和时间（复用 trigger 合并逻辑）
                    self.alert_manager.trigger(
                        name=f"task_timeout:{pair_key}",
                        level=AlertLevel.YELLOW,
                        category=AlertCategory.TASK,
                        message=(
                            f"任务超时未完成：最长单轮已运行 <strong style=\"color:#e53935\">{elapsed}</strong> 秒"
                            f"（阈值 {self.timeout_seconds} 秒），"
                            f"当前状态: {status}，超时轮次: {timed_out_rounds}，队列: {queue}"
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
                else:
                    # 新告警：创建并记录 event_id
                    event = self.alert_manager.trigger(
                        name=f"task_timeout:{pair_key}",
                        level=AlertLevel.YELLOW,
                        category=AlertCategory.TASK,
                        message=(
                            f"任务超时未完成：最长单轮已运行 <strong style=\"color:#e53935\">{elapsed}</strong> 秒"
                            f"（阈值 {self.timeout_seconds} 秒），"
                            f"当前状态: {status}，超时轮次: {timed_out_rounds}，队列: {queue}"
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

            # ---- 删除已恢复的告警：组合下无任何 running 行（任务状态联动）----
            resolved_keys = set(self._timed_out_alerts.keys()) - active_pairs
            for pair_key in resolved_keys:
                event_id = self._timed_out_alerts.pop(pair_key, None)
                if event_id:
                    try:
                        self.alert_manager.delete_event(event_id)
                        logger.info(
                            "task timeout alert auto-resolved: key=%s event_id=%s",
                            pair_key, event_id,
                        )
                    except Exception:
                        logger.exception(
                            "failed to delete resolved timeout alert: %s", event_id,
                        )

        except Exception as e:
            logger.warning("task timeout check failed: %s", e)

    def _load_timed_out_alerts(self) -> None:
        """从 alert_events 表恢复 _timed_out_alerts（处理重启后内存丢失）。

        查询 DB 中所有未确认的 task_timeout:* 告警，重建 pair_key → event_id 映射，
        确保重启后仍能通过 set-difference 检测任务恢复并自动消除告警。
        """
        try:
            with self.db_store.cursor() as cursor:
                cursor.execute(
                    "SELECT id, name FROM alert_events WHERE name LIKE %s AND acknowledged = 0",
                    ("task_timeout:%",),
                )
                rows = cursor.fetchall() or []
            for row in rows:
                pair_key = row["name"][len("task_timeout:"):]
                if pair_key:
                    self._timed_out_alerts[pair_key] = row["id"]
            if self._timed_out_alerts:
                logger.debug("loaded %d timed out alert states from db", len(self._timed_out_alerts))
        except Exception:
            logger.debug("failed to load timed out alert states from db", exc_info=True)

    def _load_failure_rate_state(self) -> None:
        """从 alert_events 表恢复 _failure_rate_alert_active。

        若 DB 中存在 task_failure_rate 告警，则设置为 True，
        下一次检查时若失败率已恢复正常即可触发 auto_resolve。
        """
        try:
            with self.db_store.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM alert_events WHERE name = %s AND acknowledged = 0",
                    ("task_failure_rate",),
                )
                if cursor.fetchone():
                    self._failure_rate_alert_active = True
                    logger.debug("task failure rate alert found in db, enabling auto-resolve check")
        except Exception:
            logger.debug("failed to load failure rate state from db", exc_info=True)

    def _check_failure_rate(self) -> None:
        """检查任务失败率 - 触发红色告警，恢复后自动消除。"""
        # 首次检查：从 DB 恢复内存状态，确保重启后能检测到状态变化
        if not self._failure_rate_alert_active and not getattr(self, '_failure_rate_loaded', False):
            self._load_failure_rate_state()
            self._failure_rate_loaded = True

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
                    self._failure_rate_alert_active = True
                    self.alert_manager.trigger(
                        name="task_failure_rate",
                        level=AlertLevel.RED,
                        category=AlertCategory.TASK,
                        message=f"任务失败率超过阈值: <strong style=\"color:#e53935\">{failure_rate:.1%}</strong> (阈值: {self.failure_rate_threshold:.1%})，总计{total}个，失败{failed}个",
                        metadata={
                            "total": total,
                            "failed": failed,
                            "failure_rate": round(failure_rate, 4),
                            "threshold": self.failure_rate_threshold,
                            "lookback_seconds": self.lookback_seconds,
                        },
                        suppress_seconds=120,
                    )
                elif self._failure_rate_alert_active:
                    # 失败率恢复正常：消除告警
                    self._failure_rate_alert_active = False
                    self.alert_manager.auto_resolve("task_failure_rate")
                    logger.info("task failure rate recovered below threshold")
        except Exception as e:
            logger.warning("task failure rate check failed: %s", e)

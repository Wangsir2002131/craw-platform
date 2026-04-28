"""Database access layer for Phase A task dispatching."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable, Iterator


class TaskMasterStatusStore:
    """Persist and query task execution state."""

    _ALLOWED_UPDATE_FIELDS = {
        "account_id",
        "server_id",
        "dispatched_at",
        "claimed_at",
        "completed_at",
        "fail_reason",
        "retry_count",
        "priority",
    }

    def __init__(
        self,
        db_config: dict[str, Any] | None = None,
        connection_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.db_config = db_config or {}
        self.connection_factory = connection_factory

    def create_task_record(self, task_unit: dict[str, Any]) -> int:
        """Create or refresh one task_master_status row and return its ID."""
        task_id, _ = self.create_or_get_task_record(task_unit)
        return task_id

    def create_or_get_task_record(self, task_unit: dict[str, Any]) -> tuple[int, bool]:
        """Create or refresh one task_master_status row and report whether it was newly inserted."""
        sql = """
        INSERT INTO task_master_status (
            product_llm_task_id,
            question_id,
            round_num,
            queue_name,
            execute_status,
            priority
        ) VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            queue_name = VALUES(queue_name),
            priority = VALUES(priority),
            updated_at = CURRENT_TIMESTAMP,
            id = LAST_INSERT_ID(id)
        """
        params = (
            task_unit["product_llm_task_id"],
            task_unit["question_id"],
            task_unit["round_num"],
            task_unit["queue_name"],
            task_unit.get("execute_status", "pending"),
            task_unit.get("priority", 50),
        )
        with self.cursor() as cursor:
            cursor.execute(sql, params)
            task_id = getattr(cursor, "lastrowid", None)
            if not task_id:
                raise RuntimeError("failed to create task_master_status record")
            return int(task_id), int(getattr(cursor, "rowcount", 0)) == 1

    def update_status(self, task_id: int, status: str, **kwargs: Any) -> None:
        """Update task execution status and optional metadata fields."""
        fields = ["execute_status = %s"]
        params: list[Any] = [status]

        for key, value in kwargs.items():
            if key not in self._ALLOWED_UPDATE_FIELDS:
                raise ValueError(f"unsupported update field: {key}")
            fields.append(f"{key} = %s")
            params.append(value)

        fields.append("updated_at = CURRENT_TIMESTAMP")
        params.append(task_id)

        sql = f"UPDATE task_master_status SET {', '.join(fields)} WHERE id = %s"
        with self.cursor() as cursor:
            cursor.execute(sql, tuple(params))

    def get_task_by_id(self, task_id: int) -> dict[str, Any] | None:
        """Return one task_master_status row by ID."""
        with self.cursor() as cursor:
            cursor.execute("SELECT * FROM task_master_status WHERE id = %s", (task_id,))
            return cursor.fetchone()

    def update_business_task_status(self, product_llm_task_id: str, status: str) -> None:
        """Update ent_data_product_llm_task.Status for one business task."""
        with self.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ent_data_product_llm_task
                SET Status = %s, UpdatedTime = NOW()
                WHERE ProductLlmTaskId = %s
                """,
                (status, product_llm_task_id),
            )

    def count_unfinished_task_units(self, product_llm_task_id: str) -> int:
        """Return how many task_master_status rows are not finished yet for one business task."""
        with self.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM task_master_status
                WHERE product_llm_task_id = %s
                  AND execute_status NOT IN ('completed', 'failed', 'cancelled')
                """,
                (product_llm_task_id,),
            )
            row = cursor.fetchone() or {}
            return int(row.get("total") or 0)

    def count_failed_task_units(self, product_llm_task_id: str) -> int:
        """Return how many task_master_status rows failed for one business task."""
        with self.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM task_master_status
                WHERE product_llm_task_id = %s
                  AND execute_status = 'failed'
                """,
                (product_llm_task_id,),
            )
            row = cursor.fetchone() or {}
            return int(row.get("total") or 0)

    def fetch_pending_llm_tasks(self, limit: int = 100) -> list[dict[str, Any]]:
        """Fetch Status='未开始' product LLM task-question rows."""
        sql = """
        SELECT
            llm_task.ProductLlmTaskId,
            llm_task.ProductTaskId,
            llm_task.ProductId,
            llm_task.LlmKey,
            llm_task.CreatedTime,
            llm_task.MaxRounds,
            50 AS PriorityScore,
            question.QuestionId,
            question.QuestionName
        FROM ent_data_product_llm_task AS llm_task
        LEFT JOIN ent_data_product_question AS prod_question
            ON llm_task.ProductId = prod_question.ProductId
            AND prod_question.Deleted = b'0'
            AND prod_question.Disabled = b'0'
        LEFT JOIN ent_data_question AS question
            ON prod_question.QuestionId = question.QuestionId
            AND question.Deleted = b'0'
            AND question.Disabled = b'0'
        WHERE llm_task.Deleted = b'0'
            AND llm_task.Disabled = b'0'
            AND llm_task.Status = '未开始'
            AND question.QuestionName IS NOT NULL
        ORDER BY llm_task.CreatedTime ASC, prod_question.CreatedTime ASC
        LIMIT %s
        """
        with self.cursor() as cursor:
            cursor.execute(sql, (limit,))
            rows = cursor.fetchall()
            return list(rows or [])

    @contextmanager
    def cursor(self) -> Iterator[Any]:
        """Yield a DictCursor and commit or rollback around the operation."""
        connection = self._connect()
        cursor = connection.cursor()
        try:
            yield cursor
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()

    def _connect(self) -> Any:
        if self.connection_factory is not None:
            return self.connection_factory()

        try:
            import pymysql
        except ImportError as exc:
            raise RuntimeError(
                "pymysql is required for database operations. Install pymysql or "
                "provide a connection_factory for tests."
            ) from exc

        config = dict(self.db_config)
        if "database" in config and "db" not in config:
            config["db"] = config.pop("database")
        config.setdefault("charset", "utf8mb4")
        config.setdefault("cursorclass", pymysql.cursors.DictCursor)
        return pymysql.connect(**config)


def utc_now_without_tz() -> datetime:
    """Return a naive timestamp suitable for MySQL DATETIME fields."""
    return datetime.utcnow()

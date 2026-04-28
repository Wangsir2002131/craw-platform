"""Collect crawler execution results and update task state."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from platform.store.db_store import TaskMasterStatusStore

logger = logging.getLogger(__name__)


class ResultCollector:
    """Normalize crawler results into task_master_status updates."""

    def __init__(self, db_store: TaskMasterStatusStore) -> None:
        self.db_store = db_store

    def collect_result(self, task_id: int, result: dict[str, Any]) -> bool:
        """Collect one crawler result and update task_master_status."""
        if not isinstance(result, dict):
            result = {"success": False, "error": f"invalid result type: {type(result).__name__}"}

        task_row = self.db_store.get_task_by_id(task_id)
        account_id = result.get("account_id")

        if result.get("success"):
            self.db_store.update_status(
                task_id,
                "completed",
                account_id=account_id,
                completed_at=datetime.now(),
            )
            self._sync_business_task_status(task_row)
            return True

        self.db_store.update_status(
            task_id,
            "failed",
            account_id=account_id,
            completed_at=datetime.now(),
            fail_reason=str(result.get("error") or "unknown crawler error")[:255],
        )
        self._sync_business_task_status(task_row)
        return False

    def _sync_business_task_status(self, task_row: dict[str, Any] | None) -> None:
        if not task_row:
            return

        product_llm_task_id = str(task_row.get("product_llm_task_id") or "").strip()
        if not product_llm_task_id:
            return

        if not hasattr(self.db_store, "count_unfinished_task_units"):
            return

        unfinished = self.db_store.count_unfinished_task_units(product_llm_task_id)
        if unfinished > 0:
            return

        failed = 0
        if hasattr(self.db_store, "count_failed_task_units"):
            failed = self.db_store.count_failed_task_units(product_llm_task_id)

        if failed == 0 and hasattr(self.db_store, "update_business_task_status"):
            self.db_store.update_business_task_status(product_llm_task_id, "爬网完成")

    def write_back_to_business(self, task_unit: dict[str, Any], result: dict[str, Any]) -> bool:
        """Best-effort business table write-back for result payloads with answers."""
        answer = result.get("answer")
        if not answer:
            return False

        try:
            from database_usage_example import insert_task_question_reply_content
        except Exception as exc:
            logger.warning("business write-back skipped: %s", exc)
            return False

        insert_task_question_reply_content(
            question_id=task_unit.get("question_id"),
            product_llm_task_id=task_unit.get("product_llm_task_id"),
            reply_content=answer,
            round_num=task_unit.get("round_num", 1),
        )
        return True

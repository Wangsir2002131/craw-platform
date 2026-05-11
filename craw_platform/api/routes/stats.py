"""Stats API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from craw_platform.config import DB_CONFIG
from craw_platform.queue.protocol import QUEUE_NAMES
from craw_platform.queue.redis_store import RedisQueueStore
from craw_platform.store.db_store import TaskMasterStatusStore


router = APIRouter(prefix="/stats", tags=["stats"])


def get_task_store() -> TaskMasterStatusStore:
    return TaskMasterStatusStore(DB_CONFIG)


def get_queue_store() -> RedisQueueStore:
    return RedisQueueStore()


@router.get("/summary")
def get_summary(
    task_store: TaskMasterStatusStore = Depends(get_task_store),
    queue_store: RedisQueueStore = Depends(get_queue_store),
) -> dict[str, Any]:
    resolved_task_store = task_store
    resolved_queue_store = queue_store

    task_summary = _collect_task_summary(resolved_task_store)
    queue_summary = _collect_queue_summary(resolved_queue_store)
    return {
        "tasks": task_summary,
        "queues": queue_summary,
    }


def _collect_task_summary(store: TaskMasterStatusStore) -> dict[str, Any]:
    sql = """
    SELECT execute_status, COUNT(*) AS total
    FROM task_master_status
    GROUP BY execute_status
    """
    with store.cursor() as cursor:
        cursor.execute(sql)
        rows = cursor.fetchall() or []

    summary = {row["execute_status"]: int(row["total"]) for row in rows}
    summary["total"] = sum(summary.values())
    return summary


def _collect_queue_summary(store: RedisQueueStore) -> dict[str, Any]:
    client = store._get_client()
    queues: dict[str, Any] = {}

    for queue_name in QUEUE_NAMES.values():
        priority_key = f"{queue_name}:priority"
        queues[queue_name] = {
            "length": store.length(queue_name),
            "priority_length": int(client.zcard(priority_key)) if hasattr(client, "zcard") else 0,
        }

    return queues

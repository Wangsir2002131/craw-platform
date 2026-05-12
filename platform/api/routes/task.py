"""Task API routes."""

from __future__ import annotations

from math import ceil
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from platform.config import DB_CONFIG
from platform.queue.protocol import get_queue_name
from platform.store.db_store import TaskMasterStatusStore


router = APIRouter(prefix="/tasks", tags=["tasks"])
dashboard_router = APIRouter(prefix="/api/tasks", tags=["dashboard-tasks"])

MODEL_LABELS = {
    "afu": "AFU",
    "deepseek": "DeepSeek",
    "doubao": "DouBao",
    "yuanbao": "YuanBao",
}

MODEL_SERVER_MAP = {
    "deepseek": "server-a",
    "yuanbao": "server-b",
    "doubao": "server-c",
    "afu": "server-d",
}
SERVER_OPTIONS = ["server-a", "server-b", "server-c", "server-d"]


class TaskCreateRequest(BaseModel):
    product_llm_task_id: str
    question_id: str
    question_name: str
    llm_key: str
    round_num: int = Field(default=1, ge=1)
    priority: int = Field(default=50, ge=0, le=100)
    execute_status: str = "pending"


class TaskCancelRequest(BaseModel):
    reason: str = "cancelled_by_api"


def get_task_store() -> TaskMasterStatusStore:
    return TaskMasterStatusStore(DB_CONFIG)


def _normalize_model(model: Any) -> str:
    return str(model or "").strip().lower()


def _model_label(model: Any) -> str:
    normalized = _normalize_model(model)
    return MODEL_LABELS.get(normalized, str(model or "-"))


def _server_label(model: Any) -> str:
    normalized = _normalize_model(model)
    return MODEL_SERVER_MAP.get(normalized, "-")


def _build_dashboard_rows(store: TaskMasterStatusStore) -> list[dict[str, Any]]:
    sql = """
    SELECT
        ProductLlmTaskId,
        ProductTaskId,
        ProductId,
        LlmKey,
        Status,
        MaxRounds,
        ActiveRound,
        CreatedTime,
        UpdatedTime
    FROM ent_data_product_llm_task
    WHERE Deleted = b'0'
      AND Disabled = b'0'
    ORDER BY CreatedTime DESC
    """
    with store.cursor() as cursor:
        cursor.execute(sql)
        rows = cursor.fetchall() or []

    items: list[dict[str, Any]] = []
    for row in rows:
        model = _model_label(row.get("LlmKey"))
        server = _server_label(row.get("LlmKey"))
        items.append(
            {
                "id": row.get("ProductLlmTaskId"),
                "productTaskId": row.get("ProductTaskId"),
                "productId": row.get("ProductId"),
                "model": model,
                "status": row.get("Status") or "-",
                "server": server,
                "maxRounds": row.get("MaxRounds"),
                "activeRound": row.get("ActiveRound"),
                "createdTime": row.get("CreatedTime").isoformat() if row.get("CreatedTime") else None,
                "updatedTime": row.get("UpdatedTime").isoformat() if row.get("UpdatedTime") else None,
                "dataMode": "legacy_task_table",
                "statusPrecision": "coarse",
                "statusSource": "ent_data_product_llm_task.Status",
            }
        )
    return items


@dashboard_router.get("")
def list_dashboard_tasks(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=5, ge=1, le=100),
    status_filter: str = Query(default="", alias="status"),
    model_filter: str = Query(default="", alias="model"),
    server_filter: str = Query(default="", alias="server"),
    model_id_filter: str = Query(default="", alias="model_id"),
    store: TaskMasterStatusStore = Depends(get_task_store),
) -> dict[str, Any]:
    all_items = _build_dashboard_rows(store)
    normalized_model_id_filter = model_id_filter.strip().lower()

    status_options = sorted({item["status"] for item in all_items if item["status"] and item["status"] != "-"})
    model_options = sorted({item["model"] for item in all_items if item["model"] and item["model"] != "-"})
    server_options = SERVER_OPTIONS

    filtered_items = all_items
    if status_filter:
        filtered_items = [item for item in filtered_items if item["status"] == status_filter]
    if model_filter:
        filtered_items = [item for item in filtered_items if item["model"] == model_filter]
    if server_filter:
        filtered_items = [item for item in filtered_items if item["server"] == server_filter]
    if normalized_model_id_filter:
        filtered_items = [
            item
            for item in filtered_items
            if normalized_model_id_filter in str(item.get("id") or "").lower()
        ]

    total = len(filtered_items)
    total_pages = max(1, ceil(total / page_size)) if total else 1
    page = min(page, total_pages)
    start = (page - 1) * page_size
    end = start + page_size
    paged_items = filtered_items[start:end]

    stats_map: dict[str, int] = {}
    for item in filtered_items:
        stats_map[item["status"]] = stats_map.get(item["status"], 0) + 1

    stats = [
        {"label": label, "count": count}
        for label, count in sorted(stats_map.items(), key=lambda pair: (-pair[1], pair[0]))
    ]

    return {
        "items": paged_items,
        "stats": stats,
        "filters": {
            "statusOptions": status_options,
            "modelOptions": model_options,
            "serverOptions": server_options,
            "selected": {
                "status": status_filter,
                "model": model_filter,
                "server": server_filter,
                "modelId": model_id_filter,
            },
        },
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "total": total,
            "totalPages": total_pages,
            "hasPrev": page > 1,
            "hasNext": page < total_pages,
        },
        "meta": {
            "dataMode": "legacy_task_table",
            "statusPrecision": "coarse",
            "statusSource": "ent_data_product_llm_task.Status",
        },
    }


@router.get("/{task_id}")
def get_task(task_id: int, store: TaskMasterStatusStore = Depends(get_task_store)) -> dict[str, Any]:
    task_store = store
    task = task_store.get_task_by_id(task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    return task


@router.post("", status_code=status.HTTP_201_CREATED)
def create_task(
    payload: TaskCreateRequest,
    store: TaskMasterStatusStore = Depends(get_task_store),
) -> dict[str, Any]:
    task_store = store
    queue_name = get_queue_name(payload.llm_key)
    task_id = task_store.create_task_record(
        {
            "product_llm_task_id": payload.product_llm_task_id,
            "question_id": payload.question_id,
            "question_name": payload.question_name,
            "queue_name": queue_name,
            "round_num": payload.round_num,
            "priority": payload.priority,
            "execute_status": payload.execute_status,
        }
    )
    task = task_store.get_task_by_id(task_id)
    return {
        "task_id": task_id,
        "queue_name": queue_name,
        "task": task,
    }


@router.post("/{task_id}/cancel")
def cancel_task(
    task_id: int,
    payload: TaskCancelRequest,
    store: TaskMasterStatusStore = Depends(get_task_store),
) -> dict[str, Any]:
    task_store = store
    task = task_store.get_task_by_id(task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")

    task_store.update_status(task_id, "cancelled", fail_reason=payload.reason)
    return {
        "task_id": task_id,
        "status": "cancelled",
        "reason": payload.reason,
    }

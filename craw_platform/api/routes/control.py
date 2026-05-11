"""Control API routes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from craw_platform.config import DB_CONFIG, PRIORITY_QUEUE_MIN
from craw_platform.queue.redis_store import RedisQueueStore
from craw_platform.queue.strategy_store import (
    DEFAULT_SCHEDULING_STRATEGY,
    QueueStrategyStore,
    VALID_SCHEDULING_STRATEGIES,
)
from craw_platform.store.db_store import TaskMasterStatusStore


router = APIRouter(prefix="/control", tags=["control"])


class ControlState:
    def __init__(self) -> None:
        self.paused = False
        self.restart_requested = False
        self.updated_at = datetime.utcnow()
        self.history: list[dict[str, Any]] = []

    def snapshot(self) -> dict[str, Any]:
        return {
            "paused": self.paused,
            "restart_requested": self.restart_requested,
            "updated_at": self.updated_at.isoformat(),
            "history": list(self.history),
        }

    def pause(self) -> dict[str, Any]:
        self.paused = True
        self.updated_at = datetime.utcnow()
        self.history.append({"action": "pause", "at": self.updated_at.isoformat()})
        return self.snapshot()

    def resume(self) -> dict[str, Any]:
        self.paused = False
        self.updated_at = datetime.utcnow()
        self.history.append({"action": "resume", "at": self.updated_at.isoformat()})
        return self.snapshot()

    def restart(self) -> dict[str, Any]:
        self.restart_requested = True
        self.updated_at = datetime.utcnow()
        self.history.append({"action": "restart", "at": self.updated_at.isoformat()})
        return self.snapshot()


_control_state = ControlState()


def get_control_state() -> ControlState:
    return _control_state


def get_queue_store() -> RedisQueueStore:
    return RedisQueueStore()


def get_task_store() -> TaskMasterStatusStore:
    return TaskMasterStatusStore(DB_CONFIG)


def get_strategy_store(queue_store: RedisQueueStore = Depends(get_queue_store)) -> QueueStrategyStore:
    return QueueStrategyStore(queue_store=queue_store)


class StrategyUpdateRequest(BaseModel):
    strategy: str


class PriorityApplyRequest(BaseModel):
    strategy: str = Field(default=DEFAULT_SCHEDULING_STRATEGY)
    product_id: str | None = None
    action: str = Field(default="none")
    amount: int = Field(default=10, ge=1, le=100)


def _load_priority_products(
    queue_store: RedisQueueStore,
    task_store: TaskMasterStatusStore,
) -> tuple[list[str], list[dict[str, Any]]]:
    queued_task_ids = queue_store.collect_product_llm_task_ids()
    products = task_store.fetch_products_for_llm_task_ids(queued_task_ids)
    return queued_task_ids, products


def _normalize_priority_action(action: str | None) -> str:
    normalized = str(action or "none").strip().lower()
    if normalized not in {"none", "raise", "lower", "reset"}:
        return "none"
    return normalized


@router.get("/status")
def get_control_status(control_state: ControlState = Depends(get_control_state)) -> dict[str, Any]:
    state = control_state
    return state.snapshot()


@router.get("/strategy")
def get_current_strategy(strategy_store: QueueStrategyStore = Depends(get_strategy_store)) -> dict[str, Any]:
    return {
        "current_strategy": strategy_store.get_strategy(),
        "available_strategies": list(VALID_SCHEDULING_STRATEGIES),
        "default_strategy": DEFAULT_SCHEDULING_STRATEGY,
    }


@router.get("/priority/products")
def get_priority_products(
    queue_store: RedisQueueStore = Depends(get_queue_store),
    task_store: TaskMasterStatusStore = Depends(get_task_store),
    strategy_store: QueueStrategyStore = Depends(get_strategy_store),
) -> dict[str, Any]:
    queue_store.normalize_model_queues(min_priority_queue_score=PRIORITY_QUEUE_MIN)
    queued_task_ids, products = _load_priority_products(queue_store, task_store)
    return {
        "products": [
            {
                "product_id": item["product_id"],
                "product_name": item["product_name"],
                "product_llm_task_ids": item["product_llm_task_ids"],
                "queued_task_count": len(item["product_llm_task_ids"]),
            }
            for item in products
        ],
        "queued_task_count": len(queued_task_ids),
        "current_strategy": strategy_store.get_strategy(),
        "available_strategies": list(VALID_SCHEDULING_STRATEGIES),
        "default_strategy": DEFAULT_SCHEDULING_STRATEGY,
        "default_priority": 50,
        "priority_threshold": PRIORITY_QUEUE_MIN,
    }


@router.post("/strategy", status_code=status.HTTP_202_ACCEPTED)
def update_current_strategy(
    payload: StrategyUpdateRequest,
    control_state: ControlState = Depends(get_control_state),
    strategy_store: QueueStrategyStore = Depends(get_strategy_store),
) -> dict[str, Any]:
    strategy = strategy_store.set_strategy(payload.strategy)
    control_state.updated_at = datetime.utcnow()
    control_state.history.append(
        {"action": "strategy", "strategy": strategy, "at": control_state.updated_at.isoformat()}
    )
    return {
        "current_strategy": strategy,
        "available_strategies": list(VALID_SCHEDULING_STRATEGIES),
    }


@router.post("/priority/apply", status_code=status.HTTP_202_ACCEPTED)
def apply_priority_control(
    payload: PriorityApplyRequest,
    control_state: ControlState = Depends(get_control_state),
    queue_store: RedisQueueStore = Depends(get_queue_store),
    task_store: TaskMasterStatusStore = Depends(get_task_store),
    strategy_store: QueueStrategyStore = Depends(get_strategy_store),
) -> dict[str, Any]:
    strategy = strategy_store.set_strategy(payload.strategy)
    queue_store.normalize_model_queues(min_priority_queue_score=PRIORITY_QUEUE_MIN)
    action = _normalize_priority_action(payload.action)
    amount = int(payload.amount)
    updated = {
        "product_id": None,
        "product_name": None,
        "action": action,
        "updated_messages": 0,
        "updated_task_ids": [],
        "updated_task_count": 0,
        "queues": [],
    }

    if action != "none":
        product_id = str(payload.product_id or "").strip()
        if not product_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="product_id is required")

        _queued_task_ids, products = _load_priority_products(queue_store, task_store)
        selected = next((item for item in products if str(item["product_id"]) == product_id), None)
        if selected is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="product not found in current queues")

        if action == "raise":
            redis_result = queue_store.update_product_task_priorities(
                selected["product_llm_task_ids"],
                delta=amount,
                min_priority_queue_score=PRIORITY_QUEUE_MIN,
            )
            task_store.adjust_task_priorities(selected["product_llm_task_ids"], amount)
        elif action == "lower":
            redis_result = queue_store.update_product_task_priorities(
                selected["product_llm_task_ids"],
                delta=-amount,
                min_priority_queue_score=PRIORITY_QUEUE_MIN,
            )
            task_store.adjust_task_priorities(selected["product_llm_task_ids"], -amount)
        else:
            redis_result = queue_store.update_product_task_priorities(
                selected["product_llm_task_ids"],
                priority=50,
                min_priority_queue_score=PRIORITY_QUEUE_MIN,
            )
            task_store.set_task_priorities(selected["product_llm_task_ids"], 50)

        updated = {
            "product_id": selected["product_id"],
            "product_name": selected["product_name"],
            "action": action,
            "updated_messages": redis_result["updated_messages"],
            "updated_task_ids": redis_result["updated_task_ids"],
            "updated_task_count": len(redis_result["updated_task_ids"]),
            "queues": redis_result["queues"],
        }

    control_state.updated_at = datetime.utcnow()
    control_state.history.append(
        {
            "action": "priority_apply",
            "strategy": strategy,
            "product_id": updated["product_id"],
            "priority_action": updated["action"],
            "updated_messages": updated["updated_messages"],
            "at": control_state.updated_at.isoformat(),
        }
    )
    return {
        "current_strategy": strategy,
        "updated": updated,
        "message": "应用成功",
    }


@router.post("/pause", status_code=status.HTTP_202_ACCEPTED)
def pause_service(control_state: ControlState = Depends(get_control_state)) -> dict[str, Any]:
    state = control_state
    return state.pause()


@router.post("/resume", status_code=status.HTTP_202_ACCEPTED)
def resume_service(control_state: ControlState = Depends(get_control_state)) -> dict[str, Any]:
    state = control_state
    return state.resume()


@router.post("/restart", status_code=status.HTTP_202_ACCEPTED)
def restart_service(control_state: ControlState = Depends(get_control_state)) -> dict[str, Any]:
    state = control_state
    return state.restart()

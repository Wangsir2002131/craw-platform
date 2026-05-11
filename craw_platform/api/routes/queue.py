"""Queue API routes."""

from __future__ import annotations

from math import ceil
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from craw_platform.consumers.manager import ConsumerManager, get_consumer_manager
from craw_platform.consumers.supervisor import ExternalSupervisorRegistry
from craw_platform.heartbeat.health_checker import HealthChecker
from craw_platform.queue.metrics import QueueMetricsStore
from craw_platform.queue.protocol import QUEUE_NAMES
from craw_platform.queue.redis_store import RedisQueueStore
from craw_platform.queue.strategy_store import QueueStrategyStore


router = APIRouter(prefix="/queues", tags=["queues"])
dashboard_router = APIRouter(prefix="/api/queues", tags=["dashboard-queues"])


def get_queue_store() -> RedisQueueStore:
    return RedisQueueStore()


def get_queue_metrics_store() -> QueueMetricsStore:
    return QueueMetricsStore()


def get_health_checker() -> HealthChecker:
    return HealthChecker()


def get_runtime_consumer_manager() -> ConsumerManager:
    return get_consumer_manager()


def get_external_supervisor_registry() -> ExternalSupervisorRegistry:
    return ExternalSupervisorRegistry()


def get_strategy_store(store: RedisQueueStore = Depends(get_queue_store)) -> QueueStrategyStore:
    return QueueStrategyStore(queue_store=store)


def _priority_queue_name(queue_name: str) -> str:
    return f"{queue_name}:priority"


def _validate_queue_name(queue_name: str) -> str:
    normalized = queue_name.strip().lower()
    if normalized not in QUEUE_NAMES.values():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="queue not found")
    return normalized


@router.get("/status")
def get_queue_status(store: RedisQueueStore = Depends(get_queue_store)) -> dict[str, Any]:
    queue_store = store
    queues = []
    for queue_name in QUEUE_NAMES.values():
        priority_size = queue_store.count_priority_messages(queue_name)
        queues.append(
            {
                "queue_name": queue_name,
                "length": queue_store.length(queue_name),
                "priority_length": priority_size,
            }
        )
    return {"queues": queues}


@router.post("/{queue_name}/clear")
def clear_queue(queue_name: str, store: RedisQueueStore = Depends(get_queue_store)) -> dict[str, Any]:
    queue_store = store
    resolved_name = _validate_queue_name(queue_name)
    client = queue_store._get_client()

    cleared = 0
    list_size = queue_store.length(resolved_name)
    if list_size and hasattr(client, "delete"):
        cleared += int(client.delete(resolved_name) or 0)

    priority_name = _priority_queue_name(resolved_name)
    if hasattr(client, "delete"):
        cleared += int(client.delete(priority_name) or 0)

    return {"queue_name": resolved_name, "cleared": cleared > 0}


@router.get("/stats")
def get_queue_stats(store: RedisQueueStore = Depends(get_queue_store)) -> dict[str, Any]:
    queue_store = store
    total_messages = 0
    priority_messages = 0
    stats: dict[str, Any] = {"redis_ping": queue_store.ping(), "queues": {}}

    for queue_name in QUEUE_NAMES.values():
        queue_length = queue_store.length(queue_name)
        priority_length = queue_store.count_priority_messages(queue_name)
        total_messages += queue_length
        priority_messages += priority_length
        stats["queues"][queue_name] = {
            "length": queue_length,
            "priority_length": priority_length,
        }

    stats["total_messages"] = total_messages
    stats["priority_messages"] = priority_messages
    return stats


@router.get("/consumers")
def get_consumer_status(consumer_manager: ConsumerManager = Depends(get_runtime_consumer_manager)) -> dict[str, Any]:
    return consumer_manager.status()


@router.post("/consumers/{model_key}/increment", status_code=status.HTTP_202_ACCEPTED)
def increment_consumer(
    model_key: str,
    consumer_manager: ConsumerManager = Depends(get_runtime_consumer_manager),
    external_supervisors: ExternalSupervisorRegistry = Depends(get_external_supervisor_registry),
) -> dict[str, Any]:
    try:
        if consumer_manager.enabled:
            return consumer_manager.increment(model_key)
        return external_supervisors.increment(model_key)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/consumers/{model_key}/decrement", status_code=status.HTTP_202_ACCEPTED)
def decrement_consumer(
    model_key: str,
    consumer_manager: ConsumerManager = Depends(get_runtime_consumer_manager),
    external_supervisors: ExternalSupervisorRegistry = Depends(get_external_supervisor_registry),
) -> dict[str, Any]:
    try:
        if consumer_manager.enabled:
            return consumer_manager.decrement(model_key)
        return external_supervisors.decrement(model_key)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


def _format_wait_seconds(wait_seconds: int) -> str:
    if wait_seconds >= 3600:
        return f"{wait_seconds // 3600}h"
    if wait_seconds >= 60:
        return f"{wait_seconds // 60}m"
    return f"{wait_seconds}s"


def _resolve_queue_state(
    *,
    backlog: int,
    consumers: int,
    stale_consumers: int,
    wait_seconds: int,
) -> tuple[str, str]:
    if stale_consumers > 0 or (backlog > 0 and consumers == 0):
        return "异常", "high"
    if backlog >= 50 or wait_seconds >= 600:
        return "积压", "medium"
    if backlog == 0 and consumers == 0:
        return "空闲", "low"
    return "正常", "low"


@dashboard_router.get("")
def list_dashboard_queues(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    state_filter: str = Query(default="", alias="state"),
    queue_store: RedisQueueStore = Depends(get_queue_store),
    metrics_store: QueueMetricsStore = Depends(get_queue_metrics_store),
    health_checker: HealthChecker = Depends(get_health_checker),
    consumer_manager: ConsumerManager = Depends(get_runtime_consumer_manager),
    external_supervisors: ExternalSupervisorRegistry = Depends(get_external_supervisor_registry),
    strategy_store: QueueStrategyStore = Depends(get_strategy_store),
) -> dict[str, Any]:
    current_strategy = strategy_store.get_strategy()
    manager_status = consumer_manager.status().get("models", {})
    external_status = external_supervisors.list_supervisors()
    stale_lookup = {
        item.get("heartbeat_key"): item
        for item in health_checker.find_stale_consumers(stale_after_seconds=60)
    }
    items: list[dict[str, Any]] = []

    model_queues = [
        queue_name
        for queue_name in QUEUE_NAMES.values()
        if queue_name not in {QUEUE_NAMES["results"], QUEUE_NAMES["dead_letter"]}
    ]

    for queue_name in model_queues:
        model_key = queue_name.rsplit(":", 1)[-1]
        queue_length = queue_store.length(queue_name)
        priority_length = queue_store.count_priority_messages(queue_name)
        normal_length = max(0, queue_length - priority_length)
        backlog = queue_length
        healthy_consumers, stale_consumers = metrics_store.queue_consumers(queue_name, stale_after_seconds=60)
        wait_seconds = metrics_store.oldest_wait_seconds(queue_name)
        throughput_per_min = metrics_store.processed_last_minute(queue_name)
        managed = manager_status.get(model_key, {})
        external = external_status.get(model_key, {})
        display_consumers = len(healthy_consumers)
        if external:
            display_consumers = int(external.get("active_consumers") or 0)
        elif managed.get("managedEnabled"):
            display_consumers = int(managed.get("activeConsumers") or 0)
        state_label, risk_level = _resolve_queue_state(
            backlog=backlog,
            consumers=display_consumers,
            stale_consumers=len(stale_consumers),
            wait_seconds=wait_seconds,
        )
        items.append(
            {
                "queueName": queue_name,
                "model": model_key,
                "backlog": backlog,
                "listLength": queue_length,
                "normalLength": normal_length,
                "priorityLength": priority_length,
                "consumers": display_consumers,
                "staleConsumers": len(stale_consumers),
                "desiredConsumers": managed.get("desiredConsumers", external.get("desired_consumers", 0)),
                "managedActiveConsumers": managed.get("activeConsumers", 0),
                "drainingConsumers": managed.get("drainingConsumers", external.get("draining_consumers", 0)),
                "externalSupervisorRunning": bool(external),
                "externalActiveConsumers": external.get("active_consumers", 0),
                "throughputPerMinute": throughput_per_min,
                "waitSeconds": wait_seconds,
                "waitLabel": _format_wait_seconds(wait_seconds),
                "state": state_label,
                "riskLevel": risk_level,
                "managedEnabled": managed.get("managedEnabled", False),
                "strategy": current_strategy,
                "consumerIds": [item.get("consumer_id") for item in healthy_consumers],
                "staleConsumerIds": [item.get("consumer_id") for item in stale_consumers],
                "staleHeartbeatKeys": [item.get("heartbeat_key") for item in stale_consumers if item.get("heartbeat_key") in stale_lookup],
            }
        )

    state_options = sorted({item["state"] for item in items})
    filtered_items = items
    if state_filter:
        filtered_items = [item for item in filtered_items if item["state"] == state_filter]

    total = len(filtered_items)
    total_pages = max(1, ceil(total / page_size)) if total else 1
    page = min(page, total_pages)
    start = (page - 1) * page_size
    end = start + page_size
    paged_items = filtered_items[start:end]

    stats_map: dict[str, int] = {}
    for item in filtered_items:
        stats_map[item["state"]] = stats_map.get(item["state"], 0) + 1

    stats = [
        {"label": label, "count": count}
        for label, count in sorted(stats_map.items(), key=lambda pair: (-pair[1], pair[0]))
    ]

    return {
        "items": paged_items,
        "stats": stats,
        "filters": {
            "stateOptions": state_options,
            "selected": {"state": state_filter},
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
            "source": "redis",
            "heartbeatTtlSeconds": 30,
            "staleAfterSeconds": 60,
        },
    }

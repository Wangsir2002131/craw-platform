"""Alert API routes — REST interface for the frontend Dashboard.

This is the canonical implementation used by the main server.
Imported by platform.api.routes.alert for registration into the FastAPI app.
"""

from __future__ import annotations

from typing import Any

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from platform.alerts.alert_levels import AlertCategory, AlertLevel
from platform.alerts.alert_manager import AlertManager, get_alert_manager

router = APIRouter(prefix="/alerts", tags=["alerts"])

logger = logging.getLogger(__name__)
# ---------------------------------------------------------------------------
#  Request / Response models
# ---------------------------------------------------------------------------

class AlertConfigRequest(BaseModel):
    """Request body for creating / updating an alert configuration."""
    name: str
    enabled: bool = True
    channels: list[str] = Field(default_factory=list)
    rules: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
#  Dependency: inject the global AlertManager singleton
# ---------------------------------------------------------------------------

def get_manager() -> AlertManager:
    return get_alert_manager()


# ---------------------------------------------------------------------------
#  Config endpoints
# ---------------------------------------------------------------------------

@router.get("/configs")
def list_alert_configs(manager: AlertManager = Depends(get_manager)) -> dict[str, Any]:
    """List all registered alert configurations."""
    return {"configs": manager.list_configs()}


@router.post("/configs", status_code=status.HTTP_201_CREATED)
def save_alert_config(
    payload: AlertConfigRequest,
    manager: AlertManager = Depends(get_manager),
) -> dict[str, Any]:
    """Create or update an alert configuration."""
    manager.register_config(payload.name, enabled=payload.enabled, params=payload.rules)
    return {"config": manager.get_config(payload.name)}


# ---------------------------------------------------------------------------
#  Event endpoints
# ---------------------------------------------------------------------------

@router.get("/events")
def list_alert_events(
    category: str | None = Query(None, description="Filter by: task / queue / account / system"),
    level: str | None = Query(None, description="Filter by: yellow / red (error merged into red)"),
    acknowledged: bool | None = Query(None, description="True=only acknowledged, False=only unacknowledged"),
    limit: int = Query(100, ge=1, le=500, description="Max events to return"),
    manager: AlertManager = Depends(get_manager),
) -> dict[str, Any]:
    """List alert events with optional filters (newest first)."""
    try:
        cat = AlertCategory(category) if category else None
        raw_level = level.strip().lower() if level else None
        # 向后兼容：error 级别已合并到 red，自动映射
        if raw_level == "error":
            raw_level = "red"
        lvl = AlertLevel(raw_level) if raw_level else None
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid filter value: category={category}, level={level}",
        )

    events = manager.list_events(category=cat, level=lvl, acknowledged=acknowledged, limit=limit)
    return {
        "events": [e.to_dict() for e in events],
        "total": len(events),
        "filters": {
            "category": category,
            "level": level,
            "acknowledged": acknowledged,
            "category_options": [c.value for c in AlertCategory],
            "level_options": [lv.value for lv in AlertLevel],
        },
    }


@router.get("/summary")
def get_alert_summary(manager: AlertManager = Depends(get_manager)) -> dict[str, Any]:
    """Get alert summary for Dashboard display."""
    return manager.get_summary()


# ---------------------------------------------------------------------------
#  Acknowledgement endpoints
# ---------------------------------------------------------------------------

@router.post("/acknowledge/{event_id}", status_code=status.HTTP_202_ACCEPTED)
def acknowledge_alert(
    event_id: str,
    acknowledged_by: str = Query("web", description="Who acknowledged this alert"),
    manager: AlertManager = Depends(get_manager),
) -> dict[str, Any]:
    """Acknowledge a single alert event by its ID."""
    ok = manager.acknowledge(event_id, acknowledged_by=acknowledged_by)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="alert event not found or already acknowledged",
        )
    return {"acknowledged": True, "event_id": event_id}


@router.post("/acknowledge-all", status_code=status.HTTP_202_ACCEPTED)
def acknowledge_all_alerts(
    category: str | None = Query(None, description="Acknowledge only this category"),
    level: str | None = Query(None, description="Acknowledge only this level"),
    acknowledged_by: str = Query("web", description="Who acknowledged"),
    manager: AlertManager = Depends(get_manager),
) -> dict[str, Any]:
    """Acknowledge all matching unacknowledged alerts. Returns count acknowledged."""
    try:
        cat = AlertCategory(category) if category else None
        lvl = AlertLevel(level) if level else None
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid filter value: category={category}, level={level}",
        )

    count = manager.acknowledge_all(category=cat, level=lvl, acknowledged_by=acknowledged_by)
    return {"acknowledged_count": count}


# ---------------------------------------------------------------------------
#  Force-check / clear endpoints
# ---------------------------------------------------------------------------

@router.post("/force-check", status_code=status.HTTP_200_OK)
def force_check_alerts(
    limit: int = Query(100, ge=1, le=500),
    clear_history: bool = Query(True, description="Whether to clear existing events before re-checking"),
    manager: AlertManager = Depends(get_manager),
) -> dict[str, Any]:
    from platform.alerts.alert_manager import get_monitors

    cleared_count = 0
    if clear_history:
        cleared_count = manager.clear_events()
    monitors = get_monitors()
    failed_monitors = []
    for monitor in monitors:
        try:
            if clear_history:
                monitor.reset_states()
            monitor.force_check()
        except Exception as e:
            logger.exception("force_check failed for %s", monitor.__class__.__name__)
            failed_monitors.append({"monitor": monitor.__class__.__name__, "error": str(e)})
    events = manager.list_events(limit=limit)
    summary = manager.get_summary()
    return {
        "cleared": clear_history,
        "cleared_count": cleared_count,
        "monitors_checked": len(monitors),
        "monitors_failed": failed_monitors,
        "events": [e.to_dict() for e in events],
        "summary": summary,
    }


@router.post("/clear", status_code=status.HTTP_200_OK)
def clear_alert_events(manager: AlertManager = Depends(get_manager)) -> dict[str, Any]:
    """Clear all stored alert events without re-checking monitors."""
    count = manager.clear_events()
    return {"cleared_count": count}


@router.patch("/config/queue-thresholds", status_code=status.HTTP_200_OK)
def update_queue_thresholds(
    warning: int = Query(100, ge=1, description="Queue length warning threshold (yellow)"),
    critical: int = Query(500, ge=2, description="Queue length critical threshold (red)"),
) -> dict[str, Any]:
    """Update QueueMonitor warning and critical thresholds at runtime."""
    from platform.alerts.alert_manager import get_monitors
    if critical <= warning:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="critical threshold must be greater than warning threshold",
        )
    updated = 0
    for monitor in get_monitors():
        if hasattr(monitor, "warning_threshold") and hasattr(monitor, "critical_threshold"):
            monitor.warning_threshold = max(1, warning)
            monitor.critical_threshold = critical
            updated += 1
    return {"warning_threshold": warning, "critical_threshold": critical, "monitors_updated": updated}

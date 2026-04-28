"""Alert API routes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field


router = APIRouter(prefix="/alerts", tags=["alerts"])


class AlertConfigRequest(BaseModel):
    name: str
    enabled: bool = True
    channels: list[str] = Field(default_factory=list)
    rules: dict[str, Any] = Field(default_factory=dict)


class AlertTriggerRequest(BaseModel):
    name: str
    level: str = "warning"
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class AlertRegistry:
    def __init__(self) -> None:
        self._configs: dict[str, dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []

    def upsert_config(self, payload: AlertConfigRequest) -> dict[str, Any]:
        config = payload.model_dump()
        config["updated_at"] = datetime.utcnow().isoformat()
        self._configs[payload.name] = config
        return config

    def list_configs(self) -> list[dict[str, Any]]:
        return list(self._configs.values())

    def trigger(self, payload: AlertTriggerRequest) -> dict[str, Any]:
        config = self._configs.get(payload.name)
        if not config:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="alert config not found")
        event = payload.model_dump()
        event["triggered_at"] = datetime.utcnow().isoformat()
        event["enabled"] = bool(config.get("enabled", True))
        self._events.append(event)
        return event

    def list_events(self) -> list[dict[str, Any]]:
        return list(self._events)


_registry = AlertRegistry()


def get_alert_registry() -> AlertRegistry:
    return _registry


@router.get("/configs")
def list_alert_configs(registry: AlertRegistry = Depends(get_alert_registry)) -> dict[str, Any]:
    alert_registry = registry
    return {"configs": alert_registry.list_configs()}


@router.post("/configs", status_code=status.HTTP_201_CREATED)
def save_alert_config(
    payload: AlertConfigRequest,
    registry: AlertRegistry = Depends(get_alert_registry),
) -> dict[str, Any]:
    alert_registry = registry
    return {"config": alert_registry.upsert_config(payload)}


@router.post("/trigger", status_code=status.HTTP_202_ACCEPTED)
def trigger_alert(
    payload: AlertTriggerRequest,
    registry: AlertRegistry = Depends(get_alert_registry),
) -> dict[str, Any]:
    alert_registry = registry
    return {"event": alert_registry.trigger(payload)}


@router.get("/events")
def list_alert_events(registry: AlertRegistry = Depends(get_alert_registry)) -> dict[str, Any]:
    alert_registry = registry
    return {"events": alert_registry.list_events()}

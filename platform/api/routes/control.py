"""Control API routes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, status


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


@router.get("/status")
def get_control_status(control_state: ControlState = Depends(get_control_state)) -> dict[str, Any]:
    state = control_state
    return state.snapshot()


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

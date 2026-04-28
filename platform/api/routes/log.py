"""Log API routes."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from platform.config import LOG_DIR


router = APIRouter(prefix="/logs", tags=["logs"])


def get_log_dir() -> Path:
    return LOG_DIR


@router.get("")
def list_logs(log_dir: Path = Depends(get_log_dir)) -> dict[str, Any]:
    resolved_log_dir = log_dir
    if not resolved_log_dir.exists():
        return {"logs": []}

    logs = []
    for path in sorted(resolved_log_dir.glob("*.log")):
        stats = path.stat()
        logs.append(
            {
                "name": path.name,
                "size": stats.st_size,
                "modified_at": stats.st_mtime,
            }
        )
    return {"logs": logs}


@router.get("/{log_name}")
def read_log(
    log_name: str,
    lines: int = Query(default=100, ge=1, le=1000),
    log_dir: Path = Depends(get_log_dir),
) -> dict[str, Any]:
    resolved_log_dir = log_dir
    log_path = (resolved_log_dir / log_name).resolve()
    if not str(log_path).startswith(str(resolved_log_dir.resolve())):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid log path")
    if not log_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="log not found")

    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        tail = list(deque(handle, maxlen=lines))

    return {
        "log_name": log_name,
        "lines": [line.rstrip("\n") for line in tail],
    }

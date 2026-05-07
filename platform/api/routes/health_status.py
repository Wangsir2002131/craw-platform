"""Health status API route – checks master, Redis, database, and consumer nodes."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter

from platform.config import DB_CONFIG, REDIS_URL
from platform.heartbeat.health_checker import HealthChecker


router = APIRouter(prefix="/api/health-status", tags=["health-status"])


@router.get("")
def get_health_status() -> dict[str, Any]:
    """Return a comprehensive health snapshot of all platform components."""
    return {
        "master":    _check_master(),
        "redis":     _check_redis(),
        "database":  _check_db(),
        "consumers": _check_consumers(),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


# ── component checks ──────────────────────────────────────────────────────────

def _check_master() -> dict[str, Any]:
    try:
        from platform.api.routes.control import get_control_state
        state = get_control_state().snapshot()
        detail = "调度器已暂停" if state["paused"] else "运行中"
        return {
            "status": "online",
            "dispatcher_paused": state["paused"],
            "restart_requested": state["restart_requested"],
            "details": detail,
        }
    except Exception as exc:
        return {
            "status": "error",
            "dispatcher_paused": None,
            "restart_requested": None,
            "details": str(exc),
        }


def _check_redis() -> dict[str, Any]:
    try:
        import redis as redis_lib
        client = redis_lib.Redis.from_url(
            REDIS_URL,
            decode_responses=False,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        t0 = time.monotonic()
        client.ping()
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        return {
            "status": "connected",
            "latency_ms": latency_ms,
            "details": f"平均延迟 {latency_ms} ms",
        }
    except Exception as exc:
        return {
            "status": "error",
            "latency_ms": None,
            "details": str(exc),
        }


def _check_db() -> dict[str, Any]:
    try:
        import pymysql
        config = dict(DB_CONFIG)
        if "database" in config and "db" not in config:
            config["db"] = config.pop("database")
        config.setdefault("charset", "utf8mb4")
        config["connect_timeout"] = 3
        t0 = time.monotonic()
        conn = pymysql.connect(**config)
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.close()
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        return {
            "status": "connected",
            "latency_ms": latency_ms,
            "details": f"查询延迟 {latency_ms} ms",
        }
    except Exception as exc:
        return {
            "status": "error",
            "latency_ms": None,
            "details": str(exc),
        }


def _check_consumers() -> list[dict[str, Any]]:
    try:
        checker = HealthChecker(redis_url=REDIS_URL)
        now = datetime.now(timezone.utc)
        consumers = checker.list_consumers()
        result = []
        for c in consumers:
            ts = c.get("timestamp")
            stale_seconds: int | None = None
            is_stale = False
            if ts:
                try:
                    last_seen = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
                    stale_seconds = int((now - last_seen).total_seconds())
                    is_stale = stale_seconds > 60
                except Exception:
                    pass
            result.append({
                "consumer_id":       c.get("consumer_id", "-"),
                "queue_name":        c.get("queue_name", "-"),
                "status":            c.get("status", "unknown"),
                "last_seen_seconds": stale_seconds,
                "is_stale":          is_stale,
            })
        return result
    except Exception:
        return []

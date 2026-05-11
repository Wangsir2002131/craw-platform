"""Heartbeat health checking."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable


class HealthChecker:
    """Detect stale consumer heartbeats in Redis."""

    def __init__(
        self,
        redis_url: str | None = None,
        *,
        client: Any | None = None,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.redis_url = redis_url or "redis://localhost:6379/0"
        self._client = client
        self._client_factory = client_factory

    def list_consumers(self) -> list[dict[str, Any]]:
        client = self._get_client()
        keys = self._iter_keys("heartbeat:consumer:*")
        consumers = []
        for key in keys:
            payload = client.get(key)
            if payload is None:
                continue
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8")
            data = json.loads(payload)
            data["heartbeat_key"] = key.decode("utf-8") if isinstance(key, bytes) else key
            consumers.append(data)
        return consumers

    def find_stale_consumers(self, stale_after_seconds: int = 60) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        stale = []
        for consumer in self.list_consumers():
            timestamp = consumer.get("timestamp")
            if not timestamp:
                stale.append({**consumer, "stale_seconds": None})
                continue
            last_seen = datetime.fromisoformat(timestamp).replace(tzinfo=timezone.utc)
            delta = (now - last_seen).total_seconds()
            if delta > stale_after_seconds:
                stale.append({**consumer, "stale_seconds": int(delta)})
        return stale

    def clear_stale_consumers(self, stale_after_seconds: int = 60) -> list[str]:
        client = self._get_client()
        deleted_keys = []
        for consumer in self.find_stale_consumers(stale_after_seconds=stale_after_seconds):
            key = consumer["heartbeat_key"]
            client.delete(key)
            deleted_keys.append(key)
        return deleted_keys

    def _iter_keys(self, pattern: str) -> list[Any]:
        client = self._get_client()
        if hasattr(client, "scan_iter"):
            return list(client.scan_iter(match=pattern))
        if hasattr(client, "keys"):
            return list(client.keys(pattern))
        return []

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if self._client_factory is not None:
            self._client = self._client_factory()
            return self._client

        try:
            import redis
        except ImportError as exc:
            raise RuntimeError("redis is required for health checking.") from exc

        self._client = redis.Redis.from_url(self.redis_url, decode_responses=False)
        return self._client

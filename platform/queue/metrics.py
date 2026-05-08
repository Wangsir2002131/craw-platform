"""Redis-backed queue metrics helpers for dashboard observability."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from platform.heartbeat.health_checker import HealthChecker


class QueueMetricsStore:
    """Collect queue runtime metrics from Redis."""

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

    def record_processed(self, queue_name: str, *, success: bool, task_id: int | None = None) -> None:
        client = self._get_client()
        now_ts = int(datetime.now(timezone.utc).timestamp())
        key = self.processed_key(queue_name)
        member = f"{now_ts}:{task_id or 0}:{'ok' if success else 'fail'}"
        client.zadd(key, {member: now_ts})
        cutoff = now_ts - 3600
        client.zremrangebyscore(key, 0, cutoff)
        client.expire(key, 7200)

    def processed_last_minute(self, queue_name: str) -> int:
        client = self._get_client()
        now_ts = int(datetime.now(timezone.utc).timestamp())
        key = self.processed_key(queue_name)
        return int(client.zcount(key, now_ts - 60, now_ts))

    def oldest_wait_seconds(self, queue_name: str) -> int:
        oldest_ts = self.oldest_enqueued_timestamp(queue_name)
        if oldest_ts is None:
            return 0
        now_ts = int(datetime.now(timezone.utc).timestamp())
        return max(0, now_ts - oldest_ts)

    def oldest_enqueued_timestamp(self, queue_name: str) -> int | None:
        timestamps: list[int] = []
        list_ts = self._oldest_list_enqueued_timestamp(queue_name)
        if list_ts is not None:
            timestamps.append(list_ts)
        priority_ts = self._oldest_priority_enqueued_timestamp(queue_name)
        if priority_ts is not None:
            timestamps.append(priority_ts)
        return min(timestamps) if timestamps else None

    def queue_consumers(
        self,
        queue_name: str,
        *,
        stale_after_seconds: int = 60,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        checker = HealthChecker(
            redis_url=self.redis_url,
            client=self._client,
            client_factory=self._client_factory,
        )
        consumers = [item for item in checker.list_consumers() if item.get("queue_name") == queue_name]
        stale_by_key = {
            item.get("heartbeat_key"): item
            for item in checker.find_stale_consumers(stale_after_seconds=stale_after_seconds)
            if item.get("queue_name") == queue_name
        }
        stale = [stale_by_key[item.get("heartbeat_key")] for item in consumers if item.get("heartbeat_key") in stale_by_key]
        healthy = [item for item in consumers if item.get("heartbeat_key") not in stale_by_key]
        return healthy, stale

    @staticmethod
    def processed_key(queue_name: str) -> str:
        return f"metrics:{queue_name}:processed"

    @staticmethod
    def normalize_timestamp(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return int(value)
        text = str(value).strip()
        if not text:
            return None
        try:
            return int(float(text))
        except ValueError:
            pass
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return int(dt.timestamp())

    def _oldest_list_enqueued_timestamp(self, queue_name: str) -> int | None:
        client = self._get_client()
        payload = client.lindex(queue_name, -1)
        if payload is None:
            return None
        try:
            message = self._deserialize(payload)
        except (ValueError, Exception):
            return None
        return self.normalize_timestamp(message.get("enqueued_at"))

    def _oldest_priority_enqueued_timestamp(self, queue_name: str) -> int | None:
        client = self._get_client()
        key = f"{queue_name}:priority"
        if not hasattr(client, "zrange"):
            return None
        payloads = client.zrange(key, 0, -1)
        oldest: int | None = None
        for payload in payloads or []:
            message = self._deserialize(payload)
            current = self.normalize_timestamp(message.get("enqueued_at"))
            if current is None:
                continue
            if oldest is None or current < oldest:
                oldest = current
        return oldest

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if self._client_factory is not None:
            self._client = self._client_factory()
            return self._client

        try:
            import redis
        except ImportError as exc:
            raise RuntimeError("redis is required for queue metrics.") from exc

        self._client = redis.Redis.from_url(self.redis_url, decode_responses=False)
        return self._client

    @staticmethod
    def _deserialize(payload: bytes | str) -> dict[str, Any]:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("queue payload must decode to a dict")
        return data

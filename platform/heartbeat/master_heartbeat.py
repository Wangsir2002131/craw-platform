"""Master service heartbeat reporting."""

from __future__ import annotations

import json
import socket
from datetime import datetime
from typing import Any, Callable


class MasterHeartbeat:
    """Publish master service liveness into Redis."""

    def __init__(
        self,
        redis_url: str | None = None,
        *,
        client: Any | None = None,
        client_factory: Callable[[], Any] | None = None,
        server_id: str | None = None,
        ttl_seconds: int = 30,
    ) -> None:
        self.redis_url = redis_url or "redis://localhost:6379/0"
        self._client = client
        self._client_factory = client_factory
        self.server_id = server_id or socket.gethostname()
        self.ttl_seconds = ttl_seconds

    @property
    def heartbeat_key(self) -> str:
        return f"heartbeat:master:{self.server_id}"

    def beat(self, status: str = "running", extra: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {
            "server_id": self.server_id,
            "status": status,
            "timestamp": datetime.utcnow().isoformat(),
            "ttl_seconds": self.ttl_seconds,
        }
        if extra:
            payload["extra"] = dict(extra)
        self._get_client().set(self.heartbeat_key, json.dumps(payload), ex=self.ttl_seconds)
        return payload

    def clear(self) -> None:
        self._get_client().delete(self.heartbeat_key)

    def read(self) -> dict[str, Any] | None:
        payload = self._get_client().get(self.heartbeat_key)
        if payload is None:
            return None
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        return json.loads(payload)

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if self._client_factory is not None:
            self._client = self._client_factory()
            return self._client

        try:
            import redis
        except ImportError as exc:
            raise RuntimeError("redis is required for master heartbeat.") from exc

        self._client = redis.Redis.from_url(self.redis_url, decode_responses=False)
        return self._client

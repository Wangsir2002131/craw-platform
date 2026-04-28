"""Redis-backed queue storage for Phase B."""

from __future__ import annotations

import json
from typing import Any, Callable

from platform.queue.protocol import QUEUE_NAMES, QueueMessage


class RedisQueueStore:
    """Push and pop typed queue messages through Redis lists."""

    def __init__(
        self,
        redis_url: str | None = None,
        *,
        client: Any | None = None,
        client_factory: Callable[[], Any] | None = None,
        queue_names: dict[str, str] | None = None,
    ) -> None:
        self.redis_url = redis_url or "redis://localhost:6379/0"
        self._client = client
        self._client_factory = client_factory
        self.queue_names = dict(QUEUE_NAMES)
        if queue_names:
            self.queue_names.update(queue_names)

    def push(self, queue_name: str, message: QueueMessage | dict[str, Any]) -> int:
        """Serialize a message and push it to the left side of a queue."""
        payload = self._serialize(message)
        client = self._get_client()
        return int(client.lpush(queue_name, payload))

    def pop(self, queue_name: str) -> dict[str, Any] | None:
        """Pop one message from the right side of a queue."""
        client = self._get_client()
        payload = client.rpop(queue_name)
        if payload is None:
            return None
        return self._deserialize(payload)

    def blocking_pop(self, queue_name: str, timeout: int = 0) -> dict[str, Any] | None:
        """Block on the queue until one message is available or timeout expires."""
        client = self._get_client()
        result = client.brpop(queue_name, timeout=timeout)
        if not result:
            return None

        _, payload = result
        return self._deserialize(payload)

    def length(self, queue_name: str) -> int:
        """Return the current queue length."""
        client = self._get_client()
        return int(client.llen(queue_name))

    def ping(self) -> bool:
        """Validate Redis connectivity."""
        client = self._get_client()
        return bool(client.ping())

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        if self._client_factory is not None:
            self._client = self._client_factory()
            return self._client

        try:
            import redis
        except ImportError as exc:
            raise RuntimeError(
                "redis is required for queue operations. Install redis or provide a client."
            ) from exc

        self._client = redis.Redis.from_url(self.redis_url, decode_responses=False)
        return self._client

    @staticmethod
    def _serialize(message: QueueMessage | dict[str, Any]) -> str:
        if not isinstance(message, dict):
            raise TypeError("queue message must be a dict")
        return json.dumps(message, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _deserialize(payload: bytes | str) -> dict[str, Any]:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("queue payload must decode to a dict")
        return data

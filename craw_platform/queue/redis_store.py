"""Redis-backed queue storage for Phase B."""

from __future__ import annotations

import json
from typing import Any, Callable

from craw_platform.queue.protocol import MODEL_QUEUE_NAMES, QUEUE_NAMES, QueueMessage


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
        """Serialize a message and append it to the right side of a queue."""
        payload = self._serialize(message)
        client = self._get_client()
        return int(client.rpush(queue_name, payload))

    def enqueue_by_priority(
        self,
        queue_name: str,
        message: QueueMessage | dict[str, Any],
        *,
        min_priority_queue_score: int = 51,
    ) -> int:
        """Append one task into the canonical list queue."""
        return self.push(queue_name, message)

    def pop(self, queue_name: str) -> dict[str, Any] | None:
        """Pop one message from the left side of a queue."""
        client = self._get_client()
        payload = client.lpop(queue_name)
        if payload is None:
            return None
        return self._deserialize(payload)

    def blocking_pop(self, queue_name: str, timeout: int = 0) -> dict[str, Any] | None:
        """Block on the queue left side until one message is available or timeout expires."""
        client = self._get_client()
        result = client.blpop(queue_name, timeout=timeout)
        if not result:
            return None

        _, payload = result
        return self._deserialize(payload)

    def pop_latest(self, queue_name: str) -> dict[str, Any] | None:
        """Pop one message from the right side of a queue."""
        client = self._get_client()
        payload = client.rpop(queue_name)
        if payload is None:
            return None
        return self._deserialize(payload)

    def blocking_pop_latest(self, queue_name: str, timeout: int = 0) -> dict[str, Any] | None:
        """Block on the queue right side until one message is available or timeout expires."""
        client = self._get_client()
        result = client.brpop(queue_name, timeout=timeout)
        if not result:
            return None

        _, payload = result
        return self._deserialize(payload)

    def remove_message(self, queue_name: str, message: QueueMessage | dict[str, Any], count: int = 1) -> int:
        """Remove one or more serialized queue messages from a Redis list."""
        payload = self._serialize(message)
        client = self._get_client()
        return int(client.lrem(queue_name, count, payload))

    def length(self, queue_name: str) -> int:
        """Return the current queue length."""
        client = self._get_client()
        return int(client.llen(queue_name))

    def list_messages(self, queue_name: str) -> list[dict[str, Any]]:
        """Return all messages from a list queue without mutating it."""
        client = self._get_client()
        size = int(client.llen(queue_name))
        messages: list[dict[str, Any]] = []
        for index in range(size):
            payload = client.lindex(queue_name, index)
            if payload is None:
                continue
            messages.append(self._deserialize(payload))
        return messages

    def list_priority_messages(self, queue_name: str) -> list[dict[str, Any]]:
        """Return legacy priority-zset messages for migration/inspection only."""
        client = self._get_client()
        priority_queue_name = self.priority_queue_name(queue_name)
        members: list[Any] = []

        if hasattr(client, "zrange"):
            try:
                members = list(client.zrange(priority_queue_name, 0, -1))
            except TypeError:
                members = list(client.zrange(priority_queue_name, 0, -1))

        if not members:
            sorted_sets = getattr(client, "sorted_sets", {})
            members = list(sorted_sets.get(priority_queue_name, {}).keys())

        messages: list[dict[str, Any]] = []
        for payload in members:
            if payload is None:
                continue
            messages.append(self._deserialize(payload))
        return messages

    def model_queue_names(self) -> list[str]:
        return list(MODEL_QUEUE_NAMES.values())

    def collect_product_llm_task_ids(self, queue_names: list[str] | None = None) -> list[str]:
        """Collect de-duplicated ProductLlmTaskId values from queue payloads."""
        seen: set[str] = set()
        ordered: list[str] = []
        for queue_name in queue_names or self.model_queue_names():
            messages = self._canonical_queue_messages(queue_name)
            for message in messages:
                task_id = str(message.get("product_llm_task_id") or "").strip()
                if not task_id or task_id in seen:
                    continue
                seen.add(task_id)
                ordered.append(task_id)
        return ordered

    def highest_priority_score(self, queue_name: str) -> int | None:
        """Return the current max priority score for one queue, if any."""
        messages = self._canonical_queue_messages(queue_name)
        if not messages:
            return None
        return max(self._coerce_priority(message.get("priority")) for message in messages)

    def global_priority_state(self, queue_names: list[str] | None = None) -> dict[str, Any]:
        """Return the global highest-priority queues and score across model queues."""
        highest_score: int | None = None
        highest_queues: list[str] = []
        for queue_name in queue_names or self.model_queue_names():
            score = self.highest_priority_score(queue_name)
            if score is None:
                continue
            if highest_score is None or score > highest_score:
                highest_score = score
                highest_queues = [queue_name]
            elif score == highest_score:
                highest_queues.append(queue_name)
        return {
            "highest_score": highest_score,
            "queue_names": highest_queues,
        }

    def queue_priority_state(self, queue_name: str, *, default_priority: int = 50) -> dict[str, Any]:
        """Return per-queue priority distribution state."""
        messages = self._canonical_queue_messages(queue_name)
        if not messages:
            return {
                "has_messages": False,
                "all_same": True,
                "highest_priority": None,
                "has_elevated_priority": False,
            }

        priorities = [self._coerce_priority(message.get("priority")) for message in messages]
        unique_priorities = set(priorities)
        highest_priority = max(priorities)
        return {
            "has_messages": True,
            "all_same": len(unique_priorities) == 1,
            "highest_priority": highest_priority,
            "has_elevated_priority": highest_priority > int(default_priority),
        }

    def update_product_task_priorities(
        self,
        product_llm_task_ids: list[str],
        *,
        delta: int | None = None,
        priority: int | None = None,
        min_priority_queue_score: int = 51,
    ) -> dict[str, Any]:
        """Update priorities for queued messages across all model queues."""
        if delta is None and priority is None:
            return {"updated_messages": 0, "updated_task_ids": [], "queues": []}

        target_ids = {str(item).strip() for item in product_llm_task_ids if str(item).strip()}
        if not target_ids:
            return {"updated_messages": 0, "updated_task_ids": [], "queues": []}

        client = self._get_client()
        touched_queues: list[str] = []
        updated_messages = 0
        updated_task_ids: set[str] = set()

        for queue_name in self.model_queue_names():
            messages = self._canonical_queue_messages(queue_name)
            changed = False
            for message in messages:
                task_id = str(message.get("product_llm_task_id") or "").strip()
                if task_id not in target_ids:
                    continue
                old_priority = self._coerce_priority(message.get("priority"))
                new_priority = max(0, min(100, priority if priority is not None else old_priority + int(delta or 0)))
                if new_priority == old_priority:
                    continue
                message["priority"] = new_priority
                updated_messages += 1
                updated_task_ids.add(task_id)
                changed = True

            if not changed:
                continue

            touched_queues.append(queue_name)
            self._rewrite_queue(queue_name, messages, min_priority_queue_score=min_priority_queue_score)

        return {
            "updated_messages": updated_messages,
            "updated_task_ids": sorted(updated_task_ids),
            "queues": touched_queues,
        }

    def normalize_model_queues(self, *, min_priority_queue_score: int = 51) -> list[str]:
        """Normalize all model queues so list is canonical and legacy zsets are removed."""
        normalized: list[str] = []
        for queue_name in self.model_queue_names():
            messages = self._canonical_queue_messages(queue_name)
            self._rewrite_queue(queue_name, messages, min_priority_queue_score=min_priority_queue_score)
            normalized.append(queue_name)
        return normalized

    def rebuild_priority_index(self, queue_name: str, *, min_priority_queue_score: int = 51) -> None:
        """Remove any legacy priority zset for one queue."""
        client = self._get_client()
        if hasattr(client, "delete"):
            client.delete(self.priority_queue_name(queue_name))

    def count_priority_messages(self, queue_name: str, *, min_priority_queue_score: int = 51) -> int:
        """Count elevated-priority tasks inside the canonical list queue."""
        return sum(
            1
            for message in self._canonical_queue_messages(queue_name)
            if self._coerce_priority(message.get("priority")) >= min_priority_queue_score
        )

    def pop_highest_priority(self, queue_name: str, *, min_priority_queue_score: int = 51) -> dict[str, Any] | None:
        """Pop the highest-priority message from the canonical list queue."""
        messages = self.list_messages(queue_name)
        if not messages:
            return None

        best_index = -1
        best_priority = min_priority_queue_score - 1
        for index, message in enumerate(messages):
            score = self._coerce_priority(message.get("priority"))
            if score > best_priority:
                best_priority = score
                best_index = index

        if best_index < 0 or best_priority < min_priority_queue_score:
            return None

        selected = messages[best_index]
        self.remove_message(queue_name, selected, count=1)
        return selected

    def ping(self) -> bool:
        """Validate Redis connectivity."""
        client = self._get_client()
        return bool(client.ping())

    @staticmethod
    def priority_queue_name(queue_name: str) -> str:
        return f"{queue_name}:priority"

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

    @staticmethod
    def _coerce_priority(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 50

    @staticmethod
    def _message_identity(message: dict[str, Any]) -> tuple[str, str, int, int | None]:
        return (
            str(message.get("product_llm_task_id") or ""),
            str(message.get("question_id") or ""),
            int(message.get("round_num") or 0),
            RedisQueueStore._safe_int(message.get("task_id")),
        )

    def _canonical_queue_messages(self, queue_name: str) -> list[dict[str, Any]]:
        merged: dict[tuple[str, str, int, int | None], dict[str, Any]] = {}
        order: list[tuple[str, str, int, int | None]] = []

        for source in (self.list_messages(queue_name), self.list_priority_messages(queue_name)):
            for message in source:
                identity = self._message_identity(message)
                existing = merged.get(identity)
                if existing is None:
                    merged[identity] = dict(message)
                    order.append(identity)
                    continue

                existing_priority = self._coerce_priority(existing.get("priority"))
                incoming_priority = self._coerce_priority(message.get("priority"))
                if incoming_priority >= existing_priority:
                    merged[identity] = {**existing, **message}

        return [merged[identity] for identity in order]

    def _rewrite_queue(self, queue_name: str, messages: list[dict[str, Any]], *, min_priority_queue_score: int = 51) -> None:
        client = self._get_client()
        if hasattr(client, "delete"):
            client.delete(queue_name)
            client.delete(self.priority_queue_name(queue_name))
        for message in messages:
            self.push(queue_name, message)

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

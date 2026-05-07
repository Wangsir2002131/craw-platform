"""Runtime scheduling strategy storage and helpers."""

from __future__ import annotations

from typing import Any

from platform.queue.redis_store import RedisQueueStore

DEFAULT_SCHEDULING_STRATEGY = "fifo"
VALID_SCHEDULING_STRATEGIES = ("fifo", "priority")
SCHEDULING_STRATEGY_KEY = "scheduler:active_strategy"


class QueueStrategyStore:
    """Persist the active queue scheduling strategy in Redis."""

    def __init__(
        self,
        *,
        queue_store: RedisQueueStore | None = None,
        redis_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.queue_store = queue_store or RedisQueueStore(redis_url=redis_url, client=client)

    def get_strategy(self) -> str:
        client = self.queue_store._get_client()
        raw = client.get(SCHEDULING_STRATEGY_KEY)
        if raw is None:
            return DEFAULT_SCHEDULING_STRATEGY
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return self.normalize_strategy(str(raw))

    def set_strategy(self, strategy: str) -> str:
        normalized = self.normalize_strategy(strategy)
        client = self.queue_store._get_client()
        client.set(SCHEDULING_STRATEGY_KEY, normalized)
        return normalized

    @staticmethod
    def normalize_strategy(strategy: str | None) -> str:
        normalized = str(strategy or "").strip().lower()
        legacy_aliases = {
            "lifo": DEFAULT_SCHEDULING_STRATEGY,
            "priority_fifo": "priority",
            "priority_lifo": "priority",
        }
        normalized = legacy_aliases.get(normalized, normalized)
        if normalized not in VALID_SCHEDULING_STRATEGIES:
            return DEFAULT_SCHEDULING_STRATEGY
        return normalized

    @staticmethod
    def is_priority_strategy(strategy: str | None) -> bool:
        return QueueStrategyStore.normalize_strategy(strategy) == "priority"

    @staticmethod
    def uses_lifo(strategy: str | None) -> bool:
        return False

    @staticmethod
    def priority_queue_name(queue_name: str) -> str:
        return f"{queue_name}:priority"

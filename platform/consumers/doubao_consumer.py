"""DouBao queue consumer for Phase B."""

from __future__ import annotations

from typing import Any

from platform.consumers.base import BaseQueueConsumer
from platform.dispatcher.time_window import TimeWindowController
from platform.queue.protocol import get_queue_name
from platform.queue.strategy_store import QueueStrategyStore


class DoubaoConsumer(BaseQueueConsumer):
    """Consume DouBao tasks from Redis and push execution results back."""

    def __init__(
        self,
        db_config: dict[str, Any] | None = None,
        *,
        queue_store: RedisQueueStore | None = None,
        db_store: TaskMasterStatusStore | None = None,
        crawler_module: str = "doubao.doubao",
        redis_url: str | None = None,
        time_window: TimeWindowController | None = None,
        strategy_store: QueueStrategyStore | None = None,
    ) -> None:
        super().__init__(
            db_config,
            queue_store=queue_store,
            db_store=db_store,
            crawler_module=crawler_module,
            queue_name=get_queue_name("doubao"),
            redis_url=redis_url,
            time_window=time_window,
            strategy_store=strategy_store,
        )

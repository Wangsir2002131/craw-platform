"""DeepSeek queue consumer for Phase B."""

from __future__ import annotations

from typing import Any

from platform.consumers.base import BaseQueueConsumer
from platform.queue.protocol import get_queue_name


class DeepseekConsumer(BaseQueueConsumer):
    """Consume DeepSeek tasks from Redis and push execution results back."""

    def __init__(
        self,
        db_config: dict[str, Any] | None = None,
        *,
        queue_store: RedisQueueStore | None = None,
        db_store: TaskMasterStatusStore | None = None,
        crawler_module: str = "deepseek.deepseek",
        redis_url: str | None = None,
    ) -> None:
        super().__init__(
            db_config,
            queue_store=queue_store,
            db_store=db_store,
            crawler_module=crawler_module,
            queue_name=get_queue_name("deepseek"),
            redis_url=redis_url,
        )

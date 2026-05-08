"""向4个模型队列插入模拟数据。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from platform.queue.redis_store import RedisQueueStore
from platform.queue.protocol import QUEUE_NAMES


def build_mock_message(
    product_llm_task_id: str,
    question_id: str,
    queue_name: str,
    round_num: int = 1,
    priority: int = 50,
    task_id: int | None = None,
) -> dict[str, str | int]:
    """构造一条模拟的任务消息。"""
    message: dict[str, str | int] = {
        "message_type": "task",
        "product_llm_task_id": product_llm_task_id,
        "question_id": question_id,
        "question_name": f"模拟问题-{question_id}",
        "queue_name": queue_name,
        "round_num": round_num,
        "priority": priority,
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
        "retry_count": 0,
    }
    if task_id is not None:
        message["task_id"] = task_id
    return message


def insert_mock_data(
    counts: dict[str, int] | None = None,
    redis_url: str | None = None,
) -> dict[str, int]:
    """向各个队列插入模拟数据。

    参数:
        counts: 每个队列插入的条数，如 {"queue:afu": 5, ...}。
        redis_url: Redis 连接地址，默认从配置读取。

    返回:
        实际每个队列插入的条数字典。
    """
    store = RedisQueueStore(redis_url=redis_url)
    counts = counts or {
        "queue:afu": 600,
        "queue:deepseek": 600,
        "queue:doubao": 600,
        "queue:yuanbao": 600,
    }

    result: dict[str, int] = {}
    for queue_name, count in counts.items():
        inserted = 0
        for i in range(count):
            message = build_mock_message(
                product_llm_task_id=f"mock-task-{queue_name.replace(':', '-')}-{i+1}",
                question_id=f"mock-q-{i+1}",
                queue_name=queue_name,
                round_num=(i % 3) + 1,
                priority=50 + (i % 3) * 10,
                task_id=10000 + i,
            )
            store.push(queue_name, message)
            inserted += 1
        result[queue_name] = inserted
        print(f"{queue_name}: 已插入 {inserted} 条模拟数据")

    return result


def main() -> int:
    """命令行入口。"""
    print("开始向4个队列插入模拟数据...")
    result = insert_mock_data()
    print("\n插入完成:")
    total = 0
    for queue_name, count in result.items():
        print(f"  {queue_name}: {count} 条")
        total += count
    print(f"总计: {total} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""Load pending product LLM tasks from MySQL and enqueue them into Redis."""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from platform.config import DB_CONFIG, REDIS_URL  # noqa: E402
from platform.queue.protocol import MESSAGE_TYPE_TASK, get_queue_name  # noqa: E402
from platform.queue.redis_store import RedisQueueStore  # noqa: E402


PENDING_TASK_SQL = """
SELECT llm_task.ProductLlmTaskId,
       llm_task.ProductTaskId,
       llm_task.ProductId,
       llm_task.LlmKey,
       llm_task.MaxRounds,
       llm_task.CreatedTime,
       question.QuestionId,
       question.QuestionName
FROM ent_data_product_llm_task AS llm_task
LEFT JOIN ent_data_product_question AS prod_question
    ON llm_task.ProductId = prod_question.ProductId
    AND prod_question.Deleted = b'0'
    AND prod_question.Disabled = b'0'
LEFT JOIN ent_data_question AS question
    ON prod_question.QuestionId = question.QuestionId
    AND question.Deleted = b'0'
    AND question.Disabled = b'0'
WHERE llm_task.Deleted = b'0'
  AND llm_task.Disabled = b'0'
  AND llm_task.Status = '未开始'
  AND question.QuestionId IS NOT NULL
  AND question.QuestionName IS NOT NULL
ORDER BY llm_task.CreatedTime ASC, prod_question.CreatedTime ASC
"""


MODEL_KEY_ALIASES = {
    "afu": "afu",
    "afU".lower(): "afu",
    "doubao": "doubao",
    "deepseek": "deepseek",
    "yuanbao": "yuanbao",
}


def main() -> int:
    args = build_parser().parse_args()
    rows = fetch_pending_rows(limit=args.limit)
    messages = build_messages(rows, only_model=args.model)

    if args.dry_run:
        print(f"dry-run: fetched_rows={len(rows)} generated_messages={len(messages)}")
        for queue_name, message in messages[: args.preview]:
            print(queue_name, message)
        return 0

    queue_store = RedisQueueStore(redis_url=args.redis_url)
    pushed_by_queue: dict[str, int] = defaultdict(int)
    for queue_name, message in messages:
        queue_store.push(queue_name, message)
        pushed_by_queue[queue_name] += 1

    print(f"fetched_rows={len(rows)} generated_messages={len(messages)}")
    for queue_name in sorted(pushed_by_queue):
        print(f"{queue_name}: pushed={pushed_by_queue[queue_name]}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enqueue pending LLM task-question rows into Redis model queues.")
    parser.add_argument("--redis-url", default=REDIS_URL, help="Redis connection URL.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum SQL rows to read. 0 means no limit.")
    parser.add_argument("--model", default="", help="Only enqueue one model: afu, doubao, deepseek, yuanbao.")
    parser.add_argument("--dry-run", action="store_true", help="Print generated messages without writing Redis.")
    parser.add_argument("--preview", type=int, default=10, help="How many generated messages to print in dry-run mode.")
    return parser


def fetch_pending_rows(*, limit: int = 0) -> list[dict[str, Any]]:
    try:
        import pymysql
    except ImportError as exc:
        raise RuntimeError("pymysql is required. Install pymysql before running this script.") from exc

    config = dict(DB_CONFIG)
    if "database" in config and "db" not in config:
        config["db"] = config.pop("database")
    config.setdefault("charset", "utf8mb4")
    config.setdefault("cursorclass", pymysql.cursors.DictCursor)

    sql = PENDING_TASK_SQL
    params: tuple[Any, ...] = ()
    if limit > 0:
        sql = f"{PENDING_TASK_SQL}\nLIMIT %s"
        params = (limit,)

    connection = pymysql.connect(**config)
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return list(cursor.fetchall() or [])
    finally:
        connection.close()


def build_messages(
    rows: list[dict[str, Any]],
    *,
    only_model: str = "",
) -> list[tuple[str, dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    model_filter = normalize_model_key(only_model) if only_model else ""

    for row in rows:
        model_key = normalize_model_key(row.get("LlmKey"))
        if model_filter and model_key != model_filter:
            continue
        product_llm_task_id = str(row.get("ProductLlmTaskId") or "").strip()
        if not model_key or not product_llm_task_id:
            continue
        grouped[(model_key, product_llm_task_id)].append(row)

    messages: list[tuple[str, dict[str, Any]]] = []
    for (model_key, _product_llm_task_id), task_rows in sorted(
        grouped.items(),
        key=lambda item: (
            normalize_datetime(item[1][0].get("CreatedTime")),
            item[0][0],
            item[0][1],
        ),
    ):
        queue_name = get_queue_name(model_key)
        max_rounds = max(1, int(task_rows[0].get("MaxRounds") or 1))
        ordered_questions = sorted(
            task_rows,
            key=lambda row: (
                normalize_datetime(row.get("CreatedTime")),
                str(row.get("QuestionId") or ""),
            ),
        )

        for round_num in range(1, max_rounds + 1):
            for row in ordered_questions:
                messages.append((queue_name, build_message(row, queue_name=queue_name, round_num=round_num)))

    return messages


def build_message(row: dict[str, Any], *, queue_name: str, round_num: int) -> dict[str, Any]:
    """Build one Redis payload.

    PascalCase keys are kept because the source SQL returns these names.
    snake_case aliases are included because existing crawlers read that shape.
    """
    product_llm_task_id = str(row["ProductLlmTaskId"])
    product_task_id = str(row["ProductTaskId"])
    product_id = str(row["ProductId"])
    question_id = str(row["QuestionId"])
    question_name = str(row["QuestionName"])
    max_rounds = int(row.get("MaxRounds") or 1)

    return {
        "message_type": MESSAGE_TYPE_TASK,
        "queue_name": queue_name,
        "round_num": round_num,
        "priority": 50,
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
        "ProductLlmTaskId": product_llm_task_id,
        "ProductTaskId": product_task_id,
        "ProductId": product_id,
        "MaxRounds": max_rounds,
        "QuestionId": question_id,
        "QuestionName": question_name,
        "product_llm_task_id": product_llm_task_id,
        "product_task_id": product_task_id,
        "product_id": product_id,
        "max_rounds": max_rounds,
        "question_id": question_id,
        "question_name": question_name,
    }


def normalize_model_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    return MODEL_KEY_ALIASES.get(text, text)


def normalize_datetime(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())

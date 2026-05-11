"""Queue naming and payload protocol definitions for Phase B."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Final, Literal, TypedDict, cast


QUEUE_PREFIX: Final[str] = "queue"
RESULT_QUEUE_NAME: Final[str] = "queue:results"
DEAD_LETTER_QUEUE_NAME: Final[str] = "queue:dead-letter"

QUEUE_NAMES: Final[dict[str, str]] = {
    "afu": "queue:afu",
    "doubao": "queue:doubao",
    "deepseek": "queue:deepseek",
    "yuanbao": "queue:yuanbao",
    "results": RESULT_QUEUE_NAME,
    "dead_letter": DEAD_LETTER_QUEUE_NAME,
}

MODEL_QUEUE_NAMES: Final[dict[str, str]] = {
    key: value
    for key, value in QUEUE_NAMES.items()
    if key not in {"results", "dead_letter"}
}

MODEL_KEYS: Final[tuple[str, ...]] = tuple(MODEL_QUEUE_NAMES)

MESSAGE_TYPE_TASK: Final[str] = "task"
MESSAGE_TYPE_RESULT: Final[str] = "result"
MESSAGE_TYPE_CONTROL: Final[str] = "control"

MESSAGE_TYPES: Final[tuple[str, ...]] = (
    MESSAGE_TYPE_TASK,
    MESSAGE_TYPE_RESULT,
    MESSAGE_TYPE_CONTROL,
)

MessageType = Literal["task", "result", "control"]


class QueueTaskMessage(TypedDict, total=False):
    """Execution unit pushed from dispatcher to a model consumer."""

    message_type: Literal["task"]
    product_llm_task_id: str
    task_id: int
    question_id: str
    question_name: str
    queue_name: str
    round_num: int
    priority: int
    enqueued_at: str
    retry_count: int
    last_error: str


class QueueResultMessage(TypedDict, total=False):
    """Execution result pushed from a consumer back to the result queue."""

    message_type: Literal["result"]
    task_id: int
    queue_name: str
    status: str
    result: dict[str, Any]
    error: str


class QueueControlMessage(TypedDict, total=False):
    """Control messages reserved for lifecycle management commands."""

    message_type: Literal["control"]
    command: str
    queue_name: str
    payload: dict[str, Any]


QueueMessage = QueueTaskMessage | QueueResultMessage | QueueControlMessage


def get_queue_name(model_key: str) -> str:
    """Return a queue name for a known model key."""
    normalized_key = model_key.strip().lower()
    if normalized_key not in MODEL_QUEUE_NAMES:
        raise KeyError(f"unknown model queue: {model_key}")
    return MODEL_QUEUE_NAMES[normalized_key]


def is_valid_message_type(message_type: str) -> bool:
    """Check whether the given message type is supported."""
    return message_type in MESSAGE_TYPES


def build_task_message(task_unit: dict[str, Any], *, task_id: int | None = None) -> QueueTaskMessage:
    """Normalize an expanded task unit into the queue task message shape."""
    message: QueueTaskMessage = {
        "message_type": MESSAGE_TYPE_TASK,
        "product_llm_task_id": str(task_unit["product_llm_task_id"]),
        "question_id": str(task_unit["question_id"]),
        "question_name": str(task_unit["question_name"]),
        "queue_name": str(task_unit["queue_name"]),
        "round_num": int(task_unit["round_num"]),
        "priority": int(task_unit["priority"]),
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
        "retry_count": int(task_unit.get("retry_count", 0) or 0),
    }
    last_error = str(task_unit.get("last_error") or "").strip()
    if last_error:
        message["last_error"] = last_error
    if task_id is not None:
        message["task_id"] = int(task_id)
    return message


def parse_message_type(message: dict[str, Any]) -> MessageType:
    """Extract and validate the message type from a queue payload."""
    raw_type = str(message.get("message_type", "")).strip().lower()
    if not is_valid_message_type(raw_type):
        raise ValueError(f"unsupported message_type: {raw_type or '<missing>'}")
    return cast(MessageType, raw_type)

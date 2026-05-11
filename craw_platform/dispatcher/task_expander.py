"""Expand product LLM tasks into question execution units."""

from __future__ import annotations

from typing import Any, Iterable


LLM_KEY_TO_QUEUE = {
    "afu": "queue:afu",
    "deepseek": "queue:deepseek",
    "doubao": "queue:doubao",
    "yuanbao": "queue:yuanbao",
}

LLM_KEY_ALIASES = {
    "a fu": "afu",
    "ai fu": "afu",
    "af u": "afu",
    "阿福": "afu",
    "deep seek": "deepseek",
    "deep-seek": "deepseek",
    "deep_seek": "deepseek",
    "豆包": "doubao",
    "dou bao": "doubao",
    "dou-bao": "doubao",
    "dou_bao": "doubao",
    "元宝": "yuanbao",
    "腾讯元宝": "yuanbao",
    "yuan bao": "yuanbao",
    "yuan-bao": "yuanbao",
    "yuan_bao": "yuanbao",
}


class TaskExpander:
    """Build scheduler-ready execution units from one product LLM task row."""

    DEFAULT_PRIORITY = 50
    DEFAULT_MAX_ROUNDS = 1

    def __init__(self, llm_key_to_queue: dict[str, str] | None = None) -> None:
        self.llm_key_to_queue = dict(LLM_KEY_TO_QUEUE)
        if llm_key_to_queue:
            self.llm_key_to_queue.update(llm_key_to_queue)

    def expand_task(self, product_llm_task: dict[str, Any]) -> list[dict[str, Any]]:
        """Expand a task record into question x round execution units.

        The current database query can provide either one task-question row or a
        task row with a nested questions list. Both shapes are accepted so later
        dispatcher code can reuse the same expander.
        """
        if not isinstance(product_llm_task, dict):
            raise TypeError("product_llm_task must be a dict")

        product_llm_task_id = self._require_value(
            product_llm_task,
            "ProductLlmTaskId",
            "product_llm_task_id",
            "productLlmTaskId",
        )
        queue_name = self.resolve_queue_name(
            self._require_value(product_llm_task, "LlmKey", "llm_key", "llmKey")
        )
        max_rounds = self._positive_int(
            self._get_value(product_llm_task, "MaxRounds", "max_rounds", "maxRounds"),
            self.DEFAULT_MAX_ROUNDS,
        )
        priority = self._int_or_default(
            self._get_value(
                product_llm_task,
                "PriorityScore",
                "Priority",
                "priority_score",
                "priority",
            ),
            self.DEFAULT_PRIORITY,
        )

        units: list[dict[str, Any]] = []
        seen: set[tuple[Any, str]] = set()

        for question in self._iter_questions(product_llm_task):
            question_id = self._require_value(question, "QuestionId", "question_id", "questionId")
            question_name = self._require_value(
                question,
                "QuestionName",
                "question_name",
                "questionName",
            )
            dedupe_key = (question_id, str(question_name))
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            for round_num in range(1, max_rounds + 1):
                units.append(
                    {
                        "product_llm_task_id": product_llm_task_id,
                        "question_id": question_id,
                        "question_name": question_name,
                        "round_num": round_num,
                        "queue_name": queue_name,
                        "priority": priority,
                    }
                )

        return units

    def resolve_queue_name(self, llm_key: Any) -> str:
        normalized_key = self._normalize_llm_key(llm_key)
        if not normalized_key:
            raise ValueError("LlmKey is required")

        queue_key = LLM_KEY_ALIASES.get(normalized_key, normalized_key)
        if queue_key not in self.llm_key_to_queue:
            for known_key, queue_name in self.llm_key_to_queue.items():
                if known_key in queue_key:
                    return queue_name
            return f"queue:{queue_key}"

        return self.llm_key_to_queue[queue_key]

    def _iter_questions(self, product_llm_task: dict[str, Any]) -> Iterable[dict[str, Any]]:
        questions = self._get_value(product_llm_task, "Questions", "questions", "QuestionList")
        if questions is None:
            yield product_llm_task
            return

        if not isinstance(questions, list):
            raise TypeError("questions must be a list when provided")

        for question in questions:
            if not isinstance(question, dict):
                raise TypeError("each question must be a dict")
            yield question

    @staticmethod
    def _normalize_llm_key(llm_key: Any) -> str:
        return str(llm_key).strip().lower() if llm_key is not None else ""

    @classmethod
    def _get_value(cls, data: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in data:
                return data[key]
        return None

    @classmethod
    def _require_value(cls, data: dict[str, Any], *keys: str) -> Any:
        value = cls._get_value(data, *keys)
        if value is None or value == "":
            raise ValueError(f"missing required field: {'/'.join(keys)}")
        return value

    @classmethod
    def _positive_int(cls, value: Any, default: int) -> int:
        int_value = cls._int_or_default(value, default)
        return int_value if int_value > 0 else default

    @staticmethod
    def _int_or_default(value: Any, default: int) -> int:
        if value is None or value == "":
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default


if __name__ == "__main__":
    expander = TaskExpander()
    test_task = {
        "ProductLlmTaskId": "090a71b5-e9ea-11f0-a151-1c34da64f880",
        "LlmKey": "afu",
        "MaxRounds": 3,
        "QuestionId": "3f92f9ce-3ebb-11f1-8b90-6018952c5b3e",
        "QuestionName": "test question",
    }
    result = expander.expand_task(test_task)
    assert len(result) == 3
    assert result[0]["product_llm_task_id"] == test_task["ProductLlmTaskId"]
    assert result[0]["question_id"] == test_task["QuestionId"]
    assert result[0]["queue_name"] == "queue:afu"
    assert result[2]["round_num"] == 3
    print(result)

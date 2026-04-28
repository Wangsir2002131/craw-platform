"""Scheduling priority strategy for Phase D."""

from __future__ import annotations

from datetime import datetime
from typing import Any


class ScheduleStrategy:
    """Calculate bounded task priority scores for dispatcher ordering."""

    DEFAULT_CONFIG = {
        "default_priority": 50,
        "min_priority": 0,
        "max_priority": 100,
        "source_priority_weight": 1.0,
        "age_boost_per_hour": 0,
        "round_penalty": 0,
        "model_weights": {},
        "product_weights": {},
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = dict(self.DEFAULT_CONFIG)
        if config:
            self.config.update(config)

    def calculate_priority(self, task: dict[str, Any]) -> int:
        """Return a normalized 0-100 priority for one task or task unit."""
        if not isinstance(task, dict):
            raise TypeError("task must be a dict")

        priority = self._source_priority(task) * self._float_config("source_priority_weight", 1.0)
        priority += self._lookup_weight(self.config.get("model_weights"), self._get_value(task, "LlmKey", "llm_key"))
        priority += self._lookup_weight(
            self.config.get("product_weights"),
            self._get_value(task, "ProductId", "product_id"),
        )
        priority += self._age_boost(task)
        priority -= self._round_penalty(task)

        return self._clamp(round(priority))

    def _source_priority(self, task: dict[str, Any]) -> float:
        value = self._get_value(
            task,
            "PriorityScore",
            "Priority",
            "priority_score",
            "priority",
        )
        return self._float_or_default(value, self._float_config("default_priority", 50))

    def _age_boost(self, task: dict[str, Any]) -> float:
        boost_per_hour = self._float_config("age_boost_per_hour", 0)
        if boost_per_hour <= 0:
            return 0

        created_at = self._get_value(task, "CreatedTime", "created_at", "createdTime")
        if created_at is None:
            return 0
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at)
            except ValueError:
                return 0
        if not isinstance(created_at, datetime):
            return 0

        age_seconds = max(0.0, (datetime.now() - created_at.replace(tzinfo=None)).total_seconds())
        return (age_seconds / 3600) * boost_per_hour

    def _round_penalty(self, task: dict[str, Any]) -> float:
        penalty = self._float_config("round_penalty", 0)
        round_num = self._int_or_default(self._get_value(task, "round_num", "RoundNum"), 1)
        return max(0, round_num - 1) * penalty

    def _clamp(self, value: int) -> int:
        min_priority = self._int_config("min_priority", 0)
        max_priority = self._int_config("max_priority", 100)
        return max(min_priority, min(max_priority, value))

    def _float_config(self, key: str, default: float) -> float:
        return self._float_or_default(self.config.get(key), default)

    def _int_config(self, key: str, default: int) -> int:
        return self._int_or_default(self.config.get(key), default)

    @staticmethod
    def _lookup_weight(weights: Any, key: Any) -> float:
        if not isinstance(weights, dict) or key is None:
            return 0
        return ScheduleStrategy._float_or_default(weights.get(str(key).strip().lower()), 0)

    @staticmethod
    def _get_value(data: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in data:
                return data[key]
        return None

    @staticmethod
    def _float_or_default(value: Any, default: float) -> float:
        if value is None or value == "":
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _int_or_default(value: Any, default: int) -> int:
        if value is None or value == "":
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

"""测试 redis_connection_error_rule / redis_connection_failure_rule 触发逻辑"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import unittest
from io import StringIO
from unittest.mock import MagicMock, patch

from platform.alerts.alert_levels import AlertLevel, AlertCategory
from platform.alerts.alert_manager import AlertManager
from platform.alerts.monitors.queue_monitor import QueueMonitor
from platform.alerts.notifiers.console_notifier import ConsoleNotifier
from platform.alerts.rules.queue_rules import QueueAlertRule


class TestRedisConnectionErrorRule(unittest.TestCase):

    def _make_monitor(self) -> tuple[QueueMonitor, AlertManager]:
        """创建带 ConsoleNotifier 的 AlertManager + QueueMonitor"""
        manager = AlertManager()
        stream = StringIO()
        manager.register_notifier(ConsoleNotifier(stream=stream, use_colour=False))

        monitor = QueueMonitor(alert_manager=manager)
        # Mock 掉 queue_store，避免真实 Redis 连接
        monitor.queue_store = MagicMock()
        return monitor, manager

    # ------------------------------------------------------------------
    # 场景 1：ping() 返回 False（软失败）→ RED 告警
    # ------------------------------------------------------------------
    def test_ping_returns_false_triggers_red_alert(self):
        monitor, manager = self._make_monitor()
        monitor.queue_store.ping.return_value = False   # 模拟 Ping 无响应

        monitor._check_redis_health()

        events = manager.list_events(category=AlertCategory.QUEUE)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].name, "redis_connection_error")
        self.assertEqual(events[0].level, AlertLevel.RED)

    # ------------------------------------------------------------------
    # 场景 2：ping() 抛出异常（硬失败，第 1 次）→ RED 告警
    # ------------------------------------------------------------------
    def test_ping_exception_first_time_triggers_red_alert(self):
        monitor, manager = self._make_monitor()
        monitor.queue_store.ping.side_effect = ConnectionError("无法连接到 Redis")

        monitor._check_redis_health()

        events = manager.list_events()
        self.assertEqual(events[0].name, "redis_connection_error")
        self.assertEqual(events[0].level, AlertLevel.RED)
        self.assertEqual(monitor._redis_failure_count, 1)

    # ------------------------------------------------------------------
    # 场景 3：连续失败 4 次 → 第 4 次升级为 ERROR
    # ------------------------------------------------------------------
    def test_persistent_failure_escalates_to_error(self):
        monitor, manager = self._make_monitor()
        monitor.queue_store.ping.side_effect = ConnectionError("Redis 宕机")

        # 连续触发 4 次，suppress_seconds=0 绕过抑制
        for _ in range(4):
            monitor.queue_store.ping.side_effect = ConnectionError("Redis 宕机")
            # 手动跳过抑制：每次重置 suppress interval
            manager.set_suppress_interval("redis_connection_error", 0)
            manager.set_suppress_interval("redis_connection_failure", 0)
            monitor._check_redis_health()

        events = manager.list_events()
        # 最新一条应为 ERROR 级别
        latest = events[0]
        self.assertEqual(latest.name, "redis_connection_failure")
        self.assertEqual(latest.level, AlertLevel.ERROR)
        self.assertEqual(monitor._redis_failure_count, 4)

    # ------------------------------------------------------------------
    # 场景 4：失败后恢复 → failure_count 重置为 0
    # ------------------------------------------------------------------
    def test_recovery_resets_failure_count(self):
        monitor, manager = self._make_monitor()

        # 先失败一次
        monitor.queue_store.ping.side_effect = ConnectionError("临时故障")
        monitor._check_redis_health()
        self.assertEqual(monitor._redis_failure_count, 1)

        # 再恢复
        monitor.queue_store.ping.side_effect = None
        monitor.queue_store.ping.return_value = True
        monitor._check_redis_health()
        self.assertEqual(monitor._redis_failure_count, 0)

    # ------------------------------------------------------------------
    # 场景 5：验证规则元数据本身
    # ------------------------------------------------------------------
    def test_rule_metadata(self):
        rule = QueueAlertRule.redis_connection_error_rule()
        self.assertEqual(rule.name, "redis_connection_error")
        self.assertEqual(rule.level, AlertLevel.RED)
        self.assertEqual(rule.category, AlertCategory.QUEUE)
        self.assertTrue(rule.enabled)

        rule_dict = rule.to_dict()
        self.assertEqual(rule_dict["level"], "red")
        self.assertEqual(rule_dict["category"], "queue")


if __name__ == "__main__":
    unittest.main()
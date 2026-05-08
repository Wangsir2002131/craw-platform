"""手动验证 redis_connection_error 告警规则 — 直接运行此文件观察输出"""

from unittest.mock import MagicMock

from platform.alerts.alert_manager import AlertManager
from platform.alerts.monitors.queue_monitor import QueueMonitor
from platform.alerts.notifiers.console_notifier import ConsoleNotifier


def _setup():
    manager = AlertManager()
    manager.register_notifier(ConsoleNotifier())
    monitor = QueueMonitor(alert_manager=manager)
    monitor.queue_store = MagicMock()
    return monitor, manager


def verify_ping_false():
    """场景1：ping() 返回 False → 应触发 🔴 RED"""
    print("\n" + "=" * 55)
    print("【场景1】Redis Ping 无响应（ping 返回 False）")
    print("=" * 55)
    monitor, manager = _setup()
    monitor.queue_store.ping.return_value = False
    monitor._check_redis_health()
    _print_result(manager)


def verify_ping_exception():
    """场景2：ping() 抛出异常（硬失败）→ 应触发 🔴 RED"""
    print("\n" + "=" * 55)
    print("【场景2】Redis 连接异常（ping 抛出异常）")
    print("=" * 55)
    monitor, manager = _setup()
    monitor.queue_store.ping.side_effect = ConnectionError("连接超时")
    monitor._check_redis_health()
    _print_result(manager)


def verify_escalation():
    """场景3：连续失败 4 次 → 第4次升级为 ❌ ERROR"""
    print("\n" + "=" * 55)
    print("【场景3】持续失败 4 次，告警升级为 ERROR")
    print("=" * 55)
    monitor, manager = _setup()
    for i in range(4):
        monitor.queue_store.ping.side_effect = ConnectionError("Redis 宕机")
        manager.set_suppress_interval("redis_connection_error", 0)
        manager.set_suppress_interval("redis_connection_failure", 0)
        monitor._check_redis_health()
        print(f"  第 {i + 1} 次检测完成，当前 failure_count = {monitor._redis_failure_count}")
    print()
    _print_result(manager)


def verify_recovery():
    """场景4：故障恢复后 failure_count 归零"""
    print("\n" + "=" * 55)
    print("【场景4】故障后恢复，failure_count 应归零")
    print("=" * 55)
    monitor, manager = _setup()

    monitor.queue_store.ping.side_effect = ConnectionError("临时故障")
    monitor._check_redis_health()
    print(f"  故障后 failure_count = {monitor._redis_failure_count}  （预期: 1）")

    monitor.queue_store.ping.side_effect = None
    monitor.queue_store.ping.return_value = True
    monitor._check_redis_health()
    print(f"  恢复后 failure_count = {monitor._redis_failure_count}  （预期: 0）")


def _print_result(manager):
    events = manager.list_events()
    print(f"\n  触发告警数: {len(events)}")
    for e in events:
        print(f"  ├─ 规则名: {e.name}")
        print(f"  ├─ 级  别: {e.level.value}")
        print(f"  └─ 消  息: {e.message}")


if __name__ == "__main__":
    verify_ping_false()
    verify_ping_exception()
    verify_escalation()
    verify_recovery()
    print("\n\n✅ 全部验证完成")
"""Alert rule definitions for different categories."""

from platform.alerts.rules.task_rules import TaskAlertRule
from platform.alerts.rules.queue_rules import QueueAlertRule
from platform.alerts.rules.account_rules import AccountAlertRule
from platform.alerts.rules.system_rules import SystemAlertRule

__all__ = [
    "TaskAlertRule",
    "QueueAlertRule",
    "AccountAlertRule",
    "SystemAlertRule",
]
"""📋 全部告警规则汇总
🔹 账号规则 AccountAlertRule — 3条
#	规则名	工厂方法	级别	触发条件
1	account_available_low	available_low_rule(threshold=5)	🟡 YELLOW	可用账号 < 5 个
2	account_error_state	error_state_rule()	🔴 RED	账号进入 error / disabled 状态
3	account_error_rate	error_rate_rule(threshold=0.3)	❌ ERROR	账号操作错误率 > 30%
🔹 队列规则 QueueAlertRule — 4条
#	规则名	工厂方法	级别	触发条件
4	queue_length_warning	length_warning_rule(threshold=100)	🟡 YELLOW	队列积压 > 100 条
5	queue_length_critical	length_critical_rule(threshold=500)	🔴 RED	队列积压 > 500 条
6	redis_connection_error	redis_connection_error_rule()	🔴 RED	Redis Ping 无响应
7	redis_connection_failure	redis_connection_failure_rule()	❌ ERROR	Redis 连续失败 > 3 次
🔹 系统规则 SystemAlertRule — 2条
#	规则名	工厂方法	级别	触发条件
8	memory_usage_high	memory_high_rule(threshold=0.8)	🔴 RED	内存使用率 > 80%
9	database_connection_failure	db_connection_failure_rule()	❌ ERROR	数据库 SELECT 1 探测失败
🔹 任务规则 TaskAlertRule — 3条
#	规则名	工厂方法	级别	触发条件
10	task_timeout	timeout_rule(timeout_seconds=300)	🟡 YELLOW	任务执行 > 300 秒未完成
11	task_failure_rate	failure_rate_rule(threshold=0.1)	🔴 RED	任务失败率 > 10%
12	task_error_rate	error_rate_rule(threshold=0.3)	❌ ERROR	任务错误率 > 30%"""
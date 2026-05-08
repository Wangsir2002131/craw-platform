"""
手动测试脚本：覆盖所有告警规则的数据构造与清理

用法：
    python tests/test_alerts_manual.py setup <rule>   # 构造触发数据
    python tests/test_alerts_manual.py cleanup        # 清理所有测试数据
    python tests/test_alerts_manual.py status         # 查看当前测试数据概况

可用 rule 名称：
    all                    - 一次性构造所有告警触发数据
    queue_warning          - 队列积压 > 100（黄色）
    queue_critical         - 队列积压 > 500（红色）
    task_timeout           - 单个任务运行超时 > 300s（黄色）
    task_failure_rate      - 近5分钟失败率 > 10%（红色）
    task_error_rate        - 近5分钟错误率 > 30%（ERROR）
    account_low            - 平台可用账号 < 5（黄色）
    account_error_state    - 账号状态为 error/disabled（红色）
    account_error_rate     - 账号近10分钟错误状态变更 > 30%（ERROR）

测试流程：
    1. python tests/test_alerts_manual.py setup <rule>
    2. 在告警页面点击「🔄 刷新」或调用 POST /alerts/force-check
    3. 验证告警事件出现
    4. python tests/test_alerts_manual.py cleanup
    5. 再次刷新，验证告警消失
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime

import pymysql
import pymysql.cursors
import redis

# ── 连接配置（与 platform/config.py 保持一致）──────────────────────────────
DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "root",
    "password": "123456",
    "database": "test",
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
}
REDIS_URL = "redis://127.0.0.1:6379"

# 测试数据标记（方便 cleanup 精准清理，不误删真实数据）
TEST_MARKER = "TEST_ALERT_MARKER"
TEST_QUEUE = "queue:afu"


# ── 工具函数 ──────────────────────────────────────────────────────────────

def db_conn():
    return pymysql.connect(**DB_CONFIG)


def redis_conn():
    r = redis.from_url(REDIS_URL)
    r.ping()
    return r


def ok(msg: str):
    print(f"  ✅ {msg}")


def info(msg: str):
    print(f"  ℹ️  {msg}")


# ── 构造函数 ──────────────────────────────────────────────────────────────

def setup_queue_warning():
    """队列积压 > 100 → 黄色告警"""
    r = redis_conn()
    r.delete(TEST_QUEUE)
    payload = json.dumps({"_test": TEST_MARKER, "question_id": "q1", "product_llm_task_id": "t1", "round_num": 1})
    for _ in range(150):
        r.lpush(TEST_QUEUE, payload)
    ok(f"{TEST_QUEUE} 当前长度: {r.llen(TEST_QUEUE)}（阈值 warning>100）→ 应触发黄色告警")


def setup_queue_critical():
    """队列积压 > 500 → 红色告警"""
    r = redis_conn()
    r.delete(TEST_QUEUE)
    payload = json.dumps({"_test": TEST_MARKER, "question_id": "q1", "product_llm_task_id": "t1", "round_num": 1})
    for _ in range(600):
        r.lpush(TEST_QUEUE, payload)
    ok(f"{TEST_QUEUE} 当前长度: {r.llen(TEST_QUEUE)}（阈值 critical>500）→ 应触发红色告警")


def setup_task_timeout():
    """单任务 running 超过 300s → 黄色超时告警"""
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            task_id = str(uuid.uuid4())
            question_id = str(uuid.uuid4())
            cur.execute(
                """
                INSERT INTO task_master_status
                    (product_llm_task_id, question_id, round_num, queue_name, execute_status,
                     claimed_at, fail_reason)
                VALUES (%s, %s, 1, 'queue:afu', 'running',
                        DATE_SUB(NOW(), INTERVAL 400 SECOND), %s)
                """,
                (task_id, question_id, TEST_MARKER),
            )
        conn.commit()
        ok(f"插入超时任务 task_id={task_id}（claimed_at = 400s 前，阈值 300s）→ 应触发黄色告警")
    finally:
        conn.close()


def setup_task_failure_rate():
    """近5分钟：90 completed + 10 failed → 失败率 10% > 阈值 10% → 红色告警
    注意：需要 > 10%，所以插 90 completed + 11 failed"""
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            base_task_id = str(uuid.uuid4())
            question_id = str(uuid.uuid4())
            # 90 completed
            for i in range(90):
                cur.execute(
                    """
                    INSERT INTO task_master_status
                        (product_llm_task_id, question_id, round_num, queue_name,
                         execute_status, updated_at, fail_reason)
                    VALUES (%s, %s, %s, 'queue:afu', 'completed', NOW(), %s)
                    """,
                    (f"{base_task_id}-c{i}", question_id, i + 1, TEST_MARKER),
                )
            # 11 failed
            for i in range(11):
                cur.execute(
                    """
                    INSERT INTO task_master_status
                        (product_llm_task_id, question_id, round_num, queue_name,
                         execute_status, updated_at, fail_reason)
                    VALUES (%s, %s, %s, 'queue:afu', 'failed', NOW(), %s)
                    """,
                    (f"{base_task_id}-f{i}", question_id, i + 1, TEST_MARKER),
                )
        conn.commit()
        ok("插入 90 completed + 11 failed（失败率 ≈ 10.9% > 阈值 10%）→ 应触发红色告警")
    finally:
        conn.close()


def setup_task_error_rate():
    """近5分钟：60 completed + 40 error → 错误率 40% > 阈值 30% → ERROR 告警"""
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            base_task_id = str(uuid.uuid4())
            question_id = str(uuid.uuid4())
            # 60 completed
            for i in range(60):
                cur.execute(
                    """
                    INSERT INTO task_master_status
                        (product_llm_task_id, question_id, round_num, queue_name,
                         execute_status, updated_at, fail_reason)
                    VALUES (%s, %s, %s, 'queue:afu', 'completed', NOW(), %s)
                    """,
                    (f"{base_task_id}-c{i}", question_id, i + 1, TEST_MARKER),
                )
            # 40 error
            for i in range(40):
                cur.execute(
                    """
                    INSERT INTO task_master_status
                        (product_llm_task_id, question_id, round_num, queue_name,
                         execute_status, updated_at, fail_reason)
                    VALUES (%s, %s, %s, 'queue:afu', 'error', NOW(), %s)
                    """,
                    (f"{base_task_id}-e{i}", question_id, i + 1, TEST_MARKER),
                )
        conn.commit()
        ok("插入 60 completed + 40 error（错误率 40% > 阈值 30%）→ 应触发 ERROR 告警")
    finally:
        conn.close()


def setup_account_low():
    """平台可用账号 < 5 → 黄色告警
    注意：只插入 3 个 available 账号"""
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            for i in range(3):
                cur.execute(
                    """
                    INSERT IGNORE INTO account_master
                        (platform_name, account_key, account_name, account_status, disabled_reason)
                    VALUES ('test_platform', %s, %s, 'available', %s)
                    """,
                    (f"test_key_{TEST_MARKER}_{i}", f"test_acct_{i}", TEST_MARKER),
                )
        conn.commit()
        ok("插入 3 个 test_platform available 账号（< 阈值 5）→ 应触发黄色告警")
    finally:
        conn.close()


def setup_account_error_state():
    """账号状态为 error → 红色告警"""
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT IGNORE INTO account_master
                    (platform_name, account_key, account_name, account_status,
                     disabled_reason)
                VALUES ('test_platform', %s, 'test_error_acct', 'error', %s)
                """,
                (f"error_key_{TEST_MARKER}", TEST_MARKER),
            )
        conn.commit()
        ok("插入 account_status='error' 账号 → 应触发红色告警")
    finally:
        conn.close()


def setup_account_error_rate():
    """账号 10 分钟内 status_log 里 4/10 次变更为 error → 错误率 40% > 阈值 30% → ERROR"""
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            # 先确保账号存在
            cur.execute(
                """
                INSERT IGNORE INTO account_master
                    (platform_name, account_key, account_name, account_status, disabled_reason)
                VALUES ('test_platform', %s, 'test_rate_acct', 'available', %s)
                """,
                (f"rate_key_{TEST_MARKER}", TEST_MARKER),
            )
            conn.commit()

            cur.execute(
                "SELECT id FROM account_master WHERE account_key = %s",
                (f"rate_key_{TEST_MARKER}",),
            )
            row = cur.fetchone()
            account_id = row["id"] if row else None

            if account_id:
                # 6 次 available（正常）
                for _ in range(6):
                    cur.execute(
                        """
                        INSERT INTO account_status_log
                            (account_id, old_status, new_status, reason, created_at)
                        VALUES (%s, 'available', 'available', %s, NOW())
                        """,
                        (account_id, TEST_MARKER),
                    )
                # 4 次 error（触发高错误率）
                for _ in range(4):
                    cur.execute(
                        """
                        INSERT INTO account_status_log
                            (account_id, old_status, new_status, reason, created_at)
                        VALUES (%s, 'available', 'error', %s, NOW())
                        """,
                        (account_id, TEST_MARKER),
                    )
        conn.commit()
        ok(f"账号 id={account_id}：10 条状态变更，4 条 error（40% > 阈值 30%）→ 应触发 ERROR 告警")
    finally:
        conn.close()


# ── 清理函数 ──────────────────────────────────────────────────────────────

def cleanup():
    """清理所有测试数据"""
    print("\n🧹 清理测试数据...")

    # Redis 队列
    try:
        r = redis_conn()
        r.delete(TEST_QUEUE)
        ok(f"Redis {TEST_QUEUE} 已清空")
    except Exception as e:
        print(f"  ⚠️  Redis 清理失败: {e}")

    # MySQL
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            # task_master_status（通过 fail_reason 标记识别）
            cur.execute(
                "DELETE FROM task_master_status WHERE fail_reason = %s",
                (TEST_MARKER,),
            )
            ok(f"task_master_status 删除 {cur.rowcount} 条测试记录")

            # account_status_log（通过 reason 标记识别）
            cur.execute(
                "DELETE FROM account_status_log WHERE reason = %s",
                (TEST_MARKER,),
            )
            ok(f"account_status_log 删除 {cur.rowcount} 条测试记录")

            # account_master（通过 disabled_reason 标记识别）
            cur.execute(
                "DELETE FROM account_master WHERE disabled_reason = %s",
                (TEST_MARKER,),
            )
            ok(f"account_master 删除 {cur.rowcount} 条测试记录")

        conn.commit()
    except Exception as e:
        print(f"  ⚠️  MySQL 清理失败: {e}")
    finally:
        conn.close()


def status():
    """查看当前测试数据概况"""
    print("\n📊 当前测试数据概况...")

    try:
        r = redis_conn()
        info(f"Redis {TEST_QUEUE} 长度: {r.llen(TEST_QUEUE)}")
    except Exception as e:
        print(f"  ⚠️  Redis 连接失败: {e}")

    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT execute_status, COUNT(*) AS cnt FROM task_master_status WHERE fail_reason=%s GROUP BY execute_status",
                (TEST_MARKER,),
            )
            rows = cur.fetchall()
            for row in rows:
                info(f"task_master_status [{row['execute_status']}]: {row['cnt']} 条")

            cur.execute(
                "SELECT account_status, COUNT(*) AS cnt FROM account_master WHERE disabled_reason=%s GROUP BY account_status",
                (TEST_MARKER,),
            )
            rows = cur.fetchall()
            for row in rows:
                info(f"account_master [{row['account_status']}]: {row['cnt']} 条")

            cur.execute(
                "SELECT new_status, COUNT(*) AS cnt FROM account_status_log WHERE reason=%s GROUP BY new_status",
                (TEST_MARKER,),
            )
            rows = cur.fetchall()
            for row in rows:
                info(f"account_status_log [{row['new_status']}]: {row['cnt']} 条")
    finally:
        conn.close()


# ── 规则分发表 ────────────────────────────────────────────────────────────

RULES: dict[str, tuple[str, callable]] = {
    "queue_warning":       ("队列积压黄色告警 [YELLOW]", setup_queue_warning),
    "queue_critical":      ("队列积压红色告警 [RED]", setup_queue_critical),
    "task_timeout":        ("任务超时告警 [YELLOW]", setup_task_timeout),
    "task_failure_rate":   ("任务失败率告警 [RED]", setup_task_failure_rate),
    "task_error_rate":     ("任务错误率告警 [ERROR]", setup_task_error_rate),
    "account_low":         ("账号不足告警 [YELLOW]", setup_account_low),
    "account_error_state": ("账号异常状态告警 [RED]", setup_account_error_state),
    "account_error_rate":  ("账号错误率告警 [ERROR]", setup_account_error_rate),
}

# ── 入口 ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or args[0] == "help":
        print(__doc__)
        sys.exit(0)

    cmd = args[0]

    if cmd == "cleanup":
        cleanup()
        print("\n✅ 清理完成。刷新告警页面，告警事件应已消失。")

    elif cmd == "status":
        status()

    elif cmd == "setup":
        if len(args) < 2:
            print("❌ 用法: python tests/test_alerts_manual.py setup <rule>")
            print("   可用 rule:", ", ".join(RULES.keys()), ", all")
            sys.exit(1)

        rule = args[1]
        if rule == "all":
            for name, (desc, fn) in RULES.items():
                print(f"\n▶ [{name}] {desc}")
                try:
                    fn()
                except Exception as e:
                    print(f"  ❌ 失败: {e}")
            print("\n✅ 全部测试数据已构造。刷新告警页面查看结果。")
        elif rule in RULES:
            desc, fn = RULES[rule]
            print(f"\n▶ [{rule}] {desc}")
            fn()
            print("\n✅ 数据构造完成。在告警页面点击「🔄 刷新」查看结果。")
        else:
            print(f"❌ 未知规则: {rule}")
            print("   可用 rule:", ", ".join(RULES.keys()), ", all")
            sys.exit(1)

    else:
        print(f"❌ 未知命令: {cmd}，可用: setup / cleanup / status / help")
        sys.exit(1)

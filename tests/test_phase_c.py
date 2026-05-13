"""Phase C integration tests for unified account ownership."""

from __future__ import annotations

import importlib
import unittest

from platform.account.account_allocator import AccountAllocator
from platform.account.account_state_machine import AccountStateMachine
from platform.account.backup_account_handler import BackupAccountHandler
from platform.scripts.register_accounts import AccountRegistrar, normalize_resources


class FakeCursor:
    def __init__(self) -> None:
        self.lastrowid = 1
        self.executed: list[tuple[str, tuple]] = []
        self.account = {
            "id": 1,
            "platform_name": "afu",
            "account_key": "acc-1",
            "account_name": "Account 1",
            "account_status": "available",
            "current_task_count": 0,
            "max_concurrent_tasks": 1,
        }
        self.resources = [
            {
                "resource_type": "cookie",
                "resource_key": "cookie_file",
                "resource_value": "cookies1.json",
                "expire_at": None,
            }
        ]

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        sql = self.executed[-1][0]
        if "FROM account_master" in sql:
            return dict(self.account)
        return {"id": 1}

    def fetchall(self):
        sql = self.executed[-1][0]
        if "FROM account_resource" in sql:
            return list(self.resources)
        return []

    def close(self) -> None:
        return None


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_obj = FakeCursor()
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> FakeCursor:
        return self.cursor_obj

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class FakeAllocator:
    def __init__(self) -> None:
        self.allocated = []
        self.released = []
        self.next_id = 1

    def allocate(self, platform_name: str, task_id=None, exclude_account_ids=None):
        account_id = self.next_id
        self.next_id += 1
        info = {
            "account_master_id": account_id,
            "account_id": f"{platform_name}-acc-{account_id}",
            "account_key": f"{platform_name}-acc-{account_id}",
            "platform_name": platform_name,
            "cookie_file": f"{platform_name}{account_id}.json",
            "resources": {"cookie_file": f"{platform_name}{account_id}.json"},
        }
        self.allocated.append((platform_name, task_id, exclude_account_ids))
        return info

    def release(self, account_info, success=True, task_id=None, reason=None):
        self.released.append((account_info, success, task_id, reason))


class TestPhaseC(unittest.TestCase):
    def test_register_accounts_normalizes_resources_and_inserts(self):
        connection = FakeConnection()
        registrar = AccountRegistrar(connection_factory=lambda: connection)
        count = registrar.register_many(
            [
                {
                    "platform_name": "afu",
                    "account_key": "acc-1",
                    "resources": [
                        {
                            "resource_type": "cookie",
                            "resource_key": "cookie_file",
                            "resource_value": "cookies1.json",
                        }
                    ],
                }
            ]
        )

        self.assertEqual(1, count)
        self.assertEqual(1, connection.commits)
        self.assertTrue(any("INSERT INTO account_master" in sql for sql, _ in connection.cursor_obj.executed))
        self.assertTrue(any("INSERT INTO account_resource" in sql for sql, _ in connection.cursor_obj.executed))

    def test_normalize_resources_accepts_json_string(self):
        resources = normalize_resources(
            '[{"resource_type":"cookie","resource_key":"cookie_file","resource_value":"cookies1.json"}]'
        )

        self.assertEqual("cookie", resources[0]["resource_type"])
        self.assertEqual("cookie_file", resources[0]["resource_key"])

    def test_allocator_allocate_and_release_account(self):
        connection = FakeConnection()
        allocator = AccountAllocator(connection_factory=lambda: connection)

        account_info = allocator.allocate("afu", task_id=9)
        allocator.release(account_info, success=True, task_id=9)

        self.assertEqual("acc-1", account_info["account_id"])
        self.assertEqual("cookies1.json", account_info["cookie_file"])
        self.assertGreaterEqual(connection.commits, 2)
        self.assertTrue(any("last_allocated_at" in sql for sql, _ in connection.cursor_obj.executed))
        self.assertTrue(any("last_released_at" in sql for sql, _ in connection.cursor_obj.executed))

    def test_allocator_prefers_lowest_id_non_disabled_account(self):
        connection = FakeConnection()
        allocator = AccountAllocator(connection_factory=lambda: connection)

        allocator.allocate("afu", task_id=9)

        select_sql = next(sql for sql, _ in connection.cursor_obj.executed if "FOR UPDATE" in sql)
        self.assertIn("account_status IN ('available', 'cooling', 'error')", select_sql)
        self.assertIn("ORDER BY priority DESC, id ASC", select_sql)

    def test_allocator_failure_release_truncates_long_error_reason(self):
        connection = FakeConnection()
        allocator = AccountAllocator(connection_factory=lambda: connection)
        long_reason = "browser closed\n" + ("x" * 400)

        allocator.release({"account_master_id": 1}, success=False, task_id=9, reason=long_reason)

        update_params = next(
            params
            for sql, params in connection.cursor_obj.executed
            if "UPDATE account_master SET current_task_count" in sql
        )
        self.assertEqual("cooling", update_params[1])
        self.assertEqual(1, update_params[2])
        self.assertLessEqual(len(update_params[3]), 255)
        self.assertNotIn("\n", update_params[3])

    def test_state_machine_rejects_invalid_transition(self):
        machine = AccountStateMachine()

        self.assertTrue(machine.can_transition("available", "allocated"))
        self.assertTrue(machine.can_transition("cooling", "allocated"))
        self.assertTrue(machine.can_transition("error", "allocated"))
        self.assertFalse(machine.can_transition("disabled", "allocated"))
        with self.assertRaises(ValueError):
            machine.transition({"id": 1, "account_status": "disabled"}, "allocated")

    def test_backup_account_handler_retries_with_backup_account(self):
        allocator = FakeAllocator()
        handler = BackupAccountHandler(allocator=allocator, max_attempts=2)
        calls = []

        def execute_func(task_info, account_info):
            calls.append(account_info["account_id"])
            return {
                "success": len(calls) == 2,
                "answer": "",
                "error": "" if len(calls) == 2 else "temporary failure",
                "account_id": account_info["account_id"],
            }

        result = handler.execute_with_backup("afu", {"task_id": 9}, execute_func)

        self.assertTrue(result["success"])
        self.assertEqual(2, len(allocator.allocated))
        self.assertEqual(False, allocator.released[0][1])
        self.assertEqual(True, allocator.released[1][1])

    def test_crawler_execute_task_uses_allocated_account_in_dry_run(self):
        specs = [
            ("afu.afu", "afu"),
            ("doubao.doubao", "doubao"),
            ("deepseek.deepseek", "deepseek"),
            ("yuanbao.yuanbao", "yuanbao"),
        ]

        for module_name, platform_name in specs:
            with self.subTest(module_name=module_name):
                module = importlib.import_module(module_name)
                allocator = FakeAllocator()
                result = module.execute_task(
                    {
                        "task_id": 99,
                        "question_id": 1,
                        "question_name": "phase c question",
                        "round_num": 1,
                        "dry_run": True,
                        "account_allocator": allocator,
                    }
                )

                self.assertTrue(result["success"])
                self.assertEqual(f"{platform_name}-acc-1", result["account_id"])
                self.assertEqual(platform_name, allocator.allocated[0][0])
                self.assertEqual(True, allocator.released[0][1])


if __name__ == "__main__":
    unittest.main()

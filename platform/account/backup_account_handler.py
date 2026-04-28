"""Fallback account handling for Phase C."""

from __future__ import annotations

from typing import Any, Callable

from platform.account.account_allocator import AccountAllocator


class BackupAccountHandler:
    """Retry a task with backup accounts when the current account fails."""

    def __init__(
        self,
        allocator: AccountAllocator | None = None,
        max_attempts: int = 2,
    ) -> None:
        self.allocator = allocator or AccountAllocator(operator="backup_account_handler")
        self.max_attempts = max_attempts

    def execute_with_backup(
        self,
        platform_name: str,
        task_info: dict[str, Any],
        execute_func: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        attempted: set[int] = set()
        last_result: dict[str, Any] | None = None
        task_id = task_info.get("task_id")

        for _ in range(self.max_attempts):
            account_info = self.allocator.allocate(
                platform_name,
                task_id=task_id,
                exclude_account_ids=attempted,
            )
            account_master_id = account_info.get("account_master_id")
            if account_master_id is not None:
                attempted.add(int(account_master_id))

            result = execute_func(task_info, account_info)
            success = bool(result.get("success"))
            self.allocator.release(
                account_info,
                success=success,
                task_id=task_id,
                reason=result.get("error"),
            )
            if success:
                return result
            last_result = result

        return last_result or {
            "success": False,
            "answer": "",
            "error": "no backup account available",
            "account_id": "",
        }

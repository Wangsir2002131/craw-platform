"""Unified account allocation for Phase C crawler execution."""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from typing import Any, Iterator

from platform.account.account_state_machine import AccountStateMachine


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
SHARED_DIR = os.path.join(ROOT_DIR, "shared-methods")
if SHARED_DIR not in sys.path:
    sys.path.insert(0, SHARED_DIR)

try:
    from shared_methods import DB_CONFIG
except Exception:
    from platform.config import DB_CONFIG


class AccountAllocator:
    """Allocate and release accounts from account_master."""

    def __init__(
        self,
        db_config: dict[str, Any] | None = None,
        connection_factory: Any | None = None,
        operator: str = "account_allocator",
    ) -> None:
        self.db_config = db_config or DB_CONFIG
        self.connection_factory = connection_factory
        self.operator = operator

    def allocate(
        self,
        platform_name: str,
        task_id: int | None = None,
        exclude_account_ids: set[int] | None = None,
    ) -> dict[str, Any]:
        excludes = exclude_account_ids or set()
        with self.cursor() as cursor:
            account = self._fetch_available_account(cursor, platform_name, excludes)
            if not account:
                raise RuntimeError(f"no available account for platform: {platform_name}")

            current_count = int(account.get("current_task_count") or 0) + 1
            max_count = int(account.get("max_concurrent_tasks") or 1)
            new_status = "allocated" if current_count >= max_count else "available"

            cursor.execute(
                """
                UPDATE account_master
                SET current_task_count = %s,
                    account_status = %s,
                    last_allocated_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (current_count, new_status, account["id"]),
            )
            AccountStateMachine(cursor, self.operator).transition(
                account,
                new_status,
                reason="allocated",
                task_id=task_id,
            )
            resources = self._fetch_resources(cursor, account["id"])
            return self._build_account_info(account, resources, new_status, current_count)

    def release(
        self,
        account_info: dict[str, Any],
        success: bool = True,
        task_id: int | None = None,
        reason: str | None = None,
    ) -> None:
        account_id = account_info.get("account_master_id") or account_info.get("id")
        if not account_id:
            return

        with self.cursor() as cursor:
            self._ensure_fail_count_column(cursor)
            cursor.execute("SELECT * FROM account_master WHERE id = %s FOR UPDATE", (account_id,))
            account = cursor.fetchone()
            if not account:
                return

            current_count = max(int(account.get("current_task_count") or 0) - 1, 0)
            fail_count = 0 if success else int(account.get("fail_count") or 0) + 1
            new_status = self._release_status(success, fail_count)
            safe_reason = self._safe_reason(reason)
            disabled_reason = None if success else safe_reason
            cursor.execute(
                """
                UPDATE account_master
                SET current_task_count = %s,
                    account_status = %s,
                    fail_count = %s,
                    last_released_at = CURRENT_TIMESTAMP,
                    disabled_reason = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (current_count, new_status, fail_count, disabled_reason, account_id),
            )
            AccountStateMachine(cursor, self.operator).transition(
                account,
                new_status,
                reason=safe_reason or ("released" if success else f"execution_failed_{fail_count}"),
                task_id=task_id,
            )

    @staticmethod
    def _safe_reason(reason: str | None) -> str | None:
        if reason is None:
            return None
        return str(reason).replace("\r", " ").replace("\n", " ")[:255]

    def _release_status(self, success: bool, fail_count: int) -> str:
        if success:
            return "available"
        if fail_count == 1:
            return "cooling"
        if fail_count == 2:
            return "error"
        return "disabled"

    def _ensure_fail_count_column(self, cursor: Any) -> None:
        cursor.execute(
            """
            SELECT COUNT(*) AS count
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'account_master'
              AND COLUMN_NAME = 'fail_count'
            """
        )
        row = cursor.fetchone() or {}
        if int(row.get("count") or 0) == 0:
            cursor.execute(
                """
                ALTER TABLE account_master
                ADD COLUMN fail_count INT NOT NULL DEFAULT 0 COMMENT 'Consecutive execution failure count'
                AFTER current_task_count
                """
            )

    @contextmanager
    def cursor(self) -> Iterator[Any]:
        connection = self._connect()
        cursor = connection.cursor()
        try:
            yield cursor
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()

    def _fetch_available_account(
        self,
        cursor: Any,
        platform_name: str,
        exclude_account_ids: set[int],
    ) -> dict[str, Any] | None:
        exclude_sql = ""
        params: list[Any] = [platform_name]
        if exclude_account_ids:
            placeholders = ", ".join(["%s"] * len(exclude_account_ids))
            exclude_sql = f"AND id NOT IN ({placeholders})"
            params.extend(sorted(exclude_account_ids))

        cursor.execute(
            f"""
            SELECT *
            FROM account_master
            WHERE platform_name = %s
                AND account_status IN ('available', 'cooling', 'error')
                AND current_task_count < max_concurrent_tasks
                {exclude_sql}
            ORDER BY priority DESC, id ASC
            LIMIT 1
            FOR UPDATE
            """,
            tuple(params),
        )
        return cursor.fetchone()

    def _fetch_resources(self, cursor: Any, account_id: int) -> dict[str, Any]:
        cursor.execute(
            """
            SELECT resource_type, resource_key, resource_value, expire_at
            FROM account_resource
            WHERE account_id = %s AND resource_status = 'active'
            """,
            (account_id,),
        )
        resources = {}
        for row in cursor.fetchall() or []:
            key = row["resource_key"]
            resources[key] = row.get("resource_value")
            resources[f"{row['resource_type']}_{key}"] = row.get("resource_value")
            if row.get("expire_at") is not None:
                resources[f"{key}_expire_at"] = row["expire_at"]
        return resources

    def _build_account_info(
        self,
        account: dict[str, Any],
        resources: dict[str, Any],
        status: str,
        current_count: int,
    ) -> dict[str, Any]:
        account_key = str(account["account_key"])
        return {
            "account_master_id": account["id"],
            "account_id": account_key,
            "account_key": account_key,
            "account_name": account.get("account_name"),
            "platform_name": account["platform_name"],
            "account_status": status,
            "current_task_count": current_count,
            "resources": resources,
            **resources,
        }

    def _connect(self) -> Any:
        if self.connection_factory is not None:
            return self.connection_factory()

        try:
            import pymysql
        except ImportError as exc:
            raise RuntimeError(
                "pymysql is required for account allocation. Provide a connection_factory for tests."
            ) from exc

        config = dict(self.db_config)
        if "database" in config and "db" not in config:
            config["db"] = config.pop("database")
        config.setdefault("charset", "utf8mb4")
        config.setdefault("cursorclass", pymysql.cursors.DictCursor)
        return pymysql.connect(**config)

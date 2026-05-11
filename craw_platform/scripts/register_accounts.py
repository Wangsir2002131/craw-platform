"""Register crawler accounts into the Phase C account tables."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from contextlib import contextmanager
from typing import Any, Iterable, Iterator


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
SHARED_DIR = os.path.join(ROOT_DIR, "shared-methods")
if SHARED_DIR not in sys.path:
    sys.path.insert(0, SHARED_DIR)

try:
    from shared_methods import DB_CONFIG
except Exception:
    DB_CONFIG = {}


class AccountRegistrar:
    """Bulk import account master rows and their resource records."""

    def __init__(
        self,
        db_config: dict[str, Any] | None = None,
        connection_factory: Any | None = None,
    ) -> None:
        self.db_config = db_config or DB_CONFIG
        self.connection_factory = connection_factory

    def register_many(self, accounts: Iterable[dict[str, Any]]) -> int:
        count = 0
        for account in accounts:
            self.register_one(account)
            count += 1
        return count

    def register_one(self, account: dict[str, Any]) -> int:
        platform_name = require_value(account, "platform_name")
        account_key = require_value(account, "account_key")
        resources = normalize_resources(account.get("resources"))

        sql = """
        INSERT INTO account_master (
            platform_name,
            account_key,
            account_name,
            account_status,
            priority,
            max_concurrent_tasks
        ) VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            account_name = VALUES(account_name),
            account_status = VALUES(account_status),
            priority = VALUES(priority),
            max_concurrent_tasks = VALUES(max_concurrent_tasks),
            updated_at = CURRENT_TIMESTAMP,
            id = LAST_INSERT_ID(id)
        """
        params = (
            platform_name,
            account_key,
            account.get("account_name") or account_key,
            account.get("account_status") or "available",
            int(account.get("priority") or 50),
            int(account.get("max_concurrent_tasks") or 1),
        )

        with self.cursor() as cursor:
            cursor.execute(sql, params)
            account_id = int(getattr(cursor, "lastrowid", 0) or 0)
            if not account_id:
                cursor.execute(
                    """
                    SELECT id
                    FROM account_master
                    WHERE platform_name = %s AND account_key = %s
                    """,
                    (platform_name, account_key),
                )
                row = cursor.fetchone()
                account_id = int(row["id"])

            for resource in resources:
                cursor.execute(
                    """
                    INSERT INTO account_resource (
                        account_id,
                        resource_type,
                        resource_key,
                        resource_value,
                        resource_status,
                        expire_at
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        resource_value = VALUES(resource_value),
                        resource_status = VALUES(resource_status),
                        expire_at = VALUES(expire_at),
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        account_id,
                        resource["resource_type"],
                        resource["resource_key"],
                        resource.get("resource_value"),
                        resource.get("resource_status") or "active",
                        resource.get("expire_at"),
                    ),
                )
            return account_id

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

    def _connect(self) -> Any:
        if self.connection_factory is not None:
            return self.connection_factory()

        try:
            import pymysql
        except ImportError as exc:
            raise RuntimeError("pymysql is required to register accounts") from exc

        config = dict(self.db_config)
        if "database" in config and "db" not in config:
            config["db"] = config.pop("database")
        config.setdefault("charset", "utf8mb4")
        config.setdefault("cursorclass", pymysql.cursors.DictCursor)
        return pymysql.connect(**config)


def load_accounts(path: str) -> list[dict[str, Any]]:
    if path.lower().endswith(".json"):
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            data = data.get("accounts", [])
        if not isinstance(data, list):
            raise ValueError("JSON input must be a list or an object with an accounts list")
        return data

    if path.lower().endswith(".csv"):
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    raise ValueError("input file must be .json or .csv")


def normalize_resources(raw_resources: Any) -> list[dict[str, Any]]:
    if not raw_resources:
        return []
    if isinstance(raw_resources, str):
        raw_resources = json.loads(raw_resources)
    if isinstance(raw_resources, dict):
        raw_resources = [raw_resources]
    if not isinstance(raw_resources, list):
        raise ValueError("resources must be a dict, list, or JSON string")

    resources = []
    for resource in raw_resources:
        resource_type = require_value(resource, "resource_type")
        resource_key = require_value(resource, "resource_key")
        resources.append(
            {
                **resource,
                "resource_type": resource_type,
                "resource_key": resource_key,
            }
        )
    return resources


def require_value(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if value is None or str(value).strip() == "":
        raise ValueError(f"missing required field: {key}")
    return str(value).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Register crawler accounts")
    parser.add_argument("input", help="JSON or CSV account file")
    args = parser.parse_args(argv)

    accounts = load_accounts(args.input)
    count = AccountRegistrar().register_many(accounts)
    print(f"registered {count} accounts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

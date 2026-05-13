"""Synchronize account tables from real local cookie/profile sources."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from platform.config import DB_CONFIG

ACCOUNT_SOURCES = {
    "afu": {"type": "profile", "path": Path("D:/afu_real_profiles")},
    "doubao": {"type": "profile", "path": Path("D:/doubao_real_profiles")},
    "deepseek": {"type": "cookie", "path": Path("D:/deepseek_cookie_file")},
    "yuanbao": {"type": "cookie", "path": Path("D:/yuanbao_cookie_file")},
}


def scan_real_accounts() -> list[dict[str, Any]]:
    accounts: list[dict[str, Any]] = []
    for platform_name, source in ACCOUNT_SOURCES.items():
        source_type = str(source["type"])
        source_path = Path(source["path"])
        if source_type == "profile":
            accounts.extend(_scan_profile_accounts(platform_name, source_path))
        elif source_type == "cookie":
            accounts.extend(_scan_cookie_accounts(platform_name, source_path))
    return sorted(accounts, key=lambda item: (item["platform_name"], _natural_key(item["account_key"])))


def sync_real_accounts(db_config: dict[str, Any] | None = None) -> int:
    accounts = scan_real_accounts()
    config = _db_config(db_config or DB_CONFIG)

    import pymysql

    connection = pymysql.connect(**config)
    try:
        with connection.cursor() as cursor:
            _ensure_fail_count_column(cursor)
            existing_states = _load_existing_account_states(cursor)

            cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
            cursor.execute("TRUNCATE TABLE tep_data_accounts")
            cursor.execute("TRUNCATE TABLE account_status_log")
            cursor.execute("TRUNCATE TABLE account_resource")
            cursor.execute("TRUNCATE TABLE account_master")
            cursor.execute("SET FOREIGN_KEY_CHECKS = 1")

            for account in accounts:
                cursor.execute(
                    """
                    INSERT INTO account_master (
                        platform_name,
                        account_key,
                        account_name,
                        account_status,
                        priority,
                        max_concurrent_tasks,
                        current_task_count,
                        fail_count,
                        disabled_reason,
                        created_at,
                        last_allocated_at,
                        last_released_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        account["platform_name"],
                        account["account_key"],
                        account["account_name"],
                        existing_states.get((account["platform_name"], account["account_key"]), {}).get("account_status", "available"),
                        50,
                        1,
                        0,
                        int(existing_states.get((account["platform_name"], account["account_key"]), {}).get("fail_count") or 0),
                        existing_states.get((account["platform_name"], account["account_key"]), {}).get("disabled_reason"),
                        account["created_at"],
                        existing_states.get((account["platform_name"], account["account_key"]), {}).get("last_allocated_at"),
                        existing_states.get((account["platform_name"], account["account_key"]), {}).get("last_released_at"),
                    ),
                )
                account_id = int(cursor.lastrowid)
                for resource in account["resources"]:
                    cursor.execute(
                        """
                        INSERT INTO account_resource (
                            account_id,
                            resource_type,
                            resource_key,
                            resource_value,
                            resource_status
                        ) VALUES (%s, %s, %s, %s, %s)
                        """,
                        (
                            account_id,
                            resource["resource_type"],
                            resource["resource_key"],
                            resource["resource_value"],
                            "active",
                        ),
                    )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return len(accounts)


def _load_existing_account_states(cursor: Any) -> dict[tuple[str, str], dict[str, Any]]:
    cursor.execute(
        """
        SELECT platform_name, account_key, account_status, fail_count, disabled_reason, last_allocated_at, last_released_at
        FROM account_master
        """
    )
    states: dict[tuple[str, str], dict[str, Any]] = {}
    for row in cursor.fetchall() or []:
        states[(row["platform_name"], str(row["account_key"]))] = row
    return states


def _ensure_fail_count_column(cursor: Any) -> None:
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


def _scan_profile_accounts(platform_name: str, source_path: Path) -> list[dict[str, Any]]:
    if not source_path.exists():
        return []

    accounts: list[dict[str, Any]] = []
    for profile_dir in source_path.iterdir():
        if not profile_dir.is_dir():
            continue
        account_key = _extract_account_key(profile_dir.name, prefix="account_")
        if not account_key:
            continue
        accounts.append(
            {
                "platform_name": platform_name,
                "account_key": account_key,
                "account_name": profile_dir.name,
                "created_at": _created_at(profile_dir),
                "resources": [
                    {
                        "resource_type": "profile",
                        "resource_key": "profile_dir",
                        "resource_value": str(profile_dir),
                    }
                ],
            }
        )
    return accounts


def _scan_cookie_accounts(platform_name: str, source_path: Path) -> list[dict[str, Any]]:
    if not source_path.exists():
        return []

    accounts: list[dict[str, Any]] = []
    for cookie_file in source_path.glob("cookies*.json"):
        if not cookie_file.is_file():
            continue
        account_key = _extract_account_key(cookie_file.stem, prefix="cookies")
        if not account_key:
            continue
        accounts.append(
            {
                "platform_name": platform_name,
                "account_key": account_key,
                "account_name": cookie_file.name,
                "created_at": _created_at(cookie_file),
                "resources": [
                    {
                        "resource_type": "cookie",
                        "resource_key": "cookie_file_path",
                        "resource_value": str(cookie_file),
                    },
                    {
                        "resource_type": "cookie",
                        "resource_key": "cookie_file",
                        "resource_value": cookie_file.name,
                    },
                ],
            }
        )
    return accounts


def _extract_account_key(name: str, *, prefix: str) -> str | None:
    if not name.startswith(prefix):
        return None
    account_key = name[len(prefix) :].strip()
    return account_key or None


def _created_at(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_ctime)


def _natural_key(value: str) -> tuple[int, int | str]:
    if value.isdigit():
        return (0, int(value))
    match = re.search(r"(\d+)$", value)
    if match:
        return (0, int(match.group(1)))
    return (1, value)


def _db_config(db_config: dict[str, Any]) -> dict[str, Any]:
    import pymysql

    config = dict(db_config)
    if "database" in config and "db" not in config:
        config["db"] = config.pop("database")
    config.setdefault("charset", "utf8mb4")
    config.setdefault("cursorclass", pymysql.cursors.DictCursor)
    return config

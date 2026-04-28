#!/usr/bin/env python
"""Sync tep_data_accounts into account_master and account_resource."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from platform.config import DB_CONFIG  # noqa: E402


STATUS_MAP = {
    "正常": "available",
    "备用": "available",
    "可疑": "cooling",
    "异常": "error",
    "已停用": "disabled",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync tep_data_accounts into account_* tables.")
    parser.add_argument("--truncate-first", action="store_true", help="Clear account tables before syncing.")
    parser.add_argument("--dry-run", action="store_true", help="Preview sync without writing database.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    rows = fetch_source_rows()
    plans = [build_account_plan(row) for row in rows]

    if args.dry_run:
        print(f"source_rows={len(rows)} plans={len(plans)}")
        for item in plans[:10]:
            print(item["master"], item["resources"])
        return 0

    sync_rows(plans, truncate_first=args.truncate_first)
    print(f"synced_accounts={len(plans)}")
    return 0


def fetch_source_rows() -> list[dict[str, Any]]:
    import pymysql

    config = _db_config()
    conn = pymysql.connect(**config)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, account, crawler, account_type, status, fail_count, created_at
                FROM tep_data_accounts
                ORDER BY crawler ASC, id ASC
                """
            )
            return list(cursor.fetchall() or [])
    finally:
        conn.close()


def sync_rows(plans: list[dict[str, Any]], *, truncate_first: bool) -> None:
    import pymysql

    config = _db_config()
    conn = pymysql.connect(**config)
    try:
        with conn.cursor() as cursor:
            if truncate_first:
                cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
                cursor.execute("TRUNCATE TABLE account_status_log")
                cursor.execute("TRUNCATE TABLE account_resource")
                cursor.execute("TRUNCATE TABLE account_master")
                cursor.execute("SET FOREIGN_KEY_CHECKS = 1")

            for item in plans:
                master = item["master"]
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
                        disabled_reason
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        account_name = VALUES(account_name),
                        account_status = VALUES(account_status),
                        priority = VALUES(priority),
                        max_concurrent_tasks = VALUES(max_concurrent_tasks),
                        current_task_count = VALUES(current_task_count),
                        disabled_reason = VALUES(disabled_reason),
                        updated_at = CURRENT_TIMESTAMP,
                        id = LAST_INSERT_ID(id)
                    """,
                    (
                        master["platform_name"],
                        master["account_key"],
                        master["account_name"],
                        master["account_status"],
                        master["priority"],
                        master["max_concurrent_tasks"],
                        master["current_task_count"],
                        master["disabled_reason"],
                    ),
                )
                account_id = int(cursor.lastrowid)
                for resource in item["resources"]:
                    cursor.execute(
                        """
                        INSERT INTO account_resource (
                            account_id,
                            resource_type,
                            resource_key,
                            resource_value,
                            resource_status
                        ) VALUES (%s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            resource_value = VALUES(resource_value),
                            resource_status = VALUES(resource_status),
                            updated_at = CURRENT_TIMESTAMP
                        """,
                        (
                            account_id,
                            resource["resource_type"],
                            resource["resource_key"],
                            resource["resource_value"],
                            resource["resource_status"],
                        ),
                    )
        conn.commit()
    finally:
        conn.close()


def build_account_plan(row: dict[str, Any]) -> dict[str, Any]:
    source_status = str(row.get("status") or "").strip()
    crawler = str(row.get("crawler") or "").strip().lower()
    account_name = str(row.get("account") or "").strip()
    account_number = extract_account_number(account_name)
    account_key = str(account_number if account_number is not None else row["id"])
    account_type = str(row.get("account_type") or "").strip()
    account_status = STATUS_MAP.get(source_status, "available")
    priority = build_priority(account_type=account_type, source_status=source_status)

    master = {
        "platform_name": crawler,
        "account_key": account_key,
        "account_name": account_name,
        "account_status": account_status,
        "priority": priority,
        "max_concurrent_tasks": 1,
        "current_task_count": 0,
        "disabled_reason": None if account_status == "available" else f"source_status:{source_status}",
    }
    return {
        "master": master,
        "resources": build_resources(crawler=crawler, account_key=account_key),
    }


def build_resources(*, crawler: str, account_key: str) -> list[dict[str, str]]:
    resources: list[dict[str, str]] = []
    if crawler == "deepseek":
        cookie_path = PROJECT_ROOT / "deepseek" / "deepseek_cookie_file" / f"cookies{account_key}.json"
        if cookie_path.exists():
            resources.append(resource("cookie", "cookie_file_path", str(cookie_path)))
            resources.append(resource("cookie", "cookie_file", cookie_path.name))
    elif crawler == "yuanbao":
        cookie_path = PROJECT_ROOT / "yuanbao" / "yuanbao_cookie_file" / f"cookies{account_key}.json"
        if cookie_path.exists():
            resources.append(resource("cookie", "cookie_file_path", str(cookie_path)))
            resources.append(resource("cookie", "cookie_file", cookie_path.name))
    elif crawler == "doubao":
        profile_dir = Path(f"D:/doubao_real_profiles/account_{account_key}")
        if profile_dir.exists():
            resources.append(resource("profile", "profile_dir", str(profile_dir)))
    elif crawler == "afu":
        profile_dir = Path(f"D:/afu_real_profiles/account_{account_key}")
        if profile_dir.exists():
            resources.append(resource("profile", "profile_dir", str(profile_dir)))
    return resources


def resource(resource_type: str, resource_key: str, resource_value: str) -> dict[str, str]:
    return {
        "resource_type": resource_type,
        "resource_key": resource_key,
        "resource_value": resource_value,
        "resource_status": "active",
    }


def build_priority(*, account_type: str, source_status: str) -> int:
    base = 80 if account_type == "主账号" else 30
    if source_status == "正常":
        return base
    if source_status == "备用":
        return max(base - 10, 10)
    if source_status == "可疑":
        return max(base - 30, 5)
    if source_status == "异常":
        return 1
    if source_status == "已停用":
        return 0
    return base


def extract_account_number(account_name: str) -> int | None:
    match = re.search(r"(\d+)$", account_name)
    if not match:
        return None
    return int(match.group(1))


def _db_config() -> dict[str, Any]:
    import pymysql

    config = dict(DB_CONFIG)
    if "database" in config and "db" not in config:
        config["db"] = config.pop("database")
    config.setdefault("charset", "utf8mb4")
    config.setdefault("cursorclass", pymysql.cursors.DictCursor)
    return config


if __name__ == "__main__":
    raise SystemExit(main())

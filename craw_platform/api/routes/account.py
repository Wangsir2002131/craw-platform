"""Account API routes."""

from __future__ import annotations

from contextlib import contextmanager
from math import ceil
import re
from typing import Any, Iterator

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from craw_platform.account.account_state_machine import AccountStateMachine
from craw_platform.config import DB_CONFIG


router = APIRouter(prefix="/accounts", tags=["accounts"])
dashboard_router = APIRouter(prefix="/api/accounts", tags=["dashboard-accounts"])



class AccountStatusUpdateRequest(BaseModel):
    status: str
    reason: str | None = None
    task_id: int | None = None


class AccountQueryService:
    def __init__(
        self,
        db_config: dict[str, Any] | None = None,
        connection_factory: Any | None = None,
    ) -> None:
        self.db_config = db_config or DB_CONFIG
        self.connection_factory = connection_factory

    def list_accounts(
        self,
        platform_name: str | None = None,
        account_status: str | None = None,
    ) -> list[dict[str, Any]]:
        conditions = ["1 = 1"]
        params: list[Any] = []
        if platform_name:
            conditions.append("platform_name = %s")
            params.append(platform_name)
        if account_status:
            conditions.append("account_status = %s")
            params.append(account_status)

        sql = f"""
        SELECT *
        FROM account_master
        WHERE {' AND '.join(conditions)}
        ORDER BY priority DESC, id ASC
        """
        with self.cursor() as cursor:
            cursor.execute(sql, tuple(params))
            return list(cursor.fetchall() or [])

    def list_dashboard_accounts(self) -> list[dict[str, Any]]:
        sql = """
        SELECT
            id,
            account_name AS account,
            platform_name AS crawler,
            '-' AS account_type,
            CASE account_status
                WHEN 'available' THEN '正常'
                WHEN 'disabled' THEN '已停用'
                WHEN 'cooling' THEN '可疑'
                WHEN 'error' THEN '异常'
                WHEN 'allocated' THEN '占用中'
                ELSE account_status
            END AS status,
            0 AS fail_count,
            created_at
        FROM account_master
        ORDER BY created_at DESC, id DESC
        """
        with self.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall() or []

        items: list[dict[str, Any]] = []
        for row in rows:
            items.append(self._format_dashboard_account(row))
        return items

    def update_dashboard_account_status(
        self,
        account_id: int,
        new_status: str,
        *,
        reason: str | None = None,
    ) -> dict[str, Any]:
        if new_status not in {"正常", "已停用"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="dashboard account status must be 正常 or 已停用",
            )

        with self.cursor() as cursor:
            cursor.execute("SELECT * FROM account_master WHERE id = %s FOR UPDATE", (account_id,))
            account = cursor.fetchone()
            if not account:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="dashboard account not found")

            cursor.execute(
                """
                UPDATE account_master
                SET account_status = %s,
                    disabled_reason = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (
                    "disabled" if new_status == "已停用" else "available",
                    reason if new_status == "已停用" else None,
                    account_id,
                ),
            )

            updated = dict(account)
            updated["account"] = updated.get("account_name")
            updated["crawler"] = updated.get("platform_name")
            updated["account_type"] = "-"
            updated["status"] = new_status
            updated["fail_count"] = 0
            item = self._format_dashboard_account(updated)
            item["masterRowsUpdated"] = 1
            return item

    def _format_dashboard_account(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row.get("id"),
            "account": row.get("account") or "-",
            "crawler": row.get("crawler") or "-",
            "accountType": row.get("account_type") or "-",
            "status": row.get("status") or "-",
            "failCount": int(row.get("fail_count") or 0),
            "createdAt": row.get("created_at").isoformat() if row.get("created_at") else None,
            "dataMode": "account_master",
            "statusSource": "account_master.account_status",
        }

    def _sync_account_master_status(
        self,
        cursor: Any,
        account: dict[str, Any],
        *,
        master_status: str,
        reason: str,
    ) -> int:
        crawler = str(account.get("crawler") or "").strip().lower()
        if not crawler:
            return 0

        account_keys = self._candidate_account_keys(account)
        if not account_keys:
            return 0

        placeholders = ", ".join(["%s"] * len(account_keys))
        cursor.execute(
            f"""
            UPDATE account_master
            SET account_status = %s,
                disabled_reason = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE platform_name = %s
              AND account_key IN ({placeholders})
            """,
            (
                master_status,
                reason if master_status == "disabled" else None,
                crawler,
                *account_keys,
            ),
        )
        return int(getattr(cursor, "rowcount", 0) or 0)

    def _candidate_account_keys(self, account: dict[str, Any]) -> list[str]:
        keys: list[str] = []
        account_name = str(account.get("account") or "").strip()
        match = re.search(r"(\d+)$", account_name)
        if match:
            keys.append(match.group(1))

        source_id = str(account.get("id") or "").strip()
        if source_id and source_id not in keys:
            keys.append(source_id)
        return keys

    def update_account_status(
        self,
        account_id: int,
        new_status: str,
        *,
        reason: str | None = None,
        task_id: int | None = None,
    ) -> dict[str, Any]:
        with self.cursor() as cursor:
            cursor.execute("SELECT * FROM account_master WHERE id = %s FOR UPDATE", (account_id,))
            account = cursor.fetchone()
            if not account:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")

            machine = AccountStateMachine(cursor, operator="api")
            updated = machine.transition(account, new_status, reason=reason, task_id=task_id)
            return updated

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
            raise RuntimeError("pymysql is required for account API operations.") from exc

        config = dict(self.db_config)
        if "database" in config and "db" not in config:
            config["db"] = config.pop("database")
        config.setdefault("charset", "utf8mb4")
        config.setdefault("cursorclass", pymysql.cursors.DictCursor)
        return pymysql.connect(**config)


def get_account_service() -> AccountQueryService:
    return AccountQueryService()


@router.get("")
def list_accounts(
    platform_name: str | None = None,
    account_status: str | None = None,
    service: AccountQueryService = Depends(get_account_service),
) -> dict[str, Any]:
    account_service = service
    return {
        "accounts": account_service.list_accounts(
            platform_name=platform_name,
            account_status=account_status,
        )
    }


@dashboard_router.get("")
def list_dashboard_accounts(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=5, ge=1, le=100),
    account_filter: str = Query(default="", alias="account"),
    crawler_filter: str = Query(default="", alias="crawler"),
    status_filter: str = Query(default="", alias="status"),
    account_type_filter: str = Query(default="", alias="account_type"),
    service: AccountQueryService = Depends(get_account_service),
) -> dict[str, Any]:
    account_service = service
    all_items = account_service.list_dashboard_accounts()
    normalized_account_filter = account_filter.strip().lower()

    crawler_options = sorted({item["crawler"] for item in all_items if item["crawler"] and item["crawler"] != "-"})
    status_options = sorted({item["status"] for item in all_items if item["status"] and item["status"] != "-"})
    account_type_options = sorted(
        {item["accountType"] for item in all_items if item["accountType"] and item["accountType"] != "-"}
    )

    filtered_items = all_items
    if normalized_account_filter:
        filtered_items = [
            item
            for item in filtered_items
            if normalized_account_filter in str(item.get("account") or "").lower()
            or normalized_account_filter in str(item.get("id") or "").lower()
        ]
    if crawler_filter:
        filtered_items = [item for item in filtered_items if item["crawler"] == crawler_filter]
    if status_filter:
        filtered_items = [item for item in filtered_items if item["status"] == status_filter]
    if account_type_filter:
        filtered_items = [item for item in filtered_items if item["accountType"] == account_type_filter]

    total = len(filtered_items)
    total_pages = max(1, ceil(total / page_size)) if total else 1
    page = min(page, total_pages)
    start = (page - 1) * page_size
    end = start + page_size
    paged_items = filtered_items[start:end]

    stats_map: dict[str, int] = {}
    for item in filtered_items:
        stats_map[item["status"]] = stats_map.get(item["status"], 0) + 1

    stats = [
        {"label": label, "count": count}
        for label, count in sorted(stats_map.items(), key=lambda pair: (-pair[1], pair[0]))
    ]

    return {
        "items": paged_items,
        "stats": stats,
        "filters": {
            "crawlerOptions": crawler_options,
            "statusOptions": status_options,
            "accountTypeOptions": account_type_options,
            "selected": {
                "account": account_filter,
                "crawler": crawler_filter,
                "status": status_filter,
                "accountType": account_type_filter,
            },
        },
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "total": total,
            "totalPages": total_pages,
            "hasPrev": page > 1,
            "hasNext": page < total_pages,
        },
        "meta": {
            "dataMode": "account_master",
            "statusSource": "account_master.account_status",
        },
    }


@dashboard_router.patch("/{account_id}/status")
def update_dashboard_account_status(
    account_id: int,
    payload: AccountStatusUpdateRequest,
    service: AccountQueryService = Depends(get_account_service),
) -> dict[str, Any]:
    account_service = service
    account = account_service.update_dashboard_account_status(
        account_id,
        payload.status,
        reason=payload.reason,
    )
    return {"account": account}


@router.patch("/{account_id}/status")
def update_account_status(
    account_id: int,
    payload: AccountStatusUpdateRequest,
    service: AccountQueryService = Depends(get_account_service),
) -> dict[str, Any]:
    account_service = service
    try:
        account = account_service.update_account_status(
            account_id,
            payload.status,
            reason=payload.reason,
            task_id=payload.task_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {"account": account}

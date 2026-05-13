"""Account status transition rules for Phase C."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class AccountStateMachine:
    """Validate and persist account status transitions."""

    TRANSITIONS = {
        "available": {"allocated", "cooling", "disabled", "error"},
        "allocated": {"available", "cooling", "disabled", "error"},
        "cooling": {"available", "allocated", "disabled", "error"},
        "disabled": {"available"},
        "error": {"available", "allocated", "disabled", "cooling"},
    }

    def __init__(self, cursor: Any | None = None, operator: str = "account_state_machine") -> None:
        self.cursor = cursor
        self.operator = operator

    def can_transition(self, old_status: str | None, new_status: str) -> bool:
        if old_status is None or old_status == new_status:
            return True
        return new_status in self.TRANSITIONS.get(old_status, set())

    def transition(
        self,
        account: dict[str, Any],
        new_status: str,
        reason: str | None = None,
        task_id: int | None = None,
    ) -> dict[str, Any]:
        old_status = account.get("account_status")
        if not self.can_transition(old_status, new_status):
            raise ValueError(f"invalid account status transition: {old_status} -> {new_status}")

        account_id = account.get("id") or account.get("account_master_id")
        if self.cursor is not None and account_id is not None:
            self.cursor.execute(
                """
                UPDATE account_master
                SET account_status = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (new_status, account_id),
            )
            self.cursor.execute(
                """
                INSERT INTO account_status_log (
                    account_id,
                    old_status,
                    new_status,
                    task_id,
                    reason,
                    operator
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (account_id, old_status, new_status, task_id, reason, self.operator),
            )

        updated = dict(account)
        updated["account_status"] = new_status
        updated["status_changed_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
        return updated

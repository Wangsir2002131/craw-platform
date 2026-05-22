"""Database-backed storage for alert events."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable, Iterator


class AlertEventStore:
    """Persist and query alert events in MySQL."""

    def __init__(
        self,
        db_config: dict[str, Any] | None = None,
        connection_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.db_config = db_config or {}
        self.connection_factory = connection_factory

    def ensure_table(self) -> None:
        """Create the alert_events table when it is missing."""
        sql = """
        CREATE TABLE IF NOT EXISTS alert_events (
            id VARCHAR(36) PRIMARY KEY COMMENT 'UUID event ID',
            name VARCHAR(255) NOT NULL COMMENT 'Alert name',
            level VARCHAR(32) NOT NULL COMMENT 'Alert level: yellow/red/error',
            category VARCHAR(32) NOT NULL COMMENT 'Alert category: task/queue/account/system',
            message TEXT NOT NULL COMMENT 'Alert message',
            metadata_json JSON COMMENT 'Alert metadata JSON',
            triggered_at DATETIME(3) NOT NULL COMMENT 'When alert was triggered',
            acknowledged TINYINT(1) NOT NULL DEFAULT 0 COMMENT 'Whether acknowledged',
            acknowledged_at DATETIME(3) DEFAULT NULL COMMENT 'When acknowledged',
            acknowledged_by VARCHAR(64) DEFAULT NULL COMMENT 'Who acknowledged',
            INDEX idx_alert_name (name),
            INDEX idx_alert_category (category),
            INDEX idx_alert_level (level),
            INDEX idx_alert_acknowledged (acknowledged),
            INDEX idx_alert_triggered_at (triggered_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Alert events table'
        """
        with self.cursor() as cursor:
            cursor.execute(sql)

    # ------------------------------------------------------------------
    #  Insert
    # ------------------------------------------------------------------

    def insert_alert_event(self, event: dict[str, Any]) -> None:
        """Insert one alert event into the database."""
        sql = """
        INSERT INTO alert_events (
            id, name, level, category, message, metadata_json,
            triggered_at, acknowledged, acknowledged_at, acknowledged_by
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        metadata_value = event.get("metadata")
        if metadata_value is not None and isinstance(metadata_value, dict):
            metadata_json = json.dumps(metadata_value, ensure_ascii=False, separators=(",", ":"))
        elif metadata_value is not None:
            metadata_json = json.dumps(metadata_value, ensure_ascii=False, separators=(",", ":"))
        else:
            metadata_json = None

        params = (
            event["id"],
            event["name"],
            event["level"],
            event["category"],
            event["message"],
            metadata_json,
            event.get("triggered_at"),
            1 if event.get("acknowledged") else 0,
            event.get("acknowledged_at"),
            event.get("acknowledged_by"),
        )
        with self.cursor() as cursor:
            cursor.execute(sql, params)

    # ------------------------------------------------------------------
    #  Query
    # ------------------------------------------------------------------

    def list_alert_events(
        self,
        *,
        category: str | None = None,
        level: str | None = None,
        acknowledged: bool | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List alert events with optional filters (most recent first)."""
        sql = """
        SELECT id, name, level, category, message, metadata_json,
               triggered_at, acknowledged, acknowledged_at, acknowledged_by
        FROM alert_events
        WHERE 1=1
        """
        params: list[Any] = []

        if category is not None:
            sql += " AND category = %s"
            params.append(category)
        if level is not None:
            sql += " AND level = %s"
            params.append(level)
        if acknowledged is not None:
            sql += " AND acknowledged = %s"
            params.append(1 if acknowledged else 0)

        sql += " ORDER BY triggered_at DESC LIMIT %s"
        params.append(int(limit))

        with self.cursor() as cursor:
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall()
        return [self._decode_row(row) for row in (rows or [])]

    def count_unacknowledged_alert_events(
        self,
        *,
        category: str | None = None,
        level: str | None = None,
    ) -> int:
        """Get count of unacknowledged alerts matching filters."""
        sql = "SELECT COUNT(*) AS total FROM alert_events WHERE acknowledged = 0"
        params: list[Any] = []

        if category is not None:
            sql += " AND category = %s"
            params.append(category)
        if level is not None:
            sql += " AND level = %s"
            params.append(level)

        with self.cursor() as cursor:
            cursor.execute(sql, tuple(params))
            row = cursor.fetchone()
        return int((row or {}).get("total", 0))

    def count_alert_events_by_name(self, name: str) -> int:
        """Get trigger count for a specific alert name."""
        sql = "SELECT COUNT(*) AS total FROM alert_events WHERE name = %s"
        with self.cursor() as cursor:
            cursor.execute(sql, (name,))
            row = cursor.fetchone()
        return int((row or {}).get("total", 0))

    def latest_alert_triggered_at(self, name: str) -> datetime | None:
        """Return the latest triggered_at for a given alert name."""
        sql = """
        SELECT triggered_at FROM alert_events
        WHERE name = %s
        ORDER BY triggered_at DESC
        LIMIT 1
        """
        with self.cursor() as cursor:
            cursor.execute(sql, (name,))
            row = cursor.fetchone()
        if not row or not row.get("triggered_at"):
            return None
        value = row["triggered_at"]
        if isinstance(value, datetime):
            return value
        return value

    # ------------------------------------------------------------------
    #  Acknowledgement
    # ------------------------------------------------------------------

    def acknowledge_alert_event(self, event_id: str, acknowledged_by: str = "system") -> bool:
        """Acknowledge a single alert event by ID. Returns True if updated."""
        sql = """
        UPDATE alert_events
        SET acknowledged = 1, acknowledged_at = NOW(3), acknowledged_by = %s
        WHERE id = %s AND acknowledged = 0
        """
        with self.cursor() as cursor:
            cursor.execute(sql, (acknowledged_by, event_id))
            return int(getattr(cursor, "rowcount", 0) or 0) > 0

    def acknowledge_alert_events(
        self,
        *,
        category: str | None = None,
        level: str | None = None,
        acknowledged_by: str = "system",
    ) -> int:
        """Acknowledge all matching unacknowledged alerts. Returns count updated."""
        sql = "UPDATE alert_events SET acknowledged = 1, acknowledged_at = NOW(3), acknowledged_by = %s WHERE acknowledged = 0"
        params: list[Any] = [acknowledged_by]

        if category is not None:
            sql += " AND category = %s"
            params.append(category)
        if level is not None:
            sql += " AND level = %s"
            params.append(level)

        with self.cursor() as cursor:
            cursor.execute(sql, tuple(params))
            return int(getattr(cursor, "rowcount", 0) or 0)

    # ------------------------------------------------------------------
    #  Delete
    # ------------------------------------------------------------------

    def delete_alert_event(self, event_id: str) -> bool:
        """Delete a single alert event by ID. Returns True if deleted."""
        sql = "DELETE FROM alert_events WHERE id = %s"
        with self.cursor() as cursor:
            cursor.execute(sql, (event_id,))
            return int(getattr(cursor, "rowcount", 0) or 0) > 0

    # ------------------------------------------------------------------
    #  Summary
    # ------------------------------------------------------------------

    def get_alert_event_summary(self, *, latest_limit: int = 10) -> dict[str, Any]:
        """Get alert summary for dashboard display."""
        # Total and unacknowledged counts
        with self.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS total FROM alert_events")
            total = int((cursor.fetchone() or {}).get("total", 0))

            cursor.execute("SELECT COUNT(*) AS total FROM alert_events WHERE acknowledged = 0")
            unacknowledged = int((cursor.fetchone() or {}).get("total", 0))

        # Counts by level
        by_level: dict[str, int] = {}
        with self.cursor() as cursor:
            cursor.execute("SELECT level, COUNT(*) AS cnt FROM alert_events GROUP BY level")
            for row in cursor.fetchall() or []:
                by_level[str(row["level"])] = int(row["cnt"])

        # Counts by category
        by_category: dict[str, int] = {}
        with self.cursor() as cursor:
            cursor.execute("SELECT category, COUNT(*) AS cnt FROM alert_events GROUP BY category")
            for row in cursor.fetchall() or []:
                by_category[str(row["category"])] = int(row["cnt"])

        # Counters by name
        counters: dict[str, int] = {}
        with self.cursor() as cursor:
            cursor.execute("SELECT name, COUNT(*) AS cnt FROM alert_events GROUP BY name")
            for row in cursor.fetchall() or []:
                counters[str(row["name"])] = int(row["cnt"])

        # Latest events
        latest = self.list_alert_events(limit=latest_limit)

        return {
            "total_events": total,
            "unacknowledged": unacknowledged,
            "by_level": by_level,
            "by_category": by_category,
            "counters": counters,
            "latest_events": latest,
        }

    # ------------------------------------------------------------------
    #  Clear
    # ------------------------------------------------------------------

    def clear_alert_events(self, *, before: datetime | None = None) -> int:
        """Clear old alert events. If before is None, clears all."""
        sql = "DELETE FROM alert_events"
        params: tuple[Any, ...] = ()
        if before is not None:
            sql += " WHERE triggered_at < %s"
            params = (before,)

        with self.cursor() as cursor:
            cursor.execute(sql, params)
            return int(getattr(cursor, "rowcount", 0) or 0)

    # ------------------------------------------------------------------
    #  DB helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _decode_row(row: dict[str, Any] | None) -> dict[str, Any]:
        """Decode a database row into a dict suitable for AlertEvent consumption."""
        if not row:
            return {}
        decoded = dict(row)
        metadata_raw = decoded.pop("metadata_json", None)
        if isinstance(metadata_raw, str):
            try:
                decoded["metadata"] = json.loads(metadata_raw)
            except (json.JSONDecodeError, TypeError):
                decoded["metadata"] = {}
        elif metadata_raw is not None:
            decoded["metadata"] = metadata_raw
        else:
            decoded["metadata"] = {}
        decoded["acknowledged"] = bool(decoded.get("acknowledged"))
        # Convert triggered_at / acknowledged_at to ISO string for AlertEvent compatibility
        for date_key in ("triggered_at", "acknowledged_at"):
            value = decoded.get(date_key)
            if isinstance(value, datetime):
                decoded[date_key] = value.isoformat()
        return decoded

    @contextmanager
    def cursor(self) -> Iterator[Any]:
        """Yield a DictCursor and commit or rollback around the operation."""
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
            connection.close()

    def _connect(self) -> Any:
        if self.connection_factory is not None:
            return self.connection_factory()

        try:
            import pymysql
        except ImportError as exc:
            raise RuntimeError(
                "pymysql is required for alert event storage. Install pymysql or "
                "provide a connection_factory for tests."
            ) from exc

        config = dict(self.db_config)
        if "database" in config and "db" not in config:
            config["db"] = config.pop("database")
        config.setdefault("charset", "utf8mb4")
        config.setdefault("cursorclass", pymysql.cursors.DictCursor)
        return pymysql.connect(**config)

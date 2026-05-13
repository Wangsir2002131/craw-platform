"""Persistent storage for Phase D scheduling strategy configuration."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Callable, Iterator


class StrategyConfigStore:
    """Store versioned scheduler strategy configuration in MySQL."""

    def __init__(
        self,
        db_config: dict[str, Any] | None = None,
        connection_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.db_config = db_config or {}
        self.connection_factory = connection_factory

    def ensure_table(self) -> None:
        """Create the strategy configuration table when it is missing."""
        sql = """
        CREATE TABLE IF NOT EXISTS schedule_strategy_config (
            id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT 'Primary key ID',
            config_name VARCHAR(64) NOT NULL COMMENT 'Strategy config name',
            config_payload JSON NOT NULL COMMENT 'Strategy config JSON payload',
            enabled TINYINT(1) NOT NULL DEFAULT 1 COMMENT 'Whether config is active',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT 'Created time',
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'Updated time',
            UNIQUE KEY uk_config_name (config_name),
            INDEX idx_enabled (enabled)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Schedule strategy config table'
        """
        with self.cursor() as cursor:
            cursor.execute(sql)

    def save_config(self, config_name: str, config_payload: dict[str, Any], enabled: bool = True) -> int:
        """Create or update one named strategy config and return its ID."""
        if not config_name.strip():
            raise ValueError("config_name is required")
        if not isinstance(config_payload, dict):
            raise TypeError("config_payload must be a dict")

        sql = """
        INSERT INTO schedule_strategy_config (
            config_name,
            config_payload,
            enabled
        ) VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
            config_payload = VALUES(config_payload),
            enabled = VALUES(enabled),
            updated_at = CURRENT_TIMESTAMP,
            id = LAST_INSERT_ID(id)
        """
        params = (
            config_name.strip(),
            json.dumps(config_payload, ensure_ascii=False, separators=(",", ":")),
            1 if enabled else 0,
        )
        with self.cursor() as cursor:
            cursor.execute(sql, params)
            config_id = getattr(cursor, "lastrowid", None)
            return int(config_id or 0)

    def get_config(self, config_name: str) -> dict[str, Any] | None:
        """Return one config by name."""
        sql = """
        SELECT id, config_name, config_payload, enabled, created_at, updated_at
        FROM schedule_strategy_config
        WHERE config_name = %s
        """
        with self.cursor() as cursor:
            cursor.execute(sql, (config_name.strip(),))
            row = cursor.fetchone()
        return self._decode_row(row)

    def get_active_config(self) -> dict[str, Any]:
        """Return the newest enabled config payload, or an empty config."""
        sql = """
        SELECT id, config_name, config_payload, enabled, created_at, updated_at
        FROM schedule_strategy_config
        WHERE enabled = 1
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """
        with self.cursor() as cursor:
            cursor.execute(sql)
            row = cursor.fetchone()
        decoded = self._decode_row(row)
        return dict(decoded["config_payload"]) if decoded else {}

    def list_configs(self, enabled: bool | None = None) -> list[dict[str, Any]]:
        """List strategy configs, optionally filtered by enabled state."""
        sql = """
        SELECT id, config_name, config_payload, enabled, created_at, updated_at
        FROM schedule_strategy_config
        """
        params: tuple[Any, ...] = ()
        if enabled is not None:
            sql += " WHERE enabled = %s"
            params = (1 if enabled else 0,)
        sql += " ORDER BY updated_at DESC, id DESC"

        with self.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        return [row for row in (self._decode_row(row) for row in rows or []) if row is not None]

    @staticmethod
    def _decode_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        decoded = dict(row)
        payload = decoded.get("config_payload")
        if isinstance(payload, str):
            decoded["config_payload"] = json.loads(payload)
        elif payload is None:
            decoded["config_payload"] = {}
        decoded["enabled"] = bool(decoded.get("enabled"))
        return decoded

    @contextmanager
    def cursor(self) -> Iterator[Any]:
        """Yield a database cursor and commit or rollback around the operation."""
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
                "pymysql is required for strategy config storage. Install pymysql or "
                "provide a connection_factory for tests."
            ) from exc

        config = dict(self.db_config)
        if "database" in config and "db" not in config:
            config["db"] = config.pop("database")
        config.setdefault("charset", "utf8mb4")
        config.setdefault("cursorclass", pymysql.cursors.DictCursor)
        return pymysql.connect(**config)

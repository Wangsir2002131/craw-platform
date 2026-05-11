-- Add task priority field for Phase D scheduling strategy.
-- This migration is idempotent because earlier task table definitions may
-- already include the priority column in fresh installations.
SET @priority_column_exists := (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'task_master_status'
      AND COLUMN_NAME = 'priority'
);

SET @add_priority_sql := IF(
    @priority_column_exists = 0,
    'ALTER TABLE task_master_status ADD COLUMN priority INT NOT NULL DEFAULT 50 COMMENT ''Priority from 0 to 100'' AFTER server_id',
    'SELECT ''task_master_status.priority already exists'' AS message'
);

PREPARE add_priority_stmt FROM @add_priority_sql;
EXECUTE add_priority_stmt;
DEALLOCATE PREPARE add_priority_stmt;

SET @priority_index_exists := (
    SELECT COUNT(*)
    FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'task_master_status'
      AND INDEX_NAME = 'idx_priority'
);

SET @add_priority_index_sql := IF(
    @priority_index_exists = 0,
    'ALTER TABLE task_master_status ADD INDEX idx_priority (priority)',
    'SELECT ''task_master_status.idx_priority already exists'' AS message'
);

PREPARE add_priority_index_stmt FROM @add_priority_index_sql;
EXECUTE add_priority_index_stmt;
DEALLOCATE PREPARE add_priority_index_stmt;

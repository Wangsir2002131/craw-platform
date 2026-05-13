-- Add reliable consecutive failure counter for account state transitions.
SET @fail_count_column_exists := (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'account_master'
      AND COLUMN_NAME = 'fail_count'
);

SET @add_fail_count_sql := IF(
    @fail_count_column_exists = 0,
    'ALTER TABLE account_master ADD COLUMN fail_count INT NOT NULL DEFAULT 0 COMMENT ''Consecutive execution failure count'' AFTER current_task_count',
    'SELECT ''account_master.fail_count already exists'' AS message'
);

PREPARE add_fail_count_stmt FROM @add_fail_count_sql;
EXECUTE add_fail_count_stmt;
DEALLOCATE PREPARE add_fail_count_stmt;

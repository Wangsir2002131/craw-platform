-- Task master status table for Phase A unified dispatching.
CREATE TABLE IF NOT EXISTS task_master_status (
    id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT 'Primary key ID',
    product_llm_task_id CHAR(36) NOT NULL COMMENT 'Product LLM task UUID',
    question_id CHAR(36) NOT NULL COMMENT 'Question UUID',
    round_num INT NOT NULL COMMENT 'Round number',
    queue_name VARCHAR(32) NOT NULL COMMENT 'Consumer queue name',
    execute_status VARCHAR(16) NOT NULL DEFAULT 'pending' COMMENT 'Execution status: pending/dispatched/claimed/running/completed/failed',
    account_id VARCHAR(128) NULL COMMENT 'Executor account ID',
    server_id VARCHAR(32) NULL COMMENT 'Executor server ID',
    priority INT DEFAULT 50 COMMENT 'Priority from 0 to 100',
    dispatched_at DATETIME NULL COMMENT 'Dispatched time',
    claimed_at DATETIME NULL COMMENT 'Claimed time',
    completed_at DATETIME NULL COMMENT 'Completed time',
    fail_reason VARCHAR(255) NULL COMMENT 'Failure reason',
    retry_count INT DEFAULT 0 COMMENT 'Retry count',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT 'Created time',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'Updated time',
    UNIQUE KEY uk_task_execution (product_llm_task_id, question_id, round_num),
    INDEX idx_execute_status (execute_status),
    INDEX idx_queue_name (queue_name),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Task master status table';

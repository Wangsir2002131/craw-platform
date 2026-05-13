-- Account master tables for Phase C unified account ownership.
CREATE TABLE IF NOT EXISTS account_master (
    id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT 'Primary key ID',
    platform_name VARCHAR(32) NOT NULL COMMENT 'Crawler platform name: afu/doubao/deepseek/yuanbao',
    account_key VARCHAR(128) NOT NULL COMMENT 'Stable account identifier such as username, phone, or external account ID',
    account_name VARCHAR(128) NULL COMMENT 'Display name or login name',
    account_status VARCHAR(16) NOT NULL DEFAULT 'available' COMMENT 'Account status: available/allocated/cooling/disabled/error',
    priority INT NOT NULL DEFAULT 50 COMMENT 'Allocation priority from 0 to 100',
    max_concurrent_tasks INT NOT NULL DEFAULT 1 COMMENT 'Maximum concurrent tasks for this account',
    current_task_count INT NOT NULL DEFAULT 0 COMMENT 'Current allocated task count',
    fail_count INT NOT NULL DEFAULT 0 COMMENT 'Consecutive execution failure count',
    last_allocated_at DATETIME NULL COMMENT 'Last allocation time',
    last_released_at DATETIME NULL COMMENT 'Last release time',
    disabled_reason VARCHAR(255) NULL COMMENT 'Reason when account is disabled or unavailable',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT 'Created time',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'Updated time',
    UNIQUE KEY uk_platform_account (platform_name, account_key),
    INDEX idx_platform_status (platform_name, account_status),
    INDEX idx_priority (priority),
    INDEX idx_last_allocated_at (last_allocated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Unified account master table';

CREATE TABLE IF NOT EXISTS account_resource (
    id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT 'Primary key ID',
    account_id BIGINT NOT NULL COMMENT 'Account master ID',
    resource_type VARCHAR(32) NOT NULL COMMENT 'Resource type: cookie/token/profile/proxy/config',
    resource_key VARCHAR(64) NOT NULL COMMENT 'Resource key name',
    resource_value TEXT NULL COMMENT 'Resource value or serialized JSON payload',
    resource_status VARCHAR(16) NOT NULL DEFAULT 'active' COMMENT 'Resource status: active/expired/disabled',
    expire_at DATETIME NULL COMMENT 'Resource expiration time',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT 'Created time',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'Updated time',
    UNIQUE KEY uk_account_resource (account_id, resource_type, resource_key),
    INDEX idx_account_id (account_id),
    INDEX idx_resource_status (resource_status),
    INDEX idx_expire_at (expire_at),
    CONSTRAINT fk_account_resource_account
        FOREIGN KEY (account_id) REFERENCES account_master (id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Account resource table';

CREATE TABLE IF NOT EXISTS account_status_log (
    id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT 'Primary key ID',
    account_id BIGINT NOT NULL COMMENT 'Account master ID',
    old_status VARCHAR(16) NULL COMMENT 'Previous account status',
    new_status VARCHAR(16) NOT NULL COMMENT 'New account status',
    task_id BIGINT NULL COMMENT 'Related task ID when status change is task-driven',
    reason VARCHAR(255) NULL COMMENT 'Status change reason',
    operator VARCHAR(64) NULL COMMENT 'Operator or service name',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT 'Created time',
    INDEX idx_account_id (account_id),
    INDEX idx_new_status (new_status),
    INDEX idx_created_at (created_at),
    CONSTRAINT fk_account_status_log_account
        FOREIGN KEY (account_id) REFERENCES account_master (id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Account status transition log table';

-- Alert events table for persistent alert storage.
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Alert events table';
